"""Scoped file executor for Phase 7A Step 2B.

Executes only file_create and file_modify actions from a confirmed,
eligible execution request.  Writes real files to the project workspace.

Constraints:
- Only file_create / file_modify (no delete, shell, git)
- workspace_root must be explicitly provided
- All paths validated against workspace boundary (including parent chain)
- Symlink targets resolved and checked against workspace
- No auto-creation of multi-level parent directories
- Sensitive paths denied (.git/, .env, node_modules/, etc.)
- UTF-8 text only
- Atomic writes via temp file + os.replace()
- before/after SHA-256 recorded for each file
- Fail-fast: first failure stops execution, no rollback

Usage::

    from agents.scoped_file_executor import execute_scoped_files

    result = execute_scoped_files(execution_request_id, workspace_root)
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from agents.action_policy_service import ActionType, build_action_plan
from agents.execution_eligibility_service import (
    ALLOWED_ACTION_TYPES,
    _validate_file_path,
    _verify_content_hash,
    check_execution_eligibility,
)
from database import get_connection


# ── Sensitive path deny list ──────────────────────────────

_SENSITIVE_PATH_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".git\\",
    ".env",
    ".venv/",
    ".venv\\",
    "node_modules/",
    "node_modules\\",
    "dist/",
    "dist\\",
    "build/",
    "build\\",
    "__pycache__/",
    "__pycache__\\",
)

_SENSITIVE_EXACT: frozenset[str] = frozenset({
    ".env",
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
})


def _is_sensitive_path(target: str) -> tuple[bool, str]:
    """Check if a path targets a sensitive location.

    Returns (is_sensitive, reason).
    """
    normalized = target.replace("\\", "/")

    # Check exact matches
    if normalized in _SENSITIVE_EXACT:
        return True, f"Sensitive path: {target}"

    # Check prefix matches (directory entries)
    for prefix in _SENSITIVE_PATH_PREFIXES:
        p = prefix.replace("\\", "/")
        if normalized.startswith(p) or ("/" + p) in normalized:
            stripped = prefix.rstrip("/\\")
            return True, f"Path inside sensitive directory: {stripped}"

    # Also check if any path component is sensitive
    parts = normalized.split("/")
    for part in parts:
        if part in _SENSITIVE_EXACT:
            return True, f"Path contains sensitive component: {part}"

    return False, ""


# ── File hash helper ──────────────────────────────────────

def _file_sha256(path: str) -> str | None:
    """Compute SHA-256 of a file. Returns None if file doesn't exist."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, IOError):
        return None


# ── Core executor ─────────────────────────────────────────

def execute_scoped_files(
    execution_request_id: str,
    workspace_root: str,
) -> dict:
    """Execute file_create / file_modify actions from a confirmed request.

    Parameters
    ----------
    execution_request_id : str
        Must pass eligibility check.
    workspace_root : str
        Explicit project workspace path. Must be an existing directory.

    Returns
    -------
    dict
        Execution result record (also persisted to execution_results table).

    Raises
    ------
    ValueError
        If eligibility check fails, hash mismatch, or workspace invalid.
    """
    # ── 0. Validate workspace_root ────────────────────────
    if not workspace_root or not workspace_root.strip():
        raise ValueError("workspace_root must be explicitly provided")
    real_workspace = os.path.realpath(workspace_root)
    if not os.path.isdir(real_workspace):
        raise ValueError(f"workspace_root is not a directory: {workspace_root}")

    # ── 1. Eligibility check ──────────────────────────────
    elig = check_execution_eligibility(execution_request_id)
    if not elig["eligible"]:
        raise ValueError(
            f"Execution request not eligible: {elig['blocked_reasons']}"
        )

    # ── 2. Fetch snapshot and re-verify hash ──────────────
    conn = get_connection()
    try:
        req_row = conn.execute(
            "SELECT * FROM execution_requests WHERE id = ?",
            (execution_request_id,),
        ).fetchone()
        req = dict(req_row)

        snap_row = conn.execute(
            "SELECT * FROM execution_snapshots WHERE id = ?",
            (req["snapshot_id"],),
        ).fetchone()
        snap = dict(snap_row)

        if not _verify_content_hash(snap["snapshot_data"], snap["content_hash"]):
            raise ValueError("Snapshot content_hash mismatch at execution time")

        # ── 3. Parse snapshot and extract file actions ─────
        data = json.loads(snap["snapshot_data"])
        proposed_files = data.get("proposed_files", [])

        # ── 4. Check for existing real_run result (idempotent) ──
        existing = conn.execute(
            "SELECT * FROM execution_results WHERE execution_request_id = ? AND mode = 'real_run'",
            (execution_request_id,),
        ).fetchone()
        if existing:
            return dict(existing)

        # ── 5. Execute file actions ───────────────────────
        file_results: list[dict] = []
        stopped_at: int | None = None
        stop_reason: str | None = None
        success_count = 0
        fail_count = 0

        for i, f in enumerate(proposed_files):
            path = f.get("path", "")
            operation = f.get("operation", "")
            content = f.get("content")

            # Skip non-file actions (e.g., delete, unsupported)
            if operation not in ("create", "modify"):
                file_results.append({
                    "path": path,
                    "operation": operation,
                    "status": "skipped",
                    "reason": f"Operation '{operation}' not allowed in 7A",
                    "before_hash": None,
                    "after_hash": None,
                })
                continue

            # ── Pre-write validation ──────────────────────
            result_entry: dict = {
                "path": path,
                "operation": operation,
                "status": "pending",
                "before_hash": None,
                "after_hash": None,
            }

            # a. Path boundary check
            valid, reason = _validate_file_path(path, real_workspace)
            if not valid:
                result_entry["status"] = "failed"
                result_entry["error"] = reason
                file_results.append(result_entry)
                stopped_at = i
                stop_reason = f"Path validation failed for '{path}': {reason}"
                fail_count += 1
                break

            # b. Sensitive path check
            sensitive, s_reason = _is_sensitive_path(path)
            if sensitive:
                result_entry["status"] = "failed"
                result_entry["error"] = s_reason
                file_results.append(result_entry)
                stopped_at = i
                stop_reason = f"Sensitive path denied: '{path}'"
                fail_count += 1
                break

            # c. Resolve full path
            full_path = os.path.normpath(os.path.join(real_workspace, path))
            real_full = os.path.realpath(full_path)

            # d. Symlink check on target
            if os.path.islink(full_path):
                result_entry["status"] = "failed"
                result_entry["error"] = "Target is a symlink"
                file_results.append(result_entry)
                stopped_at = i
                stop_reason = f"Symlink target rejected: '{path}'"
                fail_count += 1
                break

            # e. Re-verify resolved path still in workspace
            if not real_full.startswith(real_workspace + os.sep):
                result_entry["status"] = "failed"
                result_entry["error"] = "Resolved path escapes workspace"
                file_results.append(result_entry)
                stopped_at = i
                stop_reason = f"Resolved path escapes workspace: '{path}'"
                fail_count += 1
                break

            # f. Parent directory check
            parent_dir = os.path.dirname(full_path)
            if not os.path.isdir(parent_dir):
                result_entry["status"] = "failed"
                result_entry["error"] = "Parent directory does not exist"
                file_results.append(result_entry)
                stopped_at = i
                stop_reason = f"Parent directory missing for '{path}'"
                fail_count += 1
                break

            # g. Parent directory symlink check
            real_parent = os.path.realpath(parent_dir)
            if not real_parent.startswith(real_workspace + os.sep) and real_parent != real_workspace:
                result_entry["status"] = "failed"
                result_entry["error"] = "Parent directory resolves outside workspace"
                file_results.append(result_entry)
                stopped_at = i
                stop_reason = f"Parent dir escapes workspace: '{path}'"
                fail_count += 1
                break

            # h. Operation-specific checks
            if operation == "create":
                if os.path.exists(full_path):
                    result_entry["status"] = "failed"
                    result_entry["error"] = "File already exists (create conflict)"
                    file_results.append(result_entry)
                    stopped_at = i
                    stop_reason = f"File already exists: '{path}'"
                    fail_count += 1
                    break
            elif operation == "modify":
                if not os.path.exists(full_path):
                    result_entry["status"] = "failed"
                    result_entry["error"] = "File does not exist (modify target missing)"
                    file_results.append(result_entry)
                    stopped_at = i
                    stop_reason = f"File not found for modify: '{path}'"
                    fail_count += 1
                    break

            # i. Content validation
            if content is None or not isinstance(content, str):
                result_entry["status"] = "failed"
                result_entry["error"] = "Content must be a non-null string"
                file_results.append(result_entry)
                stopped_at = i
                stop_reason = f"Invalid content for '{path}'"
                fail_count += 1
                break

            # ── Execute write ─────────────────────────────
            before_hash = _file_sha256(full_path) if operation == "modify" else None
            result_entry["before_hash"] = before_hash

            try:
                # Atomic write: temp file + os.replace()
                fd, tmp_path = tempfile.mkstemp(
                    prefix=".tmp_exec_",
                    dir=parent_dir,
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                        tmp_f.write(content)
                    os.replace(tmp_path, full_path)
                except Exception:
                    # Clean up temp file on failure
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise

                after_hash = _file_sha256(full_path)
                result_entry["after_hash"] = after_hash
                result_entry["status"] = "success"
                file_results.append(result_entry)
                success_count += 1

            except Exception as e:
                result_entry["status"] = "failed"
                result_entry["error"] = str(e)
                file_results.append(result_entry)
                stopped_at = i
                stop_reason = f"Write failed for '{path}': {e}"
                fail_count += 1
                break

        # ── Mark remaining files as skipped ───────────────
        if stopped_at is not None:
            for j in range(stopped_at + 1, len(proposed_files)):
                fj = proposed_files[j]
                file_results.append({
                    "path": fj.get("path", ""),
                    "operation": fj.get("operation", ""),
                    "status": "skipped",
                    "reason": "Execution stopped due to earlier failure",
                    "before_hash": None,
                    "after_hash": None,
                })

        # ── 6. Record result ──────────────────────────────
        overall_status = "completed" if fail_count == 0 else "failed"
        now = datetime.now(timezone.utc).isoformat()
        result_id = str(_uuid.uuid4())

        result_data = {
            "mode": "real_run",
            "execution_request_id": execution_request_id,
            "snapshot_id": snap["id"],
            "snapshot_content_hash": snap["content_hash"],
            "summary": f"Real execution: {success_count} file(s) written, {fail_count} failed",
            "file_results": file_results,
            "stopped_at": stopped_at,
            "stop_reason": stop_reason,
        }

        conn.execute(
            """INSERT INTO execution_results
               (id, execution_request_id, task_id, snapshot_id,
                snapshot_content_hash, mode, status, result_data,
                started_at, completed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result_id,
                execution_request_id,
                req["task_id"],
                snap["id"],
                snap["content_hash"],
                "real_run",
                overall_status,
                json.dumps(result_data),
                now,
                now,
                now,
            ),
        )

        # ── Audit log ─────────────────────────────────────
        log_id = str(_uuid.uuid4())
        conn.execute(
            """INSERT INTO log_events
               (id, task_id, run_id, level, source, message, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id,
                req["task_id"],
                None,
                "info" if overall_status == "completed" else "warn",
                "scoped_file_executor",
                f"Real execution {overall_status}: {success_count} written, {fail_count} failed",
                json.dumps({
                    "event_type": f"execution_result:{overall_status}",
                    "execution_request_id": execution_request_id,
                    "execution_result_id": result_id,
                    "mode": "real_run",
                    "success_count": success_count,
                    "fail_count": fail_count,
                    "stopped_at": stopped_at,
                }),
                now,
            ),
        )

        conn.commit()

        return {
            "id": result_id,
            "execution_request_id": execution_request_id,
            "task_id": req["task_id"],
            "snapshot_id": snap["id"],
            "snapshot_content_hash": snap["content_hash"],
            "mode": "real_run",
            "status": overall_status,
            "result_data": json.dumps(result_data),
            "started_at": now,
            "completed_at": now,
            "created_at": now,
        }

    finally:
        conn.close()
