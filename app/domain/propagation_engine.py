"""Propagation engine — one correction, only the affected outputs, one step (TRD 3).

`propagate` runs, inside a single transaction:
  1. the ledger change (new attribute version, AuditEvent, Reconciler re-run
     for the affected fields — via cp2.apply_seller_change),
  2. every output the dependency graph says depends on the field, in
     OUTPUT_ORDER (compat lookup → templated sections → checklist),
  3. a new draft version with a snapshot in listing_draft_versions, and
  4. one `propagate` AuditEvent with the before/after of each output.

Outputs that need the LLM summary regenerated are flagged (`summary_stale`);
the regeneration runs in the background and is never on this path.

Also here: E35 (reference version change re-runs lookups and re-evaluates
drafts) and E50 (checklist version change re-evaluates parts).

Per Rule (g): no LLM. Per Rule (b): no agent calls this directly; the chat's
LedgerUpdate reaches it through orchestration.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.data.reference_data import Checklist, CompatReference, get_checklist, get_compat_reference
from app.domain.compatibility import CompatResult, check_part, stored_result
from app.domain.cp2 import apply_seller_change
from app.domain.listing_profile import load_seller_profile, render_profile_sections
from app.domain.listing import PROTECTED_SPECIFICS, draft_staleness, refresh_draft, seller_edits
from app.domain.listing_render import OUTPUT_COLUMNS, render_output
from app.domain.part_facts import load_part_facts
from app.domain.propagation import (
    CHECKLIST,
    COMPAT_LOOKUP,
    DESCRIPTION,
    ITEM_SPECIFICS,
    TITLE,
    DISCLOSURE_BLOCK,
    DONOR_BLOCK,
    OUTPUT_ORDER,
    needs_llm,
    node_for_field,
    outputs_for,
)
from app.domain.readiness import ReadinessResult, evaluate_part

# Draft columns kept in each version snapshot.
_SNAPSHOT_COLUMNS = (
    "title", "item_specifics_json", "compatibility_json", "donor_block", "disclosure_block",
    "shipping_json", "price", "currency", "description", "summary", "summary_stale", "summary_status",
    "category", "ref_version", "status", "built_from_versions",
)


@dataclass
class PropagationResult:
    part_id: str
    sku: str | None
    trigger: str  # the field (or reference) that changed
    recomputed: list[str]
    llm_regenerate: list[str]  # outputs whose LLM summary must be regenerated
    old_value: Any = None
    new_value: Any = None
    attribute_version_id: str | None = None
    draft_id: str | None = None
    draft_version: int | None = None
    compat: CompatResult | None = None
    readiness: ReadinessResult | None = None
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "sku": self.sku,
            "trigger": self.trigger,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "recomputed": self.recomputed,
            "llm_regenerate": self.llm_regenerate,
            "attribute_version_id": self.attribute_version_id,
            "draft_id": self.draft_id,
            "draft_version": self.draft_version,
            "compat": self.compat.to_dict() if self.compat else None,
            "readiness": self.readiness.to_json() if self.readiness else None,
            "changes": self.changes,
        }


# ── Core: recompute a set of outputs ────────────────────────


def _latest_draft(conn: sqlite3.Connection, part_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM listing_drafts WHERE part_id = ? ORDER BY created_at DESC LIMIT 1", (part_id,)
    ).fetchone()
    return dict(row) if row else None


def _latest_status(conn: sqlite3.Connection, part_id: str) -> str | None:
    row = conn.execute(
        "SELECT checklist_json FROM checklist_results WHERE part_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (part_id,),
    ).fetchone()
    return json.loads(row["checklist_json"]).get("status") if row else None


def _recompute(
    conn: sqlite3.Connection,
    part_id: str,
    outputs: tuple[str, ...],
    *,
    reference: CompatReference,
    checklist: Checklist,
    reason: str,
    actor: str,
) -> tuple[dict[str, dict[str, Any]], CompatResult, ReadinessResult | None, dict | None]:
    """Recompute `outputs` for one part. Does not commit."""
    changes: dict[str, dict[str, Any]] = {}

    previous_compat = stored_result(conn, part_id)
    compat = check_part(conn, part_id, reference, persist=COMPAT_LOOKUP in outputs)
    if COMPAT_LOOKUP in outputs:
        changes[COMPAT_LOOKUP] = {
            "before": {"status": previous_compat["status"], "reason": previous_compat["reason"]} if previous_compat else None,
            "after": {"status": compat.status.value, "reason": compat.reason},
        }

    facts = load_part_facts(conn, part_id)  # after CP4, which may promote values to Verified
    profile = load_seller_profile(conn, facts.seller_id)
    sections = render_profile_sections(profile) if profile else None

    draft = _latest_draft(conn, part_id)
    summary_invalidated = False
    if draft is not None:
        updates: dict[str, Any] = {}
        edits = seller_edits(draft)
        edits_before = dict(edits)
        title_replaced = False
        for output in outputs:
            if output not in OUTPUT_COLUMNS:
                continue
            values = render_output(output, facts, compat, sections)
            if output == TITLE and edits.get("title"):
                if edits.setdefault("base_title", values["title"]) == values["title"]:
                    continue  # the facts behind the title are unchanged: the seller's title stays
                # A correction changed the facts the title is built from: the template replaces the edit.
                edits.pop("title")
                edits.pop("base_title")
                title_replaced = True
            if output == ITEM_SPECIFICS and edits.get("item_specifics"):
                merged = {**json.loads(values["item_specifics_json"]), **{
                    k: v for k, v in edits["item_specifics"].items() if k not in PROTECTED_SPECIFICS}}
                values = {"item_specifics_json": json.dumps(merged, ensure_ascii=False)}
            before = {c: draft.get(c) for c in values}
            if before == values:
                continue
            if output == COMPAT_LOOKUP:  # the lookup's own before/after is already recorded
                changes[output] = {**changes.get(output, {}), "draft_before": before, "draft_after": values}
            else:
                changes[output] = {"before": before, "after": values}
            updates.update(values)
        if title_replaced:
            changes.setdefault(TITLE, {})["seller_edit_replaced"] = True
        if edits != edits_before:
            updates["seller_edits_json"] = json.dumps(edits, ensure_ascii=False)
        if updates:
            assignments = ", ".join(f"{c} = ?" for c in updates)
            conn.execute(f"UPDATE listing_drafts SET {assignments} WHERE id = ?", (*updates.values(), draft["id"]))
            draft = {**draft, **updates}
        # Claims, CP5, description and the version snapshot follow every touch of the draft.
        before_blocks = {c: draft.get(c) for c in ("donor_block", "disclosure_block")}
        refresh = refresh_draft(conn, draft, facts, compat, sections, force_summary_stale=DESCRIPTION in outputs)
        summary_invalidated = refresh.summary_invalidated
        draft = _latest_draft(conn, part_id)
        for output, column in ((DONOR_BLOCK, "donor_block"), (DISCLOSURE_BLOCK, "disclosure_block")):
            if output in outputs and draft[column] != before_blocks[column]:
                changes[output] = {"before": {column: before_blocks[column]}, "after": {column: draft[column]}}
        if updates or refresh.changed_claims or refresh.retired_claims or refresh.guard.removed:
            bump = {"version": draft["version"] + 1}
            if draft["status"] == "approved":
                bump["status"] = "draft"  # the approval covered the old text (Rule b: re-approve)
            conn.execute(
                f"UPDATE listing_drafts SET {', '.join(f'{c} = ?' for c in bump)}, updated_at = datetime('now') "
                "WHERE id = ?",
                (*bump.values(), draft["id"]),
            )
            draft = {**draft, **bump}
            _snapshot_draft(conn, draft, reason=reason, actor=actor)
        if refresh.guard.removed:
            changes["guard"] = {"removed": [f.text for f in refresh.guard.removed]}

    readiness = None
    if CHECKLIST in outputs:
        before = _latest_status(conn, part_id)
        readiness = evaluate_part(conn, part_id, compat, checklist=checklist)
        changes[CHECKLIST] = {"before": before, "after": readiness.status.value}

    if summary_invalidated:
        changes["summary"] = {"stale": True}
    return changes, compat, readiness, draft


def _snapshot_draft(conn: sqlite3.Connection, draft: dict, *, reason: str, actor: str) -> None:
    conn.execute(
        """INSERT INTO listing_draft_versions (id, draft_id, version, snapshot_json, reason, actor)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), draft["id"], draft["version"],
            json.dumps({c: draft.get(c) for c in _SNAPSHOT_COLUMNS}, ensure_ascii=False, default=str),
            reason, actor,
        ),
    )


def _audit(conn: sqlite3.Connection, part_id: str, actor: str, detail: dict) -> None:
    conn.execute(
        """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
           VALUES (?, 'propagate', ?, ?)""",
        (part_id, actor, json.dumps(detail, ensure_ascii=False, default=str)),
    )


# ── Public API ──────────────────────────────────────────────


def propagate(
    conn: sqlite3.Connection,
    part_id: str,
    field_name: str,
    new_value: Any,
    *,
    evidence_id: str | None = None,
    source: str = "seller_answer",
    actor: str = "seller",
    reference: CompatReference | None = None,
    checklist: Checklist | None = None,
) -> PropagationResult:
    """Apply one correction and recompute only the outputs that depend on it, in one step."""
    reference = reference or get_compat_reference()
    checklist = checklist or get_checklist()
    outputs = outputs_for(field_name)
    sku = conn.execute("SELECT sku FROM parts WHERE id = ?", (part_id,)).fetchone()["sku"]
    old = conn.execute(
        "SELECT value FROM attributes WHERE part_id = ? AND name = ?", (part_id, field_name)
    ).fetchone()

    try:
        change = apply_seller_change(
            conn, part_id, field_name, new_value,
            evidence_id=evidence_id, source=source, commit=False,
        )
        node = node_for_field(field_name)
        if node == "part_number":
            conn.execute("UPDATE parts SET part_number = ?, updated_at = datetime('now') WHERE id = ?",
                         (new_value, part_id))
        elif node == "position":
            conn.execute("UPDATE parts SET side = ?, updated_at = datetime('now') WHERE id = ?",
                         (new_value, part_id))

        changes, compat, readiness, draft = _recompute(
            conn, part_id, outputs, reference=reference, checklist=checklist,
            reason=f"{field_name} changed", actor=actor,
        )
        llm = needs_llm(outputs)
        if "summary" in changes and DESCRIPTION not in llm:
            llm = [*llm, DESCRIPTION]  # a claim the summary cites changed
        result = PropagationResult(
            part_id=part_id, sku=sku, trigger=field_name,
            recomputed=list(outputs), llm_regenerate=llm,
            old_value=old["value"] if old else None, new_value=new_value,
            attribute_version_id=change["version_id"],
            draft_id=draft["id"] if draft else None,
            draft_version=draft["version"] if draft else None,
            compat=compat, readiness=readiness, changes=changes,
        )
        _audit(conn, part_id, actor, {
            "field": field_name,
            "old_value": result.old_value,
            "new_value": new_value,
            "attribute_version_id": change["version_id"],
            "recomputed": result.recomputed,
            "llm_regenerate": result.llm_regenerate,
            "draft_version": result.draft_version,
            "changes": changes,
        })
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def render_draft(
    conn: sqlite3.Connection,
    part_id: str,
    *,
    reference: CompatReference | None = None,
    checklist: Checklist | None = None,
    actor: str = "listing_service",
) -> PropagationResult:
    """Create (or fully re-render) a part's draft: every templated output, then the checklist."""
    reference = reference or get_compat_reference()
    checklist = checklist or get_checklist()
    sku = conn.execute("SELECT sku FROM parts WHERE id = ?", (part_id,)).fetchone()["sku"]
    try:
        if _latest_draft(conn, part_id) is None:
            draft_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO listing_drafts (id, part_id, status, version) VALUES (?, ?, 'draft', 0)",
                (draft_id, part_id),
            )
        changes, compat, readiness, draft = _recompute(
            conn, part_id, OUTPUT_ORDER, reference=reference, checklist=checklist,
            reason="full render", actor=actor,
        )
        result = PropagationResult(
            part_id=part_id, sku=sku, trigger="render", recomputed=list(OUTPUT_ORDER),
            llm_regenerate=needs_llm(OUTPUT_ORDER), draft_id=draft["id"], draft_version=draft["version"],
            compat=compat, readiness=readiness, changes=changes,
        )
        _audit(conn, part_id, actor, {"trigger": "render", "draft_version": draft["version"]})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def ensure_fresh_draft(
    conn: sqlite3.Connection,
    part_id: str,
    *,
    reference: CompatReference | None = None,
    checklist: Checklist | None = None,
) -> tuple[dict | None, list[str]]:
    """E49: a draft whose built_from_versions no longer match is marked stale and re-rendered.

    Returns (draft, staleness reasons that triggered a re-render; [] if it was fresh).
    """
    draft = _latest_draft(conn, part_id)
    if draft is None:
        return None, []
    reasons = draft_staleness(conn, draft)
    if not reasons or draft["status"] == "exported":
        return draft, []
    conn.execute("UPDATE listing_drafts SET stale = 1 WHERE id = ?", (draft["id"],))
    conn.execute(
        """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
           VALUES (?, 'draft_stale', 'listing_service', ?)""",
        (part_id, json.dumps({"draft_id": draft["id"], "reasons": reasons})),
    )
    render_draft(conn, part_id, reference=reference, checklist=checklist, actor="listing_service")
    return _latest_draft(conn, part_id), reasons


def record_reference_load(conn: sqlite3.Connection, name: str, version: str, checksum: str) -> bool:
    """Record a loaded reference version. Returns True when it differs from the last one recorded."""
    last = conn.execute(
        "SELECT version, checksum FROM reference_versions WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)
    ).fetchone()
    if last and (last["version"], last["checksum"]) == (version, checksum):
        return False
    conn.execute(
        "INSERT INTO reference_versions (name, version, checksum) VALUES (?, ?, ?)", (name, version, checksum)
    )
    conn.commit()
    return last is not None


def on_reference_change(
    conn: sqlite3.Connection,
    reference: CompatReference | None = None,
    checklist: Checklist | None = None,
) -> list[PropagationResult]:
    """E35: re-run lookups and re-evaluate drafts built from another reference version."""
    reference = reference or get_compat_reference()
    checklist = checklist or get_checklist()
    stale = [r["part_id"] for r in conn.execute(
        """SELECT part_id FROM compat_results WHERE ref_version != ? OR ref_checksum != ?
           UNION
           SELECT part_id FROM listing_drafts WHERE ref_version IS NOT NULL AND ref_version != ?""",
        (reference.version, reference.checksum, reference.version),
    ).fetchall()]
    results = []
    for part_id in stale:
        outputs = outputs_for("compat_reference")
        try:
            changes, compat, readiness, draft = _recompute(
                conn, part_id, outputs, reference=reference, checklist=checklist,
                reason=f"compatibility reference {reference.version}", actor="system",
            )
            _audit(conn, part_id, "system", {
                "trigger": "compat_reference", "ref_version": reference.version,
                "recomputed": list(outputs), "changes": changes,
            })
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        results.append(PropagationResult(
            part_id=part_id, sku=None, trigger="compat_reference", recomputed=list(outputs),
            llm_regenerate=needs_llm(outputs), draft_id=draft["id"] if draft else None,
            draft_version=draft["version"] if draft else None, compat=compat, readiness=readiness,
            changes=changes,
        ))
    return results


def on_checklist_change(
    conn: sqlite3.Connection,
    checklist: Checklist | None = None,
    reference: CompatReference | None = None,
) -> list[str]:
    """E50: re-evaluate every part whose latest evaluation used another checklist version."""
    checklist = checklist or get_checklist()
    reference = reference or get_compat_reference()
    reevaluated = []
    for row in conn.execute("SELECT DISTINCT part_id FROM checklist_results").fetchall():
        latest = conn.execute(
            "SELECT checklist_json FROM checklist_results WHERE part_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (row["part_id"],),
        ).fetchone()
        data = json.loads(latest["checklist_json"])
        if (data.get("version"), data.get("checksum")) != (checklist.version, checklist.checksum):
            compat = check_part(conn, row["part_id"], reference, persist=False)
            evaluate_part(conn, row["part_id"], compat, checklist=checklist)
            reevaluated.append(row["part_id"])
    conn.commit()
    return reevaluated


TITLE_LIMIT = 80


@dataclass
class EditResult:
    draft_id: str
    draft_version: int
    removed: list[dict[str, Any]]
    rejected: list[str]
    readiness: str | None = None


def _editor_evidence(conn: sqlite3.Connection, part_id: str, where: str, text: str) -> str:
    """The seller's editor text, stored as seller evidence (T1 input, kept as data)."""
    evidence_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO evidence (id, part_id, kind, source_ref, content, raw_data)
           VALUES (?, ?, 'seller_answer', ?, ?, ?)""",
        (evidence_id, part_id, f"editor:{where}", text, json.dumps({"channel": "editor", "field": where})),
    )
    return evidence_id


def apply_seller_edits(
    conn: sqlite3.Connection,
    part_id: str,
    *,
    title: str | None = None,
    category: str | None = None,
    item_specifics: dict[str, str] | None = None,
    notes: list[str] | None = None,
    price: str | None = None,
    reference: CompatReference | None = None,
    checklist: Checklist | None = None,
) -> EditResult:
    """Apply the seller's listing-editor changes, then re-run the guard (CP5) on the draft.

    The disclosure block and the other templated sentences are not editable here; they change
    when the ledger changes (a chat answer or correction). A price edit goes through propagation.
    """
    reference = reference or get_compat_reference()
    checklist = checklist or get_checklist()
    draft = _latest_draft(conn, part_id)
    if draft is None:
        raise LookupError("no draft yet")
    if draft["status"] == "exported":
        raise ValueError("an exported listing cannot be edited")
    if title is not None and len(title.strip()) > TITLE_LIMIT:
        raise ValueError(f"the title is longer than {TITLE_LIMIT} characters")

    if price is not None:
        evidence_id = _editor_evidence(conn, part_id, "price", price)
        propagate(conn, part_id, "price", price.strip(), evidence_id=evidence_id, source="editor",
                  reference=reference, checklist=checklist)
        draft = _latest_draft(conn, part_id)

    before = {k: draft.get(k) for k in ("title", "category", "item_specifics_json", "seller_edits_json")}
    edits = seller_edits(draft)
    rejected: list[str] = []
    outputs: list[str] = []
    try:
        if title is not None and title.strip() != (draft.get("title") or ""):
            # An empty title hands the title back to the template.
            edits.pop("base_title", None)
            if title.strip():
                edits["title"] = title.strip()
                _editor_evidence(conn, part_id, "title", edits["title"])
                conn.execute("UPDATE listing_drafts SET title = ? WHERE id = ?", (edits["title"], draft["id"]))
            else:
                edits.pop("title", None)
            outputs.append(TITLE)
        if category is not None:
            edits["category"] = category.strip() or None
        if item_specifics is not None:
            rejected = sorted(k for k in item_specifics if k in PROTECTED_SPECIFICS)
            edits["item_specifics"] = {k: str(v) for k, v in item_specifics.items()
                                       if k not in PROTECTED_SPECIFICS and str(v).strip()}
            outputs.append(ITEM_SPECIFICS)
        if notes is not None:
            edits["notes"] = [{"text": n.strip(), "evidence_id": _editor_evidence(conn, part_id, f"note:{i}", n.strip())}
                              for i, n in enumerate(notes) if n.strip()]
        conn.execute("UPDATE listing_drafts SET seller_edits_json = ? WHERE id = ?",
                     (json.dumps(edits, ensure_ascii=False), draft["id"]))
        changes, _, _, draft = _recompute(conn, part_id, (*outputs, CHECKLIST), reference=reference,
                                          checklist=checklist, reason="seller edit", actor="seller")
        after = {k: draft.get(k) for k in before}
        removed = json.loads(draft.get("guard_json") or "{}").get("removed", [])  # every finding, with its reason
        conn.execute(
            """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
               VALUES (?, 'seller_edit', 'seller', ?)""",
            (part_id, json.dumps({"before": before, "after": after, "rejected": rejected,
                                  "guard_removed": removed}, ensure_ascii=False, default=str)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return EditResult(draft["id"], draft["version"], removed, rejected, readiness=changes[CHECKLIST]["after"])
