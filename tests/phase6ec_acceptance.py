#!/usr/bin/env python3
"""Phase 6E-C acceptance tests -- Guarded Execution Request (Step 1).

Sections
--------
1.  Database V8 migration verification (execution_requests table exists)
2.  Snapshot not found -> ValueError
3.  Snapshot not frozen -> ValueError
4.  Normal creation of execution request
5.  Idempotent creation returns same request
6.  snapshot_content_hash matches snapshot
7.  Default status is 'requested'
8.  updated_at equals created_at on creation
9.  Audit log exists (execution_request:create)
10. Audit chain traceability

Run
---
  cd services/runtime
  set RUNTIME_DB=<temp_db_path>
  set RUNTIME_PORT=<port>
  python -m uvicorn main:app --port %RUNTIME_PORT%

  set TEST_API_BASE=http://127.0.0.1:<port>/api
  python ../../tests/phase6ec_acceptance.py
"""

import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any

# ── Add runtime to sys.path for unit-test imports ─────────────
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")

# Ensure RUNTIME_DB is set so that database module connects to the same
# DB as the running test server.
if not os.environ.get("RUNTIME_DB"):
    _test_db = os.environ.get("TEST_RUNTIME_DB", "")
    if _test_db:
        os.environ["RUNTIME_DB"] = _test_db

PASS = 0
FAIL = 0

# Module-level state
_project_id: str = ""
_task_id: str = ""
_proposal_id: str = ""
_approval_id: str = ""
_snapshot_id: str = ""
_snapshot_content_hash: str = ""
_request_id: str = ""


def _req(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def GET(path: str):
    return _req("GET", path)


def POST(path: str, body: Any = None):
    return _req("POST", path, body)


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {name}"
        if detail:
            msg += f"  ({detail})"
        print(msg)


# ── Section 1: V8 migration ──────────────────────────────────
def section_1_migration():
    print("\n=== Section 1: Database V8 migration verification ===")
    status, data = GET("/projects")
    check("Server is running (GET /projects returns 2xx)", status in (200, 201))

    # Create test infrastructure and a frozen snapshot
    global _project_id, _task_id, _proposal_id, _approval_id
    global _snapshot_id, _snapshot_content_hash

    status, proj = POST("/projects", {"name": f"6EC-test-{os.getpid()}", "local_repo_path": "/tmp/6ec"})
    check("Create project -> 200/201", status in (200, 201))
    _project_id = proj.get("id", "")

    status, task = POST(f"/projects/{_project_id}/tasks", {"title": "6EC request test"})
    check("Create task -> 200/201", status in (200, 201))
    _task_id = task.get("id", "")

    # Orchestrate to generate a proposal
    POST(f"/tasks/{_task_id}/orchestrate", {"roles": ["builder"]})
    _, props = GET(f"/tasks/{_task_id}/proposals")
    proposals = props.get("proposals", [])
    check("At least one proposal exists", len(proposals) > 0)
    if not proposals:
        return

    _proposal_id = proposals[0]["id"]

    # Approve the proposal and create a frozen snapshot
    from database import get_connection
    import uuid as _uuid
    from datetime import datetime, timezone

    conn = get_connection()
    try:
        conn.execute("UPDATE execution_proposals SET status = 'approved' WHERE id = ?", (_proposal_id,))
        _approval_id = str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload, status, proposal_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (_approval_id, _task_id, None, "proposal:builder",
             json.dumps({"proposal_id": _proposal_id}), "approved", _proposal_id, now),
        )
        conn.commit()
    finally:
        conn.close()

    # Freeze snapshot
    from agents.snapshot_service import freeze_snapshot
    snapshot = freeze_snapshot(_proposal_id)
    _snapshot_id = snapshot["id"]
    _snapshot_content_hash = snapshot["content_hash"]
    check("Snapshot frozen successfully", snapshot["status"] == "frozen")
    check("Snapshot has content_hash", len(_snapshot_content_hash) == 64)


# ── Section 2: Snapshot not found ─────────────────────────────
def section_2_not_found():
    print("\n=== Section 2: Snapshot not found -> ValueError ===")
    from agents.execution_request_service import create_execution_request

    try:
        create_execution_request("nonexistent-snapshot-id")
        check("Nonexistent snapshot raises ValueError", False, "No exception raised")
    except ValueError as e:
        check("Nonexistent snapshot raises ValueError", True)
        check("Error message mentions 'not found'", "not found" in str(e).lower(), str(e))


# ── Section 3: Snapshot not frozen ────────────────────────────
def section_3_not_frozen():
    print("\n=== Section 3: Snapshot not frozen -> ValueError ===")
    from agents.execution_request_service import create_execution_request
    from database import get_connection

    # Temporarily set snapshot status to something other than 'frozen'
    conn = get_connection()
    try:
        conn.execute("UPDATE execution_snapshots SET status = 'invalid_test' WHERE id = ?", (_snapshot_id,))
        conn.commit()
    finally:
        conn.close()

    try:
        create_execution_request(_snapshot_id)
        check("Non-frozen snapshot raises ValueError", False, "No exception raised")
    except ValueError as e:
        check("Non-frozen snapshot raises ValueError", True)
        check("Error message mentions 'not frozen'", "not frozen" in str(e).lower(), str(e))

    # Restore snapshot status
    conn = get_connection()
    try:
        conn.execute("UPDATE execution_snapshots SET status = 'frozen' WHERE id = ?", (_snapshot_id,))
        conn.commit()
    finally:
        conn.close()


# ── Section 4: Normal creation ────────────────────────────────
def section_4_create():
    print("\n=== Section 4: Normal creation of execution request ===")
    from agents.execution_request_service import create_execution_request

    global _request_id
    result = create_execution_request(_snapshot_id)
    check("create returns a dict", isinstance(result, dict))
    check("Result has 'id'", "id" in result)
    check("Result has 'snapshot_id'", result.get("snapshot_id") == _snapshot_id)
    check("Result has 'task_id'", result.get("task_id") == _task_id)
    check("Result has 'proposal_id'", result.get("proposal_id") == _proposal_id)
    check("Result has 'approval_id'", result.get("approval_id") == _approval_id)
    check("Result has 'risk_level'", len(result.get("risk_level", "")) > 0)
    _request_id = result["id"]


# ── Section 5: Idempotent creation ────────────────────────────
def section_5_idempotent():
    print("\n=== Section 5: Idempotent creation returns same request ===")
    from agents.execution_request_service import create_execution_request

    result2 = create_execution_request(_snapshot_id)
    check("Second call returns same id", result2["id"] == _request_id)
    check("Second call returns same snapshot_id", result2["snapshot_id"] == _snapshot_id)
    check("Second call returns same status", result2["status"] == "requested")


# ── Section 6: content_hash match ─────────────────────────────
def section_6_hash():
    print("\n=== Section 6: snapshot_content_hash matches snapshot ===")
    from database import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_content_hash FROM execution_requests WHERE id = ?",
            (_request_id,),
        ).fetchone()
        check("Request row found", row is not None)
        if row:
            check("snapshot_content_hash matches snapshot",
                  row[0] == _snapshot_content_hash,
                  f"got {row[0]}, expected {_snapshot_content_hash}")
    finally:
        conn.close()


# ── Section 7: Default status ─────────────────────────────────
def section_7_status():
    print("\n=== Section 7: Default status is 'requested' ===")
    from database import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM execution_requests WHERE id = ?",
            (_request_id,),
        ).fetchone()
        check("Status is 'requested'", row is not None and row[0] == "requested",
              f"got {row[0] if row else 'None'}")
    finally:
        conn.close()


# ── Section 8: updated_at == created_at ───────────────────────
def section_8_timestamps():
    print("\n=== Section 8: updated_at equals created_at on creation ===")
    from database import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT created_at, updated_at FROM execution_requests WHERE id = ?",
            (_request_id,),
        ).fetchone()
        check("Timestamps found", row is not None)
        if row:
            check("updated_at equals created_at", row[0] == row[1],
                  f"created={row[0]}, updated={row[1]}")
    finally:
        conn.close()


# ── Section 9: Audit log ─────────────────────────────────────
def section_9_audit():
    print("\n=== Section 9: Audit log exists (execution_request:create) ===")
    from database import get_connection

    conn = get_connection()
    try:
        logs = conn.execute(
            "SELECT message, payload FROM log_events WHERE source = 'execution_request_service' AND task_id = ?",
            (_task_id,),
        ).fetchall()
        check("At least one execution_request_service log exists", len(logs) > 0)
        if logs:
            payload = json.loads(logs[0][1])
            check("Log payload has event_type 'execution_request:create'",
                  payload.get("event_type") == "execution_request:create")
            check("Log payload has request_id", payload.get("request_id") == _request_id)
            check("Log payload has snapshot_id", payload.get("snapshot_id") == _snapshot_id)
            check("Log payload has snapshot_content_hash",
                  payload.get("snapshot_content_hash") == _snapshot_content_hash)
            check("Log payload has risk_level", "risk_level" in payload)
            check("Log payload has proposal_id", payload.get("proposal_id") == _proposal_id)
            check("Log payload has approval_id", payload.get("approval_id") == _approval_id)
    finally:
        conn.close()


# ── Section 10: Audit chain traceability ──────────────────────
def section_10_traceability():
    print("\n=== Section 10: Audit chain traceability ===")
    from database import get_connection

    conn = get_connection()
    try:
        # request -> snapshot -> proposal -> approval: all IDs should resolve
        req_row = conn.execute(
            "SELECT snapshot_id, proposal_id, approval_id, task_id FROM execution_requests WHERE id = ?",
            (_request_id,),
        ).fetchone()
        check("Request record found", req_row is not None)
        if not req_row:
            return

        snap_row = conn.execute(
            "SELECT id FROM execution_snapshots WHERE id = ?", (req_row[0],)
        ).fetchone()
        check("Snapshot exists for request.snapshot_id", snap_row is not None)

        prop_row = conn.execute(
            "SELECT id FROM execution_proposals WHERE id = ?", (req_row[1],)
        ).fetchone()
        check("Proposal exists for request.proposal_id", prop_row is not None)

        appr_row = conn.execute(
            "SELECT id FROM approval_requests WHERE id = ?", (req_row[2],)
        ).fetchone()
        check("Approval exists for request.approval_id", appr_row is not None)

        task_row = conn.execute(
            "SELECT id FROM tasks WHERE id = ?", (req_row[3],)
        ).fetchone()
        check("Task exists for request.task_id", task_row is not None)
    finally:
        conn.close()


# ── Section 11: API - POST request-execution ─────────────────
def section_11_api_create():
    print("\n=== Section 11: API POST /snapshots/{id}/request-execution ===")

    # Create a fresh snapshot for API testing
    status, proj = POST("/projects", {"name": f"6EC-api-{os.getpid()}", "local_repo_path": "/tmp/6ec-api"})
    api_proj_id = proj.get("id", "")
    status, task = POST(f"/projects/{api_proj_id}/tasks", {"title": "6EC API test"})
    api_task_id = task.get("id", "")
    POST(f"/tasks/{api_task_id}/orchestrate", {"roles": ["builder"]})
    _, props = GET(f"/tasks/{api_task_id}/proposals")
    proposals = props.get("proposals", [])
    if not proposals:
        check("API test has proposal", False, "No proposals")
        return
    api_proposal_id = proposals[0]["id"]

    # Approve and freeze
    from database import get_connection
    import uuid as _uuid
    from datetime import datetime, timezone
    conn = get_connection()
    try:
        conn.execute("UPDATE execution_proposals SET status = 'approved' WHERE id = ?", (api_proposal_id,))
        _aid = str(_uuid.uuid4())
        _now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload, status, proposal_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (_aid, api_task_id, None, "proposal:builder",
             json.dumps({"proposal_id": api_proposal_id}), "approved", api_proposal_id, _now),
        )
        conn.commit()
    finally:
        conn.close()

    status, snap = POST(f"/proposals/{api_proposal_id}/freeze")
    check("Freeze -> 201", status == 201)
    api_snapshot_id = snap.get("id", "")

    # Nonexistent snapshot -> 404
    status, _ = POST("/snapshots/nonexistent-id/request-execution")
    check("Request nonexistent snapshot -> 404", status == 404)

    # Request execution on frozen snapshot -> 201
    status, req = POST(f"/snapshots/{api_snapshot_id}/request-execution")
    check("Request execution -> 201", status == 201)
    check("Response has id", "id" in req)
    check("Response has snapshot_id", req.get("snapshot_id") == api_snapshot_id)
    check("Response status is 'requested'", req.get("status") == "requested")
    check("Response has snapshot_content_hash", len(req.get("snapshot_content_hash", "")) == 64)

    # Idempotent re-request -> still 201
    status2, req2 = POST(f"/snapshots/{api_snapshot_id}/request-execution")
    check("Re-request -> 201", status2 == 201)
    check("Re-request returns same id", req2.get("id") == req.get("id"))

    # Store for later sections
    global _api_request_id, _api_snapshot_id_global
    _api_request_id = req.get("id", "")
    _api_snapshot_id_global = api_snapshot_id


_api_request_id: str = ""
_api_snapshot_id_global: str = ""


# ── Section 12: API - GET execution-request ───────────────────
def section_12_api_get():
    print("\n=== Section 12: API GET /execution-requests/{id} ===")

    status, req = GET(f"/execution-requests/{_api_request_id}")
    check("GET request -> 200", status == 200)
    check("Response has id", req.get("id") == _api_request_id)
    check("Response has snapshot_id", len(req.get("snapshot_id", "")) > 0)
    check("Response has snapshot_content_hash", len(req.get("snapshot_content_hash", "")) == 64)
    check("Response status is 'requested'", req.get("status") == "requested")

    # Nonexistent -> 404
    status, _ = GET("/execution-requests/nonexistent-id")
    check("GET nonexistent request -> 404", status == 404)


# ── Section 13: API - GET snapshot execution-request ──────────
def section_13_api_get_by_snapshot():
    print("\n=== Section 13: API GET /snapshots/{id}/execution-request ===")

    # Snapshot that has a request -> 200
    status, req = GET(f"/snapshots/{_api_snapshot_id_global}/execution-request")
    check("GET by snapshot -> 200", status == 200)
    check("Response has id", req.get("id") == _api_request_id)
    check("Response has snapshot_id", req.get("snapshot_id") == _api_snapshot_id_global)
    check("Response status is 'requested'", req.get("status") == "requested")

    # Snapshot with no request -> 404
    # Use a fake snapshot id that doesn't exist in execution_requests
    status, _ = GET("/snapshots/nonexistent-snapshot/execution-request")
    check("Snapshot with no request -> 404", status == 404)

    # Use a real snapshot that has no request (create a fresh one)
    status2, proj2 = POST("/projects", {"name": f"6EC-s13-{os.getpid()}", "local_repo_path": "/tmp/6ec-s13"})
    proj2_id = proj2.get("id", "")
    _, task2 = POST(f"/projects/{proj2_id}/tasks", {"title": "6EC s13 no-req"})
    task2_id = task2.get("id", "")
    POST(f"/tasks/{task2_id}/orchestrate", {"roles": ["builder"]})
    _, props2 = GET(f"/tasks/{task2_id}/proposals")
    proposals2 = props2.get("proposals", [])
    if proposals2:
        prop2_id = proposals2[0]["id"]
        # Approve + freeze but do NOT create execution request
        from database import get_connection
        import uuid as _uuid
        from datetime import datetime, timezone
        conn = get_connection()
        try:
            conn.execute("UPDATE execution_proposals SET status = 'approved' WHERE id = ?", (prop2_id,))
            _aid2 = str(_uuid.uuid4())
            _now2 = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO approval_requests
                   (id, task_id, run_id, action_type, action_payload, status, proposal_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (_aid2, task2_id, None, "proposal:builder",
                 json.dumps({"proposal_id": prop2_id}), "approved", prop2_id, _now2),
            )
            conn.commit()
        finally:
            conn.close()
        _, snap2 = POST(f"/proposals/{prop2_id}/freeze")
        snap2_id = snap2.get("id", "")
        status3, _ = GET(f"/snapshots/{snap2_id}/execution-request")
        check("Real snapshot with no request -> 404", status3 == 404)
    else:
        check("Real snapshot with no request -> 404", False, "No proposals for test")


# ── Main ──────────────────────────────────────────────────────
if __name__ == "__main__":
    section_1_migration()
    section_2_not_found()
    section_3_not_frozen()
    section_4_create()
    section_5_idempotent()
    section_6_hash()
    section_7_status()
    section_8_timestamps()
    section_9_audit()
    section_10_traceability()
    section_11_api_create()
    section_12_api_get()
    section_13_api_get_by_snapshot()

    print(f"\n{'=' * 60}")
    print(f"Phase 6E-C acceptance: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 60}")
    sys.exit(1 if FAIL else 0)
