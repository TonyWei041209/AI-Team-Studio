#!/usr/bin/env python3
"""Phase 7E (route-3) acceptance: strict_parent missing-parent policy.

Service-level tests against an isolated SQLite DB (RUNTIME_DB) and real
temp-directory workspaces. No HTTP server needed.

Proves BOTH behaviors of the strict_parent flag end-to-end:
  - strict_parent=True  (default): missing parent dir → fail-fast, error
    mentions "parent", downstream command skipped, NO directory created.
  - strict_parent=False (opt-in): missing parent dir → auto-created →
    file written, downstream command runs.
  - dry-run parity: run_dry_execution honors strict_parent (predicts
    failure vs success) WITHOUT ever creating a directory.
  - result_data records the effective strict_parent value.

Usage:
    python tests/phase7e_strict_parent_acceptance.py
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
from agents.execution_result_service import run_dry_execution
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


def _build_chain(
    snapshot_data: dict,
    local_repo_path: str,
    with_dry_run: bool = True,
) -> dict:
    """Insert project → task → proposal → approval → snapshot → confirmed
    request, and optionally a completed dry-run row (needed for the execute
    eligibility gate; omitted when we want run_dry_execution to compute).
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

        snap_json = json.dumps(snapshot_data)
        content_hash = _content_hash(snap_json)

        conn.execute(
            """INSERT INTO projects (id, name, local_repo_path, default_branch,
               description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, f"test-{project_id[:8]}", local_repo_path,
             "main", "", now, now),
        )
        conn.execute(
            """INSERT INTO tasks (id, project_id, title, description, status,
               priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, project_id, "test task", "", "in_progress",
             "medium", now, now),
        )
        conn.execute(
            """INSERT INTO execution_proposals
               (id, task_id, run_id, role, proposal_data, risk_level,
                requires_approval, approval_reasons, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (proposal_id, task_id, None, "builder", snap_json,
             "low", 1, "[]", "approved", now, now),
        )
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload,
                status, proposal_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, task_id, None, "proposal:builder",
             json.dumps({"proposal_id": proposal_id}), "approved",
             proposal_id, now),
        )
        conn.execute(
            """INSERT INTO execution_snapshots
               (id, proposal_id, approval_id, task_id, snapshot_data,
                content_hash, risk_level, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, proposal_id, approval_id, task_id,
             snap_json, content_hash, "low", "frozen", now),
        )
        conn.execute(
            """INSERT INTO execution_requests
               (id, task_id, proposal_id, approval_id, snapshot_id,
                snapshot_content_hash, risk_level, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, task_id, proposal_id, approval_id, snapshot_id,
             content_hash, "low", "confirmed", now, now),
        )

        if with_dry_run:
            result_id = str(_uuid.uuid4())
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
    finally:
        conn.close()

    return {"request_id": request_id, "task_id": task_id, "snapshot_id": snapshot_id}


# A proposal whose single file targets a path with a MISSING parent dir,
# followed by a whitelisted command. Reused across tests.
def _missing_parent_snapshot() -> dict:
    return {
        "summary": "route-3 strict_parent fixture",
        "proposed_files": [
            {"path": "deep/nested/dir/file.py", "operation": "create", "content": "x = 1"},
        ],
        "proposed_commands": [
            {"command": "python --version"},
        ],
    }


def _rd(result: dict) -> dict:
    raw = result.get("result_data", "{}")
    return json.loads(raw) if isinstance(raw, str) else raw


# ══════════════════════════════════════════════════════════════
# T1: strict_parent=True (DEFAULT) → fail-fast, command skipped,
#     auto-create NOT taken
# ══════════════════════════════════════════════════════════════
print("\n[T1]  strict_parent=True (default): missing parent → fail-fast")

ws1 = tempfile.mkdtemp(prefix="7e_t1_")
try:
    ids = _build_chain(_missing_parent_snapshot(), ws1, with_dry_run=True)
    # Call with NO third arg → must default to strict_parent=True.
    result = execute_scoped_files(ids["request_id"], ws1)
    check("batch status is failed", result["status"] == "failed",
          f"got {result['status']}")

    rd = _rd(result)
    fr = rd["file_results"][0]
    check("file status failed", fr.get("status") == "failed", f"got {fr.get('status')}")
    check("error mentions parent", "parent" in fr.get("error", "").lower(),
          f"got: {fr.get('error')}")
    check("batch stop_reason set", bool(rd.get("stop_reason")))

    cr = rd.get("command_results", [])
    check("command_results has entry", len(cr) >= 1, f"got {len(cr)}")
    if cr:
        check("downstream command skipped", cr[0].get("status") == "skipped",
              f"got {cr[0].get('status')}")

    # Auto-create path must NOT have been taken.
    check("parent dir NOT created (no auto-mkdir)",
          not os.path.isdir(os.path.join(ws1, "deep")))
    check("target file NOT written",
          not os.path.isfile(os.path.join(ws1, "deep", "nested", "dir", "file.py")))
    # result_data records the effective flag.
    check("result_data.strict_parent is True", rd.get("strict_parent") is True,
          f"got {rd.get('strict_parent')}")
finally:
    shutil.rmtree(ws1, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T2: strict_parent=False → auto-create → success, command runs,
#     directory created inside workspace
# ══════════════════════════════════════════════════════════════
print("\n[T2]  strict_parent=False: missing parent → auto-create → success")

ws2 = tempfile.mkdtemp(prefix="7e_t2_")
try:
    ids = _build_chain(_missing_parent_snapshot(), ws2, with_dry_run=True)
    result = execute_scoped_files(ids["request_id"], ws2, strict_parent=False)
    check("batch status is completed", result["status"] == "completed",
          f"got {result['status']}")

    rd = _rd(result)
    fr = rd["file_results"][0]
    check("file status success", fr.get("status") == "success", f"got {fr.get('status')}")
    check("parent dir auto-created inside workspace",
          os.path.isdir(os.path.join(ws2, "deep", "nested", "dir")))
    check("target file written",
          os.path.isfile(os.path.join(ws2, "deep", "nested", "dir", "file.py")))

    cr = rd.get("command_results", [])
    check("command_results has entry", len(cr) >= 1, f"got {len(cr)}")
    if cr:
        check("downstream command ran (success)", cr[0].get("status") == "success",
              f"got {cr[0].get('status')}")
    check("result_data.strict_parent is False", rd.get("strict_parent") is False,
          f"got {rd.get('strict_parent')}")
finally:
    shutil.rmtree(ws2, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T3: dry-run with strict_parent=True predicts FAILURE (no dir created)
# ══════════════════════════════════════════════════════════════
print("\n[T3]  dry-run strict_parent=True: predicts failure, creates nothing")

ws3 = tempfile.mkdtemp(prefix="7e_t3_")
try:
    # No pre-inserted dry-run row → run_dry_execution computes fresh.
    ids = _build_chain(_missing_parent_snapshot(), ws3, with_dry_run=False)
    dr = run_dry_execution(ids["request_id"], strict_parent=True)
    check("dry-run status failed", dr.get("status") == "failed", f"got {dr.get('status')}")

    rdd = _rd(dr)
    pfa = rdd.get("planned_file_actions", [])
    check("planned_file_actions has entry", len(pfa) >= 1)
    if pfa:
        check("planned file predicted failed", pfa[0].get("status") == "failed",
              f"got {pfa[0].get('status')}")
        check("predicted reason mentions parent",
              "parent" in pfa[0].get("reason", "").lower(),
              f"got: {pfa[0].get('reason')}")
    pca = rdd.get("planned_command_actions", [])
    if pca:
        check("downstream command predicted skipped", pca[0].get("status") == "skipped",
              f"got {pca[0].get('status')}")
    check("dry-run created NO directory",
          not os.path.isdir(os.path.join(ws3, "deep")))
    check("result_data.strict_parent is True", rdd.get("strict_parent") is True,
          f"got {rdd.get('strict_parent')}")
finally:
    shutil.rmtree(ws3, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T4: dry-run with strict_parent=False predicts SUCCESS (no dir created)
# ══════════════════════════════════════════════════════════════
print("\n[T4]  dry-run strict_parent=False: predicts success, creates nothing")

ws4 = tempfile.mkdtemp(prefix="7e_t4_")
try:
    ids = _build_chain(_missing_parent_snapshot(), ws4, with_dry_run=False)
    dr = run_dry_execution(ids["request_id"], strict_parent=False)
    check("dry-run status completed", dr.get("status") == "completed",
          f"got {dr.get('status')}")

    rdd = _rd(dr)
    pfa = rdd.get("planned_file_actions", [])
    check("planned_file_actions has entry", len(pfa) >= 1)
    if pfa:
        check("planned file predicted ok", pfa[0].get("status") == "ok",
              f"got {pfa[0].get('status')}")
    check("dry-run created NO directory (pure simulation)",
          not os.path.isdir(os.path.join(ws4, "deep")))
    check("result_data.strict_parent is False", rdd.get("strict_parent") is False,
          f"got {rdd.get('strict_parent')}")
finally:
    shutil.rmtree(ws4, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7E strict_parent: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
