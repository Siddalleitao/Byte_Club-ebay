"""Initialize the SQLite database from schema.sql."""

from app.data.database import init_db

if __name__ == "__main__":
    init_db()
    print("Database initialized at data/partproof.db")
