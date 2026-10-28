"""CP2 checkpoint — reconciliation orchestration.

CP2 ties together the evidence ledger, provenance rules, and Reconciler.
It:
  1. Writes Question rows for each finding (text_en and text_ar blank for Inquiry agent)
  2. Writes AuditEvents
  3. Updates part status per the TRD 7 matrix
  4. Handles seller changes: new version + AuditEvent + re-run for affected fields (E43)
  5. Rejects stale writes (E49)

Per Rule (g): domain services never call an LLM. This module is pure logic + DB I/O.
Per Rule (e): never auto-resolve a conflict.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Any

from app.data.audit import write_audit
from app.domain.enums import AttributeStatus, PartStatus
from app.domain.ledger import EvidenceLedger, REQUIRED_FIELDS
from app.domain.provenance import (
    apply_seller_override,
    check_test_claim,
    check_fitment,
    check_stale_draft,
    detect_hedged_answer,
    is_visual_flag_field,
)
from app.domain.reconciler import (
    Finding,
    ReconcileResult,
    Reconciler,
    rerun_for_fields,
)

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── CP2 main entry point ────────────────────────────────────


def run_cp2(
    conn: sqlite3.Connection,
    part_id: str,
) -> ReconcileResult:
    """Run CP2 reconciliation on a part.

    Reads all attributes, evidence, donor vehicles, and compat refs from
    the database, runs the Reconciler, writes Question rows + AuditEvents,
    and updates the part status.

    Returns the ReconcileResult.
    """
    # Gather data from DB
    attributes = [
        dict(r) for r in conn.execute(
            "SELECT * FROM attributes WHERE part_id = ? ORDER BY name",
            (part_id,),
        ).fetchall()
    ]

    donor_vehicles = [
        dict(r) for r in conn.execute(
            "SELECT * FROM donor_vehicles WHERE part_id = ?",
            (part_id,),
        ).fetchall()
    ]
    donor_vehicle = donor_vehicles[0] if donor_vehicles else None

    evidence_rows = [
        dict(r) for r in conn.execute(
            "SELECT * FROM evidence WHERE part_id = ?",
            (part_id,),
        ).fetchall()
    ]

    # Get compat refs for this part number
    part_row = conn.execute(
        "SELECT part_number FROM parts WHERE id = ?",
        (part_id,),
    ).fetchone()
    part_number = part_row["part_number"] if part_row else None

    compat_refs = []
    if part_number:
        compat_refs = [
            dict(r) for r in conn.execute(
                "SELECT * FROM compat_refs WHERE part_number = ?",
                (part_number,),
            ).fetchall()
        ]

    # Run the Reconciler
    reconciler = Reconciler()
    result = reconciler.reconcile(
        part_id=part_id,
        attributes=attributes,
        donor_vehicle=donor_vehicle,
        donor_vehicles=donor_vehicles,
        compat_refs=compat_refs,
        evidence_rows=evidence_rows,
    )

    # Write Question rows
    for finding in result.findings:
        question_dict = finding.to_question_dict(part_id)
        conn.execute(
            """INSERT INTO questions
               (id, part_id, question_text, text_ar, priority, edge_case,
                attribute_name, answer_text, status, answered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'open', NULL)""",
            (
                question_dict["id"],
                part_id,
                question_dict["question_text"],
                question_dict["text_ar"],
                question_dict["priority"],
                question_dict["edge_case"],
                question_dict["attribute_name"],
            ),
        )

    # Write AuditEvent
    write_audit(
        conn, part_id=part_id, event_type="reconcile", actor="cp2", action="reconciled", source="evidence ledger",
        detail={
            "findings": len(result.findings),
            "edge_cases": result.edge_cases,
            "part_status": result.part_status.value,
            "blocks_export": result.blocks_export,
        },
    )

    # Update part status
    conn.execute(
        "UPDATE parts SET status = ?, updated_at = ? WHERE id = ?",
        (result.part_status.value, _now(), part_id),
    )

    conn.commit()
    return result


# ── Seller change handler (E43) ─────────────────────────────


def apply_seller_change(
    conn: sqlite3.Connection,
    part_id: str,
    attribute_name: str,
    new_value: Any,
    *,
    evidence_id: str | None = None,
    source: str = "seller_answer",
    is_visual_flag: bool | None = None,
    commit: bool = True,
) -> dict:
    """Handle a seller change to an attribute.

    Per E43:
      1. Create a new version (via the ledger)
      2. Write an AuditEvent
      3. Re-run the Reconciler for the affected fields only

    Per E25: If the seller contradicts a visual flag, create a close-up
    photo question and mark the override for the review summary.

    With commit=False the caller owns the transaction (the propagation engine
    runs the ledger change and every recomputed output as one step).

    Returns a dict with:
      - version_id: the new version ID
      - created_questions: list of Question dicts created
      - marks_for_review: whether this override is marked for review
      - review_note: the review note (if any)
    """
    ledger = EvidenceLedger(conn, part_id)

    # Get existing value
    existing = ledger.get_attribute(attribute_name)
    existing_value = existing.get("value") if existing else None
    existing_status = AttributeStatus(existing.get("status", "Missing")) if existing else AttributeStatus.MISSING

    # Determine if this is a visual flag field
    if is_visual_flag is None:
        is_visual_flag = is_visual_flag_field(attribute_name)

    # Apply provenance rules
    prov_result = apply_seller_override(
        attribute_name=attribute_name,
        new_value=new_value,
        existing_value=existing_value,
        existing_status=existing_status,
        is_visual_flag=is_visual_flag,
    )

    # Write the new version via the ledger
    version = ledger.set_attribute(
        name=attribute_name,
        value=new_value,
        status=prov_result.status,
        source=source,
        evidence_id=evidence_id,
        reason=prov_result.reason,
        actor="seller",
    )

    # Write AuditEvent for the seller change
    ledger.log_audit(
        event_type="seller_change",
        action="seller correction",
        source=source or "seller",
        field=attribute_name,
        before=existing_value,
        after=new_value,
        detail={
            "attribute": attribute_name,
            "old_value": existing_value,
            "new_value": new_value,
            "version_id": version.id,
            "marks_for_review": prov_result.marks_for_review,
            "edge_case": prov_result.question_edge_case,
        },
        actor="seller",
    )

    # If the seller contradicts a visual flag (E25), write the question
    created_questions = []
    if prov_result.creates_question:
        question_id = _new_id()
        conn.execute(
            """INSERT INTO questions
               (id, part_id, question_text, text_ar, priority, edge_case,
                attribute_name, answer_text, status, answered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'open', NULL)""",
            (
                question_id,
                part_id,
                prov_result.question_text,
                "",  # text_ar blank for Inquiry agent
                "high",
                prov_result.question_edge_case,
                attribute_name,
            ),
        )
        created_questions.append({
            "id": question_id,
            "question_text": prov_result.question_text,
            "edge_case": prov_result.question_edge_case,
        })

    # Re-run the Reconciler for affected fields only (E43)
    affected_fields = [attribute_name]
    # If this is a visual flag, also re-check condition_notes
    if is_visual_flag:
        affected_fields.append("condition_notes")

    # Gather data for partial re-run
    attributes = [
        dict(r) for r in conn.execute(
            "SELECT * FROM attributes WHERE part_id = ? ORDER BY name",
            (part_id,),
        ).fetchall()
    ]
    donor_vehicles = [
        dict(r) for r in conn.execute(
            "SELECT * FROM donor_vehicles WHERE part_id = ?",
            (part_id,),
        ).fetchall()
    ]
    donor_vehicle = donor_vehicles[0] if donor_vehicles else None
    evidence_rows = [
        dict(r) for r in conn.execute(
            "SELECT * FROM evidence WHERE part_id = ?",
            (part_id,),
        ).fetchall()
    ]

    reconciler = Reconciler()
    partial_result = rerun_for_fields(
        reconciler,
        part_id=part_id,
        affected_fields=affected_fields,
        attributes=attributes,
        donor_vehicle=donor_vehicle,
        donor_vehicles=donor_vehicles,
        evidence_rows=evidence_rows,
    )

    # Write any new findings as questions
    for finding in partial_result.findings:
        question_dict = finding.to_question_dict(part_id)
        conn.execute(
            """INSERT INTO questions
               (id, part_id, question_text, text_ar, priority, edge_case,
                attribute_name, answer_text, status, answered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'open', NULL)""",
            (
                question_dict["id"],
                part_id,
                question_dict["question_text"],
                question_dict["text_ar"],
                question_dict["priority"],
                question_dict["edge_case"],
                question_dict["attribute_name"],
            ),
        )
        created_questions.append(question_dict)

    # Write audit for re-run
    ledger.log_audit(
        event_type="reconcile_rerun",
        action="reconciled again",
        source="seller correction",
        detail={
            "trigger": "seller_change",
            "affected_fields": affected_fields,
            "findings": len(partial_result.findings),
        },
        actor="cp2",
    )

    if commit:
        conn.commit()

    return {
        "version_id": version.id,
        "created_questions": created_questions,
        "marks_for_review": prov_result.marks_for_review,
        "review_note": prov_result.review_note,
        "rerun_findings": len(partial_result.findings),
    }


# ── Stale draft check (E49) ─────────────────────────────────


def check_stale_draft(
    conn: sqlite3.Connection,
    draft_id: str,
) -> bool:
    """E49: Check if a listing draft is stale.

    A draft is stale if any attribute version has changed since the draft
    was created. Stale writes are rejected.
    """
    draft = conn.execute(
        "SELECT * FROM listing_drafts WHERE id = ?",
        (draft_id,),
    ).fetchone()
    if not draft:
        return True  # Draft doesn't exist — definitely stale

    built_from = draft["built_from_versions"]
    if not built_from:
        return False  # No version tracking — can't determine

    built_from_versions = json.loads(built_from)
    ledger = EvidenceLedger(conn, draft["part_id"])
    return ledger.is_stale(built_from_versions)


def reject_stale_write(
    conn: sqlite3.Connection,
    draft_id: str,
) -> ValueError:
    """E49: Reject a stale write.

    Returns a ValueError with a descriptive message.
    """
    return ValueError(
        f"E49: Listing draft {draft_id} is stale — "
        "attribute versions have changed since the draft was created. "
        "Please regenerate the draft."
    )
