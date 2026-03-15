#!/usr/bin/env python3
"""Phase 7A Step 1 acceptance: execution eligibility gate.

Pure service-level tests — no API server needed.

Usage:
    python tests/phase7a_eligibility_acceptance.py
"""

import hashlib
import json
import os
import sys
import uuid as _uuid
from datetime import datetime, timezone

# ── Add runtime to sys.path ──────────────────────────────────
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.execution_eligibility_service import check_execution_eligibility
from database import get_connection, init_db

# Ensure schema is up to date (V10 adds mode column)
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


def _build_full_chain(
    snapshot_data: dict,
    local_repo_path: str = "/tmp/test-workspace",
    confirm: bool = True,
    create_dry_run: bool = True,
    dry_run_status: str = "completed",
) -> dict:
    """Build complete chain: project → task → proposal → approval → snapshot → request.

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
        snap_json = json.dumps(snapshot_data)
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
        content_hash = _content_hash(snap_json)
        conn.execute(
            """INSERT INTO execution_snapshots
               (id, proposal_id, approval_id, task_id, snapshot_data,
                content_hash, risk_level, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, proposal_id, approval_id, task_id,
             snap_json, content_hash, "low", "frozen", now),
        )

        # Execution request
        status = "confirmed" if confirm else "requested"
        conn.execute(
            """INSERT INTO execution_requests
               (id, task_id, proposal_id, approval_id, snapshot_id,
                snapshot_content_hash, risk_level, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, task_id, proposal_id, approval_id, snapshot_id,
             content_hash, "low", status, now, now),
        )

        # Dry-run result (optional)
        result_id = None
        if create_dry_run:
            result_id = str(_uuid.uuid4())
            result_data = json.dumps({
                "mode": "dry_run",
                "summary": "test dry-run",
                "planned_file_actions": [],
                "planned_command_actions": [],
                "warnings": [],
            })
            conn.execute(
                """INSERT INTO execution_results
                   (id, execution_request_id, task_id, snapshot_id,
                    snapshot_content_hash, mode, status, result_data,
                    started_at, completed_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (result_id, request_id, task_id, snapshot_id,
                 content_hash, "dry_run", dry_run_status, result_data,
                 now, now, now),
            )

        conn.commit()

        return {
            "project_id": project_id,
            "task_id": task_id,
            "proposal_id": proposal_id,
            "approval_id": approval_id,
            "snapshot_id": snapshot_id,
            "request_id": request_id,
            "result_id": result_id,
            "content_hash": content_hash,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# T1: Request not found
# ══════════════════════════════════════════════════════════════
print("\n[T1]  Request not found")
r = check_execution_eligibility(str(_uuid.uuid4()))
check("not eligible", r["eligible"] is False)
check("blocked: request_not_found", "request_not_found" in r["blocked_reasons"])


# ══════════════════════════════════════════════════════════════
# T2: Request not confirmed
# ══════════════════════════════════════════════════════════════
print("\n[T2]  Request not confirmed")
ids_t2 = _build_full_chain(
    {"proposed_files": [{"path": "a.py", "operation": "create"}],
     "proposed_commands": []},
    confirm=False,
)
r = check_execution_eligibility(ids_t2["request_id"])
check("not eligible", r["eligible"] is False)
check("blocked: request_not_confirmed", "request_not_confirmed" in r["blocked_reasons"])


# ══════════════════════════════════════════════════════════════
# T3: Snapshot hash mismatch
# ══════════════════════════════════════════════════════════════
print("\n[T3]  Snapshot hash mismatch")
ids_t3 = _build_full_chain(
    {"proposed_files": [{"path": "a.py", "operation": "create"}],
     "proposed_commands": []},
)
# Tamper with snapshot_data
conn = get_connection()
try:
    conn.execute(
        "UPDATE execution_snapshots SET snapshot_data = ? WHERE id = ?",
        (json.dumps({"proposed_files": [], "proposed_commands": [], "tampered": True}),
         ids_t3["snapshot_id"]),
    )
    conn.commit()
finally:
    conn.close()

r = check_execution_eligibility(ids_t3["request_id"])
check("not eligible", r["eligible"] is False)
check("blocked: snapshot_hash_mismatch", "snapshot_hash_mismatch" in r["blocked_reasons"])


# ══════════════════════════════════════════════════════════════
# T4: Action plan has denied actions
# ══════════════════════════════════════════════════════════════
print("\n[T4]  Action plan has denied actions")
ids_t4 = _build_full_chain(
    {"proposed_files": [
        {"path": "C:\\Windows\\system32\\evil.dll", "operation": "create"},
    ], "proposed_commands": []},
)
r = check_execution_eligibility(ids_t4["request_id"])
check("not eligible", r["eligible"] is False)
check("blocked: has_denied_actions", "has_denied_actions" in r["blocked_reasons"])


# ══════════════════════════════════════════════════════════════
# T5: Action plan has needs_confirmation actions
# ══════════════════════════════════════════════════════════════
print("\n[T5]  Action plan has needs_confirmation actions")
ids_t5 = _build_full_chain(
    {"proposed_files": [
        {"path": ".env", "operation": "modify"},
    ], "proposed_commands": []},
)
r = check_execution_eligibility(ids_t5["request_id"])
check("not eligible", r["eligible"] is False)
check("blocked: has_unconfirmed_actions",
      "has_unconfirmed_actions" in r["blocked_reasons"])


# ══════════════════════════════════════════════════════════════
# T6: Disallowed action types (file_delete, command_run, git_*)
# ══════════════════════════════════════════════════════════════
print("\n[T6]  Disallowed action types")

# file_delete
ids_t6a = _build_full_chain(
    {"proposed_files": [{"path": "old.txt", "operation": "delete"}],
     "proposed_commands": []},
)
r = check_execution_eligibility(ids_t6a["request_id"])
check("file_delete → not eligible", r["eligible"] is False)
check("file_delete → has_disallowed_action_types",
      "has_disallowed_action_types" in r["blocked_reasons"])

# git_commit (still disallowed)
ids_t6b = _build_full_chain(
    {"proposed_files": [],
     "proposed_commands": ["git commit -m 'test'"]},
)
r = check_execution_eligibility(ids_t6b["request_id"])
check("git_commit → not eligible", r["eligible"] is False)
check("git_commit → has_disallowed_action_types",
      "has_disallowed_action_types" in r["blocked_reasons"],
      f"got {r['blocked_reasons']}")


# ══════════════════════════════════════════════════════════════
# T7: Workspace boundary violation
# ══════════════════════════════════════════════════════════════
print("\n[T7]  Workspace boundary violation")

# Path traversal
ids_t7a = _build_full_chain(
    {"proposed_files": [{"path": "../../etc/passwd", "operation": "create"}],
     "proposed_commands": []},
)
r = check_execution_eligibility(ids_t7a["request_id"])
check("path traversal → not eligible", r["eligible"] is False)
check("path traversal → workspace_boundary_violation",
      "workspace_boundary_violation" in r["blocked_reasons"])

# Absolute path
ids_t7b = _build_full_chain(
    {"proposed_files": [{"path": "/etc/passwd", "operation": "create"}],
     "proposed_commands": []},
)
r = check_execution_eligibility(ids_t7b["request_id"])
check("absolute path → not eligible", r["eligible"] is False)
# Note: absolute paths may trigger workspace_boundary_violation or
# has_denied_actions (from RiskClassifier detecting system dirs)
check("absolute path → blocked",
      "workspace_boundary_violation" in r["blocked_reasons"] or
      "has_denied_actions" in r["blocked_reasons"],
      f"got {r['blocked_reasons']}")


# ══════════════════════════════════════════════════════════════
# T8: Dry-run not completed
# ══════════════════════════════════════════════════════════════
print("\n[T8]  Dry-run not completed")

# No dry-run at all
ids_t8a = _build_full_chain(
    {"proposed_files": [{"path": "src/app.py", "operation": "create"}],
     "proposed_commands": []},
    create_dry_run=False,
)
r = check_execution_eligibility(ids_t8a["request_id"])
check("no dry-run → not eligible", r["eligible"] is False)
check("no dry-run → dry_run_not_completed",
      "dry_run_not_completed" in r["blocked_reasons"])

# Dry-run exists but failed
ids_t8b = _build_full_chain(
    {"proposed_files": [{"path": "src/app.py", "operation": "create"}],
     "proposed_commands": []},
    dry_run_status="failed",
)
r = check_execution_eligibility(ids_t8b["request_id"])
check("failed dry-run → not eligible", r["eligible"] is False)
check("failed dry-run → dry_run_not_completed",
      "dry_run_not_completed" in r["blocked_reasons"])


# ══════════════════════════════════════════════════════════════
# T9: All conditions met → eligible
# ══════════════════════════════════════════════════════════════
print("\n[T9]  All conditions met → eligible")
ids_t9 = _build_full_chain(
    {"proposed_files": [
        {"path": "src/app.py", "operation": "create"},
        {"path": "src/utils.py", "operation": "modify"},
    ], "proposed_commands": []},
)
r = check_execution_eligibility(ids_t9["request_id"])
check("eligible", r["eligible"] is True, f"blocked: {r['blocked_reasons']}")
check("no blocked reasons", len(r["blocked_reasons"]) == 0)
check("allowed_action_count == 2", r["allowed_action_count"] == 2,
      f"got {r['allowed_action_count']}")
check("blocked_action_count == 0", r["blocked_action_count"] == 0)
check("total_action_count == 2", r["total_action_count"] == 2)
check("has workspace_root", r["workspace_root"] is not None)
check("summary starts with Eligible", r["summary"].startswith("Eligible"))


# ══════════════════════════════════════════════════════════════
# T10: Multiple blocked reasons collected
# ══════════════════════════════════════════════════════════════
print("\n[T10] Multiple blocked reasons")
ids_t10 = _build_full_chain(
    {"proposed_files": [
        {"path": "../../etc/passwd", "operation": "create"},
    ], "proposed_commands": ["git commit -m 'x'"]},
    create_dry_run=False,
)
r = check_execution_eligibility(ids_t10["request_id"])
check("not eligible", r["eligible"] is False)
check("multiple blocked reasons (>=2)",
      len(r["blocked_reasons"]) >= 2,
      f"got {r['blocked_reasons']}")


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7A eligibility: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
