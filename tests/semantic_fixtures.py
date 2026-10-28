"""Shared fixtures for the semantic layer tests.

`two_seller_db` seeds the synthetic test pack (seller S-001, Noor) and adds a
second synthetic seller (S-002, Omar) with one part that has a row in every
entity table, so cross-seller isolation can be checked on every view.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.data.database import get_connection
from app.domain.models import SellerProfile
from app.semantic.prompt_prefix import clear_prompt_prefix_cache
from app.semantic.seller_context import upsert_profile
from scripts.seed import seed_all

OTHER_SELLER = "S-002"
OTHER_SKU = "X-01"


def _id() -> str:
    return str(uuid.uuid4())


def add_other_seller(db_path: Path) -> dict[str, str]:
    """Add seller S-002 with one fully populated part. Returns the created IDs."""
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO sellers (id, name, location, seller_type) VALUES (?, 'Omar', 'Sharjah, UAE', 'dealer')",
        (OTHER_SELLER,),
    )
    upsert_profile(conn, SellerProfile(
        seller_id=OTHER_SELLER, display_name="Omar", business_name="Omar Parts (synthetic)",
        city="Sharjah, UAE", languages=["ar"], corridor="UAE-UK", handling_days=7,
        rack_codes=["Z1-Z2"], product_family="side_view_mirror", checklist_version="v0.1-test",
    ))
    ids = {k: _id() for k in ("part", "attr", "ev", "q", "claim", "cr", "dv", "draft")}
    conn.execute(
        """INSERT INTO parts (id, seller_id, sku, part_number, part_type, side, status)
           VALUES (?, ?, ?, '11111-22A33', 'side-view mirror', 'right', 'Awaiting seller')""",
        (ids["part"], OTHER_SELLER, OTHER_SKU),
    )
    conn.execute(
        "INSERT INTO evidence (id, part_id, kind, source_ref, content) VALUES (?, ?, 'spreadsheet_cell', 'sheet:X-01:weight', '')",
        (ids["ev"], ids["part"]),
    )
    conn.execute(
        "INSERT INTO attributes (id, part_id, name, value, status) VALUES (?, ?, 'donor_year', '2015', 'Conflicting')",
        (ids["attr"], ids["part"]),
    )
    conn.execute(
        "INSERT INTO attribute_evidence (id, attribute_id, evidence_id, claim_type, claim_value) VALUES (?, ?, ?, 'fact', '2015')",
        (_id(), ids["attr"], ids["ev"]),
    )
    conn.execute(
        """INSERT INTO questions (id, part_id, question_text, priority, attribute_name, status)
           VALUES (?, ?, 'Which year is correct?', 'high', 'donor_year', 'open')""",
        (ids["q"], ids["part"]),
    )
    conn.execute(
        "INSERT INTO claims (id, part_id, attribute_name, claim_type, claim_value, evidence_id) VALUES (?, ?, 'donor_year', 'donor_info', '2015', NULL)",
        (ids["claim"], ids["part"]),
    )
    conn.execute(
        "INSERT INTO checklist_results (id, part_id, checklist_json, passed) VALUES (?, ?, ?, 1)",
        (ids["cr"], ids["part"], json.dumps({"version": "0.1", "items": [{"item_id": "C01", "result": "pass"}]})),
    )
    conn.execute(
        "INSERT INTO donor_vehicles (id, part_id, make, model, year, vin) VALUES (?, ?, 'Kia', 'Rio', 2015, 'SYNTHVIN000123456')",
        (ids["dv"], ids["part"]),
    )
    conn.execute(
        "INSERT INTO listing_drafts (id, part_id, title, status, handling_days) VALUES (?, ?, 'Kia Rio mirror', 'draft', 7)",
        (ids["draft"], ids["part"]),
    )
    conn.commit()
    conn.close()
    return ids


@pytest.fixture
def two_seller_db(tmp_path):
    clear_prompt_prefix_cache()
    db_path = tmp_path / "semantic.db"
    summary = seed_all(db_path)
    other = add_other_seller(db_path)
    yield {"db_path": db_path, "sku_to_part_id": summary["sku_to_part_id"], "other": other}
    clear_prompt_prefix_cache()


@pytest.fixture
def conn(two_seller_db):
    c = get_connection(two_seller_db["db_path"])
    yield c
    c.close()
