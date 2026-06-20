#!/usr/bin/env python3
"""Phase 6E-E acceptance tests -- Audit Trail API (Step 1).

Sections
--------
1.  Task not found -> 404
2.  Empty trail for task with no pipeline objects
3.  Full chain trail has correct event count and types
4.  Event ordering: timestamp ASC + fixed event order tie-break
5.  Pending approvals are excluded
6.  execution_request:finalized only for terminal status
7.  Response structure validation (task_id, events, count)

Run
---
  cd services/runtime
  set RUNTIME_DB=<temp_db_path>
  set RUNTIME_PORT=<port>
  python -m uvicorn main:app --port %RUNTIME_PORT%

  set TEST_API_BASE=http://127.0.0.1:<port>/api
  python ../../tests/phase6ee_acceptance.py
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

if not os.environ.get("RUNTIME_DB"):
    _test_db = os.environ.get("TEST_RUNTIME_DB", "")
    if _test_db:
        os.environ["RUNTIME_DB"] = _test_db

PASS = 0
FAIL = 0

# Module-level state
_project_id: str = ""
_task_id: str = ""
_empty_task_id: str = ""
_proposal_id: str = ""
_approval_id: str = ""
_pending_approval_id: str = ""
_snapshot_id: str = ""
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


def PATCH(path: str, body: Any = None):
    return _req("PATCH", path, body)


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


# ── Setup: create full chain ───────────────────────────────────

def setup():
    """Create project, two tasks, full pipeline chain on one task."""
    global _project_id, _task_id, _empty_task_id
    global _proposal_id, _approval_id, _pending_approval_id
    global _snapshot_id, _request_id

    print("\n=== Setup: creating test data ===")

    status, proj = POST("/projects", {
        "name": f"6EE-test-{os.getpid()}",
        "local_repo_path": "/tmp/6ee",
    })
    check("Create project", status in (200, 201))
    _project_id = proj.get("id", "")

    # Task with full chain
    status, task = POST(f"/projects/{_project_id}/tasks", {"title": "6EE audit trail test"})
    check("Create task", status in (200, 201))
    _task_id = task.get("id", "")

    # Empty task (no pipeline objects)
    status, task2 = POST(f"/projects/{_project_id}/tasks", {"title": "6EE empty task"})
    check("Create empty task", status in (200, 201))
    _empty_task_id = task2.get("id", "")

    # Orchestrate to generate a proposal
    POST(f"/tasks/{_task_id}/orchestrate", {"roles": ["builder"]})
    _, props = GET(f"/tasks/{_task_id}/proposals")
    proposals = props.get("proposals", [])
    check("At least one proposal exists", len(proposals) > 0)
    if not proposals:
        return

    _proposal_id = proposals[0]["id"]

    # Approve the proposal
    from database import get_connection
    import uuid as _uuid
    from datetime import datetime, timezone

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE execution_proposals SET status = 'approved' WHERE id = ?",
            (_proposal_id,),
        )
        _approval_id = str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload, status, proposal_id, created_at, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_approval_id, _task_id, None, "proposal:builder",
             json.dumps({"proposal_id": _proposal_id}), "approved", _proposal_id, now, now),
        )

        # Also insert a pending approval (should NOT appear in trail)
        _pending_approval_id = str(_uuid.uuid4())
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload, status, proposal_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (_pending_approval_id, _task_id, None, "proposal:builder",
             json.dumps({"proposal_id": _proposal_id}), "pending", _proposal_id, now),
        )

        conn.commit()
    finally:
        conn.close()

    # Freeze snapshot
    from agents.snapshot_service import freeze_snapshot
    snapshot = freeze_snapshot(_proposal_id)
    _snapshot_id = snapshot["id"]
    check("Snapshot frozen", snapshot["status"] == "frozen")

    # Create execution request
    from agents.execution_request_service import create_execution_request
    req = create_execution_request(_snapshot_id)
    _request_id = req["id"]
    check("Execution request created", req["status"] == "requested")

    # Confirm the execution request (so we get a finalized event)
    from agents.execution_request_service import update_execution_request_status
    updated = update_execution_request_status(_request_id, "confirmed", reason="test")
    check("Execution request confirmed", updated["status"] == "confirmed")

    print("  Setup complete.\n")


# ── Section 1: Task not found -> 404 ──────────────────────────

def section_1_not_found():
    print("\n=== Section 1: Task not found -> 404 ===")
    status, data = GET("/tasks/nonexistent-task-id/audit-trail")
    check("Nonexistent task -> 404", status == 404)
    check("Detail mentions 'not found'", "not found" in data.get("detail", "").lower())


# ── Section 2: Empty trail for task with no pipeline objects ──

def section_2_empty_trail():
    print("\n=== Section 2: Empty trail for empty task ===")
    status, data = GET(f"/tasks/{_empty_task_id}/audit-trail")
    check("Status 200", status == 200)
    check("events is empty list", data.get("events") == [])
    check("count is 0", data.get("count") == 0)
    check("task_id matches", data.get("task_id") == _empty_task_id)


# ── Section 3: Full chain trail has correct event count ───────

def section_3_full_chain():
    print("\n=== Section 3: Full chain trail event count and types ===")
    status, data = GET(f"/tasks/{_task_id}/audit-trail")
    check("Status 200", status == 200)

    events = data.get("events", [])
    count = data.get("count", 0)
    check("count matches events length", count == len(events))

    # Expected events: proposal:created, approval:decided, snapshot:frozen,
    # execution_request:created, execution_request:finalized = 5, PLUS (DOC-3) a second
    # proposal:created for the Documentation proposal = 6.
    check("6 events in full chain", count == 6, f"got {count}")

    event_types = [e["event_type"] for e in events]
    check("proposal:created present", "proposal:created" in event_types)
    check("approval:decided present", "approval:decided" in event_types)
    check("snapshot:frozen present", "snapshot:frozen" in event_types)
    check("execution_request:created present", "execution_request:created" in event_types)
    check("execution_request:finalized present", "execution_request:finalized" in event_types)


# ── Section 4: Event ordering ─────────────────────────────────

def section_4_ordering():
    print("\n=== Section 4: Event ordering (timestamp ASC + tie-break) ===")
    status, data = GET(f"/tasks/{_task_id}/audit-trail")
    events = data.get("events", [])

    # Timestamps should be non-decreasing
    timestamps = [e["timestamp"] for e in events]
    is_sorted = all(timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1))
    check("Timestamps are non-decreasing", is_sorted)

    # Fixed event order for tie-break: proposal < approval < snapshot < req:created < req:finalized
    event_order_map = {
        "proposal:created": 0,
        "approval:decided": 1,
        "snapshot:frozen": 2,
        "execution_request:created": 3,
        "execution_request:finalized": 4,
    }
    # Group events by timestamp, then check order within each group
    from itertools import groupby
    groups = groupby(events, key=lambda e: e["timestamp"])
    order_ok = True
    for _ts, group_iter in groups:
        group_list = list(group_iter)
        if len(group_list) > 1:
            orders = [event_order_map.get(e["event_type"], 99) for e in group_list]
            if orders != sorted(orders):
                order_ok = False
                break
    check("Within same timestamp, events follow fixed order", order_ok)


# ── Section 5: Pending approvals excluded ─────────────────────

def section_5_pending_excluded():
    print("\n=== Section 5: Pending approvals excluded ===")
    status, data = GET(f"/tasks/{_task_id}/audit-trail")
    events = data.get("events", [])

    approval_events = [e for e in events if e["event_type"] == "approval:decided"]
    check("Exactly 1 approval:decided event", len(approval_events) == 1,
          f"got {len(approval_events)}")

    # The pending approval's object_id should NOT be present
    approval_ids = [e["object_id"] for e in approval_events]
    check("Pending approval ID not in events",
          _pending_approval_id not in approval_ids)

    # The decided approval's object_id SHOULD be present
    check("Decided approval ID in events",
          _approval_id in approval_ids)


# ── Section 6: Finalized only for terminal status ─────────────

def section_6_finalized_only_terminal():
    print("\n=== Section 6: execution_request:finalized only for terminal ===")
    status, data = GET(f"/tasks/{_task_id}/audit-trail")
    events = data.get("events", [])

    finalized = [e for e in events if e["event_type"] == "execution_request:finalized"]
    check("1 finalized event (confirmed request)", len(finalized) == 1,
          f"got {len(finalized)}")

    if finalized:
        ev = finalized[0]
        check("Finalized top-level status=confirmed",
              ev.get("status") == "confirmed")
        check("Finalized detail has new_status=confirmed",
              ev.get("detail", {}).get("new_status") == "confirmed")
        check("Finalized summary mentions 'confirmed'",
              "confirmed" in ev.get("summary", ""))
        check("Finalized object_type is execution_request",
              ev.get("object_type") == "execution_request")


# ── Section 7: Response structure validation ──────────────────

def section_7_response_structure():
    print("\n=== Section 7: Response structure validation ===")
    status, data = GET(f"/tasks/{_task_id}/audit-trail")
    check("Has task_id key", "task_id" in data)
    check("Has events key", "events" in data)
    check("Has count key", "count" in data)
    check("task_id is correct", data.get("task_id") == _task_id)

    # Each event must have all 8 top-level fields
    events = data.get("events", [])
    required_keys = {
        "event_type", "object_type", "object_id", "status",
        "timestamp", "summary", "related_ids", "detail",
    }
    if events:
        for idx, ev in enumerate(events):
            missing = required_keys - set(ev.keys())
            check(f"Event[{idx}] has all 8 required keys",
                  len(missing) == 0,
                  f"missing={missing}")
        # Type checks on first event
        ev = events[0]
        check("object_type is a string", isinstance(ev.get("object_type"), str))
        check("status is a string", isinstance(ev.get("status"), str))
        check("related_ids is a dict", isinstance(ev.get("related_ids"), dict))
        check("detail is a dict", isinstance(ev.get("detail"), dict))
        check("summary is a string", isinstance(ev.get("summary"), str))

        # Verify object_type values are valid
        valid_object_types = {"proposal", "approval", "snapshot", "execution_request"}
        actual_types = {e["object_type"] for e in events}
        check("All object_types are valid",
              actual_types.issubset(valid_object_types),
              f"got {actual_types}")

        # Verify every event has related_ids with at least task_id
        all_have_task = all("task_id" in e.get("related_ids", {}) for e in events)
        check("Every event's related_ids contains task_id", all_have_task)
    else:
        check("Events list is not empty (expected full chain)", False)


# ── Main ──────────────────────────────────────────────────────

def main():
    global PASS, FAIL
    try:
        setup()
        section_1_not_found()
        section_2_empty_trail()
        section_3_full_chain()
        section_4_ordering()
        section_5_pending_excluded()
        section_6_finalized_only_terminal()
        section_7_response_structure()
    except Exception as exc:
        FAIL += 1
        print(f"\n  [ERROR] Unexpected exception: {exc}")
        import traceback
        traceback.print_exc()

    total = PASS + FAIL
    print(f"\n{'='*60}")
    print(f"Phase 6E-E acceptance: {PASS}/{total} passed, {FAIL} failed")
    print(f"{'='*60}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
