#!/usr/bin/env python3
"""Phase 7C-1 acceptance: pre-execution file backup.

Service-level tests verifying backup records are created for file_modify
operations before real writes occur.

Usage:
    python tests/phase7c1_backup_acceptance.py
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


def _content_hash(snapshot_data: str) -> str:
    canonical = json.dumps(
        json.loads(snapshot_data), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_eligible_chain(snapshot_data: dict, local_repo_path: str) -> dict:
    """Build full object chain returning IDs dict."""
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
        # Dry-run result (required for eligibility)
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


def _get_backups(result_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM execution_file_backups WHERE execution_result_id = ?",
            (result_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# T1: file_modify → backup exists with correct content and hash
# ══════════════════════════════════════════════════════════════
print("\n[T1]  file_modify → backup exists")

ws1 = tempfile.mkdtemp(prefix="7c1_t1_")
try:
    os.makedirs(os.path.join(ws1, "src"), exist_ok=True)
    orig_path = os.path.join(ws1, "src", "app.py")
    orig_content = "original content here"
    with open(orig_path, "w", encoding="utf-8") as f:
        f.write(orig_content)
    orig_hash = _file_sha256(orig_path)

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/app.py", "operation": "modify", "content": "new content"},
        ], "proposed_commands": []},
        ws1,
    )
    result = execute_scoped_files(ids["request_id"], ws1)
    check("execution completed", result["status"] == "completed")

    backups = _get_backups(result["id"])
    check("one backup record", len(backups) == 1, f"got {len(backups)}")
    if backups:
        b = backups[0]
        check("backup path correct", b["path"] == "src/app.py")
        check("backup content matches original", b["original_content"] == orig_content)
        check("backup hash matches original", b["original_hash"] == orig_hash)
        check("backup execution_request_id correct", b["execution_request_id"] == ids["request_id"])
        check("backup task_id correct", b["task_id"] == ids["task_id"])
finally:
    shutil.rmtree(ws1, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T2: file_create → no backup
# ══════════════════════════════════════════════════════════════
print("\n[T2]  file_create → no backup")

ws2 = tempfile.mkdtemp(prefix="7c1_t2_")
try:
    os.makedirs(os.path.join(ws2, "src"), exist_ok=True)
    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/new.py", "operation": "create", "content": "brand new"},
        ], "proposed_commands": []},
        ws2,
    )
    result = execute_scoped_files(ids["request_id"], ws2)
    check("execution completed", result["status"] == "completed")

    backups = _get_backups(result["id"])
    check("no backup for create", len(backups) == 0, f"got {len(backups)}")
finally:
    shutil.rmtree(ws2, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T3: create + modify mixed → only modify has backup
# ══════════════════════════════════════════════════════════════
print("\n[T3]  Mixed create + modify")

ws3 = tempfile.mkdtemp(prefix="7c1_t3_")
try:
    os.makedirs(os.path.join(ws3, "src"), exist_ok=True)
    with open(os.path.join(ws3, "src", "existing.py"), "w", encoding="utf-8") as f:
        f.write("old code")

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/brand_new.py", "operation": "create", "content": "new file"},
            {"path": "src/existing.py", "operation": "modify", "content": "updated code"},
        ], "proposed_commands": []},
        ws3,
    )
    result = execute_scoped_files(ids["request_id"], ws3)
    check("execution completed", result["status"] == "completed")

    backups = _get_backups(result["id"])
    check("exactly one backup (modify only)", len(backups) == 1, f"got {len(backups)}")
    if backups:
        check("backup is for existing.py", backups[0]["path"] == "src/existing.py")
        check("backup content is old", backups[0]["original_content"] == "old code")
finally:
    shutil.rmtree(ws3, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T4: fail-fast → backup for completed modify is preserved
# ══════════════════════════════════════════════════════════════
print("\n[T4]  Fail-fast preserves earlier backup")

ws4 = tempfile.mkdtemp(prefix="7c1_t4_")
try:
    os.makedirs(os.path.join(ws4, "src"), exist_ok=True)
    with open(os.path.join(ws4, "src", "first.py"), "w", encoding="utf-8") as f:
        f.write("first original")

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/first.py", "operation": "modify", "content": "first updated"},
            {"path": "src/missing.py", "operation": "modify", "content": "will fail"},
            {"path": "src/third.py", "operation": "create", "content": "skipped"},
        ], "proposed_commands": []},
        ws4,
    )
    result = execute_scoped_files(ids["request_id"], ws4)
    check("execution failed", result["status"] == "failed")

    backups = _get_backups(result["id"])
    # first.py modify succeeded → backup exists
    # missing.py modify failed (file not found) → no backup (fail before backup)
    check("one backup (first.py only)", len(backups) == 1, f"got {len(backups)}")
    if backups:
        check("backup is for first.py", backups[0]["path"] == "src/first.py")
        check("backup content preserved", backups[0]["original_content"] == "first original")
finally:
    shutil.rmtree(ws4, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T5: Non-UTF-8 original file → fail-fast
# ══════════════════════════════════════════════════════════════
print("\n[T5]  Non-UTF-8 original → fail-fast")

ws5 = tempfile.mkdtemp(prefix="7c1_t5_")
try:
    os.makedirs(os.path.join(ws5, "src"), exist_ok=True)
    # Write binary content that's not valid UTF-8
    bin_path = os.path.join(ws5, "src", "binary.dat")
    with open(bin_path, "wb") as f:
        f.write(b"\x80\x81\x82\xff\xfe\xfd")

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/binary.dat", "operation": "modify", "content": "new text"},
        ], "proposed_commands": []},
        ws5,
    )
    result = execute_scoped_files(ids["request_id"], ws5)
    check("execution failed", result["status"] == "failed")

    rd = json.loads(result["result_data"]) if isinstance(result["result_data"], str) else result["result_data"]
    check("stop_reason mentions backup", "backup" in rd.get("stop_reason", "").lower(),
          f"got: {rd.get('stop_reason')}")
    check("file_result error mentions UTF-8", "utf-8" in rd["file_results"][0].get("error", "").lower(),
          f"got: {rd['file_results'][0].get('error')}")

    # No backup should exist for this file
    backups = _get_backups(result["id"])
    check("no backup for failed read", len(backups) == 0, f"got {len(backups)}")
finally:
    shutil.rmtree(ws5, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7C-1 backup: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
