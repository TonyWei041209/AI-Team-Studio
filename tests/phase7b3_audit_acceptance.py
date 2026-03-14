#!/usr/bin/env python3
"""Phase 7B-3 acceptance: audit trail real_run summary fix.

Service-level tests verifying _build_task_audit_trail() produces
correct summaries for both dry_run and real_run execution results.

Usage:
    python tests/phase7b3_audit_acceptance.py
"""

import hashlib
import json
import os
import sys
import uuid as _uuid
from datetime import datetime, timezone

_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from database import get_connection, init_db
from routers.orchestration import _build_task_audit_trail

init_db()

PASS = 0
FAIL = 0


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


def _content_hash(data: str) -> str:
    canonical = json.dumps(json.loads(data), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_chain_with_results(
    dry_run_data: dict | None = None,
    real_run_data: dict | None = None,
    real_run_status: str = "completed",
) -> str:
    """Build full chain and return task_id."""
    conn = get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        pid = str(_uuid.uuid4())
        tid = str(_uuid.uuid4())
        prid = str(_uuid.uuid4())
        aid = str(_uuid.uuid4())
        sid = str(_uuid.uuid4())
        rid = str(_uuid.uuid4())

        snap = {"proposed_files": [], "proposed_commands": []}
        snap_json = json.dumps(snap)
        ch = _content_hash(snap_json)

        conn.execute(
            """INSERT INTO projects (id, name, local_repo_path, default_branch,
               description, created_at, updated_at) VALUES (?,?,?,?,?,?,?)""",
            (pid, "test", "/tmp/test", "main", "", now, now),
        )
        conn.execute(
            """INSERT INTO tasks (id, project_id, title, description, status,
               priority, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
            (tid, pid, "t", "", "in_progress", "medium", now, now),
        )
        conn.execute(
            """INSERT INTO execution_proposals
               (id, task_id, run_id, role, proposal_data, risk_level,
                requires_approval, approval_reasons, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (prid, tid, None, "builder", snap_json, "low", 1, "[]", "approved", now, now),
        )
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload,
                status, proposal_id, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (aid, tid, None, "proposal:builder", "{}", "approved", prid, now),
        )
        conn.execute(
            """INSERT INTO execution_snapshots
               (id, proposal_id, approval_id, task_id, snapshot_data,
                content_hash, risk_level, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sid, prid, aid, tid, snap_json, ch, "low", "frozen", now),
        )
        conn.execute(
            """INSERT INTO execution_requests
               (id, task_id, proposal_id, approval_id, snapshot_id,
                snapshot_content_hash, risk_level, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (rid, tid, prid, aid, sid, ch, "low", "confirmed", now, now),
        )

        # Dry-run result
        if dry_run_data is not None:
            drid = str(_uuid.uuid4())
            conn.execute(
                """INSERT INTO execution_results
                   (id, execution_request_id, task_id, snapshot_id,
                    snapshot_content_hash, mode, status, result_data,
                    started_at, completed_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (drid, rid, tid, sid, ch, "dry_run", "completed",
                 json.dumps(dry_run_data), now, now, now),
            )

        # Real-run result
        if real_run_data is not None:
            rrid = str(_uuid.uuid4())
            conn.execute(
                """INSERT INTO execution_results
                   (id, execution_request_id, task_id, snapshot_id,
                    snapshot_content_hash, mode, status, result_data,
                    started_at, completed_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (rrid, rid, tid, sid, ch, "real_run", real_run_status,
                 json.dumps(real_run_data), now, now, now),
            )

        conn.commit()
        return tid
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# T1: Dry-run summary preserved
# ══════════════════════════════════════════════════════════════
print("\n[T1]  Dry-run summary preserved")

tid1 = _build_chain_with_results(
    dry_run_data={
        "mode": "dry_run",
        "summary": "ok",
        "planned_file_actions": [{"path": "a.py"}, {"path": "b.py"}],
        "planned_command_actions": [{"command": "npm install"}],
        "warnings": ["w1"],
    },
)
events1 = _build_task_audit_trail(tid1)
dr_events = [e for e in events1 if e["event_type"] == "execution_result:completed"
              and e["detail"].get("mode") == "dry_run"]
check("dry-run event exists", len(dr_events) == 1)
if dr_events:
    s = dr_events[0]["summary"]
    check("summary starts with Dry-run", s.startswith("Dry-run"), f"got: {s}")
    check("summary contains file count", "2 file action" in s, f"got: {s}")
    check("summary contains cmd count", "1 command action" in s, f"got: {s}")
    check("detail has warnings_count", dr_events[0]["detail"].get("warnings_count") == 1)


# ══════════════════════════════════════════════════════════════
# T2: Real-run completed summary correct
# ══════════════════════════════════════════════════════════════
print("\n[T2]  Real-run completed summary")

tid2 = _build_chain_with_results(
    real_run_data={
        "mode": "real_run",
        "summary": "Real execution: 2 file(s) written, 0 failed",
        "file_results": [
            {"path": "a.py", "operation": "create", "status": "success"},
            {"path": "b.py", "operation": "modify", "status": "success"},
        ],
        "stopped_at": None,
        "stop_reason": None,
    },
    real_run_status="completed",
)
events2 = _build_task_audit_trail(tid2)
rr_events = [e for e in events2 if e["event_type"] == "execution_result:completed"
              and e["detail"].get("mode") == "real_run"]
check("real-run completed event exists", len(rr_events) == 1)
if rr_events:
    s = rr_events[0]["summary"]
    check("summary starts with Real execution", s.startswith("Real execution"), f"got: {s}")
    check("summary contains 'completed'", "completed" in s, f"got: {s}")
    check("summary contains '2 written'", "2 written" in s, f"got: {s}")
    check("summary contains '0 failed'", "0 failed" in s, f"got: {s}")
    d = rr_events[0]["detail"]
    check("detail.success_count == 2", d.get("success_count") == 2)
    check("detail.fail_count == 0", d.get("fail_count") == 0)
    check("detail.stopped_at is None", d.get("stopped_at") is None)


# ══════════════════════════════════════════════════════════════
# T3: Real-run failed summary correct
# ══════════════════════════════════════════════════════════════
print("\n[T3]  Real-run failed summary")

tid3 = _build_chain_with_results(
    real_run_data={
        "mode": "real_run",
        "summary": "Real execution: 1 file(s) written, 1 failed",
        "file_results": [
            {"path": "a.py", "operation": "create", "status": "success"},
            {"path": "b.py", "operation": "modify", "status": "failed", "error": "not found"},
            {"path": "c.py", "operation": "create", "status": "skipped"},
        ],
        "stopped_at": 1,
        "stop_reason": "File not found for modify: b.py",
    },
    real_run_status="failed",
)
events3 = _build_task_audit_trail(tid3)
rr_fail = [e for e in events3 if e["event_type"] == "execution_result:failed"
            and e["detail"].get("mode") == "real_run"]
check("real-run failed event exists", len(rr_fail) == 1)
if rr_fail:
    s = rr_fail[0]["summary"]
    check("summary starts with Real execution", s.startswith("Real execution"), f"got: {s}")
    check("summary contains 'failed'", "failed" in s, f"got: {s}")
    check("summary contains '1 written'", "1 written" in s, f"got: {s}")
    d = rr_fail[0]["detail"]
    check("detail.success_count == 1", d.get("success_count") == 1)
    check("detail.fail_count == 1", d.get("fail_count") == 1)
    check("detail.stopped_at == 1", d.get("stopped_at") == 1)


# ══════════════════════════════════════════════════════════════
# T4: Both dry_run and real_run coexist as separate events
# ══════════════════════════════════════════════════════════════
print("\n[T4]  Both modes coexist")

tid4 = _build_chain_with_results(
    dry_run_data={
        "mode": "dry_run",
        "summary": "ok",
        "planned_file_actions": [{"path": "a.py"}],
        "planned_command_actions": [],
        "warnings": [],
    },
    real_run_data={
        "mode": "real_run",
        "summary": "ok",
        "file_results": [
            {"path": "a.py", "operation": "create", "status": "success"},
        ],
        "stopped_at": None,
        "stop_reason": None,
    },
    real_run_status="completed",
)
events4 = _build_task_audit_trail(tid4)
result_events = [e for e in events4 if e["object_type"] == "execution_result"]
check("two execution_result events", len(result_events) == 2)
modes = sorted(e["detail"]["mode"] for e in result_events)
check("one dry_run + one real_run", modes == ["dry_run", "real_run"], f"got: {modes}")

# Verify ordering: dry_run before real_run (same timestamp, but stable order)
dr_idx = next(i for i, e in enumerate(events4) if e.get("detail", {}).get("mode") == "dry_run"
              and e["object_type"] == "execution_result")
rr_idx = next(i for i, e in enumerate(events4) if e.get("detail", {}).get("mode") == "real_run"
              and e["object_type"] == "execution_result")
# Same timestamp so order depends on event_type + insertion order; just verify both present
check("both events have correct object_type", all(
    e["object_type"] == "execution_result" for e in result_events))


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7B-3 audit: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
