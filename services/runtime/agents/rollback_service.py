"""Rollback service for Phase 7C-2.

Reverts the effects of a real_run execution:
- file_modify (success): restores original content from backup
- file_create (success): deletes the created file

Strategy: best-effort (continues on individual file failure).
Safety: re-validates paths, symlinks, sensitive dirs before each operation.

Usage::

    from agents.rollback_service import rollback_execution

    result = rollback_execution(execution_result_id, workspace_root)
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from agents.execution_eligibility_service import _validate_file_path
from agents.scoped_file_executor import _file_sha256, _is_sensitive_path
from database import get_connection


def rollback_execution(
    execution_result_id: str,
    workspace_root: str,
) -> dict:
    """Rollback all success files from a real_run execution result.

    Parameters
    ----------
    execution_result_id : str
        Must reference a real_run result with status completed or failed.
    workspace_root : str
        Explicit project workspace path.

    Returns
    -------
    dict
        Rollback result record (also persisted to execution_results table).

    Raises
    ------
    ValueError
        If result not found, not real_run, or workspace invalid.
    """
    # ── 0. Validate workspace_root ────────────────────────
    if not workspace_root or not workspace_root.strip():
        raise ValueError("workspace_root must be explicitly provided")
    real_workspace = os.path.realpath(workspace_root)
    if not os.path.isdir(real_workspace):
        raise ValueError(f"workspace_root is not a directory: {workspace_root}")

    conn = get_connection()
    try:
        # ── 1. Fetch and validate execution result ────────
        row = conn.execute(
            "SELECT * FROM execution_results WHERE id = ?",
            (execution_result_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Execution result not found: {execution_result_id}")

        result = dict(row)

        if result.get("mode") != "real_run":
            raise ValueError(
                f"Only real_run results can be rolled back (got mode={result.get('mode')})"
            )

        if result["status"] not in ("completed", "failed"):
            raise ValueError(
                f"Cannot rollback result with status={result['status']}"
            )

        # ── 2. Check for existing rollback (idempotent) ───
        existing = conn.execute(
            "SELECT * FROM execution_results WHERE execution_request_id = ? AND mode = 'rollback'",
            (result["execution_request_id"],),
        ).fetchone()
        if existing:
            return dict(existing)

        # ── 3. Parse file_results from real_run ───────────
        rd = result.get("result_data", "{}")
        if isinstance(rd, str):
            try:
                rd = json.loads(rd)
            except (json.JSONDecodeError, TypeError):
                rd = {}

        file_results = rd.get("file_results", [])

        # ── 4. Load backups ───────────────────────────────
        backup_rows = conn.execute(
            "SELECT * FROM execution_file_backups WHERE execution_result_id = ?",
            (execution_result_id,),
        ).fetchall()
        backups_by_path: dict[str, dict] = {}
        for br in backup_rows:
            bd = dict(br)
            backups_by_path[bd["path"]] = bd

        # ── 5. Rollback each success file (best-effort) ──
        rollback_results: list[dict] = []
        restore_count = 0
        fail_count = 0

        for fr in file_results:
            path = fr.get("path", "")
            operation = fr.get("operation", "")
            status = fr.get("status", "")

            # Only rollback success files
            if status != "success":
                continue

            rb_entry: dict[str, Any] = {
                "path": path,
                "operation": operation,
                "rollback_action": "",
                "status": "pending",
            }

            # a. Path boundary check
            valid, reason = _validate_file_path(path, real_workspace)
            if not valid:
                rb_entry["status"] = "failed"
                rb_entry["error"] = f"Path validation: {reason}"
                rollback_results.append(rb_entry)
                fail_count += 1
                continue  # best-effort: continue to next file

            # b. Sensitive path check
            sensitive, s_reason = _is_sensitive_path(path)
            if sensitive:
                rb_entry["status"] = "failed"
                rb_entry["error"] = f"Sensitive path: {s_reason}"
                rollback_results.append(rb_entry)
                fail_count += 1
                continue

            # c. Resolve full path
            full_path = os.path.normpath(os.path.join(real_workspace, path))
            real_full = os.path.realpath(full_path)

            # d. Symlink check
            if os.path.islink(full_path):
                rb_entry["status"] = "failed"
                rb_entry["error"] = "Target is a symlink"
                rollback_results.append(rb_entry)
                fail_count += 1
                continue

            # e. Resolved path boundary
            if not real_full.startswith(real_workspace + os.sep):
                rb_entry["status"] = "failed"
                rb_entry["error"] = "Resolved path escapes workspace"
                rollback_results.append(rb_entry)
                fail_count += 1
                continue

            # ── Perform rollback action ───────────────────
            if operation == "modify":
                rb_entry["rollback_action"] = "restore"

                backup = backups_by_path.get(path)
                if not backup:
                    rb_entry["status"] = "failed"
                    rb_entry["error"] = "Backup not found for modified file"
                    rollback_results.append(rb_entry)
                    fail_count += 1
                    continue

                try:
                    parent_dir = os.path.dirname(full_path)
                    fd, tmp_path = tempfile.mkstemp(
                        prefix=".tmp_rb_",
                        dir=parent_dir,
                    )
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                            tmp_f.write(backup["original_content"])
                        os.replace(tmp_path, full_path)
                    except Exception:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                        raise

                    # Verify restored hash
                    restored_hash = _file_sha256(full_path)
                    rb_entry["restored_hash"] = restored_hash
                    rb_entry["expected_hash"] = backup["original_hash"]

                    if restored_hash == backup["original_hash"]:
                        rb_entry["status"] = "restored"
                        restore_count += 1
                    else:
                        rb_entry["status"] = "restored_hash_mismatch"
                        rb_entry["error"] = "Restored but hash differs from backup"
                        restore_count += 1  # file was written, just hash differs

                except Exception as e:
                    rb_entry["status"] = "failed"
                    rb_entry["error"] = str(e)
                    fail_count += 1

                rollback_results.append(rb_entry)

            elif operation == "create":
                rb_entry["rollback_action"] = "delete"

                if not os.path.exists(full_path):
                    rb_entry["status"] = "already_absent"
                    restore_count += 1
                    rollback_results.append(rb_entry)
                    continue

                try:
                    os.unlink(full_path)
                    rb_entry["status"] = "deleted"
                    restore_count += 1
                except Exception as e:
                    rb_entry["status"] = "failed"
                    rb_entry["error"] = str(e)
                    fail_count += 1

                rollback_results.append(rb_entry)

        # ── 6. Record rollback result ─────────────────────
        overall_status = "completed" if fail_count == 0 else "failed"
        now = datetime.now(timezone.utc).isoformat()
        rollback_id = str(_uuid.uuid4())

        rollback_data = {
            "mode": "rollback",
            "source_execution_result_id": execution_result_id,
            "execution_request_id": result["execution_request_id"],
            "summary": f"Rollback {overall_status}: {restore_count} restored, {fail_count} failed",
            "rollback_results": rollback_results,
        }

        conn.execute(
            """INSERT INTO execution_results
               (id, execution_request_id, task_id, snapshot_id,
                snapshot_content_hash, mode, status, result_data,
                started_at, completed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rollback_id,
                result["execution_request_id"],
                result["task_id"],
                result["snapshot_id"],
                result["snapshot_content_hash"],
                "rollback",
                overall_status,
                json.dumps(rollback_data),
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
                result["task_id"],
                None,
                "info" if overall_status == "completed" else "warn",
                "rollback_service",
                f"Rollback {overall_status}: {restore_count} restored, {fail_count} failed",
                json.dumps({
                    "event_type": f"rollback:{overall_status}",
                    "execution_request_id": result["execution_request_id"],
                    "source_execution_result_id": execution_result_id,
                    "rollback_result_id": rollback_id,
                    "restore_count": restore_count,
                    "fail_count": fail_count,
                }),
                now,
            ),
        )

        conn.commit()

        return {
            "id": rollback_id,
            "execution_request_id": result["execution_request_id"],
            "task_id": result["task_id"],
            "snapshot_id": result["snapshot_id"],
            "snapshot_content_hash": result["snapshot_content_hash"],
            "mode": "rollback",
            "status": overall_status,
            "result_data": json.dumps(rollback_data),
            "started_at": now,
            "completed_at": now,
            "created_at": now,
        }

    finally:
        conn.close()
