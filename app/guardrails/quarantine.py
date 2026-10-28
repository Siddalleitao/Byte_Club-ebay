"""Quarantine records — shown to the seller and logged with the triggering text (TRD 6 layers 4 and 10).

Quarantined text is still stored as evidence (it is data, Rule f); the
quarantine record says why it was flagged, so the seller sees it on the
dashboard and no reviewer has to guess.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from app.domain.enums import TrustTier
from app.guardrails.screen import ScreenResult


def record_quarantine(
    conn: sqlite3.Connection,
    screen: ScreenResult,
    *,
    channel: str,
    tier: TrustTier,
    seller_id: str | None,
    part_id: str | None = None,
    verdict: str | None = None,
) -> str:
    """Store a quarantine record and an audit event with the text that triggered it. Commits."""
    quarantine_id = str(uuid.uuid4())
    reason = screen.reason or "flagged by the classifier"
    pattern_ids = [h["pattern_id"] for h in screen.hits]
    conn.execute(
        """INSERT INTO quarantine (id, seller_id, part_id, channel, source, trust_tier, text, reason,
               pattern_ids_json, flags_json, classifier_verdict)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (quarantine_id, seller_id, part_id, channel, screen.source, tier.value, screen.original, reason,
         json.dumps(pattern_ids), json.dumps(screen.flags), verdict),
    )
    conn.execute(
        """INSERT INTO audit_events (part_id, event_type, actor, detail_json)
           VALUES (?, 'injection_quarantine', ?, ?)""",
        (part_id, "chat_screen" if channel == "chat" else "cp0_gate", json.dumps({
            "quarantine_id": quarantine_id, "seller_id": seller_id, "source": screen.source,
            "text": screen.original, "reason": reason, "pattern_ids": pattern_ids, "flags": screen.flags,
        }, ensure_ascii=False)),
    )
    conn.commit()
    return quarantine_id


def set_verdict(conn: sqlite3.Connection, quarantine_id: str, verdict: str) -> None:
    conn.execute("UPDATE quarantine SET classifier_verdict = ? WHERE id = ?", (verdict, quarantine_id))
    conn.commit()


def list_quarantine(conn: sqlite3.Connection, seller_id: str) -> list[dict[str, Any]]:
    """The seller's quarantine records, newest first (their own parts and chat only)."""
    rows = conn.execute(
        """SELECT q.*, p.sku FROM quarantine q LEFT JOIN parts p ON p.id = q.part_id
           WHERE q.seller_id = ? ORDER BY q.created_at DESC, q.rowid DESC""",
        (seller_id,),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["pattern_ids"] = json.loads(item.pop("pattern_ids_json"))
        item["flags"] = json.loads(item.pop("flags_json"))
        out.append(item)
    return out


def seller_of_part(conn: sqlite3.Connection, part_id: str | None) -> str | None:
    if not part_id:
        return None
    row = conn.execute("SELECT seller_id FROM parts WHERE id = ?", (part_id,)).fetchone()
    return row["seller_id"] if row else None
