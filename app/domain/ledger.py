"""Evidence ledger — versioned attributes with status handling (TRD 3).

The ledger enforces:
  - An attribute value never exists without at least one evidence row.
  - Versioning per field: every change creates a new version row.
  - Status handling per TRD 3:
      Seller-confirmed → plain fact
      Verified → plain fact
      Extracted → plain fact for low-risk fields (colour, rack, storage_location)
                  otherwise held for confirmation
      Conflicting → blocked until resolved
      Missing → blocked if the field is required
      Unverified → never stated as fact

Per Rule (g): domain services never call an LLM. This module is pure logic + DB I/O.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.data.audit import write_audit
from app.domain.enums import AttributeStatus, EvidenceKind, PartStatus

logger = logging.getLogger(__name__)

# ── Required fields (Missing blocks the part) ──────────────

REQUIRED_FIELDS: set[str] = {
    "part_number",
    "part_type",
    "make",
    "model",
    "year",
    "position",
    "price",
}

# ── Low-risk fields: Extracted shows as plain fact ─────────
# Per TRD 3: "Extracted shows as plain fact only for low-risk fields
# such as colour and rack"
LOW_RISK_FIELDS: set[str] = {
    "colour",
    "storage_location",
    "rack",
    "features",
}

# ── Fields that must come from seller or test record (Rule d) ──
SELLER_OR_TEST_FIELDS: set[str] = {
    "functionality",
    "authenticity",
    "test_status",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Version model ───────────────────────────────────────────


@dataclass
class AttributeVersion:
    """A single version of an attribute value."""

    id: str = field(default_factory=_new_id)
    attribute_id: str = ""
    part_id: str = ""
    version: int = 1
    value: Any = None
    status: AttributeStatus = AttributeStatus.MISSING
    source: str | None = None
    reason: str = ""
    actor: str = "system"
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "attribute_id": self.attribute_id,
            "part_id": self.part_id,
            "version": self.version,
            "value": self.value,
            "status": self.status.value,
            "source": self.source,
            "reason": self.reason,
            "actor": self.actor,
            "created_at": self.created_at,
        }


# ── Ledger ──────────────────────────────────────────────────


class EvidenceLedger:
    """Versioned evidence ledger for a single part.

    All writes go through this class to enforce the invariant:
    no attribute value without evidence.
    """

    def __init__(self, conn: sqlite3.Connection, part_id: str):
        self.conn = conn
        self.part_id = part_id

    # ── Read ──

    def get_attribute(self, name: str) -> dict | None:
        """Get the current attribute row by name."""
        row = self.conn.execute(
            "SELECT * FROM attributes WHERE part_id = ? AND name = ?",
            (self.part_id, name),
        ).fetchone()
        return dict(row) if row else None

    def get_all_attributes(self) -> list[dict]:
        """Get all attribute rows for this part."""
        rows = self.conn.execute(
            "SELECT * FROM attributes WHERE part_id = ? ORDER BY name",
            (self.part_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_versions(self, name: str) -> list[AttributeVersion]:
        """Get all versions for an attribute, ordered by version number."""
        attr = self.get_attribute(name)
        if not attr:
            return []
        rows = self.conn.execute(
            "SELECT * FROM attribute_versions WHERE attribute_id = ? ORDER BY version",
            (attr["id"],),
        ).fetchall()
        return [
            AttributeVersion(
                id=r["id"],
                attribute_id=r["attribute_id"],
                part_id=r["part_id"],
                version=r["version"],
                value=r["value"],
                status=AttributeStatus(r["status"]),
                source=r["source"],
                reason=r["reason"],
                actor=r["actor"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_current_version(self, name: str) -> AttributeVersion | None:
        """Get the latest version for an attribute."""
        versions = self.get_versions(name)
        return versions[-1] if versions else None

    def get_evidence_for_attribute(self, name: str) -> list[dict]:
        """Get all evidence rows linked to an attribute."""
        attr = self.get_attribute(name)
        if not attr:
            return []
        rows = self.conn.execute(
            """SELECT e.* FROM evidence e
               JOIN attribute_evidence ae ON ae.evidence_id = e.id
               WHERE ae.attribute_id = ?""",
            (attr["id"],),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Write ──

    def set_attribute(
        self,
        name: str,
        value: Any,
        status: AttributeStatus,
        *,
        source: str | None = None,
        evidence_id: str | None = None,
        reason: str = "",
        actor: str = "system",
    ) -> AttributeVersion:
        """Set an attribute value, creating a new version.

        Enforces: no attribute value without evidence.
        If value is not None, evidence_id or source must be provided.
        """
        if value is not None and not evidence_id and not source:
            raise ValueError(
                f"Cannot set attribute '{name}' with value but no evidence"
            )

        attr = self.get_attribute(name)

        if attr is None:
            # Create the attribute row
            attr_id = _new_id()
            self.conn.execute(
                """INSERT INTO attributes (id, part_id, name, value, status)
                   VALUES (?, ?, ?, ?, ?)""",
                (attr_id, self.part_id, name, _stringify(value), status.value),
            )
            attr = {"id": attr_id}
        else:
            # Update the current attribute row
            self.conn.execute(
                """UPDATE attributes SET value = ?, status = ?, updated_at = ?
                   WHERE id = ?""",
                (_stringify(value), status.value, _now(), attr["id"]),
            )

        before = None if attr.get("value", None) is None and "status" not in attr else {
            "value": attr.get("value"), "status": attr.get("status")}

        # Determine next version number
        existing = self.get_versions(name)
        next_version = len(existing) + 1

        # Create the version row
        version = AttributeVersion(
            attribute_id=attr["id"],
            part_id=self.part_id,
            version=next_version,
            value=value,
            status=status,
            source=source,
            reason=reason,
            actor=actor,
        )
        self.conn.execute(
            """INSERT INTO attribute_versions
               (id, attribute_id, part_id, version, value, status, source, reason, actor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version.id,
                version.attribute_id,
                version.part_id,
                version.version,
                _stringify(version.value),
                version.status.value,
                version.source,
                version.reason,
                version.actor,
            ),
        )

        write_audit(self.conn, part_id=self.part_id, event_type="attribute_change", actor=actor,
                    action="value set", source=source or (f"evidence {evidence_id}" if evidence_id else None),
                    field=name, before=before, after={"value": _stringify(value), "status": status.value},
                    detail={"version": next_version, "reason": reason, "evidence_id": evidence_id})

        # Link evidence if provided
        if evidence_id:
            self._link_evidence(attr["id"], evidence_id, value)
        elif source:
            # Find evidence by source_ref match
            ev = self._find_evidence_by_source(source)
            if ev:
                self._link_evidence(attr["id"], ev["id"], value)

        return version

    def confirm_from_seller(
        self,
        name: str,
        value: Any,
        *,
        evidence_id: str | None = None,
        source: str = "seller_answer",
        reason: str = "",
    ) -> AttributeVersion:
        """Seller confirms an attribute value.

        Per TRD 3: Seller-confirmed shows as plain fact.
        A seller confirmation may override extracted data but is logged.
        """
        return self.set_attribute(
            name,
            value,
            AttributeStatus.SELLER_CONFIRMED,
            source=source,
            evidence_id=evidence_id,
            reason=reason or "Seller confirmed",
            actor="seller",
        )

    def verify_from_test(
        self,
        name: str,
        value: Any,
        *,
        evidence_id: str | None = None,
        source: str = "test_record",
        reason: str = "",
    ) -> AttributeVersion:
        """Test record verifies an attribute value.

        Per Rule (d): test status, functionality, authenticity come only
        from a seller answer or a test record.
        """
        return self.set_attribute(
            name,
            value,
            AttributeStatus.VERIFIED,
            source=source,
            evidence_id=evidence_id,
            reason=reason or "Verified by test record",
            actor="test_record",
        )

    # ── Status visibility (TRD 3) ──

    def is_visible_as_fact(self, name: str) -> bool:
        """Check whether an attribute can be stated as a plain fact.

        Per TRD 3:
          Seller-confirmed → yes
          Verified → yes
          Extracted → yes for low-risk fields, no otherwise
          Conflicting → no (blocked until resolved)
          Missing → no
          Unverified → no
        """
        attr = self.get_attribute(name)
        if not attr:
            return False

        status = AttributeStatus(attr["status"])

        if status in (AttributeStatus.SELLER_CONFIRMED, AttributeStatus.VERIFIED):
            return True

        if status == AttributeStatus.EXTRACTED:
            return name in LOW_RISK_FIELDS

        return False

    def is_blocked(self, name: str) -> bool:
        """Check whether an attribute blocks the part from advancing.

        Per TRD 3:
          Conflicting → blocked until resolved
          Missing → blocked if the field is required
        """
        attr = self.get_attribute(name)
        if not attr:
            return name in REQUIRED_FIELDS

        status = AttributeStatus(attr["status"])

        if status == AttributeStatus.CONFLICTING:
            return True

        if status == AttributeStatus.MISSING and name in REQUIRED_FIELDS:
            return True

        return False

    def get_blocked_fields(self) -> list[str]:
        """Get all fields that are currently blocked."""
        return [a["name"] for a in self.get_all_attributes() if self.is_blocked(a["name"])]

    # ── Stale detection (E49) ──

    def get_version_snapshot(self) -> dict[str, str]:
        """Get a snapshot of current version IDs for all attributes.

        Used by listing drafts to detect stale writes (E49).
        """
        snapshot = {}
        for attr in self.get_all_attributes():
            versions = self.get_versions(attr["name"])
            if versions:
                snapshot[attr["name"]] = versions[-1].id
        return snapshot

    def is_stale(self, built_from_versions: dict[str, str]) -> bool:
        """Check whether a draft built from a previous version snapshot is stale.

        E49: stale writes are rejected. If any attribute version has changed
        since the draft was created, the draft is stale.
        """
        current = self.get_version_snapshot()
        for name, version_id in built_from_versions.items():
            if current.get(name) != version_id:
                return True
        return False

    # ── Audit ──

    def log_audit(
        self,
        event_type: str,
        detail: dict[str, Any],
        actor: str = "system",
        *,
        action: str | None = None,
        source: str | None = None,
        field: str | None = None,
        before: Any = None,
        after: Any = None,
    ) -> None:
        """Write an audit event for this part."""
        write_audit(self.conn, part_id=self.part_id, event_type=event_type, actor=actor,
                    action=action or event_type.replace("_", " "), source=source, field=field, before=before, after=after, detail=detail)

    # ── Internal helpers ──

    def _link_evidence(
        self,
        attribute_id: str,
        evidence_id: str,
        claim_value: Any,
    ) -> None:
        """Link an evidence row to an attribute."""
        self.conn.execute(
            """INSERT INTO attribute_evidence (id, attribute_id, evidence_id, claim_type, claim_value)
               VALUES (?, ?, ?, ?, ?)""",
            (_new_id(), attribute_id, evidence_id, "fact", _stringify(claim_value)),
        )

    def _find_evidence_by_source(self, source: str) -> dict | None:
        """Find an evidence row by source_ref substring match."""
        row = self.conn.execute(
            "SELECT * FROM evidence WHERE part_id = ? AND source_ref LIKE ?",
            (self.part_id, f"%{source}%"),
        ).fetchone()
        return dict(row) if row else None


# ── Helpers ─────────────────────────────────────────────────


def _stringify(value: Any) -> str | None:
    """Convert a value to a string for SQLite storage, or None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
