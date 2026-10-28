"""Seed the PartProof SQLite database from the synthetic test pack.

Loads:
  - Seller S-001 (Noor) and their profile (TRD 4 card) from data/reference/seller_context.json
  - 20 parts from data/seed/inventory.csv
  - Donor vehicles from data/seed/donor_vehicle.csv
  - Donor notes from data/seed/donor_notes/*.txt
  - Spreadsheet cells from data/seed/spreadsheet.csv (as evidence)
  - Photo references (as evidence records), tagged with the views they show
  - Included components per part (data/seed/components.csv) as evidence + attribute
  - Compatibility references from data/reference/compat_ref.csv

Run:  python scripts/seed.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.audit import write_audit
from app.data.database import DB_PATH, get_connection, init_db
from app.domain.enums import AttributeStatus, TrustTier
from app.guardrails.quarantine import record_quarantine
from app.guardrails.screen import screen_text
from app.domain.ledger import EvidenceLedger
from app.domain.models import SellerProfile
from app.semantic.seller_context import upsert_profile

SEED_DIR = Path("data/seed")
REFERENCE_DIR = Path("data/reference")
ANSWER_KEY_DIR = Path("data/answer_key")

DONOR_NOTES_DIR = SEED_DIR / "donor_notes"
PHOTOS_DIR = SEED_DIR / "photos"

SELLER_ID = "S-001"


# ── Helpers ──


def _new_id() -> str:
    return str(uuid.uuid4())


def _read_csv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_donor_note(sku: str) -> str | None:
    """Load the donor note text for a SKU, if it exists."""
    note_path = DONOR_NOTES_DIR / f"{sku}.txt"
    if note_path.exists():
        return note_path.read_text(encoding="utf-8").strip()
    return None


def _photo_refs(sku: str) -> list[str]:
    """Return list of photo file paths for a SKU."""
    refs = []
    for n in (1, 2, 3):
        p = PHOTOS_DIR / f"{sku}_p{n}.jpg"
        if p.exists():
            refs.append(str(p))
    return refs


# ── Seeding functions ──


def _screen_ingested(conn: sqlite3.Connection, text: str, source: str, part_id: str) -> None:
    """Seeded content is ingested content (T2): screen it like an upload, quarantine hits (E53, J3)."""
    screen = screen_text(text, source=source)
    if screen.quarantine:
        record_quarantine(conn, screen, channel="ingest", tier=TrustTier.T2, seller_id=SELLER_ID, part_id=part_id)


def seed_seller(conn: sqlite3.Connection) -> None:
    """Insert seller S-001 (Noor)."""
    ctx = _read_json(REFERENCE_DIR / "seller_context.json")
    conn.execute(
        """INSERT OR REPLACE INTO sellers (id, name, phone, whatsapp, location, seller_type)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            SELLER_ID,
            ctx.get("name", "Noor"),
            None,
            ctx.get("whatsapp"),
            ctx.get("location"),
            ctx.get("seller_type"),
        ),
    )
    upsert_profile(conn, SellerProfile(seller_id=SELLER_ID, **ctx["profile"]))
    conn.commit()


def seed_parts(conn: sqlite3.Connection) -> dict[str, str]:
    """Insert 20 parts from inventory.csv. Returns SKU→part_id mapping."""
    inventory = _read_csv(SEED_DIR / "inventory.csv")
    spreadsheet = _read_csv(SEED_DIR / "spreadsheet.csv")
    spread_map = {row["sku"]: row for row in spreadsheet}

    sku_to_part_id = {}

    for row in inventory:
        sku = row["sku"]
        part_id = _new_id()
        sku_to_part_id[sku] = part_id

        spread = spread_map.get(sku, {})
        part_type = spread.get("part_type", "side-view mirror")
        side = spread.get("side", "").strip().lower()
        if side in ("", "n/a"):
            side = None
        part_number = spread.get("part_number", "").strip()
        if not part_number:
            part_number = None

        # Asking price from inventory
        price_aed = float(row.get("asking_price_aed", 0) or 0)

        conn.execute(
            """INSERT INTO parts (id, seller_id, sku, title, part_number, part_type, side, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'Ingested')""",
            (
                part_id,
                SELLER_ID,
                sku,
                row.get("short_description", ""),
                part_number,
                part_type,
                side,
            ),
        )

        # Audit: ingest event
        write_audit(conn, part_id=part_id, event_type="ingest", actor="seed.py", action="part ingested",
                    source="data/seed/spreadsheet.csv", field="part", before=None, after=sku,
                    detail={"sku": sku, "price_aed": price_aed, "rack_code": row.get("rack_code")})

    conn.commit()
    return sku_to_part_id


def seed_donor_vehicles(conn: sqlite3.Connection, sku_to_part_id: dict[str, str]) -> None:
    """Insert donor vehicle records and link donor notes as evidence."""
    donor_csv = _read_csv(SEED_DIR / "donor_vehicle.csv")

    for row in donor_csv:
        sku = row["sku"]
        part_id = sku_to_part_id.get(sku)
        if not part_id:
            continue

        dv_id = _new_id()
        note_text = _load_donor_note(sku)

        conn.execute(
            """INSERT INTO donor_vehicles
               (id, part_id, make, model, year, vin, trim, market, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dv_id,
                part_id,
                row.get("make"),
                row.get("model"),
                int(row["year"]) if row.get("year") else None,
                row.get("vin"),
                row.get("trim"),
                row.get("market"),
                note_text,
            ),
        )

        # Store donor note as evidence if present
        if note_text:
            ev_id = _new_id()
            # Determine if note is Arabic-only, mixed, or English
            is_arabic = any("\u0600" <= ch <= "\u06FF" for ch in note_text)
            is_arabic_indic = any("\u0660" <= ch <= "\u0669" for ch in note_text)

            kind = "donor_note"
            if is_arabic and not any(ch.isascii() and ch.isalpha() for ch in note_text):
                kind = "arabic_note"

            conn.execute(
                """INSERT INTO evidence (id, part_id, kind, source_ref, content, confidence)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    ev_id,
                    part_id,
                    kind,
                    f"data/seed/donor_notes/{sku}.txt",
                    note_text,
                    None,
                ),
            )
            _screen_ingested(conn, note_text, f"note:{sku}.txt", part_id)

    conn.commit()


def seed_spreadsheet_evidence(conn: sqlite3.Connection, sku_to_part_id: dict[str, str]) -> None:
    """Store spreadsheet cell values as evidence (Rule f: text is data, not instructions)."""
    spreadsheet = _read_csv(SEED_DIR / "spreadsheet.csv")

    for row in spreadsheet:
        sku = row["sku"]
        part_id = sku_to_part_id.get(sku)
        if not part_id:
            continue

        # Store each non-empty cell as spreadsheet_cell evidence
        for col, val in row.items():
            if col == "sku" or not val.strip():
                continue
            ev_id = _new_id()
            conn.execute(
                """INSERT INTO evidence (id, part_id, kind, source_ref, content, confidence)
                   VALUES (?, ?, 'spreadsheet_cell', ?, ?, ?)""",
                (
                    ev_id,
                    part_id,
                    f"data/seed/spreadsheet.csv:{sku}:{col}",
                    val.strip(),
                    None,
                ),
            )
            _screen_ingested(conn, val.strip(), f"spreadsheet:{sku}:{col}", part_id)

    conn.commit()


def seed_photo_evidence(conn: sqlite3.Connection, sku_to_part_id: dict[str, str]) -> None:
    """Store photo references as evidence, with the views each photo shows (C09) and its regions."""
    tags = _read_csv(SEED_DIR / "photo_views.csv")
    views = {row["photo"]: [v for v in row["views"].split(";") if v] for row in tags}
    regions = {row["photo"]: json.loads(row.get("regions") or "[]") for row in tags}
    for sku, part_id in sku_to_part_id.items():
        for photo_ref in _photo_refs(sku):
            ev_id = _new_id()
            conn.execute(
                """INSERT INTO evidence (id, part_id, kind, source_ref, content, confidence, raw_data)
                   VALUES (?, ?, 'photo_region', ?, ?, ?, ?)""",
                (
                    ev_id,
                    part_id,
                    photo_ref,
                    f"Placeholder photo for {sku}",
                    None,
                    json.dumps({"views": views.get(Path(photo_ref).name, []),
                                "regions": regions.get(Path(photo_ref).name, [])}),
                ),
            )

    conn.commit()


def seed_components(conn: sqlite3.Connection, sku_to_part_id: dict[str, str]) -> None:
    """Record included components (glass, cover, indicator, wiring plug) per part (C08)."""
    for row in _read_csv(SEED_DIR / "components.csv"):
        part_id = sku_to_part_id.get(row["sku"])
        components = {k: v.strip() for k, v in row.items() if k != "sku" and v.strip()}
        if not part_id or not components:
            continue
        ev_id = _new_id()
        conn.execute(
            """INSERT INTO evidence (id, part_id, kind, source_ref, content)
               VALUES (?, ?, 'spreadsheet_cell', ?, ?)""",
            (ev_id, part_id, f"data/seed/components.csv:{row['sku']}", json.dumps(components)),
        )
        EvidenceLedger(conn, part_id).set_attribute(
            "included_components",
            components,
            AttributeStatus.EXTRACTED,
            evidence_id=ev_id,
            reason="Inventory component list",
            actor="seed",
        )

    conn.commit()


def seed_compat_refs(conn: sqlite3.Connection) -> None:
    """Load compatibility reference data from compat_ref.csv."""
    compat_csv = _read_csv(REFERENCE_DIR / "compat_ref.csv")

    for row in compat_csv:
        cr_id = _new_id()
        conn.execute(
            """INSERT INTO compat_refs
               (id, part_number, make, model, year_start, year_end, trim, market, region, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cr_id,
                row.get("part_number", "").strip(),
                row.get("make", ""),
                row.get("model", ""),
                int(row["year_from"]) if row.get("year_from") else None,
                int(row["year_to"]) if row.get("year_to") else None,
                None,
                row.get("market"),
                row.get("market"),  # Using market as region too for simplicity
                f"mock_oem_catalogue_{row.get('ref_version', 'v0.1')}",
            ),
        )

    conn.commit()


def seed_all(db_path: Path | str | None = None) -> dict:
    """Run the full seed sequence. Returns a summary dict."""
    path = Path(db_path) if db_path else DB_PATH
    init_db(path)
    conn = get_connection(path)

    seed_seller(conn)
    sku_to_part_id = seed_parts(conn)
    seed_donor_vehicles(conn, sku_to_part_id)
    seed_spreadsheet_evidence(conn, sku_to_part_id)
    seed_photo_evidence(conn, sku_to_part_id)
    seed_components(conn, sku_to_part_id)
    seed_compat_refs(conn)

    # Summary counts
    part_count = conn.execute("SELECT COUNT(*) as c FROM parts").fetchone()["c"]
    donor_count = conn.execute("SELECT COUNT(*) as c FROM donor_vehicles").fetchone()["c"]
    evidence_count = conn.execute("SELECT COUNT(*) as c FROM evidence").fetchone()["c"]
    compat_count = conn.execute("SELECT COUNT(*) as c FROM compat_refs").fetchone()["c"]

    conn.close()

    summary = {
        "parts": part_count,
        "donor_vehicles": donor_count,
        "evidence": evidence_count,
        "compat_refs": compat_count,
        "sku_to_part_id": sku_to_part_id,
    }
    return summary


if __name__ == "__main__":
    summary = seed_all()
    print("Seed complete:")
    print(f"  Parts:           {summary['parts']}")
    print(f"  Donor vehicles:  {summary['donor_vehicles']}")
    print(f"  Evidence records:{summary['evidence']}")
    print(f"  Compat refs:     {summary['compat_refs']}")
    print(f"  DB path:         {DB_PATH}")
