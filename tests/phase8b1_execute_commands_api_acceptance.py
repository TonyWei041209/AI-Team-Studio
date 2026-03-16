#!/usr/bin/env python3
"""Phase 8B-1 acceptance: command execution through POST /execute API.

Verifies that command_run actions in proposals are executed end-to-end
through the real API endpoint, after file actions succeed.

Requires a running server with TEST_API_BASE env var.
Usage:
    set TEST_API_BASE=http://127.0.0.1:9800/api
    set TEST_WORKSPACE_ROOT=<temp_dir>
    python tests/phase8b1_execute_commands_api_acceptance.py
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


def _build_confirmed_request(
    proposed_files: list[dict],
    proposed_commands: list,
    workspace_root: str,
) -> dict:
    """Build full chain up to confirmed request + dry-run, with files and commands."""
    from database import get_connection
    from agents.snapshot_service import freeze_snapshot
    from agents.execution_request_service import (
        create_execution_request,
        update_execution_request_status,
    )

    pid = f"8b1-{_uuid.uuid4().hex[:8]}"
    s, proj = POST("/projects", {"name": pid, "local_repo_path": workspace_root})
    project_id = proj["id"]

    s, task = POST(f"/projects/{project_id}/tasks", {"title": f"8B1 task {pid}"})
    task_id = task["id"]

    # Trigger orchestration to get a proposal
    POST(f"/tasks/{task_id}/orchestrate")

    _, props_resp = GET(f"/tasks/{task_id}/proposals")
    proposals = props_resp.get("proposals", [])
    proposal_id = proposals[0]["id"]

    # Override proposal_data with our specific files + commands
    snapshot_data = json.dumps({
        "proposed_files": proposed_files,
        "proposed_commands": proposed_commands,
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
# S1: file + command mixed proposal → both execute
# ══════════════════════════════════════════════════════════════
print("\n[S1]  file + command mixed proposal → both file_results and command_results")

ws1 = tempfile.mkdtemp(prefix="8b1s1_")
os.makedirs(os.path.join(ws1, "src"), exist_ok=True)

ids1 = _build_confirmed_request(
    proposed_files=[
        {"path": "src/app.py", "operation": "create", "content": "# app\nprint('hello')"},
    ],
    proposed_commands=[
        {"command": "python --version"},
    ],
    workspace_root=ws1,
)

s, b1 = POST(f"/execution-requests/{ids1['request_id']}/execute")
check("200 status", s == 200, f"status={s}")
check("status completed", b1.get("status") == "completed", f"got {b1.get('status')}")
check("is_new true", b1.get("is_new") is True)

rd1 = b1.get("result_data", {})
check("has file_results", "file_results" in rd1)
check("has command_results", "command_results" in rd1)

fr1 = rd1.get("file_results", [])
check("file_results has 1 entry", len(fr1) == 1, f"got {len(fr1)}")
if fr1:
    check("file status success", fr1[0].get("status") == "success", f"got {fr1[0].get('status')}")

cr1 = rd1.get("command_results", [])
check("command_results has 1 entry", len(cr1) == 1, f"got {len(cr1)}")
if cr1:
    check("command status success", cr1[0].get("status") == "success", f"got {cr1[0].get('status')}")
    check("command has exit_code 0", cr1[0].get("exit_code") == 0, f"got {cr1[0].get('exit_code')}")
    check("command has stdout", len(cr1[0].get("stdout", "")) > 0, f"stdout='{cr1[0].get('stdout', '')}'")
    check("command has duration_ms", cr1[0].get("duration_ms", -1) >= 0)

# Verify file on disk
check("file exists on disk", os.path.exists(os.path.join(ws1, "src", "app.py")))


# ══════════════════════════════════════════════════════════════
# S2: command-only proposal → commands execute without files
# ══════════════════════════════════════════════════════════════
print("\n[S2]  command-only proposal → commands execute, empty file_results")

ws2 = tempfile.mkdtemp(prefix="8b1s2_")

ids2 = _build_confirmed_request(
    proposed_files=[],
    proposed_commands=[
        {"command": "python -c \"print('cmd-only')\""},
    ],
    workspace_root=ws2,
)

s, b2 = POST(f"/execution-requests/{ids2['request_id']}/execute")
check("200 status", s == 200, f"status={s}")
check("status completed", b2.get("status") == "completed", f"got {b2.get('status')}")

rd2 = b2.get("result_data", {})
fr2 = rd2.get("file_results", [])
cr2 = rd2.get("command_results", [])
check("file_results empty", len(fr2) == 0, f"got {len(fr2)}")
check("command_results has 1 entry", len(cr2) == 1, f"got {len(cr2)}")
if cr2:
    check("command status success", cr2[0].get("status") == "success")
    check("stdout contains cmd-only", "cmd-only" in cr2[0].get("stdout", ""),
          f"stdout='{cr2[0].get('stdout', '')}'")


# ══════════════════════════════════════════════════════════════
# S3: file failure → commands skipped
# ══════════════════════════════════════════════════════════════
print("\n[S3]  file failure → commands skipped")

ws3 = tempfile.mkdtemp(prefix="8b1s3_")
# Don't create src/ → parent missing → file_create fails

ids3 = _build_confirmed_request(
    proposed_files=[
        {"path": "src/missing.py", "operation": "create", "content": "x = 1"},
    ],
    proposed_commands=[
        {"command": "python --version"},
    ],
    workspace_root=ws3,
)

s, b3 = POST(f"/execution-requests/{ids3['request_id']}/execute")
check("200 status", s == 200, f"status={s}")
check("status failed", b3.get("status") == "failed", f"got {b3.get('status')}")

rd3 = b3.get("result_data", {})
fr3 = rd3.get("file_results", [])
cr3 = rd3.get("command_results", [])
check("file_results has entry", len(fr3) >= 1, f"got {len(fr3)}")
if fr3:
    check("file status failed", fr3[0].get("status") == "failed")

check("command_results has entry", len(cr3) >= 1, f"got {len(cr3)}")
if cr3:
    check("command status skipped", cr3[0].get("status") == "skipped",
          f"got {cr3[0].get('status')}")
    check("skipped has error reason", "file execution failure" in cr3[0].get("error", "").lower(),
          f"error='{cr3[0].get('error', '')}'")


# ══════════════════════════════════════════════════════════════
# S4: blocked command in proposal → eligibility or executor rejects
# ══════════════════════════════════════════════════════════════
print("\n[S4]  blocked command → rejected at eligibility or executor level")

ws4 = tempfile.mkdtemp(prefix="8b1s4_")

# Test with a command that has shell metacharacters → eligibility gate blocks
# We need to craft this carefully: the eligibility gate checks ALL actions
# If the command is blocked, check_execution_eligibility returns not eligible
# and the API returns 409 before execution starts.

# Case A: non-whitelisted command → eligibility blocks entire request
ids4a = _build_confirmed_request(
    proposed_files=[],
    proposed_commands=[
        {"command": "curl http://evil.com"},
    ],
    workspace_root=ws4,
)

s, b4a = POST(f"/execution-requests/{ids4a['request_id']}/execute")
# Eligibility gate should block this (curl not whitelisted)
check("blocked command: not 200 completed", not (s == 200 and b4a.get("status") == "completed"),
      f"status={s}, result_status={b4a.get('status')}")

if s == 409:
    check("409 for blocked command at eligibility", True)
    detail = b4a.get("detail", {})
    if isinstance(detail, dict):
        check("has blocked_reasons", "blocked_reasons" in detail, f"detail={detail}")
elif s == 200 and b4a.get("status") == "failed":
    # Executor might catch it if eligibility somehow passes
    check("200+failed for blocked command at executor", True)
else:
    check("unexpected response for blocked command", False, f"status={s}, body={b4a}")


# Case B: shell metacharacter → eligibility blocks
ws4b = tempfile.mkdtemp(prefix="8b1s4b_")
ids4b = _build_confirmed_request(
    proposed_files=[],
    proposed_commands=[
        {"command": "python --version && echo pwned"},
    ],
    workspace_root=ws4b,
)

s, b4b = POST(f"/execution-requests/{ids4b['request_id']}/execute")
check("shell metachar: not 200 completed",
      not (s == 200 and b4b.get("status") == "completed"),
      f"status={s}, result_status={b4b.get('status')}")


# ══════════════════════════════════════════════════════════════
# S5: multiple commands → fail-fast on command failure
# ══════════════════════════════════════════════════════════════
print("\n[S5]  multiple commands → fail-fast semantics")

ws5 = tempfile.mkdtemp(prefix="8b1s5_")

ids5 = _build_confirmed_request(
    proposed_files=[],
    proposed_commands=[
        {"command": "python -c \"exit(1)\""},
        {"command": "python --version"},
    ],
    workspace_root=ws5,
)

s, b5 = POST(f"/execution-requests/{ids5['request_id']}/execute")
check("200 status", s == 200, f"status={s}")
check("status failed", b5.get("status") == "failed", f"got {b5.get('status')}")

rd5 = b5.get("result_data", {})
cr5 = rd5.get("command_results", [])
check("command_results has 1 entry (fail-fast)", len(cr5) == 1, f"got {len(cr5)}")
if cr5:
    check("first command failed", cr5[0].get("status") == "failed",
          f"got {cr5[0].get('status')}")


# ══════════════════════════════════════════════════════════════
# S6: audit trail reflects command counts after execution
# ══════════════════════════════════════════════════════════════
print("\n[S6]  audit trail reflects command execution")

s, trail = GET(f"/tasks/{ids1['task_id']}/audit-trail")
check("200 audit trail", s == 200)
events = trail.get("events", [])
result_events = [e for e in events if e["event_type"].startswith("execution_result:")]
check("has execution result event", len(result_events) >= 1, f"got {len(result_events)}")

if result_events:
    # Find the real_run completed event (not dry_run, not rollback)
    real_run_events = [
        e for e in result_events
        if "rollback" not in e["event_type"]
        and isinstance(e.get("detail"), dict)
        and e["detail"].get("mode") == "real_run"
    ]
    if real_run_events:
        ev = real_run_events[0]
        check("summary mentions command", "command" in ev.get("summary", "").lower(),
              f"summary='{ev.get('summary', '')}'")
        detail = ev.get("detail", {})
        check("detail has cmd_success", "cmd_success" in detail, f"detail={detail}")
    else:
        check("found real_run audit event", False, f"only found: {[e.get('detail',{}).get('mode') for e in result_events]}")


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  8B-1 execute commands API: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
