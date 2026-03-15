"""Execution eligibility gate.

Pure read-only check that determines whether a confirmed execution request
is eligible for real (scoped) execution.  No side effects, no DB writes,
no file/shell/git operations.

Allowed action types:
- file_create, file_modify (Phase 7A)
- command_run (Phase 8A — restricted: whitelist + no shell metacharacters)

All other types (file_delete, git_*, unsupported) block eligibility.

Usage::

    from agents.execution_eligibility_service import check_execution_eligibility

    result = check_execution_eligibility(request_id)
    if result["eligible"]:
        # proceed to executor
    else:
        print(result["blocked_reasons"])
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from typing import Any

from agents.action_policy_service import ActionType, build_action_plan
from database import get_connection
from tools.shell_executor import COMMAND_WHITELIST


# ── Allowed action types ──────────────────────────────────
# file_create / file_modify (Phase 7A) + command_run (Phase 8A restricted).

ALLOWED_ACTION_TYPES: frozenset[str] = frozenset({
    ActionType.file_create.value,
    ActionType.file_modify.value,
    ActionType.command_run.value,
})


# ── Command safety checks (Phase 8A) ─────────────────────

# Shell metacharacters that indicate chaining, redirection, or piping.
_FORBIDDEN_SHELL_SYNTAX_RE = re.compile(
    r"&&|\|\||[;<>]|\|"
)

# Subshell / command substitution patterns.
_SUBSHELL_RE = re.compile(
    r"`[^`]*`|\$\(|\$\(\("
)


def _validate_command(cmd: str) -> list[str]:
    """Validate a command_run target against Phase 8A restrictions.

    Returns a list of blocked_reason codes (empty = valid).
    """
    reasons: list[str] = []

    # 1. Forbidden shell syntax
    if _FORBIDDEN_SHELL_SYNTAX_RE.search(cmd):
        reasons.append("command_contains_forbidden_shell_syntax")

    # 2. Subshell / command substitution
    if _SUBSHELL_RE.search(cmd):
        reasons.append("command_contains_subshell")

    # 3. Whitelist check on base command
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    base_cmd = os.path.basename(parts[0]) if parts else ""
    if base_cmd not in COMMAND_WHITELIST:
        reasons.append("command_not_whitelisted")

    return reasons


def _validate_command_workdir(
    working_dir: str | None, workspace_root: str
) -> bool:
    """Check that working_dir (if specified) resolves inside workspace_root."""
    if not working_dir:
        return True  # default to workspace_root — always ok
    resolved = os.path.normpath(os.path.join(workspace_root, working_dir))
    real_resolved = os.path.realpath(resolved)
    real_workspace = os.path.realpath(workspace_root)
    return (
        real_resolved == real_workspace
        or real_resolved.startswith(real_workspace + os.sep)
    )


# ── Content hash verification (self-contained) ───────────

def _canonical_json(data: str) -> str:
    """Re-serialize JSON with sorted keys for stable hashing."""
    return json.dumps(json.loads(data), sort_keys=True, separators=(",", ":"))


def _verify_content_hash(snapshot_data: str, expected_hash: str) -> bool:
    """Verify SHA-256 of canonical JSON matches the expected hash."""
    canonical = _canonical_json(snapshot_data)
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return actual == expected_hash


# ── Path validation ───────────────────────────────────────

def _validate_file_path(target: str, workspace_root: str) -> tuple[bool, str]:
    """Validate a file action target path against workspace boundaries.

    Rules:
    1. Must be a relative path (no absolute paths)
    2. No path traversal escaping workspace
    3. Resolved real path must stay within workspace
    4. Symlink resolution must not escape workspace

    Returns:
        (is_valid, reason) — reason is empty string if valid.
    """
    if not target or not target.strip():
        return False, "Empty file path"

    # Rule 1: reject absolute paths
    if os.path.isabs(target):
        return False, f"Absolute path not allowed: {target}"

    # Rule 2+3: resolve and check containment
    resolved = os.path.normpath(os.path.join(workspace_root, target))
    real_resolved = os.path.realpath(resolved)
    real_workspace = os.path.realpath(workspace_root)

    # Ensure resolved path is within workspace
    if real_resolved == real_workspace:
        return False, "Path resolves to workspace root itself"

    if not real_resolved.startswith(real_workspace + os.sep):
        return False, f"Path escapes workspace boundary: {target}"

    return True, ""


# ── DB helpers ────────────────────────────────────────────

def _fetch_row(conn: Any, table: str, id_value: str) -> dict | None:
    """Fetch a single row by id, return as dict or None."""
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id = ?", (id_value,)
    ).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute(
        f"SELECT * FROM {table} LIMIT 0"
    ).description]
    return dict(zip(cols, row))


# ── Main eligibility check ────────────────────────────────

def check_execution_eligibility(execution_request_id: str) -> dict:
    """Check whether an execution request is eligible for real execution.

    Pure read-only check. No side effects, no DB writes.

    Checks (in order):
    1. execution_request exists
    2. status == "confirmed"
    3. linked project exists with valid workspace
    4. linked snapshot exists
    5. snapshot content_hash verification
    6. action plan builds successfully
    7. no denied actions
    8. no needs_confirmation actions
    9. all action types in ALLOWED_ACTION_TYPES
    10. all file paths within workspace boundary
    11. command_run safety checks (whitelist, shell syntax, workdir)
    12. dry-run result exists and completed

    Returns dict with:
        eligible: bool
        blocked_reasons: list[str]
        allowed_action_count: int
        blocked_action_count: int
        total_action_count: int
        allowed_action_types: list[str]
        blocked_action_types: list[str]
        workspace_root: str | None
        summary: str
    """
    blocked_reasons: list[str] = []
    workspace_root: str | None = None
    action_plan: dict | None = None
    total_action_count = 0
    allowed_action_count = 0
    blocked_action_count = 0
    allowed_action_types: set[str] = set()
    blocked_action_types: set[str] = set()

    conn = get_connection()
    try:
        # ── 1. Fetch execution request ────────────────────────
        req = _fetch_row(conn, "execution_requests", execution_request_id)
        if not req:
            blocked_reasons.append("request_not_found")
            return _build_result(
                False, blocked_reasons, 0, 0, 0,
                [], [], None, "Execution request not found",
            )

        # ── 2. Check confirmed status ─────────────────────────
        if req["status"] != "confirmed":
            blocked_reasons.append("request_not_confirmed")

        # ── 3. Check project workspace ────────────────────────
        project_row = conn.execute(
            """SELECT p.local_repo_path
               FROM tasks t JOIN projects p ON t.project_id = p.id
               WHERE t.id = ?""",
            (req["task_id"],),
        ).fetchone()

        if not project_row or not project_row[0] or not project_row[0].strip():
            blocked_reasons.append("project_workspace_invalid")
        else:
            workspace_root = project_row[0]

        # ── 4. Fetch snapshot ─────────────────────────────────
        snap = _fetch_row(conn, "execution_snapshots", req["snapshot_id"])
        if not snap:
            blocked_reasons.append("snapshot_missing")

        # ── 5. Verify content hash ────────────────────────────
        if snap:
            try:
                if not _verify_content_hash(
                    snap["snapshot_data"], snap["content_hash"]
                ):
                    blocked_reasons.append("snapshot_hash_mismatch")
            except Exception:
                blocked_reasons.append("snapshot_hash_mismatch")

        # ── 6. Build action plan ──────────────────────────────
        if snap:
            try:
                action_plan = build_action_plan(snap["snapshot_data"])
            except Exception:
                blocked_reasons.append("action_plan_build_failed")

        # ── 7-9. Check action plan contents ───────────────────
        if action_plan:
            actions = action_plan.get("actions", [])
            total_action_count = len(actions)

            # 7. Check for denied actions
            if action_plan.get("has_denied", False):
                blocked_reasons.append("has_denied_actions")

            # 8. Check for unconfirmed actions
            if action_plan.get("needs_confirmation_count", 0) > 0:
                blocked_reasons.append("has_unconfirmed_actions")

            # 9. Check action types + 10. Check file paths
            for a in actions:
                a_type = a.get("type", "unsupported")
                if a_type in ALLOWED_ACTION_TYPES:
                    allowed_action_types.add(a_type)
                    allowed_action_count += 1
                else:
                    blocked_action_types.add(a_type)
                    blocked_action_count += 1

            if blocked_action_types:
                blocked_reasons.append("has_disallowed_action_types")

            # 10. Workspace boundary check (file actions)
            if workspace_root:
                for a in actions:
                    a_type = a.get("type", "")
                    if a_type in (
                        ActionType.file_create.value,
                        ActionType.file_modify.value,
                    ):
                        target = a.get("target", "")
                        valid, _reason = _validate_file_path(
                            target, workspace_root
                        )
                        if not valid:
                            if "workspace_boundary_violation" not in blocked_reasons:
                                blocked_reasons.append(
                                    "workspace_boundary_violation"
                                )
                            break

            # 11. Command-specific checks (Phase 8A)
            for a in actions:
                if a.get("type") != ActionType.command_run.value:
                    continue
                target = a.get("target", "")
                cmd_reasons = _validate_command(target)
                for r in cmd_reasons:
                    if r not in blocked_reasons:
                        blocked_reasons.append(r)

                # Check working_dir boundary
                if workspace_root:
                    working_dir = a.get("params", {}).get("working_dir")
                    if working_dir and not _validate_command_workdir(
                        working_dir, workspace_root
                    ):
                        if "command_workdir_outside_workspace" not in blocked_reasons:
                            blocked_reasons.append(
                                "command_workdir_outside_workspace"
                            )

        # ── 12. Check dry-run result ──────────────────────────
        dr_row = conn.execute(
            """SELECT status FROM execution_results
               WHERE execution_request_id = ? AND mode = 'dry_run'""",
            (execution_request_id,),
        ).fetchone()
        if not dr_row:
            blocked_reasons.append("dry_run_not_completed")
        elif dr_row[0] != "completed":
            blocked_reasons.append("dry_run_not_completed")

    finally:
        conn.close()

    # ── Build summary ─────────────────────────────────────
    eligible = len(blocked_reasons) == 0
    if eligible:
        summary = (
            f"Eligible: {total_action_count} action(s), "
            f"all {', '.join(sorted(allowed_action_types))}"
        )
    else:
        summary = (
            f"Not eligible: {len(blocked_reasons)} reason(s) — "
            + ", ".join(blocked_reasons)
        )

    return _build_result(
        eligible,
        blocked_reasons,
        allowed_action_count,
        blocked_action_count,
        total_action_count,
        sorted(allowed_action_types),
        sorted(blocked_action_types),
        workspace_root,
        summary,
    )


def _build_result(
    eligible: bool,
    blocked_reasons: list[str],
    allowed_action_count: int,
    blocked_action_count: int,
    total_action_count: int,
    allowed_action_types: list[str],
    blocked_action_types: list[str],
    workspace_root: str | None,
    summary: str,
) -> dict:
    """Construct the standardized eligibility result dict."""
    return {
        "eligible": eligible,
        "blocked_reasons": blocked_reasons,
        "allowed_action_count": allowed_action_count,
        "blocked_action_count": blocked_action_count,
        "total_action_count": total_action_count,
        "allowed_action_types": allowed_action_types,
        "blocked_action_types": blocked_action_types,
        "workspace_root": workspace_root,
        "summary": summary,
    }
