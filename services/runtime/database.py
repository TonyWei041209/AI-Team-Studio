"""SQLite database initialization and connection management."""

import sqlite3
from pathlib import Path

# Database file lives in the project-level data/ directory
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "ai_team_studio.db"


def get_db_path() -> Path:
    """Return the path to the SQLite database file."""
    return DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get a synchronous SQLite connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _get_current_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    cursor = conn.execute("SELECT MAX(version) FROM schema_version")
    row = cursor.fetchone()
    return row[0] if row[0] is not None else 0


def _apply_v1(conn: sqlite3.Connection) -> None:
    """V1: Initial schema_version table (Phase 0+1)."""
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")


def _apply_v2(conn: sqlite3.Connection) -> None:
    """V2: Core data models (Phase 2)."""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            local_repo_path TEXT NOT NULL,
            default_branch TEXT NOT NULL DEFAULT 'main',
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id),
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            priority TEXT NOT NULL DEFAULT 'medium',
            assigned_agent_role TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id),
            role TEXT NOT NULL,
            model_provider TEXT,
            model_name TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            input_summary TEXT NOT NULL DEFAULT '',
            output_summary TEXT NOT NULL DEFAULT '',
            started_at TEXT,
            ended_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS approval_requests (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id),
            run_id TEXT REFERENCES agent_runs(id),
            action_type TEXT NOT NULL,
            action_payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS log_events (
            id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES tasks(id),
            run_id TEXT REFERENCES agent_runs(id),
            level TEXT NOT NULL DEFAULT 'info',
            source TEXT NOT NULL DEFAULT 'system',
            message TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Indexes for common queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_task ON agent_runs(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_task ON approval_requests(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_status ON approval_requests(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_task ON log_events(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_run ON log_events(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON log_events(level)")

    conn.execute("INSERT INTO schema_version (version) VALUES (2)")


# Ordered list of migrations
_MIGRATIONS = [
    (1, _apply_v1),
    (2, _apply_v2),
]


def init_db() -> None:
    """Initialize the SQLite database and apply pending migrations."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        current = _get_current_version(conn)
        for version, migrate_fn in _MIGRATIONS:
            if version > current:
                migrate_fn(conn)
                print(f"[database] Applied migration v{version}")
        conn.commit()
    finally:
        conn.close()
