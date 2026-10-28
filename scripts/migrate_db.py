"""Bring an existing PartProof database up to date with the current schema and test pack.

Non-destructive and idempotent:
  1. init_db: adds new tables/columns and (re)creates the sem_* views.
  2. Seeds S-001's seller profile from data/reference/seller_context.json
     if it has none (TRD 4).
  3. Backfills parts.sku from the seed's `ingest` audit events.
  4. Tags photo evidence with the views and regions it shows (data/seed/photo_views.csv, C09).
  5. Records included components (data/seed/components.csv, C08) for parts
     that have none.
  6. Screens stored ingested evidence and quarantines injection text if the
     database has no quarantine records yet (TRD 6).

A fresh database from scripts/seed.py does not need this.

Run:  python scripts/migrate_db.py [db_path]
"""

from __future__ import annotations

import csv
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.database import DB_PATH, get_connection, init_db
from app.domain.enums import AttributeStatus, TrustTier
from app.guardrails.quarantine import record_quarantine
from app.guardrails.screen import screen_text
from app.domain.ledger import EvidenceLedger
from app.domain.models import SellerProfile
from app.semantic.seller_context import upsert_profile

SELLER_CONTEXT = Path("data/reference/seller_context.json")
SEED_DIR = Path("data/seed")


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def migrate(db_path: Path | str = DB_PATH) -> dict[str, int]:
    init_db(db_path)
    conn = get_connection(db_path)

    ctx = json.loads(SELLER_CONTEXT.read_text(encoding="utf-8"))
    seller_id = ctx["seller_id"]
    has_seller = conn.execute("SELECT 1 FROM sellers WHERE id = ?", (seller_id,)).fetchone()
    has_profile = conn.execute("SELECT 1 FROM seller_profiles WHERE seller_id = ?", (seller_id,)).fetchone()
    profiles_added = 0
    if has_seller and not has_profile:
        upsert_profile(conn, SellerProfile(seller_id=seller_id, **ctx["profile"]))
        profiles_added = 1

    skus_backfilled = 0
    for row in conn.execute(
        """SELECT p.id, a.detail_json FROM parts p
           JOIN audit_events a ON a.part_id = p.id AND a.event_type = 'ingest'
           WHERE p.sku IS NULL"""
    ).fetchall():
        sku = json.loads(row["detail_json"] or "{}").get("sku")
        if sku:
            conn.execute("UPDATE parts SET sku = ? WHERE id = ?", (sku, row["id"]))
            skus_backfilled += 1

    photos_tagged = 0
    for row in _read_csv(SEED_DIR / "photo_views.csv"):
        tag = json.dumps({"views": [v for v in row["views"].split(";") if v],
                          "regions": json.loads(row.get("regions") or "[]")})
        cur = conn.execute(
            """UPDATE evidence SET raw_data = ?
               WHERE kind = 'photo_region' AND source_ref LIKE ? AND source_ref NOT LIKE 'photo:%'
                 AND (raw_data IS NULL OR raw_data NOT LIKE '%"regions"%')""",  # seed files, not vision rows
            (tag, f"%{row['photo']}"),
        )
        photos_tagged += cur.rowcount

    components_added = 0
    for row in _read_csv(SEED_DIR / "components.csv"):
        components = {k: v.strip() for k, v in row.items() if k != "sku" and v.strip()}
        part = conn.execute("SELECT id FROM parts WHERE sku = ? AND seller_id = ?", (row["sku"], seller_id)).fetchone()
        if not components or part is None:
            continue
        ledger = EvidenceLedger(conn, part["id"])
        if ledger.get_attribute("included_components") is not None:
            continue
        ev_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO evidence (id, part_id, kind, source_ref, content) VALUES (?, ?, 'spreadsheet_cell', ?, ?)",
            (ev_id, part["id"], f"data/seed/components.csv:{row['sku']}", json.dumps(components)),
        )
        ledger.set_attribute("included_components", components, AttributeStatus.EXTRACTED,
                             evidence_id=ev_id, reason="Inventory component list", actor="migration")
        components_added += 1

    quarantined = 0
    if conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"] == 0:
        for ev in conn.execute(
            """SELECT e.part_id, e.kind, e.source_ref, e.content, p.seller_id FROM evidence e
               JOIN parts p ON p.id = e.part_id
               WHERE e.kind IN ('spreadsheet_cell', 'donor_note', 'arabic_note', 'ocr_text')"""
        ).fetchall():
            screen = screen_text(ev["content"] or "", source=ev["source_ref"] or ev["kind"])
            if screen.quarantine:
                record_quarantine(conn, screen, channel="ingest", tier=TrustTier.T2,
                                  seller_id=ev["seller_id"], part_id=ev["part_id"])
                quarantined += 1

    conn.commit()
    conn.close()
    return {
        "quarantined": quarantined,
        "profiles_added": profiles_added,
        "skus_backfilled": skus_backfilled,
        "photos_tagged": photos_tagged,
        "components_added": components_added,
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    result = migrate(target)
    print(f"Migrated {target}: {result}")
