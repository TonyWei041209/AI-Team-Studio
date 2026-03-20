"""Phase 18-1: Workspace search endpoint validation."""
import os, sys, uuid, sqlite3

# Ensure services/runtime is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "runtime"))

os.environ.setdefault("ATS_DB_PATH", ":memory:")

from database import get_connection, init_db

# ── helpers ──

def _setup_db():
    """Initialize DB and seed test data."""
    conn = get_connection()
    init_db()

    # Create a project
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects (id, name, local_repo_path, default_branch, description) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, "Alpha Project", "/tmp/alpha", "main", "A test project for searching"),
    )

    # Create tasks
    tid1 = str(uuid.uuid4())
    tid2 = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO tasks (id, project_id, title, description, status, priority) VALUES (?, ?, ?, ?, ?, ?)",
        (tid1, pid, "Fix login bug", "The login page crashes on submit", "failed", "high"),
    )
    conn.execute(
        "INSERT INTO tasks (id, project_id, title, description, status, priority) VALUES (?, ?, ?, ?, ?, ?)",
        (tid2, pid, "Add dashboard widget", "New widget for metrics", "done", "medium"),
    )

    # Create an approval
    aid = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO agent_runs (id, task_id, role, status) VALUES (?, ?, ?, ?)",
        (run_id, tid1, "builder", "completed"),
    )
    conn.execute(
        "INSERT INTO approval_requests (id, task_id, run_id, action_type, action_payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (aid, tid1, run_id, "file_write", '{"path":"test.py"}', "pending"),
    )

    conn.commit()
    conn.close()
    return pid, tid1, tid2, aid


def _search(q, limit=10):
    """Simulate the search query logic."""
    conn = get_connection()
    try:
        if not q.strip():
            return {"query": q, "projects": [], "tasks": [], "approvals": []}
        pattern = f"%{q.strip()}%"
        projects = conn.execute(
            "SELECT id, name, description, local_repo_path, created_at "
            "FROM projects WHERE name LIKE ? OR description LIKE ? OR local_repo_path LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (pattern, pattern, pattern, limit),
        ).fetchall()
        tasks = conn.execute(
            "SELECT t.id, t.title, t.description, t.status, t.priority, "
            "t.project_id, p.name as project_name, t.created_at, t.updated_at "
            "FROM tasks t JOIN projects p ON t.project_id = p.id "
            "WHERE t.title LIKE ? OR t.description LIKE ? ORDER BY t.updated_at DESC LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()
        approvals = conn.execute(
            "SELECT ar.id, ar.task_id, ar.action_type, ar.status, ar.created_at, "
            "t.title as task_title, t.project_id, p.name as project_name "
            "FROM approval_requests ar JOIN tasks t ON ar.task_id = t.id "
            "JOIN projects p ON t.project_id = p.id "
            "WHERE t.title LIKE ? OR ar.action_type LIKE ? ORDER BY ar.created_at DESC LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()
        return {
            "query": q,
            "projects": [dict(r) for r in projects],
            "tasks": [dict(r) for r in tasks],
            "approvals": [dict(r) for r in approvals],
        }
    finally:
        conn.close()


# ── tests ──

def test_empty_query():
    result = _search("")
    assert result["projects"] == []
    assert result["tasks"] == []
    assert result["approvals"] == []
    print("  [PASS] empty query returns empty results")

def test_search_project_by_name():
    result = _search("Alpha")
    assert len(result["projects"]) == 1
    assert result["projects"][0]["name"] == "Alpha Project"
    print("  [PASS] search project by name")

def test_search_project_by_description():
    result = _search("searching")
    assert len(result["projects"]) == 1
    print("  [PASS] search project by description")

def test_search_task_by_title():
    result = _search("login")
    assert len(result["tasks"]) >= 1
    assert any("login" in t["title"].lower() for t in result["tasks"])
    print("  [PASS] search task by title")

def test_search_task_by_description():
    result = _search("crashes")
    assert len(result["tasks"]) >= 1
    print("  [PASS] search task by description")

def test_search_approval_by_task_title():
    result = _search("login")
    assert len(result["approvals"]) >= 1
    print("  [PASS] search approval by linked task title")

def test_search_approval_by_action_type():
    result = _search("file_write")
    assert len(result["approvals"]) >= 1
    print("  [PASS] search approval by action_type")

def test_search_no_results():
    result = _search("zzz_nonexistent_zzz")
    assert len(result["projects"]) == 0
    assert len(result["tasks"]) == 0
    assert len(result["approvals"]) == 0
    print("  [PASS] no results for non-matching query")

def test_search_limit():
    result = _search("Alpha", limit=1)
    assert len(result["projects"]) <= 1
    print("  [PASS] limit parameter respected")

def test_result_structure():
    result = _search("dashboard")
    assert "query" in result
    assert "projects" in result
    assert "tasks" in result
    assert "approvals" in result
    # Check task structure
    for t in result["tasks"]:
        assert "id" in t
        assert "title" in t
        assert "status" in t
        assert "project_id" in t
        assert "project_name" in t
    print("  [PASS] result structure validation")


if __name__ == "__main__":
    print("Phase 18-1: Workspace Search Validation")
    print("=" * 50)
    _setup_db()

    passed = 0
    failed = 0
    for fn in [
        test_empty_query,
        test_search_project_by_name,
        test_search_project_by_description,
        test_search_task_by_title,
        test_search_task_by_description,
        test_search_approval_by_task_title,
        test_search_approval_by_action_type,
        test_search_no_results,
        test_search_limit,
        test_result_structure,
    ]:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            failed += 1

    print(f"\n  {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
    print("  ALL PASS")
