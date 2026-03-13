#!/usr/bin/env python3
"""Phase 6G-A Step 2 acceptance: action-plan read-only API.

Tests GET /api/execution-requests/{request_id}/action-plan endpoint.

Usage:
    set TEST_API_BASE=http://127.0.0.1:9800/api
    python tests/phase6ga_api_acceptance.py
"""

import json
import os
import sys
import uuid as _uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

# ── Add runtime to sys.path for service-level imports ────────
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


# ── Helper: build confirmed execution request ────────────────
def _build_confirmed_request() -> dict:
    """Build full chain up to confirmed execution request."""
    from database import get_connection
    from agents.snapshot_service import freeze_snapshot
    from agents.execution_request_service import (
        create_execution_request,
        update_execution_request_status,
    )

    pid = f"6ga-{_uuid.uuid4().hex[:8]}"
    s, proj = POST("/projects", {"name": pid, "local_repo_path": f"/tmp/{pid}"})
    project_id = proj["id"]

    s, task = POST(f"/projects/{project_id}/tasks", {"title": f"6GA task {pid}"})
    task_id = task["id"]

    POST(f"/tasks/{task_id}/orchestrate")

    _, props_resp = GET(f"/tasks/{task_id}/proposals")
    proposals = props_resp.get("proposals", [])
    proposal_id = proposals[0]["id"]

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

    snapshot = freeze_snapshot(proposal_id)
    req = create_execution_request(snapshot["id"])
    update_execution_request_status(req["id"], "confirmed")

    return {
        "project_id": project_id,
        "task_id": task_id,
        "proposal_id": proposal_id,
        "snapshot_id": snapshot["id"],
        "snapshot_content_hash": snapshot["content_hash"],
        "request_id": req["id"],
    }


# ══════════════════════════════════════════════════════════════
# S1: request not found → 404
# ══════════════════════════════════════════════════════════════
print("\n[S1]  request not found → 404")
s, body = GET(f"/execution-requests/{_uuid.uuid4()}/action-plan")
check("404 on missing request", s == 404, f"got {s}")


# ══════════════════════════════════════════════════════════════
# S2: snapshot missing → 409
# ══════════════════════════════════════════════════════════════
print("\n[S2]  snapshot missing → 409")

# Build a confirmed request, then delete the snapshot to simulate missing
from database import get_connection

ids_broken = _build_confirmed_request()
conn = get_connection()
try:
    # Temporarily disable FK checks to remove snapshot
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "DELETE FROM execution_snapshots WHERE id = ?",
        (ids_broken["snapshot_id"],),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
finally:
    conn.close()

s, body = GET(f"/execution-requests/{ids_broken['request_id']}/action-plan")
check("409 on missing snapshot", s == 409, f"got {s}")


# ══════════════════════════════════════════════════════════════
# S3: normal action plan → 200
# ══════════════════════════════════════════════════════════════
print("\n[S3]  normal action plan → 200")
ids = _build_confirmed_request()
s, plan = GET(f"/execution-requests/{ids['request_id']}/action-plan")
check("200 on valid request", s == 200, f"got {s}")
check("has execution_request_id", plan.get("execution_request_id") == ids["request_id"])
check("has snapshot_id", plan.get("snapshot_id") == ids["snapshot_id"])
check("has snapshot_content_hash", plan.get("snapshot_content_hash") == ids["snapshot_content_hash"])
check("has summary", isinstance(plan.get("summary"), str) and "action" in plan["summary"])
check("has actions list", isinstance(plan.get("actions"), list))
check("has overall_risk", plan.get("overall_risk") in ("safe", "low", "high", "critical"))
check("has has_denied", isinstance(plan.get("has_denied"), bool))
check("has needs_confirmation_count", isinstance(plan.get("needs_confirmation_count"), int))


# ══════════════════════════════════════════════════════════════
# S4: action structure check
# ══════════════════════════════════════════════════════════════
print("\n[S4]  action structure check")
actions = plan.get("actions", [])
check("actions non-empty", len(actions) > 0, f"count={len(actions)}")

if actions:
    a0 = actions[0]
    check("action has type", "type" in a0)
    check("action has target", "target" in a0)
    check("action has risk_level", a0.get("risk_level") in ("safe", "low", "high", "critical"))
    check("action has policy_decision", a0.get("policy_decision") in ("allow", "deny", "needs_confirmation"))
    check("action has reason", isinstance(a0.get("reason"), str))
    check("action has params", isinstance(a0.get("params"), dict))


# ══════════════════════════════════════════════════════════════
# S5: idempotent — same result on repeat
# ══════════════════════════════════════════════════════════════
print("\n[S5]  idempotent repeat call")
s2, plan2 = GET(f"/execution-requests/{ids['request_id']}/action-plan")
check("200 on repeat", s2 == 200)
check("same summary", plan2.get("summary") == plan.get("summary"))
check("same action count", len(plan2.get("actions", [])) == len(actions))


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  6G-A API: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
