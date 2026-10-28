"""Tests for the synthetic test pack: seed loads, record counts, planted issues, checksum."""

import csv
import hashlib
import json
from pathlib import Path

import pytest

from scripts.seed import seed_all
from app.data.database import get_connection, reset_db

SEED_DIR = Path("data/seed")
REFERENCE_DIR = Path("data/reference")
ANSWER_KEY_PATH = Path("data/answer_key/answer_key.json")
COMPAT_CSV = REFERENCE_DIR / "compat_ref.csv"
COMPAT_CHECKSUM = REFERENCE_DIR / "compat_ref.sha256"
INVENTORY_CSV = SEED_DIR / "inventory.csv"


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory):
    """Seed a temporary DB once for all tests in this module."""
    db = tmp_path_factory.mktemp("db") / "test_partproof.db"
    summary = seed_all(db)
    return {"db_path": db, "summary": summary}


# ── Record count tests ──


def test_inventory_has_20_records():
    with open(INVENTORY_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 20


def test_inventory_columns():
    with open(INVENTORY_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        assert set(reader.fieldnames) == {
            "sku",
            "short_description",
            "asking_price_aed",
            "rack_code",
        }


def test_inventory_sku_range():
    with open(INVENTORY_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    skus = {r["sku"] for r in rows}
    expected = {f"M-{i:02d}" for i in range(1, 21)}
    assert skus == expected


def test_rack_codes_valid():
    valid_racks = {f"A{i}" for i in range(1, 7)} | {f"B{i}" for i in range(1, 7)} | {f"C{i}" for i in range(1, 5)}
    with open(INVENTORY_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        assert row["rack_code"] in valid_racks, f"{row['sku']} has invalid rack {row['rack_code']}"


def test_seeded_db_has_20_parts(seeded_db):
    assert seeded_db["summary"]["parts"] == 20


def test_seeded_db_has_20_donor_vehicles(seeded_db):
    assert seeded_db["summary"]["donor_vehicles"] == 20


def test_seeded_db_has_compat_refs(seeded_db):
    assert seeded_db["summary"]["compat_refs"] > 0


def test_seeded_db_has_evidence(seeded_db):
    assert seeded_db["summary"]["evidence"] > 0


# ── Planted issue tests (from answer key) ──


@pytest.fixture(scope="module")
def answer_key():
    with open(ANSWER_KEY_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_answer_key_has_20_parts(answer_key):
    assert len(answer_key["parts"]) == 20


def test_clean_parts_count(answer_key):
    """PRD requires exactly 4 clean records."""
    assert len(answer_key["clean_parts"]) == 4


def test_planted_issue_counts(answer_key):
    """Verify each E-ID appears the expected number of times."""
    summary = answer_key["planted_issue_summary"]
    expected_counts = {
        "E16": 2,
        "E17": 1,
        "E12": 2,
        "E11": 1,
        "E22": 2,
        "E24": 2,
        "E28": 3,
        "E30": 1,
        "E33": 2,
        "E37": 3,
        "E44": 3,
        "E10": 1,
        "E53": 1,
        "E39": 1,
    }
    for eid, expected in expected_counts.items():
        actual = summary[eid]["count"]
        assert actual == expected, f"{eid}: expected {expected}, got {actual}"


def test_total_planted_issues(answer_key):
    """Sum of all issue counts."""
    summary = answer_key["planted_issue_summary"]
    total = sum(v["count"] for v in summary.values())
    # 2+1+2+1+2+2+3+1+2+3+3+1+1+1 = 25
    assert total == 25


def test_all_journeys_present(answer_key):
    journeys = answer_key["journeys"]
    for j in ("J1", "J2", "J3", "J4", "J5"):
        assert j in journeys, f"Missing journey {j}"
        assert len(journeys[j]["parts"]) > 0, f"Journey {j} has no parts"


def test_m01_is_clean(answer_key):
    part = answer_key["parts"]["M-01"]
    assert part["planted_issues"] == []
    assert part["expected_status"] == "Approved"
    assert part["journey"] == "J1"


def test_m07_reproduces_brief(answer_key):
    """M-07 must have: E12 (unreadable), E16 (year conflict), E24 (defect), E28 (untested), E37 (weight missing)."""
    part = answer_key["parts"]["M-07"]
    issues = set(part["planted_issues"])
    assert {"E12", "E16", "E24", "E28", "E37"} == issues
    # Donor year must be conflicting
    assert part["attributes"]["donor_year"]["status"] == "Conflicting"
    # Part number must be unverified (unreadable)
    assert part["attributes"]["part_number"]["status"] == "Unverified"
    assert part["attributes"]["part_number"]["expected_value"] == "87910-0????"


def test_m04_journey_j4(answer_key):
    """M-04 is recorded as Left so journey J4 can flip it to Right."""
    part = answer_key["parts"]["M-04"]
    assert part["journey"] == "J4"
    assert "E22" in part["planted_issues"]
    assert part["attributes"]["side"]["expected_value"] == "right"


def test_m17_is_excluded_airbag(answer_key):
    part = answer_key["parts"]["M-17"]
    assert "E39" in part["planted_issues"]
    assert part["attributes"]["part_type"]["expected_value"] == "airbag module"


def test_m15_has_prompt_injection(answer_key):
    part = answer_key["parts"]["M-15"]
    assert "E53" in part["planted_issues"]


def test_m12_has_arabic_indic_digits(answer_key):
    part = answer_key["parts"]["M-12"]
    assert "E10" in part["planted_issues"]


# ── Donor notes tests ──


def test_donor_notes_exist_for_all_parts():
    notes_dir = SEED_DIR / "donor_notes"
    for i in range(1, 21):
        sku = f"M-{i:02d}"
        assert (notes_dir / f"{sku}.txt").exists(), f"Missing donor note for {sku}"


def test_at_least_one_arabic_indic_digit_note():
    """M-12 donor note must contain Arabic-Indic digits."""
    note = (SEED_DIR / "donor_notes" / "M-12.txt").read_text(encoding="utf-8")
    # Arabic-Indic digits: ٠١٢٣٤٥٦٧٨٩ (U+0660–U+0669)
    has_arabic_indic = any("\u0660" <= ch <= "\u0669" for ch in note)
    assert has_arabic_indic, "M-12 note should contain Arabic-Indic digits"


def test_arabic_only_notes_count():
    """At least 3 Arabic-only notes (E44).

    A note is 'Arabic-only' if it has Arabic script but no English words
    (2+ consecutive ASCII letters forming a word). Individual part-number
    characters like 'A' in '3AA0A' don't count as English words.
    """
    import re

    notes_dir = SEED_DIR / "donor_notes"
    arabic_only = []
    for i in range(1, 21):
        sku = f"M-{i:02d}"
        text = (notes_dir / f"{sku}.txt").read_text(encoding="utf-8")
        has_arabic = any("\u0600" <= ch <= "\u06FF" for ch in text)
        # Look for English words: 2+ consecutive ASCII letters
        has_english_word = bool(re.search(r"[A-Za-z]{2,}", text))
        if has_arabic and not has_english_word:
            arabic_only.append(sku)
    assert len(arabic_only) >= 3, f"Expected >=3 Arabic-only notes, got {len(arabic_only)}: {arabic_only}"


def test_prompt_injection_text_in_data():
    """E53: the prompt injection text must exist as data in the spreadsheet."""
    with open(SEED_DIR / "spreadsheet.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    found = False
    for row in rows:
        for val in row.values():
            if "Ignore previous instructions" in val:
                found = True
                break
    assert found, "Prompt injection text (E53) not found in spreadsheet"


# ── Reference data tests ──


def test_compat_ref_checksum_matches():
    """The SHA-256 checksum file must match the actual CSV content."""
    if not COMPAT_CHECKSUM.exists():
        pytest.skip("Checksum file not generated yet. Run: python scripts/generate_checksum.py")

    expected_hash = COMPAT_CHECKSUM.read_text(encoding="utf-8").strip().split()[0]
    actual_hash = hashlib.sha256(COMPAT_CSV.read_bytes()).hexdigest()
    assert expected_hash == actual_hash, "compat_ref.csv checksum mismatch"


def test_compat_ref_covers_m01_not_m07():
    """Reference covers M-01's part number but NOT M-07's (so M-07 compat stays unverified)."""
    with open(COMPAT_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    part_numbers = {r["part_number"] for r in rows}

    # M-01 part number must be in reference
    assert "87910-02A10" in part_numbers, "M-01 part number must be in reference"

    # M-07 part number (87910-0????) must NOT be in reference
    assert "87910-0????" not in part_numbers, "M-07 part number must NOT be in reference"

    # M-15 part number (DB3Z-17683-A, E33) must NOT be in reference
    assert "DB3Z-17683-A" not in part_numbers, "M-15 part number must NOT be in reference"


def test_checklist_has_15_items():
    """Checklist YAML must have C01–C15."""
    import yaml

    with open(REFERENCE_DIR / "checklist_v0.1.yaml", encoding="utf-8") as f:
        checklist = yaml.safe_load(f)
    ids = [item["id"] for item in checklist["items"]]
    expected = {f"C{i:02d}" for i in range(1, 16)}
    assert set(ids) == expected
    # Must have disclaimer
    assert "disclaimer" in checklist
    assert "TEST checklist, not customs or legal guidance" in checklist["purpose"]


def test_glossary_marks_driver_side_ambiguous():
    import yaml

    with open(REFERENCE_DIR / "glossary.yaml", encoding="utf-8") as f:
        glossary = yaml.safe_load(f)
    terms = {t["term_en"]: t for t in glossary["terms"]}
    assert terms["driver side"]["ambiguous"] is True
    assert terms["passenger side"]["ambiguous"] is True
    assert terms["left side"]["ambiguous"] is False
    # UK terms present
    assert "wing mirror" in terms
    assert "door mirror" in terms


def test_seller_context_fields():
    ctx = json.loads((REFERENCE_DIR / "seller_context.json").read_text(encoding="utf-8"))
    assert ctx["seller_id"] == "S-001"
    assert ctx["name"] == "Noor"
    assert "location" in ctx
    assert "market_corridor" in ctx


# ── DB integrity after seed ──


def test_seeded_db_part_m01_has_verified_part_number(seeded_db):
    conn = get_connection(seeded_db["db_path"])
    rows = conn.execute(
        """SELECT p.part_number, p.side, p.part_type FROM parts p
           JOIN sellers s ON p.seller_id = s.id
           WHERE s.id = 'S-001' AND p.part_number = '87910-02A10'"""
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["part_type"] == "side-view mirror"


def test_seeded_db_m17_is_airbag(seeded_db):
    conn = get_connection(seeded_db["db_path"])
    rows = conn.execute(
        "SELECT part_type FROM parts WHERE part_type = 'airbag module'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_seeded_db_has_spreadsheet_evidence_with_injection(seeded_db):
    """The prompt injection text (E53) must be stored as evidence data, not executed."""
    conn = get_connection(seeded_db["db_path"])
    rows = conn.execute(
        """SELECT content FROM evidence
           WHERE kind = 'spreadsheet_cell' AND content LIKE '%Ignore previous instructions%'"""
    ).fetchall()
    conn.close()
    assert len(rows) >= 1, "Prompt injection text should be stored as spreadsheet_cell evidence"


def test_seeded_db_arabic_notes_stored(seeded_db):
    """Arabic donor notes must be stored as evidence."""
    conn = get_connection(seeded_db["db_path"])
    rows = conn.execute(
        "SELECT COUNT(*) as c FROM evidence WHERE kind IN ('arabic_note', 'donor_note')"
    ).fetchone()
    conn.close()
    assert rows["c"] >= 10, "Expected at least 10 donor note evidence records"
