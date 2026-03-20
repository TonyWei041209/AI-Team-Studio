"""SQLite database initialization and connection management."""

import os
import sqlite3
from pathlib import Path

# Database file lives in the project-level data/ directory.
# Override with RUNTIME_DB or ATS_DB_PATH env var for isolated test runs.
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_db_override = os.environ.get("ATS_DB_PATH") or os.environ.get("RUNTIME_DB")
DB_PATH = Path(_db_override) if _db_override else DATA_DIR / "ai_team_studio.db"

# For :memory: databases, reuse a single connection so all callers share state.
_MEMORY_CONN: sqlite3.Connection | None = None


def get_db_path() -> Path:
    """Return the path to the SQLite database file."""
    return DB_PATH


class _MemoryConnProxy:
    """Proxy for the shared :memory: connection.

    close() is intentionally a no-op so that helper callers using
    ``finally: conn.close()`` do not destroy the shared in-memory state.
    Test reset routines that want a clean slate should drop tables and call
    ``_ensure_schema()`` — the migration runner will rebuild from scratch.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        # No-op: keep the shared in-memory connection alive between calls.
        pass


def get_connection() -> sqlite3.Connection:
    """Get a synchronous SQLite connection with row factory.

    For :memory: databases (test mode) returns a shared-state proxy so that
    all callers see the same in-memory database.  Calling close() on the proxy
    resets the shared connection, enabling test isolation.
    For file-based databases a new connection is returned as usual.
    """
    global _MEMORY_CONN
    if str(DB_PATH) == ":memory:":
        if _MEMORY_CONN is None:
            _MEMORY_CONN = sqlite3.connect(":memory:", check_same_thread=False)
            _MEMORY_CONN.row_factory = sqlite3.Row
            _MEMORY_CONN.execute("PRAGMA foreign_keys=ON")
        return _MemoryConnProxy(_MEMORY_CONN)  # type: ignore[return-value]
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def _apply_v3(conn: sqlite3.Connection) -> None:
    """V3: Make approval_requests.task_id nullable for direct tool execution (Phase 4A).

    Direct tool invocations via POST /api/tools/execute may not have a task
    context.  The approval gate still needs to create approval_requests rows,
    so task_id must accept NULL.
    """
    conn.execute("""
        CREATE TABLE approval_requests_v3 (
            id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES tasks(id),
            run_id TEXT REFERENCES agent_runs(id),
            action_type TEXT NOT NULL,
            action_payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT
        )
    """)
    conn.execute("""
        INSERT INTO approval_requests_v3
        SELECT id, task_id, run_id, action_type, action_payload,
               status, reviewer_comment, created_at, resolved_at
        FROM approval_requests
    """)
    conn.execute("DROP TABLE approval_requests")
    conn.execute("ALTER TABLE approval_requests_v3 RENAME TO approval_requests")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_approvals_task ON approval_requests(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_approvals_status ON approval_requests(status)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (3)")


def _apply_v4(conn: sqlite3.Connection) -> None:
    """V4: Provider settings table (Phase 6A).

    Stores API keys and configuration for each LLM provider.
    API keys are stored in the local SQLite DB only — never in git-tracked files.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS provider_settings (
            provider_name TEXT PRIMARY KEY,
            api_key TEXT NOT NULL DEFAULT '',
            base_url TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 0,
            config_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("INSERT INTO schema_version (version) VALUES (4)")


def _apply_v5(conn: sqlite3.Connection) -> None:
    """V5: Role-model settings table (Phase 6C).

    Per-role model configuration: each agent role can independently select
    a provider and model.  API keys are NOT stored here — they remain in
    provider_settings (V4).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS role_model_settings (
            role TEXT PRIMARY KEY,
            provider TEXT NOT NULL DEFAULT 'mock',
            model TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Seed default rows from agent definitions
    defaults = [
        ("planner", "anthropic", "claude-sonnet-4-20250514", 1),
        ("builder", "mock", "", 0),
        ("qa", "mock", "", 0),
        ("reviewer", "anthropic", "claude-3-5-haiku-20241022", 1),
    ]
    for role, provider, model, enabled in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO role_model_settings (role, provider, model, enabled) "
            "VALUES (?, ?, ?, ?)",
            (role, provider, model, enabled),
        )
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")


def _apply_v6(conn: sqlite3.Connection) -> None:
    """V6: Execution proposals table + proposal linkage (Phase 6E-A).

    Builder produces structured execution proposals that are persisted
    separately from AgentRun output_summary.  Each proposal can be linked
    to an ApprovalRequest via the new proposal_id FK.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_proposals (
            id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES tasks(id),
            run_id TEXT REFERENCES agent_runs(id),
            role TEXT NOT NULL DEFAULT 'builder',
            proposal_data TEXT NOT NULL DEFAULT '{}',
            risk_level TEXT NOT NULL DEFAULT 'medium',
            requires_approval INTEGER NOT NULL DEFAULT 1,
            approval_reasons TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_task "
        "ON execution_proposals(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_run "
        "ON execution_proposals(run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_status "
        "ON execution_proposals(status)"
    )
    # Add proposal_id FK to approval_requests
    conn.execute(
        "ALTER TABLE approval_requests "
        "ADD COLUMN proposal_id TEXT REFERENCES execution_proposals(id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_approvals_proposal "
        "ON approval_requests(proposal_id)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (6)")


def _apply_v7(conn: sqlite3.Connection) -> None:
    """V7: Execution snapshots table (Phase 6E-B).

    An execution snapshot is a frozen, immutable copy of an approved
    proposal.  Once created it cannot be modified or deleted.
    The content_hash (SHA-256 of snapshot_data) enables tamper detection.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_snapshots (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL UNIQUE REFERENCES execution_proposals(id),
            approval_id TEXT NOT NULL REFERENCES approval_requests(id),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            snapshot_data TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'frozen',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_task "
        "ON execution_snapshots(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_proposal "
        "ON execution_snapshots(proposal_id)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (7)")


def _apply_v8(conn: sqlite3.Connection) -> None:
    """V8: Execution requests table (Phase 6E-C).

    An execution request records the intent to execute a frozen snapshot.
    Status flows: requested -> confirmed | rejected.
    snapshot_content_hash is copied at creation time for integrity verification.
    UNIQUE(snapshot_id) ensures one request per snapshot.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_requests (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id),
            proposal_id TEXT NOT NULL REFERENCES execution_proposals(id),
            approval_id TEXT NOT NULL REFERENCES approval_requests(id),
            snapshot_id TEXT NOT NULL UNIQUE REFERENCES execution_snapshots(id),
            snapshot_content_hash TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'requested'
                CHECK (status IN ('requested', 'confirmed', 'rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exec_requests_task "
        "ON execution_requests(task_id)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (8)")


def _apply_v9(conn: sqlite3.Connection) -> None:
    """V9: Execution results table (Phase 6F-A).

    Records the outcome of executing a confirmed execution request.
    In 6F-A only dry-run results are stored (no real file/shell/git ops).

    Status values:
      - pending  : reserved for async execution (not used in 6F-A)
      - running  : reserved for async execution (not used in 6F-A)
      - completed: dry-run finished successfully
      - failed   : dry-run encountered an error

    UNIQUE(execution_request_id) ensures one result per request.
    snapshot_content_hash is copied for integrity verification.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_results (
            id TEXT PRIMARY KEY,
            execution_request_id TEXT NOT NULL UNIQUE
                REFERENCES execution_requests(id),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            snapshot_id TEXT NOT NULL REFERENCES execution_snapshots(id),
            snapshot_content_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            result_data TEXT NOT NULL DEFAULT '{}',
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exec_results_task "
        "ON execution_results(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exec_results_request "
        "ON execution_results(execution_request_id)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (9)")


def _apply_v10(conn: sqlite3.Connection) -> None:
    """V10: Add mode field to execution_results (Phase 7A).

    Adds a ``mode`` column (``dry_run`` or ``real_run``) and changes the
    unique constraint from ``UNIQUE(execution_request_id)`` to
    ``UNIQUE(execution_request_id, mode)`` so a single request can have
    both a dry-run result and a real-run result.

    Existing rows are back-filled with ``mode = 'dry_run'``.
    """
    # SQLite cannot add constraints or change UNIQUE in-place.
    # Strategy: rename → recreate → copy → drop old.
    conn.execute("ALTER TABLE execution_results RENAME TO _execution_results_v9")

    conn.execute("""
        CREATE TABLE execution_results (
            id TEXT PRIMARY KEY,
            execution_request_id TEXT NOT NULL
                REFERENCES execution_requests(id),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            snapshot_id TEXT NOT NULL REFERENCES execution_snapshots(id),
            snapshot_content_hash TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'dry_run'
                CHECK (mode IN ('dry_run', 'real_run')),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            result_data TEXT NOT NULL DEFAULT '{}',
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (execution_request_id, mode)
        )
    """)

    # Copy existing rows, back-fill mode = 'dry_run'
    conn.execute("""
        INSERT INTO execution_results
            (id, execution_request_id, task_id, snapshot_id,
             snapshot_content_hash, mode, status, result_data,
             started_at, completed_at, created_at)
        SELECT id, execution_request_id, task_id, snapshot_id,
               snapshot_content_hash, 'dry_run', status, result_data,
               started_at, completed_at, created_at
        FROM _execution_results_v9
    """)

    conn.execute("DROP TABLE _execution_results_v9")

    # Recreate indexes
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exec_results_task "
        "ON execution_results(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exec_results_request "
        "ON execution_results(execution_request_id)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (10)")


def _apply_v11(conn: sqlite3.Connection) -> None:
    """V11: Execution file backups table (Phase 7C-1).

    Stores original file content before a real_run modifies it,
    enabling future rollback.  Only file_modify operations produce
    backup records; file_create does not (rollback = delete the file).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_file_backups (
            id TEXT PRIMARY KEY,
            execution_result_id TEXT NOT NULL
                REFERENCES execution_results(id),
            execution_request_id TEXT NOT NULL
                REFERENCES execution_requests(id),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            path TEXT NOT NULL,
            original_content TEXT NOT NULL,
            original_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (execution_result_id, path)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_file_backups_result "
        "ON execution_file_backups(execution_result_id)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (11)")


def _apply_v12(conn: sqlite3.Connection) -> None:
    """V12: Add 'rollback' to execution_results mode CHECK (Phase 7C-2).

    Extends the mode column to accept 'rollback' in addition to
    'dry_run' and 'real_run'.  Requires table recreation because
    SQLite cannot ALTER CHECK constraints in-place.
    """
    conn.execute("PRAGMA foreign_keys = OFF")

    # Must also recreate execution_file_backups because SQLite rewrites
    # its FK target when the referenced table is renamed, leaving a
    # dangling reference after the old table is dropped.
    conn.execute(
        "ALTER TABLE execution_file_backups RENAME TO _execution_file_backups_v11"
    )
    conn.execute("ALTER TABLE execution_results RENAME TO _execution_results_v11")

    conn.execute("""
        CREATE TABLE execution_results (
            id TEXT PRIMARY KEY,
            execution_request_id TEXT NOT NULL
                REFERENCES execution_requests(id),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            snapshot_id TEXT NOT NULL REFERENCES execution_snapshots(id),
            snapshot_content_hash TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'dry_run'
                CHECK (mode IN ('dry_run', 'real_run', 'rollback')),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            result_data TEXT NOT NULL DEFAULT '{}',
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (execution_request_id, mode)
        )
    """)

    conn.execute("""
        INSERT INTO execution_results
            (id, execution_request_id, task_id, snapshot_id,
             snapshot_content_hash, mode, status, result_data,
             started_at, completed_at, created_at)
        SELECT id, execution_request_id, task_id, snapshot_id,
               snapshot_content_hash, mode, status, result_data,
               started_at, completed_at, created_at
        FROM _execution_results_v11
    """)

    conn.execute("""
        CREATE TABLE execution_file_backups (
            id TEXT PRIMARY KEY,
            execution_result_id TEXT NOT NULL
                REFERENCES execution_results(id),
            execution_request_id TEXT NOT NULL
                REFERENCES execution_requests(id),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            path TEXT NOT NULL,
            original_content TEXT NOT NULL,
            original_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (execution_result_id, path)
        )
    """)

    conn.execute("""
        INSERT INTO execution_file_backups
            (id, execution_result_id, execution_request_id,
             task_id, path, original_content, original_hash, created_at)
        SELECT id, execution_result_id, execution_request_id,
               task_id, path, original_content, original_hash, created_at
        FROM _execution_file_backups_v11
    """)

    conn.execute("DROP TABLE _execution_file_backups_v11")
    conn.execute("DROP TABLE _execution_results_v11")
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exec_results_task "
        "ON execution_results(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exec_results_request "
        "ON execution_results(execution_request_id)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (12)")


def _apply_v13(conn: sqlite3.Connection) -> None:
    """V13: Token usage logging table (Phase 12-1).

    Records per-role token consumption for each agent execution step,
    enabling usage analysis and future budget enforcement.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_usage_log (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id),
            run_id TEXT REFERENCES agent_runs(id),
            role TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_token_usage_task "
        "ON token_usage_log(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_token_usage_run "
        "ON token_usage_log(run_id)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (13)")


def _apply_v14(conn: sqlite3.Connection) -> None:
    """V14: Skills table (Phase 14-1).

    Supports global and per-agent skills with enable/disable toggle.
    Single-table design: agent_role is NULL for global skills.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            scope_type TEXT NOT NULL DEFAULT 'global' CHECK(scope_type IN ('global', 'agent')),
            agent_role TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(
                (scope_type = 'global' AND agent_role IS NULL) OR
                (scope_type = 'agent' AND agent_role IN ('planner', 'builder', 'qa', 'reviewer'))
            )
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_skills_scope "
        "ON skills(scope_type, agent_role)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (14)")


def _apply_v15(conn: sqlite3.Connection) -> None:
    """V15: Role registry table (Phase 15-1).

    Stores both system (built-in) and custom agent roles.
    System roles (planner, builder, qa, reviewer) are seeded on migration.
    Custom roles can be created for future dynamic team composition.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            department TEXT NOT NULL DEFAULT 'engineering',
            is_system INTEGER NOT NULL DEFAULT 0,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_roles_department ON roles(department)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_roles_is_system ON roles(is_system)"
    )

    # Seed the four system roles
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    system_roles = [
        ("planner", "Planner", "Breaks down tasks into subtasks and sets acceptance criteria", "engineering"),
        ("builder", "Builder", "Produces execution proposals in supervised preparation mode", "engineering"),
        ("qa", "QA", "Validates implementation against acceptance criteria", "engineering"),
        ("reviewer", "Reviewer", "Final review and approve/reject decision", "engineering"),
    ]
    for name, display, desc, dept in system_roles:
        role_id = str(uuid.uuid4())
        conn.execute(
            """INSERT OR IGNORE INTO roles (id, name, display_name, description, department, is_system, is_enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)""",
            (role_id, name, display, desc, dept, now, now),
        )

    conn.execute("INSERT INTO schema_version (version) VALUES (15)")


def _apply_v16(conn: sqlite3.Connection) -> None:
    """V16: Project role participants table (Phase 15-3).

    Binds roles to projects. Each project can independently enable/disable
    which roles participate in its orchestration pipeline.
    Default: all 4 system roles enabled for new/existing projects.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_role_participants (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            role_name TEXT NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (project_id, role_name)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_prp_project ON project_role_participants(project_id)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (16)")


# Ordered list of migrations
_MIGRATIONS = [
    (1, _apply_v1),
    (2, _apply_v2),
    (3, _apply_v3),
    (4, _apply_v4),
    (5, _apply_v5),
    (6, _apply_v6),
    (7, _apply_v7),
    (8, _apply_v8),
    (9, _apply_v9),
    (10, _apply_v10),
    (11, _apply_v11),
    (12, _apply_v12),
    (13, _apply_v13),
    (14, _apply_v14),
    (15, _apply_v15),
    (16, _apply_v16),
]


def _ensure_schema() -> None:
    """Apply any pending migrations using the current DB_PATH connection.

    Safe to call on both file-based and :memory: databases.
    Used by tests to reset and re-run migrations without directory creation.
    """
    conn = get_connection()
    try:
        # Disable FK checks during migrations so DROP TABLE works even when
        # referenced by other tables (e.g. V3 approval_requests rebuild).
        # PRAGMA foreign_keys must be set outside a transaction.
        conn.execute("PRAGMA foreign_keys = OFF")
        current = _get_current_version(conn)
        for version, migrate_fn in _MIGRATIONS:
            if version > current:
                migrate_fn(conn)
                print(f"[database] Applied migration v{version}")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        # Do not close the shared :memory: connection
        if str(DB_PATH) != ":memory:":
            conn.close()


def init_db() -> None:
    """Initialize the SQLite database and apply pending migrations."""
    if str(DB_PATH) != ":memory:":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_schema()
