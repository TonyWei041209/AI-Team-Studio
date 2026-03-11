"""Phase 5 API-level workflow acceptance tests.

Covers the full API chain that the frontend polls and displays:
  Create project → Create task → Tool execution triggers approval
  → Approval appears in pending list → Approve → Approval disappears
  → Logs record the full audit trail

NOTE: These are *API-level* end-to-end tests exercising the same HTTP
endpoints the React frontend calls.  They do NOT test browser rendering.
"""
import json
import sqlite3
import pathlib
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:9800/api"
results = []

REPO_PATH = "D:/AI_Team_Studio"


# ── Helpers ──────────────────────────────────────────────────────


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
    extra = ""
    if not passed and detail:
        extra = f" -- {detail}"
    print(f"  [{status}] {name}{extra}")


def _wipe_test_data():
    """Remove rows created by this test suite (best-effort)."""
    db_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "ai_team_studio.db"
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        # Only delete our own test data to avoid disrupting other tests
        conn.execute("DELETE FROM log_events WHERE task_id IN (SELECT id FROM tasks WHERE title LIKE 'Phase5-WF-%')")
        conn.execute("DELETE FROM approval_requests WHERE task_id IN (SELECT id FROM tasks WHERE title LIKE 'Phase5-WF-%')")
        # Also clean up approvals that have no task_id but were created by our tool calls
        # (tool execute creates approvals with task_id from the request body)
        conn.execute("DELETE FROM agent_runs WHERE task_id IN (SELECT id FROM tasks WHERE title LIKE 'Phase5-WF-%')")
        conn.execute("DELETE FROM tasks WHERE title LIKE 'Phase5-WF-%'")
        conn.execute("DELETE FROM projects WHERE name LIKE 'Phase5-WF-%'")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
    finally:
        conn.close()


_wipe_test_data()


# ══════════════════════════════════════════════════════════════════
print("=" * 70)
print("PHASE 5 API WORKFLOW ACCEPTANCE TESTS")
print("  (API-level end-to-end, not browser E2E)")
print("=" * 70)


# ── GROUP 1: Health & Foundation ─────────────────────────────────
print("\n-- 1. Health & Foundation --")

code, health = req("GET", "/health")
test("Health endpoint reachable", code == 200)
test("Health has version field", "version" in health)

# ── GROUP 2: Create Project ──────────────────────────────────────
print("\n-- 2. Project Creation --")

code, proj = req("POST", "/projects", {
    "name": "Phase5-WF-TestProject",
    "local_repo_path": REPO_PATH,
    "description": "Workflow test project",
})
test("Create project returns 2xx", code in (200, 201), f"code={code}")
test("Project has id", "id" in proj)
PID = proj.get("id", "")

# Verify via list endpoint (same as frontend polling)
code, projects = req("GET", "/projects")
test("GET /projects returns 200", code == 200)
found = any(p["id"] == PID for p in projects)
test("New project appears in list", found)


# ── GROUP 3: Create Task ─────────────────────────────────────────
print("\n-- 3. Task Creation --")

code, task = req("POST", f"/projects/{PID}/tasks", {
    "title": "Phase5-WF-TestTask",
    "description": "Workflow test task",
    "priority": "high",
})
test("Create task returns 2xx", code in (200, 201), f"code={code}")
test("Task has id", "id" in task)
TID = task.get("id", "")

# Verify via list endpoint (same as frontend TaskBoard polling)
code, tasks = req("GET", f"/projects/{PID}/tasks")
test("GET /projects/{pid}/tasks returns 200", code == 200)
found = any(t["id"] == TID for t in tasks)
test("New task appears in task list", found)
test("Task status is pending", task.get("status") == "pending")
test("Task priority is high", task.get("priority") == "high")


# ── GROUP 4: Tool Execution → Approval Trigger ───────────────────
print("\n-- 4. Tool Execution → Approval Trigger --")

# Use shell tool with 'git clean' (whitelisted base cmd, HIGH risk pattern)
code, tool_resp = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "git clean -fd"},
    "project_id": PID,
    "task_id": TID,
})
test("Tool execute returns 200", code == 200, f"code={code}")
test("Response indicates blocked", tool_resp.get("blocked") is True)
test("Risk level is high", tool_resp.get("risk_level") == "high")
test("Approval ID returned", tool_resp.get("approval_id") is not None)

APPROVAL_ID = tool_resp.get("approval_id", "")


# ── GROUP 5: Approval Visibility (frontend polling endpoint) ─────
print("\n-- 5. Approval Visibility --")

code, pending = req("GET", "/approvals/pending")
test("GET /approvals/pending returns 200", code == 200)
test("Pending list is non-empty", len(pending) > 0)

# Find our specific approval
our_approval = None
for a in pending:
    if a["id"] == APPROVAL_ID:
        our_approval = a
        break

test("Our approval appears in pending list", our_approval is not None)
if our_approval:
    test("Approval status is pending", our_approval["status"] == "pending")
    test("Approval action_type contains tool name",
         "shell" in our_approval.get("action_type", ""))
    # Verify action_payload is valid JSON with structured fields
    try:
        payload = json.loads(our_approval.get("action_payload", "{}"))
        test("Payload has tool_name field", "tool_name" in payload)
        test("Payload has risk_level field", "risk_level" in payload)
        test("Payload tool_name matches", payload.get("tool_name") == "shell")
    except json.JSONDecodeError:
        test("Payload is valid JSON", False, "JSONDecodeError")

# Simulate frontend polling: call pending endpoint multiple times
# and verify consistency
code2, pending2 = req("GET", "/approvals/pending")
ids1 = {a["id"] for a in pending}
ids2 = {a["id"] for a in pending2}
test("Consecutive pending calls are consistent", ids1 == ids2)


# ── GROUP 6: Approve the Request ─────────────────────────────────
print("\n-- 6. Approve Request --")

code, resolved = req("PATCH", f"/approvals/{APPROVAL_ID}", {
    "status": "approved",
    "reviewer_comment": "Approved by workflow test",
})
test("Approve returns 200", code == 200, f"code={code}")
test("Resolved status is approved", resolved.get("status") == "approved")
test("Resolved_at is set", resolved.get("resolved_at") is not None)
test("Reviewer comment preserved",
     resolved.get("reviewer_comment") == "Approved by workflow test")


# ── GROUP 7: Approval Disappears from Pending ────────────────────
print("\n-- 7. Approval No Longer Pending --")

code, pending_after = req("GET", "/approvals/pending")
test("GET /approvals/pending returns 200 after resolve", code == 200)
still_pending = any(a["id"] == APPROVAL_ID for a in pending_after)
test("Approved item no longer in pending list", not still_pending)


# ── GROUP 8: Audit Trail in Logs ─────────────────────────────────
print("\n-- 8. Audit Trail in Logs --")

code, logs = req("GET", "/logs/recent?limit=200")
test("GET /logs/recent returns 200", code == 200)
test("Logs list is non-empty", len(logs) > 0)

# Find tool audit events related to our tool call.
# Use structured payload fields (not fragile message text).
audit_events = []
for log in logs:
    if log.get("source") != "tool_audit":
        continue
    try:
        payload = json.loads(log.get("payload", "{}"))
    except (json.JSONDecodeError, TypeError):
        continue
    # Match by tool_name in payload
    if payload.get("tool_name") == "shell":
        audit_events.append(payload.get("event", ""))

test("Found tool.execute.request event",
     "tool.execute.request" in audit_events)
test("Found tool.execute.risk_classified event",
     "tool.execute.risk_classified" in audit_events)
test("Found tool.execute.blocked event",
     "tool.execute.blocked" in audit_events)

# Verify log level filter works (same as frontend LogsPanel filter)
code, warn_logs = req("GET", "/logs/recent?level=warn&limit=50")
test("Level filter returns 200", code == 200)
if warn_logs:
    all_warn = all(log["level"] in ("warn", "error") for log in warn_logs)
    test("Filtered logs are all warn or error", all_warn)
else:
    test("Filtered logs are all warn or error", True, "empty list is trivially correct")


# ── GROUP 9: Rejection Workflow ──────────────────────────────────
print("\n-- 9. Rejection Workflow --")

# Trigger another approval via write_file to .env (HIGH risk)
code, tool_resp2 = req("POST", "/tools/execute", {
    "tool_name": "write_file",
    "params": {"path": "/tmp/.env", "content": "SECRET=test"},
    "project_id": PID,
    "task_id": TID,
})
test("Second tool execute returns 200", code == 200, f"code={code}")
test("Second tool blocked", tool_resp2.get("blocked") is True)
APPROVAL_ID_2 = tool_resp2.get("approval_id", "")

# Reject it
if APPROVAL_ID_2:
    code, rejected = req("PATCH", f"/approvals/{APPROVAL_ID_2}", {
        "status": "rejected",
        "reviewer_comment": "Rejected by workflow test",
    })
    test("Reject returns 200", code == 200, f"code={code}")
    test("Rejected status is rejected", rejected.get("status") == "rejected")

    # Verify it's gone from pending
    code, pending_after2 = req("GET", "/approvals/pending")
    still_there = any(a["id"] == APPROVAL_ID_2 for a in pending_after2)
    test("Rejected item no longer in pending list", not still_there)
else:
    test("Second tool produced approval_id", False, "No approval_id returned")
    test("Reject returns 200", False, "skipped")
    test("Rejected status is rejected", False, "skipped")
    test("Rejected item no longer in pending list", False, "skipped")


# ══════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
passed = sum(1 for _, s, _ in results if s == "PASS")
failed = sum(1 for _, s, _ in results if s == "FAIL")
total = len(results)
print(f"PHASE 5 API WORKFLOW: {passed}/{total} PASSED, {failed} FAILED")

if failed > 0:
    print("\nFailed tests:")
    for name, status, detail in results:
        if status == "FAIL":
            print(f"  X {name}" + (f" -- {detail}" if detail else ""))

print("=" * 70)
sys.exit(1 if failed > 0 else 0)
