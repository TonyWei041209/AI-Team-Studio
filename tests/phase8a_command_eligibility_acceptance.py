#!/usr/bin/env python3
"""Phase 8A-1 acceptance: command_run eligibility gate.

Service-level tests verifying check_execution_eligibility() correctly
handles command_run actions with whitelist, shell syntax, and workdir checks.

Usage:
    python tests/phase8a_command_eligibility_acceptance.py
"""

import hashlib
import json
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.execution_eligibility_service import check_execution_eligibility
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


def _build_chain(
    snapshot_data: dict,
    workspace: str,
) -> str:
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
        # Dry-run result (required by eligibility gate)
        dry_data = json.dumps({
            "mode": "dry_run",
            "summary": "ok",
            "planned_file_actions": [],
            "planned_command_actions": [],
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


WORKSPACE = tempfile.mkdtemp(prefix="8a_elig_")


# ══════════════════════════════════════════════════════════════
# T1: Whitelisted command → eligible
# ══════════════════════════════════════════════════════════════
print("\n[T1]  Whitelisted command → eligible")

rid1 = _build_chain(
    {"proposed_files": [], "proposed_commands": [{"command": "echo hello", "reason": "test"}]},
    WORKSPACE,
)
r1 = check_execution_eligibility(rid1)
check("eligible", r1["eligible"], f"blocked: {r1['blocked_reasons']}")
check("no blocked_reasons", len(r1["blocked_reasons"]) == 0, str(r1["blocked_reasons"]))
check("command_run in allowed types", "command_run" in r1["allowed_action_types"])


# ══════════════════════════════════════════════════════════════
# T2: Non-whitelisted command → blocked
# ══════════════════════════════════════════════════════════════
print("\n[T2]  Non-whitelisted command → blocked")

rid2 = _build_chain(
    {"proposed_files": [], "proposed_commands": [{"command": "curl http://example.com", "reason": "test"}]},
    WORKSPACE,
)
r2 = check_execution_eligibility(rid2)
check("not eligible", not r2["eligible"])
check("command_not_whitelisted in reasons",
      "command_not_whitelisted" in r2["blocked_reasons"],
      str(r2["blocked_reasons"]))


# ══════════════════════════════════════════════════════════════
# T3: Shell chaining (&&, ||, ;) → blocked
# ══════════════════════════════════════════════════════════════
print("\n[T3]  Shell chaining → blocked")

for cmd, label in [
    ("echo a && echo b", "&&"),
    ("echo a || echo b", "||"),
    ("echo a; echo b", ";"),
]:
    rid = _build_chain(
        {"proposed_files": [], "proposed_commands": [{"command": cmd, "reason": "test"}]},
        WORKSPACE,
    )
    r = check_execution_eligibility(rid)
    check(f"{label} blocked",
          "command_contains_forbidden_shell_syntax" in r["blocked_reasons"],
          f"cmd='{cmd}', reasons={r['blocked_reasons']}")


# ══════════════════════════════════════════════════════════════
# T4: Redirection and pipe (>, <, |) → blocked
# ══════════════════════════════════════════════════════════════
print("\n[T4]  Redirection/pipe → blocked")

for cmd, label in [
    ("echo hello > out.txt", ">"),
    ("echo hello >> out.txt", ">>"),
    ("cat < in.txt", "<"),
    ("echo hello | grep h", "|"),
]:
    rid = _build_chain(
        {"proposed_files": [], "proposed_commands": [{"command": cmd, "reason": "test"}]},
        WORKSPACE,
    )
    r = check_execution_eligibility(rid)
    check(f"{label} blocked",
          "command_contains_forbidden_shell_syntax" in r["blocked_reasons"],
          f"cmd='{cmd}', reasons={r['blocked_reasons']}")


# ══════════════════════════════════════════════════════════════
# T5: Subshell / command substitution → blocked
# ══════════════════════════════════════════════════════════════
print("\n[T5]  Subshell/command substitution → blocked")

for cmd, label in [
    ("echo `whoami`", "backticks"),
    ("echo $(whoami)", "$(...)"),
]:
    rid = _build_chain(
        {"proposed_files": [], "proposed_commands": [{"command": cmd, "reason": "test"}]},
        WORKSPACE,
    )
    r = check_execution_eligibility(rid)
    check(f"{label} blocked",
          "command_contains_subshell" in r["blocked_reasons"],
          f"cmd='{cmd}', reasons={r['blocked_reasons']}")


# ══════════════════════════════════════════════════════════════
# T6: working_dir outside workspace → blocked
# ══════════════════════════════════════════════════════════════
print("\n[T6]  working_dir outside workspace → blocked")

rid6 = _build_chain(
    {"proposed_files": [], "proposed_commands": [
        {"command": "echo hello", "reason": "test", "working_dir": "/tmp/outside"}
    ]},
    WORKSPACE,
)
r6 = check_execution_eligibility(rid6)
check("workdir outside blocked",
      "command_workdir_outside_workspace" in r6["blocked_reasons"],
      str(r6["blocked_reasons"]))


# ══════════════════════════════════════════════════════════════
# T7: Policy needs_confirmation → blocked
# ══════════════════════════════════════════════════════════════
print("\n[T7]  Policy needs_confirmation → blocked")

# A non-whitelisted command gets needs_confirmation from policy
rid7 = _build_chain(
    {"proposed_files": [], "proposed_commands": [{"command": "unknown_tool --verbose", "reason": "test"}]},
    WORKSPACE,
)
r7 = check_execution_eligibility(rid7)
check("not eligible", not r7["eligible"])
check("has_unconfirmed_actions in reasons",
      "has_unconfirmed_actions" in r7["blocked_reasons"],
      str(r7["blocked_reasons"]))


# ══════════════════════════════════════════════════════════════
# T8: Policy deny → blocked
# ══════════════════════════════════════════════════════════════
print("\n[T8]  Policy deny (critical risk) → blocked")

# rm -rf / is a CRITICAL risk command → deny
rid8 = _build_chain(
    {"proposed_files": [], "proposed_commands": [{"command": "rm -rf /", "reason": "test"}]},
    WORKSPACE,
)
r8 = check_execution_eligibility(rid8)
check("not eligible", not r8["eligible"])
check("has_denied_actions in reasons",
      "has_denied_actions" in r8["blocked_reasons"],
      str(r8["blocked_reasons"]))


# ══════════════════════════════════════════════════════════════
# T9: Mixed file + whitelisted command → eligible
# ══════════════════════════════════════════════════════════════
print("\n[T9]  Mixed file + whitelisted command → eligible")

rid9 = _build_chain(
    {
        "proposed_files": [
            {"path": "src/app.py", "operation": "create", "content": "# new"},
        ],
        "proposed_commands": [
            {"command": "python -m pytest", "reason": "run tests"},
        ],
    },
    WORKSPACE,
)
r9 = check_execution_eligibility(rid9)
check("eligible", r9["eligible"], f"blocked: {r9['blocked_reasons']}")
check("2 actions total", r9["total_action_count"] == 2, str(r9["total_action_count"]))
check("file_create + command_run in allowed",
      set(r9["allowed_action_types"]) == {"file_create", "command_run"},
      str(r9["allowed_action_types"]))


# ══════════════════════════════════════════════════════════════
# T10: working_dir inside workspace → eligible
# ══════════════════════════════════════════════════════════════
print("\n[T10]  working_dir inside workspace → eligible")

subdir = os.path.join(WORKSPACE, "subdir")
os.makedirs(subdir, exist_ok=True)

rid10 = _build_chain(
    {"proposed_files": [], "proposed_commands": [
        {"command": "echo ok", "reason": "test", "working_dir": "subdir"}
    ]},
    WORKSPACE,
)
r10 = check_execution_eligibility(rid10)
check("eligible with subdir workdir", r10["eligible"],
      f"blocked: {r10['blocked_reasons']}")


# ══════════════════════════════════════════════════════════════
# Cleanup
# ══════════════════════════════════════════════════════════════
import shutil
shutil.rmtree(WORKSPACE, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  8A-1 command eligibility: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
