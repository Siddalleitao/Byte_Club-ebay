"""Part pipeline steps the API runs on demand: CP4, CP6, draft render, reference checks.

The API gateway talks to orchestration and the semantic layer only (Rule g);
these functions resolve the seller's SKU through the semantic layer, then
call the deterministic domain services.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.data.reference_data import get_checklist, get_compat_reference
from app.domain.compatibility import CompatResult, check_part
from app.domain.claims import SECTION_TITLES
from app.domain.listing import PROTECTED_SPECIFICS, seller_edits
from app.domain.propagation_engine import (
    TITLE_LIMIT,
    EditResult,
    apply_seller_edits,
    ensure_fresh_draft,
    PropagationResult,
    on_checklist_change,
    on_reference_change,
    record_reference_load,
    render_draft,
)
from app.domain.readiness import ReadinessResult, evaluate_part
from app.semantic.entity_model import SemanticLayer
from app.semantic.seller_context import SellerSession


class PartNotFound(LookupError):
    """Unknown SKU, or one that belongs to another seller."""


def _part_id(conn: sqlite3.Connection, session: SellerSession, sku: str) -> str:
    part_id = SemanticLayer(conn, session).part_id_for(sku)
    if part_id is None:
        raise PartNotFound(sku)
    return part_id


def check_compatibility(conn: sqlite3.Connection, session: SellerSession, sku: str) -> CompatResult:
    """CP4 on demand."""
    result = check_part(conn, _part_id(conn, session, sku))
    conn.commit()
    return result


def evaluate_readiness(conn: sqlite3.Connection, session: SellerSession, sku: str) -> ReadinessResult:
    """CP4 then CP6 on demand; stores the checklist result."""
    part_id = _part_id(conn, session, sku)
    result = evaluate_part(conn, part_id, check_part(conn, part_id))
    conn.commit()
    return result


def evaluate_all(conn: sqlite3.Connection, session: SellerSession) -> list[ReadinessResult]:
    """Dashboard: evaluate every one of the seller's parts."""
    layer = SemanticLayer(conn, session)
    results = []
    for part in layer.parts():
        part_id = layer.part_id_for(part.sku)
        results.append(evaluate_part(conn, part_id, check_part(conn, part_id)))
    conn.commit()
    return results


def render_part_draft(conn: sqlite3.Connection, session: SellerSession, sku: str) -> PropagationResult:
    return render_draft(conn, _part_id(conn, session, sku))


def edit_part_draft(
    conn: sqlite3.Connection,
    session: SellerSession,
    sku: str,
    *,
    title: str | None = None,
    category: str | None = None,
    item_specifics: dict[str, str] | None = None,
    notes: list[str] | None = None,
    price: str | None = None,
) -> EditResult:
    """The listing editor's save: seller edits (None = unchanged), then the guard (CP5) and the checklist re-run."""
    return apply_seller_edits(conn, _part_id(conn, session, sku), title=title, category=category,
                              item_specifics=item_specifics, notes=notes, price=price)


def startup_reference_check(conn: sqlite3.Connection) -> dict[str, Any]:
    """Verify reference checksums, record versions, and re-evaluate on change (E35, E50).

    Raises ReferenceIntegrityError if the compatibility reference was tampered with.
    """
    reference = get_compat_reference()
    checklist = get_checklist()
    compat_changed = record_reference_load(conn, "compat_ref", reference.version, reference.checksum)
    checklist_changed = record_reference_load(conn, "checklist", checklist.version, checklist.checksum)
    rerun = on_reference_change(conn, reference, checklist) if compat_changed else []
    reevaluated = on_checklist_change(conn, checklist, reference) if checklist_changed else []
    return {
        "compat_ref": {"version": reference.version, "checksum": reference.checksum, "rows": reference.row_count},
        "checklist": {"version": checklist.version, "checksum": checklist.checksum},
        "compat_lookups_rerun": len(rerun),
        "parts_reevaluated": len(reevaluated),
    }


def draft_view(conn: sqlite3.Connection, session: SellerSession, sku: str) -> dict[str, Any] | None:
    """The draft as the review screen shows it: every sentence with its claim and evidence (FR-12).

    A stale draft (built_from_versions mismatch) is re-rendered first (E49).
    """
    part_id = _part_id(conn, session, sku)
    draft, stale_reasons = ensure_fresh_draft(conn, part_id)
    conn.commit()
    if draft is None:
        return None
    evidence = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, kind, source_ref, content FROM evidence WHERE part_id = ?", (part_id,)).fetchall()}
    claims = [dict(r) for r in conn.execute(
        "SELECT * FROM claims WHERE draft_id = ? AND status IN ('kept', 'removed') ORDER BY position",
        (draft["id"],)).fetchall()]

    def sentence(c: dict) -> dict:
        ids = json.loads(c["evidence_ids_json"] or "[]")
        return {
            "claim_id": c["id"],
            "claim_type": c["claim_type"],
            "text": c["claim_value"],
            "evidence_ids": ids,
            "evidence": [{"id": i, "kind": evidence[i]["kind"], "pointer": evidence[i]["source_ref"],
                          "text": evidence[i]["content"]} for i in ids if i in evidence],
            "sources": json.loads(c["sources_json"] or "[]"),
            "cites": json.loads(c["cites_json"] or "[]"),
        }

    kept = [c for c in claims if c["status"] == "kept"]
    latest = conn.execute(
        "SELECT checklist_json FROM checklist_results WHERE part_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (part_id,)).fetchone()
    readiness = json.loads(latest["checklist_json"])["status"] if latest else None
    summary_ok = not draft["summary_stale"] and draft["summary_status"] in ("critic_passed", "template")
    return {
        "draft_id": draft["id"],
        "version": draft["version"],
        "status": draft["status"],
        "rerendered_because": stale_reasons,
        "title": draft["title"],
        "title_length": len(draft["title"] or ""),
        "category": draft["category"],
        "item_specifics": json.loads(draft["item_specifics_json"] or "{}"),
        "price": draft["price"],
        "currency": draft["currency"],
        "summary": {
            "text": draft["summary"],
            "status": draft["summary_status"],
            "sentences": [sentence(c) for c in kept if c["section"] == "summary"],
        },
        "badge": None if summary_ok else "updating",
        "sections": [
            {"section": key, "title": title,
             "sentences": [sentence(c) for c in kept if c["section"] == key]}
            for key, title in SECTION_TITLES.items()
        ],
        "removed_by_guard": [{"claim_id": c["id"], "text": c["claim_value"], "reason": c["removed_reason"]}
                             for c in claims if c["status"] == "removed"],
        # The editor: the disclosure block is shown locked; title and item-specific findings come from CP5.
        "disclosure": draft["disclosure_block"],
        "title_limit": TITLE_LIMIT,
        "seller_edits": seller_edits(draft),
        "protected_specifics": sorted(PROTECTED_SPECIFICS),
        "guard_findings": [f for f in json.loads(draft.get("guard_json") or "{}").get("removed", [])
                           if f["target"] != "claim"],
        "readiness": readiness,
        "approve_enabled": summary_ok and readiness == "Ready for seller review",
    }
