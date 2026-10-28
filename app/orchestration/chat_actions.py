"""Chat actions — run a routed intent and propagate accepted seller values.

The chat lane only reads and writes small, pre-computed state (TRD 2):
  1. The semantic layer runs the intent through ChatTools (access policy applies).
  2. If the result carries an accepted LedgerUpdate, orchestration hands it to
     the propagation engine, which in one step versions the attribute, re-runs
     the Reconciler, recomputes only the affected outputs (compat lookup,
     title, item specifics, checklist, ...) and writes an AuditEvent.

The chat itself never writes an attribute or a status (Rule b); the domain
service decides the resulting status (Rule a).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.domain.propagation_engine import propagate
from app.semantic.access_policy import ChatTools
from app.semantic.entity_model import SemanticLayer
from app.semantic.intents import IntentRequest, IntentResult, LedgerUpdate, execute_intent
from app.semantic.seller_context import SellerSession


def apply_ledger_update(
    conn: sqlite3.Connection,
    session: SellerSession,
    update: LedgerUpdate,
) -> dict[str, Any]:
    """Apply a seller-confirmed value and propagate it (E43)."""
    part_id = SemanticLayer(conn, session).part_id_for(update.sku)
    if part_id is None:
        raise LookupError(f"Part {update.sku!r} is not one of this seller's records")
    return propagate(
        conn,
        part_id,
        update.field,
        update.value,
        evidence_id=update.evidence_id,
        source="seller_answer",
        actor="seller",
    ).to_dict()


def handle_intent(
    conn: sqlite3.Connection,
    session: SellerSession,
    request: IntentRequest,
) -> tuple[IntentResult, dict[str, Any] | None]:
    """Execute one chat intent; propagate it if it produced an accepted value."""
    result = execute_intent(ChatTools(conn, session), request)
    update = getattr(result, "ledger_update", None)
    propagation = apply_ledger_update(conn, session, update) if update else None
    extra = getattr(result, "additional_updates", [])
    if propagation is not None and extra:
        propagation["additional"] = [apply_ledger_update(conn, session, u) for u in extra]
    return result, propagation
