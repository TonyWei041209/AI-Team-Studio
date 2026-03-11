"""Phase 2 comprehensive acceptance test suite."""
import json
import pathlib
import sqlite3
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:9800/api"
results = []


# ── Test isolation: wipe all data tables before running ────────────
def _wipe_db():
    """Delete all data from all tables to ensure a clean slate.

    Targets only data tables, not ``schema_version``.
    The server holds no in-memory cache, so no restart is needed.
    """
    db_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "ai_team_studio.db"
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in ("log_events", "approval_requests", "agent_runs", "tasks", "projects"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
    finally:
        conn.close()


_wipe_db()


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    rq = urllib.request.Request(url, data=data, method=method)
    rq.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(rq)
        code = resp.getcode()
        text = resp.read().decode()
        return code, json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        code = e.code
        text = e.read().decode()
        try:
            return code, json.loads(text)
        except Exception:
            return code, {"raw": text}


def test(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append((name, status, detail))
    tag = "PASS" if passed else "FAIL"
    extra = ""
    if not passed and detail:
        extra = f" -- {detail}"
    print(f"  [{tag}] {name}{extra}")


print("=" * 70)
print("PHASE 2 ACCEPTANCE TEST SUITE")
print("=" * 70)

# ----------------------------------------------------------------
# 1. HEALTH CHECK
# ----------------------------------------------------------------
print("\n-- 1. Health Check --")
code, data = req("GET", "/health")
test("Health endpoint returns 200", code == 200)
test("Database connected", data.get("database") == "connected")

# ----------------------------------------------------------------
# 2. PROJECTS CRUD
# ----------------------------------------------------------------
print("\n-- 2. Projects CRUD --")

code, proj = req("POST", "/projects", {
    "name": "Test Project",
    "local_repo_path": "/tmp/test-repo",
})
test("Create project -> 201", code == 201)
test("Project has UUID id", len(proj.get("id", "")) == 36)
test("Project name correct", proj.get("name") == "Test Project")
test("Default branch is main", proj.get("default_branch") == "main")
PID = proj.get("id", "")

code, proj2 = req("POST", "/projects", {
    "name": "Second",
    "local_repo_path": "/tmp/second",
    "default_branch": "develop",
    "description": "desc",
})
test("Create second project -> 201", code == 201)
test("Custom branch preserved", proj2.get("default_branch") == "develop")
PID2 = proj2.get("id", "")

code, projs = req("GET", "/projects")
test("List projects -> 200", code == 200)
test("List returns 2 projects", len(projs) == 2)

code, p = req("GET", f"/projects/{PID}")
test("Get project by ID -> 200", code == 200)
test("Returned correct project", p.get("id") == PID)

code, _ = req("GET", "/projects/non-existent-id")
test("Get missing project -> 404", code == 404)

code, updated = req("PATCH", f"/projects/{PID}", {
    "name": "Renamed",
    "description": "updated desc",
})
test("Update project -> 200", code == 200)
test("Name updated", updated.get("name") == "Renamed")
test("Description updated", updated.get("description") == "updated desc")
test("updated_at changed", updated.get("updated_at") != proj.get("updated_at"))

code, _ = req("PATCH", "/projects/bad-id", {"name": "X"})
test("Update missing -> 404", code == 404)

code, same = req("PATCH", f"/projects/{PID}", {})
test("Empty update -> 200 (no-op)", code == 200)

code, _ = req("DELETE", f"/projects/{PID2}")
test("Delete project -> 204", code == 204)

code, _ = req("DELETE", f"/projects/{PID2}")
test("Delete already deleted -> 404", code == 404)

code, projs = req("GET", "/projects")
test("List after delete -> 1 project", len(projs) == 1)

# ----------------------------------------------------------------
# 3. TASKS CRUD + STATE MACHINE
# ----------------------------------------------------------------
print("\n-- 3. Tasks CRUD + State Machine --")

code, task = req("POST", f"/projects/{PID}/tasks", {
    "title": "Implement feature X",
    "priority": "high",
})
test("Create task -> 201", code == 201)
test("Task status is pending", task.get("status") == "pending")
test("Task priority is high", task.get("priority") == "high")
TID = task.get("id", "")

code, _ = req("POST", "/projects/nonexistent/tasks", {"title": "orphan"})
test("Create task bad project -> 404", code == 404)

code, task2 = req("POST", f"/projects/{PID}/tasks", {
    "title": "Plan architecture",
    "assigned_agent_role": "planner",
})
test("Create task with role -> 201", code == 201)
test("Agent role preserved", task2.get("assigned_agent_role") == "planner")
TID2 = task2.get("id", "")

code, tasks = req("GET", f"/projects/{PID}/tasks")
test("List tasks -> 200", code == 200)
test("Two tasks returned", len(tasks) == 2)

code, filtered = req("GET", f"/projects/{PID}/tasks?status=pending")
test("Filter by pending -> 2 tasks", len(filtered) == 2)

code, t = req("GET", f"/tasks/{TID}")
test("Get task by ID -> 200", code == 200)

code, _ = req("GET", "/tasks/no-such-task")
test("Get missing task -> 404", code == 404)

# Valid transitions
code, t = req("PATCH", f"/tasks/{TID}/status", {"status": "planning"})
test("pending -> planning OK", code == 200 and t.get("status") == "planning")

code, t = req("PATCH", f"/tasks/{TID}/status", {"status": "in_progress"})
test("planning -> in_progress OK", code == 200 and t.get("status") == "in_progress")

code, t = req("PATCH", f"/tasks/{TID}/status", {"status": "reviewing"})
test("in_progress -> reviewing OK", code == 200 and t.get("status") == "reviewing")

code, t = req("PATCH", f"/tasks/{TID}/status", {"status": "done"})
test("reviewing -> done OK", code == 200 and t.get("status") == "done")

# Invalid transitions
code, err = req("PATCH", f"/tasks/{TID}/status", {"status": "pending"})
test("done -> pending BLOCKED", code == 400)

# Failed -> pending retry
code, t = req("PATCH", f"/tasks/{TID2}/status", {"status": "in_progress"})
test("pending -> in_progress OK", code == 200)
code, t = req("PATCH", f"/tasks/{TID2}/status", {"status": "failed"})
test("in_progress -> failed OK", code == 200)
code, t = req("PATCH", f"/tasks/{TID2}/status", {"status": "pending"})
test("failed -> pending (retry) OK", code == 200 and t.get("status") == "pending")

# Bad enum value
code, err = req("PATCH", f"/tasks/{TID2}/status", {"status": "cancelled"})
test("Invalid status enum -> 422", code == 422)

code, err = req("PATCH", f"/tasks/{TID2}/status", {})
test("Missing status field -> 422", code == 422)

# ----------------------------------------------------------------
# 4. AGENT RUNS
# ----------------------------------------------------------------
print("\n-- 4. Agent Runs --")

code, run = req("POST", f"/tasks/{TID}/runs", {
    "role": "builder",
    "model_provider": "anthropic",
    "model_name": "claude-sonnet-4-20250514",
    "input_summary": "build feature",
})
test("Create run -> 201", code == 201)
test("Run status is pending", run.get("status") == "pending")
test("Run role is builder", run.get("role") == "builder")
RID = run.get("id", "")

code, _ = req("POST", "/tasks/missing/runs", {"role": "qa"})
test("Run for missing task -> 404", code == 404)

code, _ = req("POST", f"/tasks/{TID}/runs", {"role": "wizard"})
test("Invalid role enum -> 422", code == 422)

code, runs = req("GET", f"/tasks/{TID}/runs")
test("List runs -> 200", code == 200)
test("1 run returned", len(runs) == 1)

code, r = req("GET", f"/runs/{RID}")
test("Get run -> 200", code == 200)

code, _ = req("GET", "/runs/no-such-run")
test("Get missing run -> 404", code == 404)

code, r = req("PATCH", f"/runs/{RID}", {"status": "running"})
test("Update run -> running", code == 200 and r.get("status") == "running")
test("started_at set", r.get("started_at") is not None)

code, r = req("PATCH", f"/runs/{RID}", {
    "status": "completed",
    "output_summary": "feature built",
})
test("Update run -> completed", code == 200 and r.get("status") == "completed")
test("ended_at set", r.get("ended_at") is not None)
test("output_summary set", r.get("output_summary") == "feature built")

code, r = req("PATCH", f"/runs/{RID}", {})
test("Empty run update -> 200", code == 200)

# ----------------------------------------------------------------
# 5. APPROVAL REQUESTS
# ----------------------------------------------------------------
print("\n-- 5. Approval Requests --")

code, appr = req("POST", f"/tasks/{TID}/approvals", {
    "action_type": "delete_file",
    "action_payload": '{"path": "/tmp/test"}',
})
test("Create approval -> 201", code == 201)
test("Approval status is pending", appr.get("status") == "pending")
AID = appr.get("id", "")

code, _ = req("POST", "/tasks/missing/approvals", {"action_type": "shell_exec"})
test("Approval for missing task -> 404", code == 404)

code, appr2 = req("POST", f"/tasks/{TID}/approvals", {
    "run_id": RID,
    "action_type": "git_force",
})
test("Approval with run_id -> 201", code == 201)
AID2 = appr2.get("id", "")

code, apprs = req("GET", f"/tasks/{TID}/approvals")
test("List approvals -> 200", code == 200)
test("2 approvals returned", len(apprs) == 2)

code, pending = req("GET", "/approvals/pending")
test("List pending -> 200", code == 200)
test("2 pending approvals", len(pending) == 2)

code, resolved = req("PATCH", f"/approvals/{AID}", {
    "status": "approved",
    "reviewer_comment": "LGTM",
})
test("Resolve -> approved", code == 200 and resolved.get("status") == "approved")
test("resolved_at set", resolved.get("resolved_at") is not None)
test("Comment saved", resolved.get("reviewer_comment") == "LGTM")

code, _ = req("PATCH", f"/approvals/{AID}", {"status": "rejected"})
test("Double resolve blocked -> 400", code == 400)

code, rej = req("PATCH", f"/approvals/{AID2}", {
    "status": "rejected",
    "reviewer_comment": "too risky",
})
test("Resolve -> rejected", code == 200 and rej.get("status") == "rejected")

code, _ = req("PATCH", f"/approvals/{AID}", {"status": "pending"})
test("Set back to pending -> 400", code == 400)

code, _ = req("PATCH", "/approvals/no-such-id", {"status": "approved"})
test("Resolve missing -> 404", code == 404)

code, pending = req("GET", "/approvals/pending")
test("No more pending", len(pending) == 0)

# ----------------------------------------------------------------
# 6. LOG EVENTS
# ----------------------------------------------------------------
print("\n-- 6. Log Events --")

code, log = req("POST", "/logs", {
    "task_id": TID,
    "message": "Build started",
    "level": "info",
    "source": "builder",
})
test("Create log -> 201", code == 201)
test("Log level info", log.get("level") == "info")

code, log2 = req("POST", "/logs", {
    "task_id": TID,
    "message": "Compile error",
    "level": "error",
    "source": "builder",
})
test("Create error log -> 201", code == 201)

code, log3 = req("POST", "/logs", {
    "task_id": TID,
    "message": "Details",
    "level": "debug",
    "payload": '{"key": "val"}',
})
test("Create log with payload -> 201", code == 201)

code, syslog = req("POST", "/logs", {"message": "System boot"})
test("Create system log (no task) -> 201", code == 201)
test("task_id is null", syslog.get("task_id") is None)

code, logs = req("GET", f"/tasks/{TID}/logs")
test("List logs for task -> 200", code == 200)
test("3 task logs returned", len(logs) == 3)

code, errs = req("GET", f"/tasks/{TID}/logs?level=error")
test("Filter logs by error -> 1", len(errs) == 1)

code, limited = req("GET", f"/tasks/{TID}/logs?limit=1")
test("Limit logs to 1", len(limited) == 1)

code, recent = req("GET", "/logs/recent")
test("Recent logs -> 200", code == 200)
test("4 total logs", len(recent) == 4)

code, recent_err = req("GET", "/logs/recent?level=error")
test("Recent errors -> 1", len(recent_err) == 1)

code, recent_lim = req("GET", "/logs/recent?limit=2")
test("Recent limit 2", len(recent_lim) == 2)

# ----------------------------------------------------------------
# 7. EDGE CASES
# ----------------------------------------------------------------
print("\n-- 7. Edge Cases --")

code, _ = req("POST", f"/projects/{PID}/tasks", {
    "title": "x",
    "priority": "ultra",
})
test("Invalid priority enum -> 422", code == 422)

code, _ = req("POST", f"/projects/{PID}/tasks", {"description": "no title"})
test("Missing title -> 422", code == 422)

code, _ = req("POST", "/projects", {"name": "half"})
test("Missing local_repo_path -> 422", code == 422)

code, _ = req("PATCH", "/tasks/fake-task/status", {"status": "done"})
test("Transition missing task -> 404", code == 404)

# Timestamp format check
code, newt = req("POST", f"/projects/{PID}/tasks", {"title": "Timestamp check"})
ts = newt.get("created_at", "")
test("Timestamp contains T separator", "T" in ts, f"got: {ts}")

# ----------------------------------------------------------------
# SUMMARY
# ----------------------------------------------------------------
print("\n" + "=" * 70)
passes = sum(1 for _, s, _ in results if s == "PASS")
fails = sum(1 for _, s, _ in results if s == "FAIL")
total = len(results)
print(f"TOTAL: {total}  |  PASS: {passes}  |  FAIL: {fails}")
print("=" * 70)

if fails > 0:
    print("\nFAILED TESTS:")
    for name, status, detail in results:
        if status == "FAIL":
            print(f"  X {name}" + (f" -- {detail}" if detail else ""))

sys.exit(0 if fails == 0 else 1)
