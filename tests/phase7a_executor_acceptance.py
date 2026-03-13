#!/usr/bin/env python3
"""Phase 7A Step 2B acceptance: scoped file executor.

Service-level tests using real temp directories as workspace.

Usage:
    python tests/phase7a_executor_acceptance.py
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

# ── Add runtime to sys.path ──────────────────────────────────
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.scoped_file_executor import execute_scoped_files
from database import get_connection, init_db

# Ensure schema is up to date
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


# ── Helpers ───────────────────────────────────────────────────

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


def _build_eligible_chain(
    snapshot_data: dict,
    local_repo_path: str,
) -> dict:
    """Build full chain: project → ... → confirmed request + dry-run.

    Returns dict with all IDs.
    """
    conn = get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        project_id = str(_uuid.uuid4())
        task_id = str(_uuid.uuid4())
        proposal_id = str(_uuid.uuid4())
        approval_id = str(_uuid.uuid4())
        snapshot_id = str(_uuid.uuid4())
        request_id = str(_uuid.uuid4())
        result_id = str(_uuid.uuid4())

        snap_json = json.dumps(snapshot_data)
        content_hash = _content_hash(snap_json)

        # Project
        conn.execute(
            """INSERT INTO projects (id, name, local_repo_path, default_branch,
               description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, f"test-{project_id[:8]}", local_repo_path,
             "main", "", now, now),
        )

        # Task
        conn.execute(
            """INSERT INTO tasks (id, project_id, title, description, status,
               priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, project_id, "test task", "", "in_progress",
             "medium", now, now),
        )

        # Proposal
        conn.execute(
            """INSERT INTO execution_proposals
               (id, task_id, run_id, role, proposal_data, risk_level,
                requires_approval, approval_reasons, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (proposal_id, task_id, None, "builder", snap_json,
             "low", 1, "[]", "approved", now, now),
        )

        # Approval
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload,
                status, proposal_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, task_id, None, "proposal:builder",
             json.dumps({"proposal_id": proposal_id}), "approved",
             proposal_id, now),
        )

        # Snapshot
        conn.execute(
            """INSERT INTO execution_snapshots
               (id, proposal_id, approval_id, task_id, snapshot_data,
                content_hash, risk_level, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, proposal_id, approval_id, task_id,
             snap_json, content_hash, "low", "frozen", now),
        )

        # Execution request (confirmed)
        conn.execute(
            """INSERT INTO execution_requests
               (id, task_id, proposal_id, approval_id, snapshot_id,
                snapshot_content_hash, risk_level, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, task_id, proposal_id, approval_id, snapshot_id,
             content_hash, "low", "confirmed", now, now),
        )

        # Dry-run result (completed)
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
            (result_id, request_id, task_id, snapshot_id,
             content_hash, "dry_run", "completed", dr_data,
             now, now, now),
        )

        conn.commit()
        return {
            "project_id": project_id,
            "task_id": task_id,
            "request_id": request_id,
            "snapshot_id": snapshot_id,
            "content_hash": content_hash,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# T1: Not eligible → refuses
# ══════════════════════════════════════════════════════════════
print("\n[T1]  Not eligible → refuses")

ws1 = tempfile.mkdtemp(prefix="7a_t1_")
try:
    # Build chain without dry-run → not eligible
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    pid = str(_uuid.uuid4())
    tid = str(_uuid.uuid4())
    prid = str(_uuid.uuid4())
    aid = str(_uuid.uuid4())
    sid = str(_uuid.uuid4())
    rid = str(_uuid.uuid4())
    snap = {"proposed_files": [{"path": "a.py", "operation": "create", "content": "x"}],
            "proposed_commands": []}
    snap_json = json.dumps(snap)
    ch = _content_hash(snap_json)
    try:
        conn.execute(
            """INSERT INTO projects (id, name, local_repo_path, default_branch,
               description, created_at, updated_at) VALUES (?,?,?,?,?,?,?)""",
            (pid, "t", ws1, "main", "", now, now))
        conn.execute(
            """INSERT INTO tasks (id, project_id, title, description, status,
               priority, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
            (tid, pid, "t", "", "in_progress", "medium", now, now))
        conn.execute(
            """INSERT INTO execution_proposals
               (id, task_id, run_id, role, proposal_data, risk_level,
                requires_approval, approval_reasons, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (prid, tid, None, "builder", snap_json, "low", 1, "[]", "approved", now, now))
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload,
                status, proposal_id, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (aid, tid, None, "proposal:builder", "{}", "approved", prid, now))
        conn.execute(
            """INSERT INTO execution_snapshots
               (id, proposal_id, approval_id, task_id, snapshot_data,
                content_hash, risk_level, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sid, prid, aid, tid, snap_json, ch, "low", "frozen", now))
        conn.execute(
            """INSERT INTO execution_requests
               (id, task_id, proposal_id, approval_id, snapshot_id,
                snapshot_content_hash, risk_level, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (rid, tid, prid, aid, sid, ch, "low", "confirmed", now, now))
        # NO dry-run result → not eligible
        conn.commit()
    finally:
        conn.close()

    try:
        execute_scoped_files(rid, ws1)
        check("should raise ValueError", False)
    except ValueError as e:
        check("raises ValueError", True)
        check("mentions not eligible", "not eligible" in str(e).lower(),
              f"got: {e}")
finally:
    shutil.rmtree(ws1, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T2: Hash mismatch at execution time
# ══════════════════════════════════════════════════════════════
print("\n[T2]  Hash mismatch at execution time")

ws2 = tempfile.mkdtemp(prefix="7a_t2_")
try:
    ids = _build_eligible_chain(
        {"proposed_files": [{"path": "a.py", "operation": "create", "content": "hello"}],
         "proposed_commands": []},
        ws2,
    )
    # Tamper with snapshot after eligibility passes
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE execution_snapshots SET snapshot_data = ? WHERE id = ?",
            (json.dumps({"proposed_files": [], "proposed_commands": [], "tampered": True}),
             ids["snapshot_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        execute_scoped_files(ids["request_id"], ws2)
        check("should raise ValueError", False)
    except ValueError as e:
        check("raises ValueError for hash mismatch", True)
        check("mentions hash", "hash" in str(e).lower(), f"got: {e}")
finally:
    shutil.rmtree(ws2, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T3: file_create success
# ══════════════════════════════════════════════════════════════
print("\n[T3]  file_create success")

ws3 = tempfile.mkdtemp(prefix="7a_t3_")
try:
    os.makedirs(os.path.join(ws3, "src"), exist_ok=True)
    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/app.py", "operation": "create", "content": "print('hello')"},
        ], "proposed_commands": []},
        ws3,
    )
    result = execute_scoped_files(ids["request_id"], ws3)
    check("status is completed", result["status"] == "completed",
          f"got {result['status']}")

    rd = json.loads(result["result_data"]) if isinstance(result["result_data"], str) else result["result_data"]
    fr = rd["file_results"][0]
    check("file_result status success", fr["status"] == "success")

    created_path = os.path.join(ws3, "src", "app.py")
    check("file exists on disk", os.path.exists(created_path))

    with open(created_path, "r", encoding="utf-8") as f:
        check("file content correct", f.read() == "print('hello')")

    check("after_hash is not None", fr["after_hash"] is not None)
    check("before_hash is None (create)", fr["before_hash"] is None)
finally:
    shutil.rmtree(ws3, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T4: file_modify success
# ══════════════════════════════════════════════════════════════
print("\n[T4]  file_modify success")

ws4 = tempfile.mkdtemp(prefix="7a_t4_")
try:
    os.makedirs(os.path.join(ws4, "src"), exist_ok=True)
    original_path = os.path.join(ws4, "src", "utils.py")
    with open(original_path, "w", encoding="utf-8") as f:
        f.write("old content")
    original_hash = _file_sha256(original_path)

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/utils.py", "operation": "modify", "content": "new content"},
        ], "proposed_commands": []},
        ws4,
    )
    result = execute_scoped_files(ids["request_id"], ws4)
    check("status is completed", result["status"] == "completed")

    rd = json.loads(result["result_data"]) if isinstance(result["result_data"], str) else result["result_data"]
    fr = rd["file_results"][0]
    check("before_hash matches original", fr["before_hash"] == original_hash)
    check("after_hash differs from before", fr["after_hash"] != fr["before_hash"])

    with open(original_path, "r", encoding="utf-8") as f:
        check("file content updated", f.read() == "new content")
finally:
    shutil.rmtree(ws4, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T5: file_create conflict (file already exists)
# ══════════════════════════════════════════════════════════════
print("\n[T5]  file_create conflict")

ws5 = tempfile.mkdtemp(prefix="7a_t5_")
try:
    # Pre-create the file
    with open(os.path.join(ws5, "exists.txt"), "w") as f:
        f.write("already here")

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "exists.txt", "operation": "create", "content": "new"},
        ], "proposed_commands": []},
        ws5,
    )
    result = execute_scoped_files(ids["request_id"], ws5)
    check("status is failed", result["status"] == "failed")

    rd = json.loads(result["result_data"]) if isinstance(result["result_data"], str) else result["result_data"]
    fr = rd["file_results"][0]
    check("file_result status failed", fr["status"] == "failed")
    check("error mentions already exists", "already exists" in fr.get("error", ""),
          f"got: {fr.get('error')}")
finally:
    shutil.rmtree(ws5, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T6: file_modify target missing
# ══════════════════════════════════════════════════════════════
print("\n[T6]  file_modify target missing")

ws6 = tempfile.mkdtemp(prefix="7a_t6_")
try:
    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "nonexistent.py", "operation": "modify", "content": "x"},
        ], "proposed_commands": []},
        ws6,
    )
    result = execute_scoped_files(ids["request_id"], ws6)
    check("status is failed", result["status"] == "failed")

    rd = json.loads(result["result_data"]) if isinstance(result["result_data"], str) else result["result_data"]
    fr = rd["file_results"][0]
    check("error mentions not exist", "not exist" in fr.get("error", "").lower(),
          f"got: {fr.get('error')}")
finally:
    shutil.rmtree(ws6, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T7: Sensitive path denied
# ══════════════════════════════════════════════════════════════
print("\n[T7]  Sensitive path denied")

ws7 = tempfile.mkdtemp(prefix="7a_t7_")
try:
    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": ".git/config", "operation": "create", "content": "x"},
        ], "proposed_commands": []},
        ws7,
    )
    result = execute_scoped_files(ids["request_id"], ws7)
    check("status is failed", result["status"] == "failed")

    rd = json.loads(result["result_data"]) if isinstance(result["result_data"], str) else result["result_data"]
    fr = rd["file_results"][0]
    check("error mentions sensitive", "sensitive" in fr.get("error", "").lower(),
          f"got: {fr.get('error')}")
finally:
    shutil.rmtree(ws7, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T8: Parent directory missing → fail-fast
# ══════════════════════════════════════════════════════════════
print("\n[T8]  Parent directory missing")

ws8 = tempfile.mkdtemp(prefix="7a_t8_")
try:
    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "deep/nested/dir/file.py", "operation": "create", "content": "x"},
        ], "proposed_commands": []},
        ws8,
    )
    result = execute_scoped_files(ids["request_id"], ws8)
    check("status is failed", result["status"] == "failed")

    rd = json.loads(result["result_data"]) if isinstance(result["result_data"], str) else result["result_data"]
    fr = rd["file_results"][0]
    check("error mentions parent", "parent" in fr.get("error", "").lower(),
          f"got: {fr.get('error')}")
finally:
    shutil.rmtree(ws8, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T9: Multi-file fail-fast (2nd fails → 3rd skipped)
# ══════════════════════════════════════════════════════════════
print("\n[T9]  Multi-file fail-fast")

ws9 = tempfile.mkdtemp(prefix="7a_t9_")
try:
    os.makedirs(os.path.join(ws9, "src"), exist_ok=True)

    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/first.py", "operation": "create", "content": "ok"},
            {"path": "src/second.py", "operation": "modify", "content": "fail"},  # doesn't exist
            {"path": "src/third.py", "operation": "create", "content": "skip"},
        ], "proposed_commands": []},
        ws9,
    )
    result = execute_scoped_files(ids["request_id"], ws9)
    check("status is failed", result["status"] == "failed")

    rd = json.loads(result["result_data"]) if isinstance(result["result_data"], str) else result["result_data"]
    frs = rd["file_results"]
    check("first file succeeded", frs[0]["status"] == "success")
    check("second file failed", frs[1]["status"] == "failed")
    check("third file skipped", frs[2]["status"] == "skipped")

    # First file should exist on disk (not rolled back)
    check("first file exists on disk",
          os.path.exists(os.path.join(ws9, "src", "first.py")))

    check("stopped_at is 1", rd["stopped_at"] == 1)
finally:
    shutil.rmtree(ws9, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T10: Idempotent — returns existing result on repeat
# ══════════════════════════════════════════════════════════════
print("\n[T10] Idempotent repeat")

ws10 = tempfile.mkdtemp(prefix="7a_t10_")
try:
    os.makedirs(os.path.join(ws10, "src"), exist_ok=True)
    ids = _build_eligible_chain(
        {"proposed_files": [
            {"path": "src/app.py", "operation": "create", "content": "v1"},
        ], "proposed_commands": []},
        ws10,
    )
    r1 = execute_scoped_files(ids["request_id"], ws10)
    r2 = execute_scoped_files(ids["request_id"], ws10)
    check("same result id", r1["id"] == r2["id"])
finally:
    shutil.rmtree(ws10, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7A executor: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
