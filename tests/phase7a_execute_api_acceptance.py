#!/usr/bin/env python3
"""Phase 7A Step 3 acceptance: POST /execute API endpoint.

Tests POST /api/execution-requests/{request_id}/execute

Requires a running server with TEST_API_BASE env var.
Usage:
    set TEST_API_BASE=http://127.0.0.1:9800/api
    set TEST_WORKSPACE_ROOT=<temp_dir>
    python tests/phase7a_execute_api_acceptance.py
"""

import hashlib
import json
import os
import sys
import tempfile
import uuid as _uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

# ── Add runtime to sys.path ──────────────────────────────────
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")
WORKSPACE = os.environ.get("TEST_WORKSPACE_ROOT", "")

PASS = 0
FAIL = 0


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


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {label}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)


def _content_hash(snapshot_data: str) -> str:
    canonical = json.dumps(
        json.loads(snapshot_data), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_confirmed_request_with_workspace(
    proposed_files: list[dict],
    workspace_root: str,
) -> dict:
    """Build full chain up to confirmed request + dry-run, using real workspace."""
    from database import get_connection
    from agents.snapshot_service import freeze_snapshot
    from agents.execution_request_service import (
        create_execution_request,
        update_execution_request_status,
    )

    pid = f"7a3-{_uuid.uuid4().hex[:8]}"
    s, proj = POST("/projects", {"name": pid, "local_repo_path": workspace_root})
    project_id = proj["id"]

    s, task = POST(f"/projects/{project_id}/tasks", {"title": f"7A3 task {pid}"})
    task_id = task["id"]

    # Trigger orchestration to get a proposal
    POST(f"/tasks/{task_id}/orchestrate")

    _, props_resp = GET(f"/tasks/{task_id}/proposals")
    proposals = props_resp.get("proposals", [])
    proposal_id = proposals[0]["id"]

    # Override proposal_data with our specific files
    snapshot_data = json.dumps({
        "proposed_files": proposed_files,
        "proposed_commands": [],
    })

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE execution_proposals SET proposal_data = ?, status = 'approved' WHERE id = ?",
            (snapshot_data, proposal_id),
        )
        approval_id = str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload,
                status, proposal_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, task_id, None, "proposal:builder",
             json.dumps({"proposal_id": proposal_id}), "approved",
             proposal_id, now),
        )
        conn.commit()
    finally:
        conn.close()

    snapshot = freeze_snapshot(proposal_id)
    req = create_execution_request(snapshot["id"])
    update_execution_request_status(req["id"], "confirmed")

    # Create dry-run result so eligibility passes
    conn = get_connection()
    try:
        dr_id = str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        dr_data = json.dumps({
            "mode": "dry_run", "summary": "dry-run ok",
            "planned_file_actions": [], "planned_command_actions": [],
            "warnings": [],
        })
        conn.execute(
            """INSERT INTO execution_results
               (id, execution_request_id, task_id, snapshot_id,
                snapshot_content_hash, mode, status, result_data,
                started_at, completed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dr_id, req["id"], task_id, snapshot["id"],
             snapshot["content_hash"], "dry_run", "completed", dr_data,
             now, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "project_id": project_id,
        "task_id": task_id,
        "proposal_id": proposal_id,
        "snapshot_id": snapshot["id"],
        "snapshot_content_hash": snapshot["content_hash"],
        "request_id": req["id"],
    }


# ══════════════════════════════════════════════════════════════
# S1: Request not found → 404
# ══════════════════════════════════════════════════════════════
print("\n[S1]  Request not found → 404")

s, b = POST(f"/execution-requests/{_uuid.uuid4()}/execute")
check("404 for non-existent request", s == 404, f"status={s}")


# ══════════════════════════════════════════════════════════════
# S2: Request not confirmed → 409
# ══════════════════════════════════════════════════════════════
print("\n[S2]  Request not confirmed → 409")

# Create chain but leave request as 'pending'
pid2 = f"7a3s2-{_uuid.uuid4().hex[:8]}"
s, proj2 = POST("/projects", {"name": pid2, "local_repo_path": WORKSPACE or tempfile.mkdtemp()})
s, task2 = POST(f"/projects/{proj2['id']}/tasks", {"title": f"7A3 s2 {pid2}"})
POST(f"/tasks/{task2['id']}/orchestrate")
_, props2 = GET(f"/tasks/{task2['id']}/proposals")
proposal2_id = props2["proposals"][0]["id"]

from database import get_connection
from agents.snapshot_service import freeze_snapshot
from agents.execution_request_service import create_execution_request

conn = get_connection()
try:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE execution_proposals SET status = 'approved' WHERE id = ?", (proposal2_id,))
    a2id = str(_uuid.uuid4())
    conn.execute(
        """INSERT INTO approval_requests (id, task_id, run_id, action_type, action_payload,
           status, proposal_id, created_at) VALUES (?,?,?,?,?,?,?,?)""",
        (a2id, task2["id"], None, "proposal:builder", "{}", "approved", proposal2_id, now),
    )
    conn.commit()
finally:
    conn.close()

snap2 = freeze_snapshot(proposal2_id)
req2 = create_execution_request(snap2["id"])
# Don't confirm — status stays 'pending'

s, b = POST(f"/execution-requests/{req2['id']}/execute")
check("409 for non-confirmed", s == 409, f"status={s}")


# ══════════════════════════════════════════════════════════════
# S3: Eligibility not passed → 409 + blocked_reasons
# ══════════════════════════════════════════════════════════════
print("\n[S3]  Eligibility not passed → 409 + blocked_reasons")

# Create confirmed request but WITHOUT dry-run → eligibility fails
pid3 = f"7a3s3-{_uuid.uuid4().hex[:8]}"
ws3 = tempfile.mkdtemp(prefix="7a3s3_")
s, proj3 = POST("/projects", {"name": pid3, "local_repo_path": ws3})
s, task3 = POST(f"/projects/{proj3['id']}/tasks", {"title": f"7A3 s3 {pid3}"})
POST(f"/tasks/{task3['id']}/orchestrate")
_, props3 = GET(f"/tasks/{task3['id']}/proposals")
proposal3_id = props3["proposals"][0]["id"]

conn = get_connection()
try:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE execution_proposals SET status = 'approved' WHERE id = ?", (proposal3_id,))
    a3id = str(_uuid.uuid4())
    conn.execute(
        """INSERT INTO approval_requests (id, task_id, run_id, action_type, action_payload,
           status, proposal_id, created_at) VALUES (?,?,?,?,?,?,?,?)""",
        (a3id, task3["id"], None, "proposal:builder", "{}", "approved", proposal3_id, now),
    )
    conn.commit()
finally:
    conn.close()

snap3 = freeze_snapshot(proposal3_id)
req3 = create_execution_request(snap3["id"])
from agents.execution_request_service import update_execution_request_status
update_execution_request_status(req3["id"], "confirmed")
# NO dry-run result → eligibility fails

s, b = POST(f"/execution-requests/{req3['id']}/execute")
check("409 for not eligible", s == 409, f"status={s}")
detail = b.get("detail", {})
if isinstance(detail, dict):
    check("has blocked_reasons", "blocked_reasons" in detail, f"detail={detail}")
else:
    check("has blocked_reasons", "blocked_reasons" in str(detail), f"detail={detail}")


# ══════════════════════════════════════════════════════════════
# S4: file_create success → 200 + completed + file on disk
# ══════════════════════════════════════════════════════════════
print("\n[S4]  file_create success → 200 + completed")

ws4 = tempfile.mkdtemp(prefix="7a3s4_")
os.makedirs(os.path.join(ws4, "src"), exist_ok=True)

ids4 = _build_confirmed_request_with_workspace(
    [{"path": "src/hello.py", "operation": "create", "content": "print('hello')"}],
    ws4,
)

s, b4 = POST(f"/execution-requests/{ids4['request_id']}/execute")
check("200 status", s == 200, f"status={s}")
check("status completed", b4.get("status") == "completed", f"got {b4.get('status')}")
check("mode real_run", b4.get("mode") == "real_run", f"got {b4.get('mode')}")
check("is_new true", b4.get("is_new") is True, f"got {b4.get('is_new')}")
check("has id", "id" in b4)
check("has result_data", "result_data" in b4)

# Verify file on disk
created_file = os.path.join(ws4, "src", "hello.py")
check("file exists on disk", os.path.exists(created_file))
if os.path.exists(created_file):
    with open(created_file, "r", encoding="utf-8") as f:
        check("file content correct", f.read() == "print('hello')")
else:
    check("file content correct", False, "file not found")

# Check result_data structure
rd = b4.get("result_data", {})
check("result_data has file_results", "file_results" in rd)
if rd.get("file_results"):
    fr = rd["file_results"][0]
    check("file_result has after_hash", fr.get("after_hash") is not None)


# ══════════════════════════════════════════════════════════════
# S5: file_modify success → 200 + completed + content updated
# ══════════════════════════════════════════════════════════════
print("\n[S5]  file_modify success → 200 + completed")

ws5 = tempfile.mkdtemp(prefix="7a3s5_")
os.makedirs(os.path.join(ws5, "src"), exist_ok=True)
with open(os.path.join(ws5, "src", "config.py"), "w", encoding="utf-8") as f:
    f.write("old_value = True")

ids5 = _build_confirmed_request_with_workspace(
    [{"path": "src/config.py", "operation": "modify", "content": "new_value = False"}],
    ws5,
)

s, b = POST(f"/execution-requests/{ids5['request_id']}/execute")
check("200 status", s == 200, f"status={s}")
check("status completed", b.get("status") == "completed")

with open(os.path.join(ws5, "src", "config.py"), "r", encoding="utf-8") as f:
    check("content updated", f.read() == "new_value = False")

rd = b.get("result_data", {})
if rd.get("file_results"):
    fr = rd["file_results"][0]
    check("before_hash present", fr.get("before_hash") is not None)


# ══════════════════════════════════════════════════════════════
# S6: Idempotent repeat → 200 + is_new=false + same id
# ══════════════════════════════════════════════════════════════
print("\n[S6]  Idempotent repeat → 200 + is_new=false")

s2, b2 = POST(f"/execution-requests/{ids4['request_id']}/execute")
check("200 on repeat", s2 == 200, f"status={s2}")
check("is_new false", b2.get("is_new") is False, f"got {b2.get('is_new')}")
check("same result id", b2.get("id") == b4.get("id"),
      f"first={b4.get('id')}, second={b2.get('id')}")


# ══════════════════════════════════════════════════════════════
# S7: Fail-fast → 200 + failed + stopped_at
# ══════════════════════════════════════════════════════════════
print("\n[S7]  Fail-fast → 200 + failed + stopped_at")

ws7 = tempfile.mkdtemp(prefix="7a3s7_")
# Don't create src/ dir → parent missing → fail-fast

ids7 = _build_confirmed_request_with_workspace(
    [{"path": "src/missing_parent.py", "operation": "create", "content": "x"}],
    ws7,
)

s, b = POST(f"/execution-requests/{ids7['request_id']}/execute")
check("200 status", s == 200, f"status={s}")
check("status failed", b.get("status") == "failed", f"got {b.get('status')}")

rd = b.get("result_data", {})
check("stopped_at present", rd.get("stopped_at") is not None, f"rd={rd}")
check("stop_reason present", rd.get("stop_reason") is not None)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7A execute API: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
