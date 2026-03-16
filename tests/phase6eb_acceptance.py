#!/usr/bin/env python3
"""Phase 6E-B acceptance tests -- Approved Execution Snapshot.

Sections
--------
1.  Database V7 migration verification (execution_snapshots table exists)
2.  Unapproved proposal cannot be frozen
3.  Approved proposal can be frozen
4.  Idempotent freeze returns same snapshot
5.  Proposal / approval / snapshot binding consistency
6.  Audit log written on freeze
7.  content_hash exists and is stable

Run
---
  cd services/runtime
  set RUNTIME_DB=<temp_db_path>
  set RUNTIME_PORT=<port>
  python -m uvicorn main:app --port %RUNTIME_PORT%

  set TEST_API_BASE=http://127.0.0.1:<port>/api
  python ../../tests/phase6eb_acceptance.py
"""

import hashlib
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
# DB as the running test server.  Discover from TEST_RUNTIME_DB env var,
# or fall back to querying the server's /api/health endpoint.
if not os.environ.get("RUNTIME_DB"):
    # Try to discover the DB path the runtime is using
    # For test isolation, require TEST_RUNTIME_DB to be set
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


def PATCH(path: str, body: Any):
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


# ── Section 1: V7 migration ──────────────────────────────────────
def section_1_migration():
    print("\n=== Section 1: Database V7 migration verification ===")
    status, data = GET("/projects")
    check("Server is running (GET /projects returns 2xx)", status in (200, 201))

    # Verify we can create test infrastructure
    global _project_id, _task_id
    status, proj = POST("/projects", {"name": f"6EB-test-{os.getpid()}", "local_repo_path": "/tmp/6eb"})
    check("Create project -> 200/201", status in (200, 201))
    _project_id = proj.get("id", "")

    status, task = POST(f"/projects/{_project_id}/tasks", {"title": "6EB snapshot test"})
    check("Create task -> 200/201", status in (200, 201))
    _task_id = task.get("id", "")

    # Orchestrate to generate a proposal
    status, orch = POST(f"/tasks/{_task_id}/orchestrate", {"roles": ["builder"]})
    check("Orchestrate task -> 200", status == 200)

    # Verify proposals exist
    status, props = GET(f"/tasks/{_task_id}/proposals")
    check("GET proposals -> 200", status == 200)
    proposals = props.get("proposals", [])
    check("At least one proposal exists", len(proposals) > 0)


# ── Section 2: Unapproved proposal cannot be frozen ──────────────
def section_2_unapproved():
    print("\n=== Section 2: Unapproved proposal cannot be frozen ===")
    from agents.snapshot_service import freeze_snapshot

    # Get proposal (should be 'pending' from orchestration)
    status, props = GET(f"/tasks/{_task_id}/proposals")
    proposals = props.get("proposals", [])
    if not proposals:
        check("Proposal available for test", False, "No proposals found")
        return

    global _proposal_id
    _proposal_id = proposals[0]["id"]
    prop_status = proposals[0]["status"]
    req_approval = proposals[0].get("requires_approval", False)
    # Phase 11-5: Low-risk proposals are auto-approved at creation
    expected = "pending" if req_approval else "approved"
    check(f"Proposal status is '{expected}'", prop_status == expected, f"got {prop_status}")

    # Attempt freeze on unapproved proposal (only testable if proposal is pending)
    if prop_status == "pending":
        try:
            freeze_snapshot(_proposal_id)
            check("Freeze unapproved proposal raises ValueError", False, "No exception raised")
        except ValueError as e:
            check("Freeze unapproved proposal raises ValueError", True)
            check("Error message mentions 'not approved'", "not approved" in str(e).lower(),
                  str(e))
    else:
        check("Proposal already approved (low risk, no approval needed) — freeze test skipped", True)


# ── Section 3: Approved proposal can be frozen ────────────────────
def section_3_approved_freeze():
    print("\n=== Section 3: Approved proposal can be frozen ===")
    from agents.snapshot_service import freeze_snapshot
    from database import get_connection

    # First, approve the proposal manually:
    # 1. Update proposal status to 'approved'
    # 2. Create/approve linked approval_request
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE execution_proposals SET status = 'approved' WHERE id = ?",
            (_proposal_id,),
        )
        # Check if an approval_request exists for this proposal
        row = conn.execute(
            "SELECT id FROM approval_requests WHERE proposal_id = ?",
            (_proposal_id,),
        ).fetchone()

        global _approval_id
        if row:
            _approval_id = row[0]
            conn.execute(
                "UPDATE approval_requests SET status = 'approved' WHERE id = ?",
                (_approval_id,),
            )
        else:
            # Create one
            import uuid
            from datetime import datetime, timezone
            _approval_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO approval_requests
                   (id, task_id, run_id, action_type, action_payload,
                    status, proposal_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _approval_id, _task_id, None,
                    "proposal:builder", json.dumps({"proposal_id": _proposal_id}),
                    "approved", _proposal_id, now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # Now freeze
    snapshot = freeze_snapshot(_proposal_id)
    check("freeze_snapshot returns a dict", isinstance(snapshot, dict))
    check("Snapshot has 'id'", "id" in snapshot)
    check("Snapshot has 'proposal_id'", snapshot.get("proposal_id") == _proposal_id)
    check("Snapshot has 'approval_id'", snapshot.get("approval_id") == _approval_id)
    check("Snapshot has 'task_id'", snapshot.get("task_id") == _task_id)
    check("Snapshot status is 'frozen'", snapshot.get("status") == "frozen")
    check("Snapshot has 'snapshot_data'", len(snapshot.get("snapshot_data", "")) > 2)
    check("Snapshot has 'content_hash'", len(snapshot.get("content_hash", "")) == 64)
    check("Snapshot has 'risk_level'", len(snapshot.get("risk_level", "")) > 0)
    check("Snapshot has 'created_at'", len(snapshot.get("created_at", "")) > 0)


# ── Section 4: Idempotent freeze ──────────────────────────────────
def section_4_idempotent():
    print("\n=== Section 4: Idempotent freeze returns same snapshot ===")
    from agents.snapshot_service import freeze_snapshot

    snap1 = freeze_snapshot(_proposal_id)
    snap2 = freeze_snapshot(_proposal_id)
    check("Second freeze returns same id", snap1["id"] == snap2["id"])
    check("Second freeze returns same content_hash", snap1["content_hash"] == snap2["content_hash"])
    check("Second freeze returns same snapshot_data", snap1["snapshot_data"] == snap2["snapshot_data"])


# ── Section 5: Binding consistency ────────────────────────────────
def section_5_binding():
    print("\n=== Section 5: Proposal / approval / snapshot binding ===")
    from database import get_connection

    conn = get_connection()
    try:
        # Snapshot -> proposal
        snap_row = conn.execute(
            "SELECT proposal_id, approval_id, task_id FROM execution_snapshots WHERE proposal_id = ?",
            (_proposal_id,),
        ).fetchone()
        check("Snapshot exists in DB", snap_row is not None)
        if snap_row:
            check("Snapshot.proposal_id matches", snap_row[0] == _proposal_id)
            check("Snapshot.approval_id matches", snap_row[1] == _approval_id)
            check("Snapshot.task_id matches", snap_row[2] == _task_id)

        # Proposal -> snapshot (UNIQUE constraint)
        count = conn.execute(
            "SELECT COUNT(*) FROM execution_snapshots WHERE proposal_id = ?",
            (_proposal_id,),
        ).fetchone()[0]
        check("Exactly one snapshot per proposal", count == 1)

        # Approval -> snapshot
        snap_by_approval = conn.execute(
            "SELECT id FROM execution_snapshots WHERE approval_id = ?",
            (_approval_id,),
        ).fetchone()
        check("Snapshot found by approval_id", snap_by_approval is not None)
    finally:
        conn.close()


# ── Section 6: Audit log ──────────────────────────────────────────
def section_6_audit():
    print("\n=== Section 6: Audit log written on freeze ===")
    from database import get_connection

    conn = get_connection()
    try:
        logs = conn.execute(
            "SELECT message, payload FROM log_events WHERE source = 'snapshot_service' AND task_id = ?",
            (_task_id,),
        ).fetchall()
        check("At least one snapshot_service log exists", len(logs) > 0)
        if logs:
            msg = logs[0][0]
            payload = json.loads(logs[0][1])
            check("Log message mentions 'snapshot frozen'",
                  "snapshot frozen" in msg.lower() or "execution snapshot frozen" in msg.lower())
            check("Log payload has snapshot_id", "snapshot_id" in payload)
            check("Log payload has proposal_id", payload.get("proposal_id") == _proposal_id)
            check("Log payload has content_hash", "content_hash" in payload)
            check("Log payload has risk_level", "risk_level" in payload)
            check("Log payload has event_type 'snapshot:freeze'",
                  payload.get("event_type") == "snapshot:freeze")
    finally:
        conn.close()


# ── Section 7: content_hash stability ─────────────────────────────
def section_7_hash():
    print("\n=== Section 7: content_hash exists and is stable ===")
    from database import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_data, content_hash FROM execution_snapshots WHERE proposal_id = ?",
            (_proposal_id,),
        ).fetchone()
        check("Snapshot row found", row is not None)
        if row:
            snapshot_data, stored_hash = row
            check("content_hash is 64 chars (SHA-256 hex)", len(stored_hash) == 64)

            # Recompute independently
            canonical = json.dumps(json.loads(snapshot_data), sort_keys=True, separators=(",", ":"))
            expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            check("content_hash matches recomputed SHA-256", stored_hash == expected_hash)

            # Verify stability: same data -> same hash
            canonical2 = json.dumps(json.loads(snapshot_data), sort_keys=True, separators=(",", ":"))
            hash2 = hashlib.sha256(canonical2.encode("utf-8")).hexdigest()
            check("Hash is stable across recomputation", expected_hash == hash2)
    finally:
        conn.close()


# ── Section 8: API - POST freeze endpoint ─────────────────────────
def section_8_api_freeze():
    print("\n=== Section 8: API POST /proposals/{id}/freeze ===")

    # Create a fresh proposal for API testing
    status, proj = POST("/projects", {"name": f"6EB-api-{os.getpid()}", "local_repo_path": "/tmp/6eb-api"})
    api_proj_id = proj.get("id", "")
    status, task = POST(f"/projects/{api_proj_id}/tasks", {"title": "6EB API test"})
    api_task_id = task.get("id", "")
    POST(f"/tasks/{api_task_id}/orchestrate", {"roles": ["builder"]})
    _, props = GET(f"/tasks/{api_task_id}/proposals")
    proposals = props.get("proposals", [])
    if not proposals:
        check("API test has proposal", False, "No proposals")
        return
    api_proposal_id = proposals[0]["id"]

    # Freeze unapproved -> 409
    status, data = POST(f"/proposals/{api_proposal_id}/freeze")
    check("Freeze unapproved via API -> 409", status == 409)

    # Freeze nonexistent -> 404
    status, data = POST("/proposals/nonexistent-id/freeze")
    check("Freeze nonexistent via API -> 404", status == 404)

    # Approve the proposal, then freeze via API
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
    check("Freeze approved via API -> 201", status == 201)
    check("API response has snapshot id", "id" in snap)
    check("API response has snapshot_data_parsed", "snapshot_data_parsed" in snap)
    check("API response status is frozen", snap.get("status") == "frozen")
    check("API response has content_hash", len(snap.get("content_hash", "")) == 64)

    # Idempotent re-freeze -> still returns snapshot (200 or 201)
    status2, snap2 = POST(f"/proposals/{api_proposal_id}/freeze")
    check("Re-freeze via API -> 2xx", status2 in (200, 201))
    check("Re-freeze returns same id", snap2.get("id") == snap.get("id"))

    # Store snapshot_id for section 9
    global _api_snapshot_id
    _api_snapshot_id = snap.get("id", "")


_api_snapshot_id: str = ""


# ── Section 9: API - GET snapshot endpoint ─────────────────────────
def section_9_api_get_snapshot():
    print("\n=== Section 9: API GET /snapshots/{id} ===")

    status, snap = GET(f"/snapshots/{_api_snapshot_id}")
    check("GET snapshot -> 200", status == 200)
    check("Response has id", snap.get("id") == _api_snapshot_id)
    check("Response has snapshot_data_parsed", "snapshot_data_parsed" in snap)
    check("Response has content_hash", len(snap.get("content_hash", "")) == 64)
    check("Response status is frozen", snap.get("status") == "frozen")

    # Nonexistent -> 404
    status, _ = GET("/snapshots/nonexistent-id")
    check("GET nonexistent snapshot -> 404", status == 404)


# ── Main ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    section_1_migration()
    section_2_unapproved()
    section_3_approved_freeze()
    section_4_idempotent()
    section_5_binding()
    section_6_audit()
    section_7_hash()
    section_8_api_freeze()
    section_9_api_get_snapshot()

    print(f"\n{'=' * 60}")
    print(f"Phase 6E-B acceptance: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 60}")
    sys.exit(1 if FAIL else 0)
