#!/usr/bin/env python3
"""Phase 6F-A Step 3 acceptance: dry-run API endpoint.

Tests POST /api/execution-requests/{request_id}/dry-run
against a running server.

Usage:
    set TEST_API_BASE=http://127.0.0.1:9800/api
    python tests/phase6fa_api_acceptance.py
"""

import json
import os
import sys
import uuid as _uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

# ── Add runtime to sys.path for service-level imports ────────────
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")

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


# ── Helper: build full chain via orchestrate + DB helpers ────────
def _build_chain(*, confirm: bool = True, reject: bool = False) -> dict:
    """Build project -> task -> orchestrate -> approve -> freeze -> request.

    If confirm=True, also confirms the request.
    If reject=True, rejects the request instead.
    Returns dict with all IDs.
    """
    from database import get_connection
    from agents.snapshot_service import freeze_snapshot
    from agents.execution_request_service import (
        create_execution_request,
        update_execution_request_status,
    )

    pid = f"6fa-api-{_uuid.uuid4().hex[:8]}"
    s, proj = POST("/projects", {"name": pid, "local_repo_path": f"/tmp/{pid}"})
    project_id = proj["id"]

    s, task = POST(f"/projects/{project_id}/tasks", {"title": f"6FA task {pid}"})
    task_id = task["id"]

    # Orchestrate
    POST(f"/tasks/{task_id}/orchestrate")

    # Get proposal
    _, props_resp = GET(f"/tasks/{task_id}/proposals")
    proposals = props_resp.get("proposals", [])
    if not proposals:
        raise RuntimeError("No proposals created by orchestrate")
    proposal_id = proposals[0]["id"]

    # Approve proposal + create approval_request via DB
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE execution_proposals SET status = 'approved' WHERE id = ?",
            (proposal_id,),
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

    # Freeze snapshot
    snapshot = freeze_snapshot(proposal_id)
    snapshot_id = snapshot["id"]
    content_hash = snapshot["content_hash"]

    # Create execution request
    req = create_execution_request(snapshot_id)
    request_id = req["id"]

    # Confirm or reject
    if confirm:
        update_execution_request_status(request_id, "confirmed")
    elif reject:
        update_execution_request_status(request_id, "rejected")

    return {
        "project_id": project_id,
        "task_id": task_id,
        "proposal_id": proposal_id,
        "approval_id": approval_id,
        "snapshot_id": snapshot_id,
        "content_hash": content_hash,
        "request_id": request_id,
    }


# ══════════════════════════════════════════════════════════════════
# Section 1: request not found -> 404
# ══════════════════════════════════════════════════════════════════
print("\n[S1]  request not found -> 404")
s, body = POST("/execution-requests/nonexistent-id-xyz/dry-run")
check("404 on missing request", s == 404, f"got {s}")


# ══════════════════════════════════════════════════════════════════
# Section 2: request not confirmed -> 409
# ══════════════════════════════════════════════════════════════════
print("\n[S2]  request not confirmed -> 409")

ids_requested = _build_chain(confirm=False)
s, body = POST(f"/execution-requests/{ids_requested['request_id']}/dry-run")
check("409 on requested status", s == 409, f"got {s}")

ids_rejected = _build_chain(confirm=False, reject=True)
s, body = POST(f"/execution-requests/{ids_rejected['request_id']}/dry-run")
check("409 on rejected status", s == 409, f"got {s}")


# ══════════════════════════════════════════════════════════════════
# Section 3: confirmed request -> 200 + result
# ══════════════════════════════════════════════════════════════════
print("\n[S3]  confirmed request -> 200 + dry-run result")
ids = _build_chain(confirm=True)

s, result = POST(f"/execution-requests/{ids['request_id']}/dry-run")
check("200 on confirmed request", s == 200, f"got {s}")
check("has id", bool(result.get("id")))
check("execution_request_id matches", result.get("execution_request_id") == ids["request_id"])
check("task_id matches", result.get("task_id") == ids["task_id"])
check("snapshot_id matches", result.get("snapshot_id") == ids["snapshot_id"])
check("status is completed", result.get("status") == "completed")
check("has created_at", bool(result.get("created_at")))

# Top-level snapshot_content_hash
check("top-level snapshot_content_hash present",
      bool(result.get("snapshot_content_hash")),
      f"keys={list(result.keys())}")

# result_data is an object (not JSON string)
rd = result.get("result_data", {})
check("result_data is dict", isinstance(rd, dict), f"type={type(rd).__name__}")
check("result_data.mode is dry_run", rd.get("mode") == "dry_run")
check("result_data.snapshot_content_hash", bool(rd.get("snapshot_content_hash")))
check("result_data has planned_file_actions", isinstance(rd.get("planned_file_actions"), list))
check("result_data has planned_command_actions", isinstance(rd.get("planned_command_actions"), list))
check("result_data has warnings", isinstance(rd.get("warnings"), list))
check("result_data has summary", "summary" in rd)


# ══════════════════════════════════════════════════════════════════
# Section 4: idempotent on repeat -> 200 + same id
# ══════════════════════════════════════════════════════════════════
print("\n[S4]  idempotent on repeat call")
s2, result2 = POST(f"/execution-requests/{ids['request_id']}/dry-run")
check("200 on repeat", s2 == 200, f"got {s2}")
check("same id on repeat", result2.get("id") == result.get("id"))
check("same status on repeat", result2.get("status") == result.get("status"))


# ══════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  6F-A API: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
