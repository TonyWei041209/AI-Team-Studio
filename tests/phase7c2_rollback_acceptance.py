#!/usr/bin/env python3
"""Phase 7C-2 acceptance: rollback service.

Service-level tests using real temp directories as workspace.

Usage:
    python tests/phase7c2_rollback_acceptance.py
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

from agents.rollback_service import rollback_execution
from agents.scoped_file_executor import execute_scoped_files
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


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_eligible_chain(snapshot_data: dict, local_repo_path: str) -> dict:
    conn = get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        pid = str(_uuid.uuid4())
        tid = str(_uuid.uuid4())
        prid = str(_uuid.uuid4())
        aid = str(_uuid.uuid4())
        sid = str(_uuid.uuid4())
        rid = str(_uuid.uuid4())
        drid = str(_uuid.uuid4())

        snap_json = json.dumps(snapshot_data)
        ch = _content_hash(snap_json)

        conn.execute(
            """INSERT INTO projects (id, name, local_repo_path, default_branch,
               description, created_at, updated_at) VALUES (?,?,?,?,?,?,?)""",
            (pid, f"test-{pid[:8]}", local_repo_path, "main", "", now, now),
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
        return {"request_id": rid, "task_id": tid}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# T1: Rollback file_modify → content restored + hash verified
# ══════════════════════════════════════════════════════════════
print("\n[T1]  Rollback file_modify")

ws1 = tempfile.mkdtemp(prefix="7c2_t1_")
try:
    os.makedirs(os.path.join(ws1, "src"), exist_ok=True)
    orig_path = os.path.join(ws1, "src", "app.py")
    orig_content = "original content"
    with open(orig_path, "w", encoding="utf-8") as f:
        f.write(orig_content)
    orig_hash = _file_sha256(orig_path)

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/app.py", "operation": "modify", "content": "modified content"},
        ], "proposed_commands": []},
        ws1,
    )
    exec_result = execute_scoped_files(ids["request_id"], ws1)

    # File should now have modified content
    with open(orig_path, "r") as f:
        check("file was modified", f.read() == "modified content")

    # Rollback
    rb = rollback_execution(exec_result["id"], ws1)
    check("rollback status completed", rb["status"] == "completed")

    rd = json.loads(rb["result_data"]) if isinstance(rb["result_data"], str) else rb["result_data"]
    rr = rd["rollback_results"][0]
    check("rollback action is restore", rr["rollback_action"] == "restore")
    check("rollback status is restored", rr["status"] == "restored")

    # File should be restored
    with open(orig_path, "r") as f:
        check("content restored to original", f.read() == orig_content)
    check("hash matches original", _file_sha256(orig_path) == orig_hash)
finally:
    shutil.rmtree(ws1, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T2: Rollback file_create → file deleted
# ══════════════════════════════════════════════════════════════
print("\n[T2]  Rollback file_create → deleted")

ws2 = tempfile.mkdtemp(prefix="7c2_t2_")
try:
    os.makedirs(os.path.join(ws2, "src"), exist_ok=True)
    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/new_file.py", "operation": "create", "content": "new content"},
        ], "proposed_commands": []},
        ws2,
    )
    exec_result = execute_scoped_files(ids["request_id"], ws2)
    created_path = os.path.join(ws2, "src", "new_file.py")
    check("file was created", os.path.exists(created_path))

    rb = rollback_execution(exec_result["id"], ws2)
    check("rollback status completed", rb["status"] == "completed")

    rd = json.loads(rb["result_data"]) if isinstance(rb["result_data"], str) else rb["result_data"]
    rr = rd["rollback_results"][0]
    check("rollback action is delete", rr["rollback_action"] == "delete")
    check("rollback status is deleted", rr["status"] == "deleted")
    check("file no longer exists", not os.path.exists(created_path))
finally:
    shutil.rmtree(ws2, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T3: Mixed create + modify → both rolled back
# ══════════════════════════════════════════════════════════════
print("\n[T3]  Mixed rollback")

ws3 = tempfile.mkdtemp(prefix="7c2_t3_")
try:
    os.makedirs(os.path.join(ws3, "src"), exist_ok=True)
    existing = os.path.join(ws3, "src", "existing.py")
    with open(existing, "w", encoding="utf-8") as f:
        f.write("old code")

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/brand_new.py", "operation": "create", "content": "new"},
            {"path": "src/existing.py", "operation": "modify", "content": "updated"},
        ], "proposed_commands": []},
        ws3,
    )
    exec_result = execute_scoped_files(ids["request_id"], ws3)

    rb = rollback_execution(exec_result["id"], ws3)
    check("rollback completed", rb["status"] == "completed")

    rd = json.loads(rb["result_data"]) if isinstance(rb["result_data"], str) else rb["result_data"]
    check("two rollback results", len(rd["rollback_results"]) == 2,
          f"got {len(rd['rollback_results'])}")

    # Created file deleted
    check("created file deleted", not os.path.exists(os.path.join(ws3, "src", "brand_new.py")))
    # Modified file restored
    with open(existing, "r") as f:
        check("modified file restored", f.read() == "old code")
finally:
    shutil.rmtree(ws3, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T4: Backup missing for modify → rollback_failed, others continue
# ══════════════════════════════════════════════════════════════
print("\n[T4]  Backup missing → best-effort")

ws4 = tempfile.mkdtemp(prefix="7c2_t4_")
try:
    os.makedirs(os.path.join(ws4, "src"), exist_ok=True)
    with open(os.path.join(ws4, "src", "a.py"), "w", encoding="utf-8") as f:
        f.write("original a")
    with open(os.path.join(ws4, "src", "b.py"), "w", encoding="utf-8") as f:
        f.write("original b")

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/a.py", "operation": "modify", "content": "new a"},
            {"path": "src/b.py", "operation": "modify", "content": "new b"},
        ], "proposed_commands": []},
        ws4,
    )
    exec_result = execute_scoped_files(ids["request_id"], ws4)

    # Delete backup for a.py to simulate missing backup
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM execution_file_backups WHERE execution_result_id = ? AND path = ?",
            (exec_result["id"], "src/a.py"),
        )
        conn.commit()
    finally:
        conn.close()

    rb = rollback_execution(exec_result["id"], ws4)
    check("rollback status failed (partial)", rb["status"] == "failed")

    rd = json.loads(rb["result_data"]) if isinstance(rb["result_data"], str) else rb["result_data"]
    rrs = rd["rollback_results"]
    a_rb = next(r for r in rrs if r["path"] == "src/a.py")
    b_rb = next(r for r in rrs if r["path"] == "src/b.py")
    check("a.py rollback failed (no backup)", a_rb["status"] == "failed")
    check("b.py rollback restored", b_rb["status"] == "restored")

    # b.py should be restored
    with open(os.path.join(ws4, "src", "b.py"), "r") as f:
        check("b.py content restored", f.read() == "original b")
finally:
    shutil.rmtree(ws4, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T5: Created file already deleted → already_absent
# ══════════════════════════════════════════════════════════════
print("\n[T5]  Created file already deleted")

ws5 = tempfile.mkdtemp(prefix="7c2_t5_")
try:
    os.makedirs(os.path.join(ws5, "src"), exist_ok=True)
    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/temp.py", "operation": "create", "content": "temp"},
        ], "proposed_commands": []},
        ws5,
    )
    exec_result = execute_scoped_files(ids["request_id"], ws5)

    # Manually delete the file before rollback
    os.unlink(os.path.join(ws5, "src", "temp.py"))

    rb = rollback_execution(exec_result["id"], ws5)
    check("rollback completed", rb["status"] == "completed")

    rd = json.loads(rb["result_data"]) if isinstance(rb["result_data"], str) else rb["result_data"]
    check("status is already_absent", rd["rollback_results"][0]["status"] == "already_absent")
finally:
    shutil.rmtree(ws5, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T6: Non-real_run result → rejected
# ══════════════════════════════════════════════════════════════
print("\n[T6]  Non-real_run → rejected")

# Use a dry_run result id
conn = get_connection()
try:
    row = conn.execute(
        "SELECT id FROM execution_results WHERE mode = 'dry_run' LIMIT 1"
    ).fetchone()
finally:
    conn.close()

if row:
    try:
        rollback_execution(row["id"], tempfile.mkdtemp())
        check("should raise ValueError", False)
    except ValueError as e:
        check("rejects non-real_run", "real_run" in str(e).lower(), f"got: {e}")
else:
    check("rejects non-real_run (skip: no dry_run found)", True)


# ══════════════════════════════════════════════════════════════
# T7: Idempotent — repeat rollback returns same result
# ══════════════════════════════════════════════════════════════
print("\n[T7]  Idempotent rollback")

ws7 = tempfile.mkdtemp(prefix="7c2_t7_")
try:
    os.makedirs(os.path.join(ws7, "src"), exist_ok=True)
    with open(os.path.join(ws7, "src", "x.py"), "w", encoding="utf-8") as f:
        f.write("orig")

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/x.py", "operation": "modify", "content": "new"},
        ], "proposed_commands": []},
        ws7,
    )
    exec_result = execute_scoped_files(ids["request_id"], ws7)

    rb1 = rollback_execution(exec_result["id"], ws7)
    rb2 = rollback_execution(exec_result["id"], ws7)
    check("same rollback id on repeat", rb1["id"] == rb2["id"])
finally:
    shutil.rmtree(ws7, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T8: Result not found → rejected
# ══════════════════════════════════════════════════════════════
print("\n[T8]  Result not found")

try:
    rollback_execution("nonexistent-id", tempfile.mkdtemp())
    check("should raise ValueError", False)
except ValueError as e:
    check("rejects not found", "not found" in str(e).lower(), f"got: {e}")


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7C-2 rollback: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
