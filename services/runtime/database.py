"""SQLite database initialization and connection management."""

import os
import sqlite3
from pathlib import Path

# Database file lives in the project-level data/ directory
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "ai_team_studio.db"


def get_db_path() -> Path:
    """Return the path to the SQLite database file."""
    return DB_PATH


def init_db() -> None:
    """Initialize the SQLite database and create the schema version table."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Insert initial version if table is empty
        cursor = conn.execute("SELECT COUNT(*) FROM schema_version")
        if cursor.fetchone()[0] == 0:
            conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def get_connection() -> sqlite3.Connection:
    """Get a synchronous SQLite connection."""
    return sqlite3.connect(str(DB_PATH))
