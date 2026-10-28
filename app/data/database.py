"""SQLite database helper — connection management and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
VIEWS_PATH = Path(__file__).parent / "semantic_views.sql"
DB_PATH = Path("data/partproof.db")


def get_connection(
    db_path: Path | str | None = None, *, check_same_thread: bool = True
) -> sqlite3.Connection:
    """Return a SQLite connection with foreign keys enabled.

    Pass check_same_thread=False for a per-request connection that a web
    framework may open and use on different threads (never concurrently).
    """
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path | str | None = None) -> None:
    """Create all tables from schema.sql, migrate columns, and (re)create views (idempotent)."""
    conn = get_connection(db_path)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    _add_missing_columns(conn, schema_sql)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_parts_seller_sku ON parts(seller_id, sku)"
    )
    conn.executescript(VIEWS_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def reset_db(db_path: Path | str | None = None) -> None:
    """Drop and recreate the database (for testing)."""
    path = Path(db_path) if db_path else DB_PATH
    if path.exists():
        path.unlink()
    init_db(path)


def _add_missing_columns(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Add columns that schema.sql defines but an older database file lacks.

    CREATE TABLE IF NOT EXISTS never alters an existing table, so the expected
    columns are read from the schema built in memory. SQLite's ADD COLUMN
    cannot take a non-constant default or a NOT NULL without a default; such
    columns are added as plain nullable columns.
    """
    reference = sqlite3.connect(":memory:")
    reference.executescript(schema_sql)
    tables = reference.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (table,) in tables:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for _, name, col_type, notnull, default, _ in reference.execute(f"PRAGMA table_info({table})"):
            if name in existing:
                continue
            decl = f"{name} {col_type}"
            if default is not None and "(" not in default:
                decl += f" DEFAULT {default}"
                if notnull:
                    decl += " NOT NULL"
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {decl}")
    reference.close()
