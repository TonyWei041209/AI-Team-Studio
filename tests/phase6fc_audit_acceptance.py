#!/usr/bin/env python3
"""Phase 6F-C acceptance: execution_result events in audit trail.

Tests that dry-run results appear correctly in the audit trail API.

Usage:
    set TEST_API_BASE=http://127.0.0.1:9800/api
    python tests/phase6fc_audit_acceptance.py
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


# ── Helper: build full chain via DB helpers ────────────────────
def _build_chain_with_dryrun(*, fail: bool = False) -> dict:
    """Build full chain + dry-run result.

    If fail=True, manually sets result status to 'failed'.
    """
    from database import get_connection
    from agents.snapshot_service import freeze_snapshot
    from agents.execution_request_service import (
        create_execution_request,
        update_execution_request_status,
    )
    from agents.execution_result_service import run_dry_execution

    pid = f"6fc-{_uuid.uuid4().hex[:8]}"
    s, proj = POST("/projects", {"name": pid, "local_repo_path": f"/tmp/{pid}"})
    project_id = proj["id"]

    s, task = POST(f"/projects/{project_id}/tasks", {"title": f"6FC task {pid}"})
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

    # Create execution request
    req = create_execution_request(snapshot_id)
    request_id = req["id"]

    # Confirm
    update_execution_request_status(request_id, "confirmed")

    # Run dry-run
    result = run_dry_execution(request_id)
    result_id = result["id"]

    # If fail requested, manually update status
    if fail:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE execution_results SET status = 'failed' WHERE id = ?",
                (result_id,),
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "project_id": project_id,
        "task_id": task_id,
        "proposal_id": proposal_id,
        "approval_id": approval_id,
        "snapshot_id": snapshot_id,
        "request_id": request_id,
        "result_id": result_id,
    }


def _build_chain_no_dryrun() -> dict:
    """Build chain up to confirmed request but NO dry-run."""
    from database import get_connection
    from agents.snapshot_service import freeze_snapshot
    from agents.execution_request_service import (
        create_execution_request,
        update_execution_request_status,
    )

    pid = f"6fc-nd-{_uuid.uuid4().hex[:8]}"
    s, proj = POST("/projects", {"name": pid, "local_repo_path": f"/tmp/{pid}"})
    project_id = proj["id"]

    s, task = POST(f"/projects/{project_id}/tasks", {"title": f"6FC no-dr {pid}"})
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

    return {"task_id": task_id, "request_id": req["id"]}


# ══════════════════════════════════════════════════════════════════
# Section 1: completed result appears in audit trail
# ══════════════════════════════════════════════════════════════════
print("\n[S1]  completed result in audit trail")
ids = _build_chain_with_dryrun(fail=False)
s, trail = GET(f"/tasks/{ids['task_id']}/audit-trail")
check("200 on audit trail", s == 200, f"got {s}")

events = trail.get("events", [])
er_events = [e for e in events if e["object_type"] == "execution_result"]
check("has execution_result event", len(er_events) == 1, f"count={len(er_events)}")

if er_events:
    ev = er_events[0]
    check("event_type is execution_result:completed", ev["event_type"] == "execution_result:completed")
    check("object_id matches result_id", ev["object_id"] == ids["result_id"])
    check("status is completed", ev["status"] == "completed")
    check("summary contains 'Dry-run completed'", "Dry-run completed" in ev.get("summary", ""))
    check("summary contains file action count", "file action" in ev.get("summary", ""))
    check("summary contains command action count", "command action" in ev.get("summary", ""))

    # related_ids
    ri = ev.get("related_ids", {})
    check("related_ids has task_id", ri.get("task_id") == ids["task_id"])
    check("related_ids has execution_request_id", ri.get("execution_request_id") == ids["request_id"])
    check("related_ids has snapshot_id", ri.get("snapshot_id") == ids["snapshot_id"])

    # detail
    det = ev.get("detail", {})
    check("detail.mode is dry_run", det.get("mode") == "dry_run")
    check("detail.warnings_count is int", isinstance(det.get("warnings_count"), int))

    # 8 top-level keys
    REQUIRED_KEYS = {"event_type", "object_type", "object_id", "status", "timestamp", "summary", "related_ids", "detail"}
    check("8 required keys", set(ev.keys()) == REQUIRED_KEYS, f"keys={set(ev.keys())}")


# ══════════════════════════════════════════════════════════════════
# Section 2: failed result appears in audit trail
# ══════════════════════════════════════════════════════════════════
print("\n[S2]  failed result in audit trail")
ids_fail = _build_chain_with_dryrun(fail=True)
s, trail_f = GET(f"/tasks/{ids_fail['task_id']}/audit-trail")
check("200 on failed audit trail", s == 200)

events_f = trail_f.get("events", [])
er_events_f = [e for e in events_f if e["object_type"] == "execution_result"]
check("has execution_result event (failed)", len(er_events_f) == 1)

if er_events_f:
    ev_f = er_events_f[0]
    check("event_type is execution_result:failed", ev_f["event_type"] == "execution_result:failed")
    check("status is failed", ev_f["status"] == "failed")
    check("summary contains 'Dry-run failed'", "Dry-run failed" in ev_f.get("summary", ""))


# ══════════════════════════════════════════════════════════════════
# Section 3: no execution_result -> no event
# ══════════════════════════════════════════════════════════════════
print("\n[S3]  no execution_result -> no event")
ids_no = _build_chain_no_dryrun()
s, trail_no = GET(f"/tasks/{ids_no['task_id']}/audit-trail")
check("200 on no-result trail", s == 200)

events_no = trail_no.get("events", [])
er_events_no = [e for e in events_no if e["object_type"] == "execution_result"]
check("no execution_result events", len(er_events_no) == 0, f"count={len(er_events_no)}")


# ══════════════════════════════════════════════════════════════════
# Section 4: ordering — result after execution_request
# ══════════════════════════════════════════════════════════════════
print("\n[S4]  ordering: result after execution_request")
# Reuse ids from S1
event_types = [e["event_type"] for e in events]
# Find positions
req_created_idx = None
req_finalized_idx = None
result_idx = None
for i, et in enumerate(event_types):
    if et == "execution_request:created" and req_created_idx is None:
        req_created_idx = i
    if et == "execution_request:finalized" and req_finalized_idx is None:
        req_finalized_idx = i
    if et.startswith("execution_result:") and result_idx is None:
        result_idx = i

check("result after request:created", result_idx is not None and req_created_idx is not None and result_idx > req_created_idx,
      f"result={result_idx} req_created={req_created_idx}")
check("result after request:finalized", result_idx is not None and req_finalized_idx is not None and result_idx > req_finalized_idx,
      f"result={result_idx} req_finalized={req_finalized_idx}")


# ══════════════════════════════════════════════════════════════════
# Section 5: total event count for full chain
# ══════════════════════════════════════════════════════════════════
print("\n[S5]  total event count for full chain")
# Full chain: proposal:created, approval:decided, snapshot:frozen,
# execution_request:created, execution_request:finalized, execution_result:completed
# = 6 events
check("6 events in full chain", trail["count"] == 6, f"count={trail['count']}")


# ══════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  6F-C audit: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
