"""CP4 Compatibility — exact part-number lookup against the read-only reference (TRD 5, TRD 7).

  E32  exact match            → verified_compat: fitments listed with the reference version
  E33  no match               → donor info only, labelled "compatibility not verified"
  E34  matches disagree on vehicle or side → hold for specialist review
  E35  reference version change → re-run lookups (see propagation_engine.on_reference_change)

Fitment comes only from this lookup (Rule c). The donor vehicle is returned
as a separate, descriptive fact and is never turned into a fitment.

A verified match is a reference lookup in the TRD 3 sense, so it promotes an
Extracted part number (and an Extracted position the reference agrees with)
to Verified, with a reference_lookup evidence row. It never touches a
Conflicting, Seller-confirmed or Missing value (Rule e).

Per Rule (g): no LLM. Lookups are dictionary reads, well under 100 ms.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any
from dataclasses import asdict, dataclass, field
from enum import Enum

from app.data.reference_data import CompatReference, get_compat_reference
from app.domain.enums import AttributeStatus, ClaimType, EvidenceKind
from app.domain.ledger import EvidenceLedger

NOT_VERIFIED_LABEL = "compatibility not verified"
DONOR_LABEL = "Donor vehicle (descriptive only, not a fitment claim)"
POSITIONS = ("left", "right")
NO_LABEL_VALUE = "no_label"  # seller confirms the part has no label


class CompatStatus(str, Enum):
    VERIFIED = "verified"            # E32
    NOT_VERIFIED = "not_verified"    # E33, or no readable part number yet
    SIDE_MISMATCH = "side_mismatch"  # reference side differs from the part's position
    CONFLICTING = "conflicting"      # E34


@dataclass(frozen=True)
class Fitment:
    make: str
    model: str
    year_from: int | None
    year_to: int | None
    market: str | None
    position: str | None

    def label(self) -> str:
        years = f"{self.year_from}-{self.year_to}" if self.year_from and self.year_to else ""
        parts = [self.make, self.model, years, self.market or "", (self.position or "").title()]
        return " ".join(p for p in parts if p)


@dataclass(frozen=True)
class DonorFact:
    """The donor vehicle, always shown as its own fact, never as fitment."""

    make: str | None
    model: str | None
    year: int | None
    market: str | None
    year_confirmed: bool = False
    label: str = DONOR_LABEL

    def text(self) -> str:
        bits = [str(self.year) if self.year else None, self.make, self.model]
        desc = " ".join(b for b in bits if b) or "not recorded"
        if self.market:
            desc += f", {self.market} spec"
        return desc


@dataclass
class CompatResult:
    status: CompatStatus
    reason: str
    part_number: str | None
    position: str | None
    ref_version: str
    ref_checksum: str
    fitments: list[Fitment] = field(default_factory=list)
    donor: DonorFact | None = None
    elapsed_ms: float = 0.0

    @property
    def edge_case(self) -> str | None:
        return {
            CompatStatus.VERIFIED: "E32",
            CompatStatus.CONFLICTING: "E34",
        }.get(self.status, "E33" if self.reason == "not_in_reference" else None)

    @property
    def claim_type(self) -> str | None:
        return ClaimType.VERIFIED_COMPAT.value if self.status == CompatStatus.VERIFIED else None

    @property
    def specialist_review(self) -> bool:
        return self.status == CompatStatus.CONFLICTING

    @property
    def lookup_done(self) -> bool:
        """True when the lookup reached a definite answer (the C12 label can be shown)."""
        return self.status == CompatStatus.VERIFIED or self.reason in ("not_in_reference", NO_LABEL_VALUE)

    @property
    def label(self) -> str:
        if self.status == CompatStatus.VERIFIED:
            return f"Verified fitments (reference {self.ref_version})"
        return NOT_VERIFIED_LABEL

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "edge_case": self.edge_case,
            "part_number": self.part_number,
            "position": self.position,
            "claim_type": self.claim_type,
            "label": self.label,
            "fitments": [asdict(f) for f in self.fitments],
            "ref_version": self.ref_version,
            "ref_checksum": self.ref_checksum,
            "donor": asdict(self.donor) if self.donor else None,
            "specialist_review": self.specialist_review,
        }


# ── Pure lookup ─────────────────────────────────────────────


def lookup(
    reference: CompatReference,
    part_number: str | None,
    *,
    position: str | None = None,
    donor: DonorFact | None = None,
) -> CompatResult:
    """Exact part-number lookup. Pure function; no I/O."""
    start = time.perf_counter()
    position = position if position in POSITIONS else None

    def result(status: CompatStatus, reason: str, fitments: list[Fitment] | None = None) -> CompatResult:
        return CompatResult(
            status=status, reason=reason, part_number=part_number, position=position,
            ref_version=reference.version, ref_checksum=reference.checksum,
            fitments=fitments or [], donor=donor,
            elapsed_ms=round((time.perf_counter() - start) * 1000, 3),
        )

    if not part_number:
        return result(CompatStatus.NOT_VERIFIED, "part_number_missing")
    if part_number == NO_LABEL_VALUE:
        return result(CompatStatus.NOT_VERIFIED, NO_LABEL_VALUE)
    if "?" in part_number:
        return result(CompatStatus.NOT_VERIFIED, "part_number_unreadable")

    rows = reference.rows_for(part_number)
    if not rows:
        return result(CompatStatus.NOT_VERIFIED, "not_in_reference")

    fitments = [Fitment(r.make, r.model, r.year_from, r.year_to, r.market, r.position) for r in rows]
    vehicles = {(f.make.lower(), f.model.lower()) for f in fitments}
    sides = {f.position for f in fitments if f.position}
    if len(vehicles) > 1:
        return result(CompatStatus.CONFLICTING, "matches_disagree_on_vehicle", fitments)
    if len(sides) > 1:
        return result(CompatStatus.CONFLICTING, "matches_disagree_on_side", fitments)
    if position and sides and position not in sides:
        return result(CompatStatus.SIDE_MISMATCH, "reference_side_differs", fitments)
    return result(CompatStatus.VERIFIED, "exact_match", fitments)


# ── Part-level check (reads and writes the ledger) ──────────


def _attr(conn: sqlite3.Connection, part_id: str, *names: str) -> dict | None:
    for name in names:
        row = conn.execute(
            "SELECT * FROM attributes WHERE part_id = ? AND name = ?", (part_id, name)
        ).fetchone()
        if row is not None:
            return dict(row)
    return None


def part_inputs(conn: sqlite3.Connection, part_id: str) -> tuple[str | None, str | None, DonorFact | None]:
    """The part number, position and donor fact the lookup uses."""
    pn_attr = _attr(conn, part_id, "part_number")
    part = conn.execute("SELECT part_number, side FROM parts WHERE id = ?", (part_id,)).fetchone()
    if pn_attr is not None:
        part_number = pn_attr["value"] if pn_attr["status"] != AttributeStatus.CONFLICTING.value else None
    else:
        part_number = part["part_number"] if part else None

    pos_attr = _attr(conn, part_id, "position", "side")
    position = (pos_attr["value"] if pos_attr else (part["side"] if part else None)) or None
    if pos_attr and pos_attr["status"] == AttributeStatus.CONFLICTING.value:
        position = None

    donor_row = conn.execute(
        "SELECT make, model, year, market FROM donor_vehicles WHERE part_id = ? ORDER BY created_at LIMIT 1",
        (part_id,),
    ).fetchone()
    year_attr = _attr(conn, part_id, "donor_year")
    donor = None
    if donor_row or year_attr:
        year = donor_row["year"] if donor_row else None
        confirmed = False
        if year_attr and year_attr["status"] in (
            AttributeStatus.SELLER_CONFIRMED.value, AttributeStatus.VERIFIED.value
        ) and str(year_attr["value"] or "").isdigit():
            year, confirmed = int(year_attr["value"]), True
        donor = DonorFact(
            make=donor_row["make"] if donor_row else None,
            model=donor_row["model"] if donor_row else None,
            year=year,
            market=donor_row["market"] if donor_row else None,
            year_confirmed=confirmed,
        )
    return part_number, position, donor


def check_part(
    conn: sqlite3.Connection,
    part_id: str,
    reference: CompatReference | None = None,
    *,
    persist: bool = True,
) -> CompatResult:
    """Run CP4 for one part. With persist, stores the result and promotes on a verified match.

    Does not commit; the caller owns the transaction.
    """
    reference = reference or get_compat_reference()
    part_number, position, donor = part_inputs(conn, part_id)
    result = lookup(reference, part_number, position=position, donor=donor)
    if persist:
        _persist(conn, part_id, result)
    return result


def stored_result(conn: sqlite3.Connection, part_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM compat_results WHERE part_id = ?", (part_id,)).fetchone()
    return dict(row) if row else None


def _persist(conn: sqlite3.Connection, part_id: str, result: CompatResult) -> None:
    previous = stored_result(conn, part_id)
    unchanged = previous is not None and (
        previous["part_number"], previous["position"], previous["status"], previous["ref_version"]
    ) == (result.part_number, result.position, result.status.value, result.ref_version)

    conn.execute(
        """INSERT INTO compat_results
               (part_id, part_number, position, status, reason, fitments_json, ref_version, ref_checksum, checked_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(part_id) DO UPDATE SET
               part_number = excluded.part_number, position = excluded.position,
               status = excluded.status, reason = excluded.reason,
               fitments_json = excluded.fitments_json, ref_version = excluded.ref_version,
               ref_checksum = excluded.ref_checksum, checked_at = excluded.checked_at""",
        (
            part_id, result.part_number, result.position, result.status.value, result.reason,
            json.dumps([asdict(f) for f in result.fitments]), result.ref_version, result.ref_checksum,
        ),
    )
    if unchanged:
        if result.status == CompatStatus.VERIFIED:  # a value extracted after the first lookup still gets promoted
            latest = conn.execute(
                "SELECT id FROM evidence WHERE part_id = ? AND kind = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (part_id, EvidenceKind.REFERENCE_LOOKUP.value),
            ).fetchone()
            if latest:
                _promote_on_match(conn, part_id, result, latest["id"])
        return

    evidence_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO evidence (id, part_id, kind, source_ref, content, raw_data)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            evidence_id, part_id, EvidenceKind.REFERENCE_LOOKUP.value,
            f"compat_ref:{result.ref_version}:{result.part_number or '-'}",
            f"{result.label}: " + ("; ".join(f.label() for f in result.fitments) or result.reason),
            json.dumps(result.to_dict(), ensure_ascii=False),
        ),
    )
    conn.execute(
        """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
           VALUES (?, 'compat_lookup', 'cp4', ?)""",
        (part_id, json.dumps({
            "part_number": result.part_number,
            "position": result.position,
            "status": result.status.value,
            "reason": result.reason,
            "edge_case": result.edge_case,
            "ref_version": result.ref_version,
            "previous_status": previous["status"] if previous else None,
        })),
    )
    if result.status == CompatStatus.VERIFIED:
        _promote_on_match(conn, part_id, result, evidence_id)


def _promote_on_match(conn: sqlite3.Connection, part_id: str, result: CompatResult, evidence_id: str) -> None:
    """A reference lookup confirms an Extracted value (TRD 3 'Verified')."""
    ledger = EvidenceLedger(conn, part_id)
    reference_sides = {f.position for f in result.fitments if f.position}
    for name, value in (("part_number", result.part_number), ("position", result.position), ("side", result.position)):
        attr = ledger.get_attribute(name)
        if attr is None or attr["status"] != AttributeStatus.EXTRACTED.value or attr["value"] != value:
            continue
        if name in ("position", "side") and value not in reference_sides:
            continue
        ledger.set_attribute(
            name, value, AttributeStatus.VERIFIED,
            evidence_id=evidence_id, reason=f"Confirmed by reference lookup {result.ref_version}",
            actor="reference_lookup",
        )
