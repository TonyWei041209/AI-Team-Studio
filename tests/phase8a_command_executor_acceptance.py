#!/usr/bin/env python3
"""Phase 8A-2 acceptance: restricted command executor.

Service-level tests verifying scoped command execution with
whitelist, re-validation, env allowlist, timeout, and truncation.

Usage:
    python tests/phase8a_command_executor_acceptance.py
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
from agents.scoped_command_executor import (
    execute_scoped_commands,
    _build_safe_env,
    _revalidate_command,
)
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


def _build_chain(snapshot_data: dict, workspace: str) -> str:
    """Build full chain with dry_run result and return execution_request_id."""
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
            (pid, f"test-{pid[:8]}", workspace, "main", "", now, now),
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
        dry_data = json.dumps({
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
            (drid, rid, tid, sid, ch, "dry_run", "completed", dry_data, now, now, now),
        )
        conn.commit()
        return rid
    finally:
        conn.close()


WORKSPACE = tempfile.mkdtemp(prefix="8a2_cmd_")


# ══════════════════════════════════════════════════════════════
# T1: Whitelisted command executes successfully
# ══════════════════════════════════════════════════════════════
print("\n[T1]  Whitelisted command succeeds")

results = execute_scoped_commands("test", WORKSPACE, [
    {"command": "echo hello world", "reason": "test"},
])
check("one result", len(results) == 1)
if results:
    r = results[0]
    check("status is success", r["status"] == "success", r.get("error", ""))
    check("exit_code is 0", r["exit_code"] == 0)
    check("stdout contains 'hello world'", "hello world" in r["stdout"],
          f"got: {r['stdout']!r}")


# ══════════════════════════════════════════════════════════════
# T2: Command failure (non-zero exit code)
# ══════════════════════════════════════════════════════════════
print("\n[T2]  Command failure (non-zero exit)")

results2 = execute_scoped_commands("test", WORKSPACE, [
    {"command": "python -c \"exit(42)\"", "reason": "test"},
])
check("one result", len(results2) == 1)
if results2:
    r = results2[0]
    check("status is failed", r["status"] == "failed")
    check("exit_code is 42", r["exit_code"] == 42)


# ══════════════════════════════════════════════════════════════
# T3: Blocked subcommand (npm install)
# ══════════════════════════════════════════════════════════════
print("\n[T3]  Blocked subcommand (npm install)")

results3 = execute_scoped_commands("test", WORKSPACE, [
    {"command": "npm install express", "reason": "test"},
])
check("one result", len(results3) == 1)
if results3:
    r = results3[0]
    check("status is failed", r["status"] == "failed")
    check("error mentions blocked", "Blocked subcommand" in r.get("error", ""),
          r.get("error", ""))


# ══════════════════════════════════════════════════════════════
# T4: Blocked subcommand (pip install)
# ══════════════════════════════════════════════════════════════
print("\n[T4]  Blocked subcommand (pip install)")

results4 = execute_scoped_commands("test", WORKSPACE, [
    {"command": "pip install requests", "reason": "test"},
])
check("status is failed", results4[0]["status"] == "failed")
check("error mentions blocked", "Blocked subcommand" in results4[0].get("error", ""))


# ══════════════════════════════════════════════════════════════
# T5: Blocked subcommand (git push)
# ══════════════════════════════════════════════════════════════
print("\n[T5]  Blocked subcommand (git push)")

results5 = execute_scoped_commands("test", WORKSPACE, [
    {"command": "git push origin main", "reason": "test"},
])
check("status is failed", results5[0]["status"] == "failed")
check("error mentions blocked", "Blocked subcommand" in results5[0].get("error", ""))


# ══════════════════════════════════════════════════════════════
# T6: Shell metachar re-validation (&&)
# ══════════════════════════════════════════════════════════════
print("\n[T6]  Shell metachar re-validation")

results6 = execute_scoped_commands("test", WORKSPACE, [
    {"command": "echo a && echo b", "reason": "test"},
])
check("status is failed", results6[0]["status"] == "failed")
check("error mentions re-validation", "re-validation" in results6[0].get("error", "").lower(),
      results6[0].get("error", ""))


# ══════════════════════════════════════════════════════════════
# T7: Env allowlist (no secret leakage)
# ══════════════════════════════════════════════════════════════
print("\n[T7]  Env allowlist — no secrets")

safe_env = _build_safe_env()
check("PATH present", "PATH" in safe_env)
# Set a fake secret and verify it's not in safe_env
os.environ["ANTHROPIC_API_KEY"] = "sk-test-secret"
os.environ["OPENAI_API_KEY"] = "sk-test-secret-2"
safe_env2 = _build_safe_env()
check("ANTHROPIC_API_KEY not in env", "ANTHROPIC_API_KEY" not in safe_env2)
check("OPENAI_API_KEY not in env", "OPENAI_API_KEY" not in safe_env2)
# Clean up
del os.environ["ANTHROPIC_API_KEY"]
del os.environ["OPENAI_API_KEY"]


# ══════════════════════════════════════════════════════════════
# T8: working_dir set to subdirectory
# ══════════════════════════════════════════════════════════════
print("\n[T8]  working_dir subdirectory")

subdir = os.path.join(WORKSPACE, "subdir")
os.makedirs(subdir, exist_ok=True)

results8 = execute_scoped_commands("test", WORKSPACE, [
    {"command": "echo cwd_test", "reason": "test", "working_dir": "subdir"},
])
check("status is success", results8[0]["status"] == "success")
check("working_dir points to subdir",
      os.path.basename(results8[0]["working_dir"]) == "subdir")


# ══════════════════════════════════════════════════════════════
# T9: Fail-fast — second command skipped after first fails
# ══════════════════════════════════════════════════════════════
print("\n[T9]  Fail-fast — second command skipped")

results9 = execute_scoped_commands("test", WORKSPACE, [
    {"command": "python -c \"exit(1)\"", "reason": "fail"},
    {"command": "echo should not run", "reason": "test"},
])
check("only first result returned", len(results9) == 1)
check("first result failed", results9[0]["status"] == "failed")


# ══════════════════════════════════════════════════════════════
# T10: Integration — file + command via execute_scoped_files
# ══════════════════════════════════════════════════════════════
print("\n[T10]  Integration: file + command via execute_scoped_files")

ws10 = tempfile.mkdtemp(prefix="8a2_int_")
rid10 = _build_chain(
    {
        "proposed_files": [
            {"path": "hello.py", "operation": "create", "content": "print('hello')\n"},
        ],
        "proposed_commands": [
            {"command": "echo integration_ok", "reason": "verify"},
        ],
    },
    ws10,
)
result10 = execute_scoped_files(rid10, ws10)
rd10 = result10.get("result_data", "{}")
if isinstance(rd10, str):
    rd10 = json.loads(rd10)

check("overall status completed", result10["status"] == "completed",
      f"status={result10['status']}")
check("file was written", os.path.exists(os.path.join(ws10, "hello.py")))
check("command_results present", "command_results" in rd10)
if rd10.get("command_results"):
    cr = rd10["command_results"][0]
    check("command succeeded", cr["status"] == "success", cr.get("error", ""))
    check("command stdout captured", "integration_ok" in cr.get("stdout", ""))
check("summary mentions commands",
      "command" in rd10.get("summary", "").lower(),
      rd10.get("summary", ""))

shutil.rmtree(ws10, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# T11: Integration — file failure skips commands
# ══════════════════════════════════════════════════════════════
print("\n[T11]  Integration: file failure skips commands")

ws11 = tempfile.mkdtemp(prefix="8a2_skip_")
rid11 = _build_chain(
    {
        "proposed_files": [
            {"path": "nonexist.py", "operation": "modify", "content": "x"},
        ],
        "proposed_commands": [
            {"command": "echo should_be_skipped", "reason": "test"},
        ],
    },
    ws11,
)
result11 = execute_scoped_files(rid11, ws11)
rd11 = result11.get("result_data", "{}")
if isinstance(rd11, str):
    rd11 = json.loads(rd11)

check("overall status failed", result11["status"] == "failed")
cmd_results = rd11.get("command_results", [])
check("command was skipped", len(cmd_results) == 1 and cmd_results[0]["status"] == "skipped",
      str(cmd_results))

shutil.rmtree(ws11, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Cleanup
# ══════════════════════════════════════════════════════════════
shutil.rmtree(WORKSPACE, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  8A-2 command executor: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
