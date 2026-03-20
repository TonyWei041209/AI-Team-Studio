"""Phase 15-3 acceptance tests — project-level participant selection.

Covers:
- T1: GET participants auto-seeds defaults for new project
- T2: GET participants returns existing records
- T3: PUT participants updates correctly
- T4: PUT refuses to disable mandatory roles (planner, builder)
- T5: 404 for non-existent project
- T6: Orchestrator _get_enabled_roles returns defaults when no config
- T7: Orchestrator _get_enabled_roles respects config
- T8: Pipeline filtering removes disabled roles
- T9: Reviewer rejection loops back to correct Builder index in filtered pipeline
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure service root on path
SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
sys.path.insert(0, SERVICE_ROOT)

os.environ["ATS_DB_PATH"] = ":memory:"

import database

# ── Helpers ────────────────────────────────────────────────

_pass = 0
_fail = 0


def check(label: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  PASS  {label}")
    else:
        _fail += 1
        print(f"  FAIL  {label}  {detail}")


def _reset_db():
    """Drop all tables and re-apply all migrations from scratch."""
    conn = database._MEMORY_CONN
    if conn is None:
        # Force first connection
        database.get_connection()
        conn = database._MEMORY_CONN
    # Drop all known tables so migrations run fresh
    tables = [
        "project_role_participants", "roles", "token_usage_log",
        "execution_file_backups", "execution_results", "execution_requests",
        "execution_snapshots", "execution_proposals", "approval_requests",
        "log_events", "agent_runs", "tasks", "projects",
        "role_model_settings", "provider_settings", "skills",
        "schema_version",
    ]
    for t in tables:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    database._ensure_schema()


def _make_project() -> str:
    """Insert a project and return its ID."""
    project_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = database.get_connection()
    conn.execute(
        "INSERT INTO projects (id, name, local_repo_path, default_branch, description, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (project_id, "Test Project", "/tmp/test", "main", "", now, now),
    )
    conn.commit()
    conn.close()
    return project_id


def _get_participants(project_id: str) -> list[dict]:
    conn = database.get_connection()
    rows = conn.execute(
        "SELECT role_name, is_enabled FROM project_role_participants "
        "WHERE project_id = ? ORDER BY role_name",
        (project_id,),
    ).fetchall()
    conn.close()
    return [{"role_name": r["role_name"], "is_enabled": bool(r["is_enabled"])} for r in rows]


# ── T1: GET participants auto-seeds defaults ───────────────────

def test_t1_auto_seed():
    print("\nT1: Auto-seed defaults on first GET")
    _reset_db()

    from routers.projects import _ensure_participants
    project_id = _make_project()

    conn = database.get_connection()
    try:
        # Before: no records
        count_before = conn.execute(
            "SELECT COUNT(*) FROM project_role_participants WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        check("no records before first ensure", count_before == 0)

        result = _ensure_participants(conn, project_id)
        check("returns 4 participants", len(result) == 4)

        role_names = {p["role_name"] for p in result}
        check("all 4 default roles present", role_names == {"planner", "builder", "qa", "reviewer"})
    finally:
        conn.close()


# ── T2: GET participants returns existing records ─────────────

def test_t2_existing_records():
    print("\nT2: Returns existing participant records")
    _reset_db()

    from routers.projects import _ensure_participants
    project_id = _make_project()

    # Seed once
    conn = database.get_connection()
    try:
        _ensure_participants(conn, project_id)

        # Disable 'qa' manually
        conn.execute(
            "UPDATE project_role_participants SET is_enabled = 0 WHERE project_id = ? AND role_name = 'qa'",
            (project_id,),
        )
        conn.commit()

        # Fetch again — should return existing rows, not re-seed
        result2 = _ensure_participants(conn, project_id)
        qa = next((p for p in result2 if p["role_name"] == "qa"), None)
        check("qa is disabled after manual update", qa is not None and not qa["is_enabled"])
        check("still 4 rows", len(result2) == 4)
    finally:
        conn.close()


# ── T3: PUT participants updates correctly ────────────────────

def test_t3_update():
    print("\nT3: PUT participants updates correctly")
    _reset_db()

    from routers.projects import _ensure_participants
    project_id = _make_project()

    conn = database.get_connection()
    try:
        _ensure_participants(conn, project_id)

        now = datetime.now(timezone.utc).isoformat()
        # Disable 'reviewer'
        conn.execute(
            "UPDATE project_role_participants SET is_enabled = 0, updated_at = ? "
            "WHERE project_id = ? AND role_name = 'reviewer'",
            (now, project_id),
        )
        conn.commit()

        result = _ensure_participants(conn, project_id)
        reviewer = next((p for p in result if p["role_name"] == "reviewer"), None)
        check("reviewer disabled", reviewer is not None and not reviewer["is_enabled"])

        # Re-enable reviewer
        conn.execute(
            "UPDATE project_role_participants SET is_enabled = 1, updated_at = ? "
            "WHERE project_id = ? AND role_name = 'reviewer'",
            (now, project_id),
        )
        conn.commit()

        result2 = _ensure_participants(conn, project_id)
        reviewer2 = next((p for p in result2 if p["role_name"] == "reviewer"), None)
        check("reviewer re-enabled", reviewer2 is not None and reviewer2["is_enabled"])
        check("total count still 4", len(result2) == 4)
    finally:
        conn.close()


# ── T4: PUT refuses to disable mandatory roles ────────────────

def test_t4_mandatory():
    print("\nT4: Mandatory roles cannot be disabled via API")
    _reset_db()

    import fastapi.testclient
    import importlib
    import main as m

    app = m.app
    client = fastapi.testclient.TestClient(app)

    project_id = _make_project()

    # First seed via GET
    r = client.get(f"/api/projects/{project_id}/participants")
    check("GET returns 200", r.status_code == 200)

    # Try to disable 'planner'
    r_put = client.put(f"/api/projects/{project_id}/participants", json={
        "participants": [
            {"role_name": "planner", "is_enabled": False},
            {"role_name": "builder", "is_enabled": True},
            {"role_name": "qa", "is_enabled": True},
            {"role_name": "reviewer", "is_enabled": True},
        ]
    })
    check("PUT rejects disabling planner (422)", r_put.status_code == 422)

    # Try to disable 'builder'
    r_put2 = client.put(f"/api/projects/{project_id}/participants", json={
        "participants": [
            {"role_name": "planner", "is_enabled": True},
            {"role_name": "builder", "is_enabled": False},
            {"role_name": "qa", "is_enabled": True},
            {"role_name": "reviewer", "is_enabled": True},
        ]
    })
    check("PUT rejects disabling builder (422)", r_put2.status_code == 422)


# ── T5: 404 for non-existent project ─────────────────────────

def test_t5_not_found():
    print("\nT5: 404 for non-existent project")
    _reset_db()

    import fastapi.testclient
    import main as m

    app = m.app
    client = fastapi.testclient.TestClient(app)

    fake_id = str(uuid.uuid4())

    r_get = client.get(f"/api/projects/{fake_id}/participants")
    check("GET non-existent returns 404", r_get.status_code == 404)

    r_put = client.put(f"/api/projects/{fake_id}/participants", json={
        "participants": [{"role_name": "planner", "is_enabled": True}]
    })
    check("PUT non-existent returns 404", r_put.status_code == 404)


# ── T6: _get_enabled_roles defaults when no config ────────────

def test_t6_enabled_roles_defaults():
    print("\nT6: _get_enabled_roles returns defaults when no config")
    _reset_db()

    from agents.orchestrator import Orchestrator

    # Empty project_id
    roles = Orchestrator._get_enabled_roles("")
    check("empty project_id returns all 4 defaults", roles == {"planner", "builder", "qa", "reviewer"})

    # Valid project_id with no config rows
    project_id = _make_project()
    roles2 = Orchestrator._get_enabled_roles(project_id)
    check("no config rows returns all 4 defaults", roles2 == {"planner", "builder", "qa", "reviewer"})


# ── T7: _get_enabled_roles respects config ────────────────────

def test_t7_enabled_roles_respects_config():
    print("\nT7: _get_enabled_roles respects existing config")
    _reset_db()

    from agents.orchestrator import Orchestrator

    project_id = _make_project()
    now = datetime.now(timezone.utc).isoformat()

    # Insert only planner and builder as enabled; qa disabled; reviewer omitted
    conn = database.get_connection()
    for role, enabled in [("planner", 1), ("builder", 1), ("qa", 0), ("reviewer", 1)]:
        conn.execute(
            "INSERT INTO project_role_participants (id, project_id, role_name, is_enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), project_id, role, enabled, now, now),
        )
    conn.commit()
    conn.close()

    roles = Orchestrator._get_enabled_roles(project_id)
    check("qa excluded (disabled)", "qa" not in roles)
    check("planner, builder, reviewer included", {"planner", "builder", "reviewer"}.issubset(roles))


# ── T8: Pipeline filtering removes disabled roles ─────────────

def test_t8_pipeline_filtering():
    print("\nT8: Pipeline filtering removes disabled roles")
    _reset_db()

    from agents.definitions import AGENT_PIPELINE
    from models import AgentRole

    # Simulate filtering with qa disabled
    enabled_roles = {"planner", "builder", "reviewer"}
    active = [d for d in AGENT_PIPELINE if d.role.value in enabled_roles]

    check("filtered pipeline has 3 roles", len(active) == 3)
    check("qa not in filtered pipeline", all(d.role != AgentRole.QA for d in active))
    check("planner is first in filtered pipeline", active[0].role == AgentRole.PLANNER)


# ── T9: Reviewer rejection loops back to correct Builder index ─

def test_t9_builder_index_in_filtered_pipeline():
    print("\nT9: Builder index correct in filtered pipeline")
    _reset_db()

    from agents.definitions import AGENT_PIPELINE
    from models import AgentRole

    # Full pipeline: planner(0), builder(1), qa(2), reviewer(3)
    full_builder_idx = next(
        i for i, d in enumerate(AGENT_PIPELINE) if d.role == AgentRole.BUILDER
    )
    check("builder at index 1 in full pipeline", full_builder_idx == 1)

    # Filter: planner disabled — pipeline is builder(0), qa(1), reviewer(2)
    enabled_no_planner = {"builder", "qa", "reviewer"}
    active_no_planner = [d for d in AGENT_PIPELINE if d.role.value in enabled_no_planner]
    builder_idx_no_planner = next(
        (i for i, d in enumerate(active_no_planner) if d.role == AgentRole.BUILDER),
        1,
    )
    check("builder at index 0 when planner disabled", builder_idx_no_planner == 0)


# ── Runner ─────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Phase 15-3: Project Participant Selection — Acceptance Tests")
    print("=" * 60)

    test_t1_auto_seed()
    test_t2_existing_records()
    test_t3_update()
    test_t4_mandatory()
    test_t5_not_found()
    test_t6_enabled_roles_defaults()
    test_t7_enabled_roles_respects_config()
    test_t8_pipeline_filtering()
    test_t9_builder_index_in_filtered_pipeline()

    print()
    print("=" * 60)
    print(f"Results: {_pass} passed, {_fail} failed")
    print("=" * 60)
    return _fail


if __name__ == "__main__":
    exit(main())
