#!/usr/bin/env python3
"""Phase 7 E2E: execute → rollback → audit integration.

Minimal end-to-end test covering the complete execution chain
with real temp files. No server needed.

Usage:
    python tests/phase7_e2e_execute_rollback.py
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.scoped_file_executor import execute_scoped_files
from agents.rollback_service import rollback_execution
from routers.orchestration import _build_task_audit_trail
from database import get_connection, init_db

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


# ══════════════════════════════════════════════════════════════
# Setup: build temp workspace + DB chain
# ══════════════════════════════════════════════════════════════
print("\n[Setup]  Building temp workspace and DB chain")

workspace = tempfile.mkdtemp(prefix="7e2e_")

# Create an existing file that will be modified
existing_py = os.path.join(workspace, "existing.py")
original_content = '# original\nprint("hello")\n'
with open(existing_py, "w", encoding="utf-8") as f:
    f.write(original_content)

snapshot_data = {
    "proposed_files": [
        {"path": "new_file.py", "action": "create", "content": "# new file\nprint('created')\n"},
        {"path": "existing.py", "action": "modify", "content": "# modified\nprint('modified')\n"},
    ],
    "proposed_commands": [],
}

# Normalise action → operation (executor uses "operation" key)
snapshot_data_norm = {
    "proposed_files": [
        {"path": "new_file.py", "operation": "create", "content": "# new file\nprint('created')\n"},
        {"path": "existing.py", "operation": "modify", "content": "# modified\nprint('modified')\n"},
    ],
    "proposed_commands": [],
}

snap_json = json.dumps(snapshot_data_norm)
ch = _content_hash(snap_json)

conn = get_connection()
now = datetime.now(timezone.utc).isoformat()
pid = str(_uuid.uuid4())
tid = str(_uuid.uuid4())
prid = str(_uuid.uuid4())
aid = str(_uuid.uuid4())
sid = str(_uuid.uuid4())
rid = str(_uuid.uuid4())
drid = str(_uuid.uuid4())

try:
    conn.execute(
        """INSERT INTO projects (id, name, local_repo_path, default_branch,
           description, created_at, updated_at) VALUES (?,?,?,?,?,?,?)""",
        (pid, f"test-e2e-{pid[:8]}", workspace, "main", "", now, now),
    )
    conn.execute(
        """INSERT INTO tasks (id, project_id, title, description, status,
           priority, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
        (tid, pid, "e2e task", "", "in_progress", "medium", now, now),
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
    # dry_run result required by eligibility gate
    dr_data = json.dumps({
        "mode": "dry_run", "summary": "ok",
        "planned_file_actions": [], "planned_command_actions": [],
        "warnings": [],
    })
    conn.execute(
        """INSERT INTO execution_results
           (id, execution_request_id, task_id, snapshot_id,
            snapshot_content_hash, mode, status, result_data,
            started_at, completed_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (drid, rid, tid, sid, ch, "dry_run", "completed", dr_data, now, now, now),
    )
    conn.commit()
    print("  [OK]   DB chain created")
finally:
    conn.close()


# ══════════════════════════════════════════════════════════════
# E2E-1: Execute creates real files
# ══════════════════════════════════════════════════════════════
print("\n[E2E-1]  Execute creates real files")

exec_result = None
try:
    exec_result = execute_scoped_files(rid, workspace)
    check("execute returns completed", exec_result.get("status") == "completed",
          f"got: {exec_result.get('status')}")
    check("new_file.py was created",
          os.path.exists(os.path.join(workspace, "new_file.py")))
    new_file_path = os.path.join(workspace, "new_file.py")
    if os.path.exists(new_file_path):
        with open(new_file_path, "r", encoding="utf-8") as f:
            content = f.read()
        check("new_file.py content correct",
              content == "# new file\nprint('created')\n",
              f"got: {repr(content)}")
    with open(existing_py, "r", encoding="utf-8") as f:
        modified_content = f.read()
    check("existing.py was modified",
          modified_content == "# modified\nprint('modified')\n",
          f"got: {repr(modified_content)}")
    real_run_result_id = exec_result.get("id")
except Exception as e:
    check("execute_scoped_files raised unexpectedly", False, str(e))
    real_run_result_id = None


# ══════════════════════════════════════════════════════════════
# E2E-2: Rollback restores files
# ══════════════════════════════════════════════════════════════
print("\n[E2E-2]  Rollback restores files")

rollback_result = None
if real_run_result_id:
    try:
        rollback_result = rollback_execution(real_run_result_id, workspace)
        check("rollback returns completed", rollback_result.get("status") == "completed",
              f"got: {rollback_result.get('status')}")
        check("new_file.py was removed",
              not os.path.exists(os.path.join(workspace, "new_file.py")))
        with open(existing_py, "r", encoding="utf-8") as f:
            restored_content = f.read()
        check("existing.py restored to original",
              restored_content == original_content,
              f"got: {repr(restored_content)}")
    except Exception as e:
        check("rollback_execution raised unexpectedly", False, str(e))
else:
    check("rollback skipped (no real_run result)", False, "E2E-1 failed")


# ══════════════════════════════════════════════════════════════
# E2E-3: Audit trail contains both events
# ══════════════════════════════════════════════════════════════
print("\n[E2E-3]  Audit trail contains execute + rollback events")

try:
    trail = _build_task_audit_trail(tid)
    exec_result_events = [
        e for e in trail if e.get("object_type") == "execution_result"
    ]
    check("at least 2 execution_result events",
          len(exec_result_events) >= 2,
          f"got {len(exec_result_events)}")

    modes = [e.get("detail", {}).get("mode") for e in exec_result_events]
    check("real_run event present", "real_run" in modes,
          f"modes found: {modes}")
    check("rollback event present", "rollback" in modes,
          f"modes found: {modes}")

    rollback_events = [e for e in exec_result_events
                       if e.get("detail", {}).get("mode") == "rollback"]
    if rollback_events:
        rb_summary = rollback_events[0].get("summary", "")
        check("rollback summary starts with 'Rollback'",
              rb_summary.startswith("Rollback"),
              f"got: {repr(rb_summary)}")

    real_run_events = [e for e in exec_result_events
                       if e.get("detail", {}).get("mode") == "real_run"]
    if real_run_events and rollback_events:
        real_run_idx = exec_result_events.index(real_run_events[0])
        rollback_idx = exec_result_events.index(rollback_events[0])
        check("real_run event appears before rollback event",
              real_run_idx < rollback_idx,
              f"real_run at {real_run_idx}, rollback at {rollback_idx}")
except Exception as e:
    check("_build_task_audit_trail raised unexpectedly", False, str(e))


# ══════════════════════════════════════════════════════════════
# Cleanup
# ══════════════════════════════════════════════════════════════
shutil.rmtree(workspace, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7 E2E execute→rollback→audit: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
