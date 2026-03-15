#!/usr/bin/env python3
"""Phase 7D-3 acceptance: rollback events in audit trail.

Service-level tests verifying _build_task_audit_trail() produces
correct events and summaries for rollback execution results.

Usage:
    python tests/phase7d3_rollback_audit_acceptance.py
"""

import hashlib
import json
import os
import sys
import uuid as _uuid
from datetime import datetime, timezone, timedelta

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
    rollback_data: dict | None = None,
    rollback_status: str = "completed",
) -> str:
    """Build full chain and return task_id."""
    conn = get_connection()
    try:
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = t0.isoformat()
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
            ts_dr = (t0 + timedelta(seconds=1)).isoformat()
            conn.execute(
                """INSERT INTO execution_results
                   (id, execution_request_id, task_id, snapshot_id,
                    snapshot_content_hash, mode, status, result_data,
                    started_at, completed_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (str(_uuid.uuid4()), rid, tid, sid, ch, "dry_run", "completed",
                 json.dumps(dry_run_data), ts_dr, ts_dr, ts_dr),
            )

        # Real-run result
        if real_run_data is not None:
            ts_rr = (t0 + timedelta(seconds=2)).isoformat()
            conn.execute(
                """INSERT INTO execution_results
                   (id, execution_request_id, task_id, snapshot_id,
                    snapshot_content_hash, mode, status, result_data,
                    started_at, completed_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (str(_uuid.uuid4()), rid, tid, sid, ch, "real_run", real_run_status,
                 json.dumps(real_run_data), ts_rr, ts_rr, ts_rr),
            )

        # Rollback result
        if rollback_data is not None:
            ts_rb = (t0 + timedelta(seconds=3)).isoformat()
            conn.execute(
                """INSERT INTO execution_results
                   (id, execution_request_id, task_id, snapshot_id,
                    snapshot_content_hash, mode, status, result_data,
                    started_at, completed_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (str(_uuid.uuid4()), rid, tid, sid, ch, "rollback", rollback_status,
                 json.dumps(rollback_data), ts_rb, ts_rb, ts_rb),
            )

        conn.commit()
        return tid
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# T1: Rollback completed → audit trail contains rollback event
# ══════════════════════════════════════════════════════════════
print("\n[T1]  Rollback completed event in audit trail")

tid1 = _build_chain_with_results(
    real_run_data={
        "mode": "real_run",
        "summary": "ok",
        "file_results": [
            {"path": "a.py", "operation": "create", "status": "success"},
            {"path": "b.py", "operation": "modify", "status": "success"},
        ],
    },
    real_run_status="completed",
    rollback_data={
        "mode": "rollback",
        "summary": "Rollback completed",
        "file_results": [
            {"path": "a.py", "operation": "unlink", "status": "deleted"},
            {"path": "b.py", "operation": "restore", "status": "restored"},
        ],
    },
    rollback_status="completed",
)
events1 = _build_task_audit_trail(tid1)
rb_events = [e for e in events1 if e["event_type"].startswith("execution_result:rollback_")]
check("rollback event exists", len(rb_events) == 1)
if rb_events:
    e = rb_events[0]
    check("event_type is rollback_completed",
          e["event_type"] == "execution_result:rollback_completed")


# ══════════════════════════════════════════════════════════════
# T2: Rollback completed summary correct
# ══════════════════════════════════════════════════════════════
print("\n[T2]  Rollback completed summary")

if rb_events:
    s = rb_events[0]["summary"]
    check("summary starts with 'Rollback completed'",
          s.startswith("Rollback completed"), f"got: {s}")
    check("summary contains '2 restored'", "2 restored" in s, f"got: {s}")
    check("summary contains '0 failed'", "0 failed" in s, f"got: {s}")


# ══════════════════════════════════════════════════════════════
# T3: Rollback failed summary correct
# ══════════════════════════════════════════════════════════════
print("\n[T3]  Rollback failed summary")

tid3 = _build_chain_with_results(
    real_run_data={
        "mode": "real_run",
        "summary": "ok",
        "file_results": [
            {"path": "a.py", "operation": "create", "status": "success"},
            {"path": "b.py", "operation": "modify", "status": "success"},
        ],
    },
    real_run_status="completed",
    rollback_data={
        "mode": "rollback",
        "summary": "Rollback failed",
        "file_results": [
            {"path": "a.py", "operation": "unlink", "status": "deleted"},
            {"path": "b.py", "operation": "restore", "status": "rollback_failed",
             "error": "permission denied"},
        ],
    },
    rollback_status="failed",
)
events3 = _build_task_audit_trail(tid3)
rb_fail = [e for e in events3 if e["event_type"] == "execution_result:rollback_failed"]
check("rollback failed event exists", len(rb_fail) == 1)
if rb_fail:
    s = rb_fail[0]["summary"]
    check("summary starts with 'Rollback failed'",
          s.startswith("Rollback failed"), f"got: {s}")
    check("summary contains '1 restored'", "1 restored" in s, f"got: {s}")
    check("summary contains '1 failed'", "1 failed" in s, f"got: {s}")


# ══════════════════════════════════════════════════════════════
# T4: Rollback event sorted after real_run
# ══════════════════════════════════════════════════════════════
print("\n[T4]  Rollback sorted after real_run")

# Re-use tid1 which has both real_run and rollback with distinct timestamps
result_events = [e for e in events1 if e["object_type"] == "execution_result"]
modes_in_order = [e["detail"]["mode"] for e in result_events]
check("real_run before rollback in order",
      modes_in_order.index("real_run") < modes_in_order.index("rollback"),
      f"got order: {modes_in_order}")


# ══════════════════════════════════════════════════════════════
# T5: detail contains restored/failed/skipped counts
# ══════════════════════════════════════════════════════════════
print("\n[T5]  Detail contains counts")

# Use tid1 (all success rollback)
rb1 = [e for e in events1 if e["event_type"].startswith("execution_result:rollback_")][0]
d = rb1["detail"]
check("detail.mode == rollback", d.get("mode") == "rollback")
check("detail.restored_count == 2", d.get("restored_count") == 2)
check("detail.failed_count == 0", d.get("failed_count") == 0)
check("detail.skipped_count == 0", d.get("skipped_count") == 0)

# Use tid3 (mixed rollback with skipped)
tid5 = _build_chain_with_results(
    real_run_data={
        "mode": "real_run",
        "summary": "ok",
        "file_results": [
            {"path": "a.py", "operation": "create", "status": "success"},
            {"path": "b.py", "operation": "modify", "status": "success"},
            {"path": "c.py", "operation": "modify", "status": "success"},
        ],
    },
    real_run_status="completed",
    rollback_data={
        "mode": "rollback",
        "summary": "Rollback failed",
        "file_results": [
            {"path": "a.py", "operation": "unlink", "status": "deleted"},
            {"path": "b.py", "operation": "restore", "status": "rollback_failed",
             "error": "permission denied"},
            {"path": "c.py", "operation": "restore", "status": "already_absent"},
        ],
    },
    rollback_status="failed",
)
events5 = _build_task_audit_trail(tid5)
rb5 = [e for e in events5 if e["event_type"].startswith("execution_result:rollback_")][0]
d5 = rb5["detail"]
check("mixed: restored_count == 1", d5.get("restored_count") == 1)
check("mixed: failed_count == 1", d5.get("failed_count") == 1)
check("mixed: skipped_count == 1", d5.get("skipped_count") == 1,
      f"got: {d5.get('skipped_count')}")


# ══════════════════════════════════════════════════════════════
# T6: No rollback result → no rollback event
# ══════════════════════════════════════════════════════════════
print("\n[T6]  No rollback → no rollback event")

tid6 = _build_chain_with_results(
    real_run_data={
        "mode": "real_run",
        "summary": "ok",
        "file_results": [
            {"path": "a.py", "operation": "create", "status": "success"},
        ],
    },
    real_run_status="completed",
    # no rollback_data
)
events6 = _build_task_audit_trail(tid6)
rb6 = [e for e in events6 if e["event_type"].startswith("execution_result:rollback_")]
check("no rollback events when no rollback result", len(rb6) == 0)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7D-3 rollback audit: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
