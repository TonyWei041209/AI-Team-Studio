#!/usr/bin/env python3
"""Phase 6F-A Step 2 acceptance: dry-run execution service.

Tests the service-level function run_dry_execution() directly
against a temporary SQLite database.  No HTTP server needed.

Usage:
    python tests/phase6fa_dry_run_acceptance.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone

# ── Bootstrap: put services/runtime on sys.path ─────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(PROJECT_ROOT, "services", "runtime")
sys.path.insert(0, RUNTIME_DIR)

# Isolated temp DB
_db_file = tempfile.mktemp(suffix=".db", prefix="6fa_dryrun_")
os.environ["RUNTIME_DB"] = _db_file

# Force database module to initialise fresh
import database  # noqa: E402

database.init_db()

from agents.execution_result_service import run_dry_execution  # noqa: E402


# ── Test infrastructure ─────────────────────────────────────────────
_pass = 0
_fail = 0


def check(label: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  [PASS] {label}")
    else:
        _fail += 1
        msg = f"  [FAIL] {label}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)


def _canonical_json(data: str) -> str:
    return json.dumps(json.loads(data), sort_keys=True, separators=(",", ":"))


def _content_hash(data: str) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _seed_full_chain(conn: sqlite3.Connection, *, request_status: str = "confirmed") -> dict:
    """Create a full object chain (project → task → proposal → approval → snapshot → request).
    Returns dict of all IDs.
    """
    now = datetime.now(timezone.utc).isoformat()
    project_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    proposal_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())
    snapshot_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    proposal_data = json.dumps({
        "summary": "Add logging utility",
        "proposed_files": [
            {"path": "src/logger.py", "operation": "create"},
            {"path": "src/main.py", "operation": "modify"},
        ],
        "proposed_commands": [
            {"command": "pip install structlog"},
            "python -m pytest tests/",
        ],
    })
    snap_data = proposal_data
    c_hash = _content_hash(snap_data)

    conn.execute(
        "INSERT INTO projects (id, name, local_repo_path, created_at, updated_at) VALUES (?,?,?,?,?)",
        (project_id, "test-proj", "/tmp/test", now, now),
    )
    conn.execute(
        "INSERT INTO tasks (id, project_id, title, status, priority, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (task_id, project_id, "Test task", "in_progress", "medium", now, now),
    )
    conn.execute(
        "INSERT INTO agent_runs (id, task_id, role, status, input_summary, output_summary, created_at) VALUES (?,?,?,?,?,?,?)",
        (run_id, task_id, "builder", "completed", "input", "output", now),
    )
    conn.execute(
        """INSERT INTO execution_proposals
           (id, task_id, run_id, role, proposal_data, risk_level,
            requires_approval, approval_reasons, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (proposal_id, task_id, run_id, "builder", proposal_data,
         "medium", 1, "[]", "approved", now, now),
    )
    conn.execute(
        """INSERT INTO approval_requests
           (id, task_id, run_id, action_type, action_payload, status,
            reviewer_comment, created_at, proposal_id)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (approval_id, task_id, run_id, "execute_proposal", "{}", "approved",
         "LGTM", now, proposal_id),
    )
    conn.execute(
        """INSERT INTO execution_snapshots
           (id, proposal_id, approval_id, task_id, snapshot_data,
            content_hash, risk_level, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (snapshot_id, proposal_id, approval_id, task_id,
         snap_data, c_hash, "medium", "frozen", now),
    )
    conn.execute(
        """INSERT INTO execution_requests
           (id, task_id, proposal_id, approval_id, snapshot_id,
            snapshot_content_hash, risk_level, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (request_id, task_id, proposal_id, approval_id, snapshot_id,
         c_hash, "medium", request_status, now, now),
    )
    conn.commit()

    return {
        "project_id": project_id,
        "task_id": task_id,
        "run_id": run_id,
        "proposal_id": proposal_id,
        "approval_id": approval_id,
        "snapshot_id": snapshot_id,
        "request_id": request_id,
        "content_hash": c_hash,
    }


# ── Section 1: request not found ────────────────────────────────────
print("\n§1  request not found")
try:
    run_dry_execution("nonexistent-id")
    check("raises on missing request", False, "no exception raised")
except ValueError as e:
    check("raises on missing request", "not found" in str(e).lower())


# ── Section 2: request not confirmed ────────────────────────────────
print("\n§2  request not confirmed")
conn = database.get_connection()
ids_requested = _seed_full_chain(conn, request_status="requested")
conn.close()

try:
    run_dry_execution(ids_requested["request_id"])
    check("raises on non-confirmed request", False, "no exception raised")
except ValueError as e:
    check("raises on non-confirmed request", "not confirmed" in str(e).lower())

# Also test rejected status
conn = database.get_connection()
ids_rejected = _seed_full_chain(conn, request_status="rejected")
conn.close()

try:
    run_dry_execution(ids_rejected["request_id"])
    check("raises on rejected request", False, "no exception raised")
except ValueError as e:
    check("raises on rejected request", "not confirmed" in str(e).lower())


# ── Section 3: successful dry-run ────────────────────────────────────
print("\n§3  successful dry-run execution")
conn = database.get_connection()
ids = _seed_full_chain(conn, request_status="confirmed")
conn.close()

result = run_dry_execution(ids["request_id"])

check("returns dict", isinstance(result, dict))
check("has id", "id" in result and result["id"])
check("execution_request_id matches", result.get("execution_request_id") == ids["request_id"])
check("task_id matches", result.get("task_id") == ids["task_id"])
check("snapshot_id matches", result.get("snapshot_id") == ids["snapshot_id"])
check("snapshot_content_hash matches", result.get("snapshot_content_hash") == ids["content_hash"])
check("status is completed", result.get("status") == "completed")
check("has started_at", bool(result.get("started_at")))
check("has completed_at", bool(result.get("completed_at")))
check("has created_at", bool(result.get("created_at")))

# Parse result_data
rd_raw = result.get("result_data", "{}")
rd = json.loads(rd_raw) if isinstance(rd_raw, str) else rd_raw
check("result_data.mode is dry_run", rd.get("mode") == "dry_run")
check("result_data.execution_request_id", rd.get("execution_request_id") == ids["request_id"])
check("result_data.snapshot_id", rd.get("snapshot_id") == ids["snapshot_id"])
check("result_data.snapshot_content_hash", rd.get("snapshot_content_hash") == ids["content_hash"])
check("result_data.summary present", bool(rd.get("summary")))
check("result_data has planned_file_actions", isinstance(rd.get("planned_file_actions"), list))
check("planned_file_actions count", len(rd.get("planned_file_actions", [])) == 2)
check("result_data has planned_command_actions", isinstance(rd.get("planned_command_actions"), list))
check("planned_command_actions count", len(rd.get("planned_command_actions", [])) == 2)
check("result_data has warnings", isinstance(rd.get("warnings"), list))

# Verify file action structure
if rd.get("planned_file_actions"):
    fa = rd["planned_file_actions"][0]
    check("file_action has path", "path" in fa)
    check("file_action has operation", "operation" in fa)
    check("file_action dry_run=True", fa.get("dry_run") is True)
    check("file_action executed=False", fa.get("executed") is False)

# Verify command action structure
if rd.get("planned_command_actions"):
    ca = rd["planned_command_actions"][0]
    check("command_action has command", "command" in ca)
    check("command_action dry_run=True", ca.get("dry_run") is True)
    check("command_action executed=False", ca.get("executed") is False)


# ── Section 4: idempotent ────────────────────────────────────────────
print("\n§4  idempotent on repeated call")
result2 = run_dry_execution(ids["request_id"])
check("same id on repeat", result2["id"] == result["id"])
check("same status on repeat", result2["status"] == result["status"])

# Verify only one record in DB
conn = database.get_connection()
count = conn.execute(
    "SELECT COUNT(*) FROM execution_results WHERE execution_request_id = ?",
    (ids["request_id"],),
).fetchone()[0]
conn.close()
check("exactly one result record", count == 1)


# ── Section 5: audit log exists ──────────────────────────────────────
print("\n§5  audit log")
conn = database.get_connection()
logs = conn.execute(
    "SELECT * FROM log_events WHERE task_id = ? AND source = 'execution_result_service'",
    (ids["task_id"],),
).fetchall()
log_cols = [d[0] for d in conn.execute("SELECT * FROM log_events LIMIT 0").description]
conn.close()

check("audit log exists", len(logs) >= 1)

if logs:
    log_dict = dict(zip(log_cols, logs[0]))
    payload = json.loads(log_dict.get("payload", "{}"))
    check("log event_type", payload.get("event_type") == "execution_result:dry_run")
    check("log has execution_request_id", payload.get("execution_request_id") == ids["request_id"])
    check("log has execution_result_id", payload.get("execution_result_id") == result["id"])
    check("log has snapshot_id", payload.get("snapshot_id") == ids["snapshot_id"])
    check("log has status", payload.get("status") == "completed")


# ── Section 6: empty snapshot (no files/commands) ────────────────────
print("\n§6  empty snapshot (no actions)")
conn = database.get_connection()
now = datetime.now(timezone.utc).isoformat()
empty_plan = json.dumps({"summary": "Nothing to do"})
empty_hash = _content_hash(empty_plan)

pid = str(uuid.uuid4())
tid = str(uuid.uuid4())
rid = str(uuid.uuid4())
prop_id = str(uuid.uuid4())
appr_id = str(uuid.uuid4())
snap_id = str(uuid.uuid4())
ereq_id = str(uuid.uuid4())

conn.execute("INSERT INTO projects (id, name, local_repo_path, created_at, updated_at) VALUES (?,?,?,?,?)",
             (pid, "empty-proj", "/tmp/empty", now, now))
conn.execute("INSERT INTO tasks (id, project_id, title, status, priority, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
             (tid, pid, "Empty task", "in_progress", "low", now, now))
conn.execute("INSERT INTO agent_runs (id, task_id, role, status, input_summary, output_summary, created_at) VALUES (?,?,?,?,?,?,?)",
             (rid, tid, "builder", "completed", "", "", now))
conn.execute("""INSERT INTO execution_proposals (id, task_id, run_id, role, proposal_data, risk_level, requires_approval, approval_reasons, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
             (prop_id, tid, rid, "builder", empty_plan, "low", 1, "[]", "approved", now, now))
conn.execute("""INSERT INTO approval_requests (id, task_id, run_id, action_type, action_payload, status, reviewer_comment, created_at, proposal_id) VALUES (?,?,?,?,?,?,?,?,?)""",
             (appr_id, tid, rid, "execute_proposal", "{}", "approved", "ok", now, prop_id))
conn.execute("""INSERT INTO execution_snapshots (id, proposal_id, approval_id, task_id, snapshot_data, content_hash, risk_level, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
             (snap_id, prop_id, appr_id, tid, empty_plan, empty_hash, "low", "frozen", now))
conn.execute("""INSERT INTO execution_requests (id, task_id, proposal_id, approval_id, snapshot_id, snapshot_content_hash, risk_level, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
             (ereq_id, tid, prop_id, appr_id, snap_id, empty_hash, "low", "confirmed", now, now))
conn.commit()
conn.close()

empty_result = run_dry_execution(ereq_id)
erd = json.loads(empty_result["result_data"]) if isinstance(empty_result["result_data"], str) else empty_result["result_data"]

check("empty: status completed", empty_result["status"] == "completed")
check("empty: no file actions", len(erd.get("planned_file_actions", [])) == 0)
check("empty: no command actions", len(erd.get("planned_command_actions", [])) == 0)
check("empty: has warning", len(erd.get("warnings", [])) >= 1)


# ── Cleanup ──────────────────────────────────────────────────────────
try:
    os.unlink(_db_file)
except OSError:
    pass


# ── Summary ──────────────────────────────────────────────────────────
total = _pass + _fail
print()
print("-" * 60)
print(f"  6F-A dry-run: {total} checks  Pass: {_pass}  Fail: {_fail}")
print("-" * 60)

if _fail:
    print(f"\n  {_fail} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
