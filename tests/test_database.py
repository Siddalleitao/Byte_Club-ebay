"""Tests for SQLite schema initialization."""

import sqlite3
from pathlib import Path

from app.data.database import get_connection, init_db, reset_db


EXPECTED_TABLES = {
    "sellers",
    "parts",
    "donor_vehicles",
    "attributes",
    "evidence",
    "attribute_evidence",
    "compat_refs",
    "questions",
    "claims",
    "listing_drafts",
    "checklist_results",
    "audit_events",
}


def test_init_db_creates_all_tables(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = get_connection(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    tables = {r["name"] for r in rows}
    conn.close()
    assert EXPECTED_TABLES.issubset(tables)


def test_reset_db_recreates(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = get_connection(db)
    conn.execute("INSERT INTO sellers (id, name) VALUES ('s1', 'Test')")
    conn.commit()
    conn.close()

    reset_db(db)

    conn = get_connection(db)
    count = conn.execute("SELECT COUNT(*) as c FROM sellers").fetchone()["c"]
    conn.close()
    assert count == 0


def test_foreign_keys_enforced(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = get_connection(db)
    # Inserting a part with a non-existent seller should fail
    try:
        conn.execute(
            "INSERT INTO parts (id, seller_id, part_type) VALUES ('p1', 'nope', 'mirror')"
        )
        conn.commit()
        fk_ok = False
    except sqlite3.IntegrityError:
        fk_ok = True
    conn.close()
    assert fk_ok, "Foreign key constraint was not enforced"
