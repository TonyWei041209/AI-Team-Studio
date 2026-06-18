"""Dry-run execution service for Phase 6F-A Step 2.

Takes a confirmed execution request, reads its frozen snapshot,
and produces a simulated execution result.  No real file / shell / git
operations are performed — only a structured dry-run report is recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

from database import get_connection


def _canonical_json(data: str) -> str:
    """Re-serialize JSON with sorted keys for stable hashing."""
    return json.dumps(json.loads(data), sort_keys=True, separators=(",", ":"))


def _verify_content_hash(snapshot_data: str, expected_hash: str) -> bool:
    """Verify SHA-256 of canonical JSON matches the expected hash."""
    canonical = _canonical_json(snapshot_data)
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return actual == expected_hash


def run_dry_execution(
    execution_request_id: str,
    strict_parent: bool = True,
) -> dict:
    """Run a dry-run execution for a confirmed execution request.

    Rules:
    - Execution request must exist and have status 'confirmed'.
    - If an execution_result already exists for this request, return it
      (idempotent).
    - Reads the linked snapshot's snapshot_data.
    - Verifies snapshot content_hash integrity.
    - Iterates proposed_files and proposed_commands, recording each as a
      dry-run step — no real operations.
    - Creates an execution_results record (status 'completed' or 'failed').
    - Writes audit log.

    strict_parent (route-3, default True): when True the dry-run PREDICTS the
    real executor's fail-fast on a missing parent directory — a planned file
    whose parent dir does not exist is marked 'failed' and subsequent
    file/command actions 'skipped', so the preview matches real execution.
    When False, a missing-but-in-workspace parent is treated as auto-created
    (action stays ok). This is a PURE SIMULATION: parent existence is only
    INSPECTED (os.path.isdir) — no directories are ever created.

    Returns:
        dict with execution result fields.

    Raises:
        ValueError: request not found, not confirmed, snapshot missing,
                    or hash mismatch.
    """
    conn = get_connection()
    try:
        # ── Idempotent: return existing dry_run result ─────────────
        existing = conn.execute(
            "SELECT * FROM execution_results WHERE execution_request_id = ? AND mode = 'dry_run'",
            (execution_request_id,),
        ).fetchone()
        if existing:
            cols = [d[0] for d in conn.execute(
                "SELECT * FROM execution_results LIMIT 0"
            ).description]
            return dict(zip(cols, existing))

        # ── Validate execution request ──────────────────────────────
        req_row = conn.execute(
            "SELECT * FROM execution_requests WHERE id = ?",
            (execution_request_id,),
        ).fetchone()
        if not req_row:
            raise ValueError(
                f"Execution request not found: {execution_request_id}"
            )

        req_cols = [d[0] for d in conn.execute(
            "SELECT * FROM execution_requests LIMIT 0"
        ).description]
        req = dict(zip(req_cols, req_row))

        if req["status"] != "confirmed":
            raise ValueError(
                f"Execution request not confirmed (status={req['status']})"
            )

        # ── Fetch snapshot ──────────────────────────────────────────
        snap_row = conn.execute(
            "SELECT * FROM execution_snapshots WHERE id = ?",
            (req["snapshot_id"],),
        ).fetchone()
        if not snap_row:
            raise ValueError(
                f"Snapshot not found: {req['snapshot_id']}"
            )

        snap_cols = [d[0] for d in conn.execute(
            "SELECT * FROM execution_snapshots LIMIT 0"
        ).description]
        snap = dict(zip(snap_cols, snap_row))

        # ── Verify content hash ─────────────────────────────────────
        if not _verify_content_hash(snap["snapshot_data"], snap["content_hash"]):
            raise ValueError(
                "Snapshot content_hash mismatch — data may have been tampered"
            )

        # ── Parse snapshot data ─────────────────────────────────────
        try:
            plan = json.loads(snap["snapshot_data"])
        except (json.JSONDecodeError, TypeError):
            plan = {}

        proposed_files = plan.get("proposed_files", [])
        proposed_commands = plan.get("proposed_commands", [])
        summary = plan.get("summary", "No summary in snapshot")

        # ── Resolve workspace (read-only) for parent prediction ─────
        # Only predict parent-directory outcomes when a REAL workspace dir
        # is visible; otherwise fall back to a pure listing so callers with
        # placeholder / non-existent workspaces are unaffected.
        real_workspace = None
        proj_row = conn.execute(
            """SELECT p.local_repo_path
               FROM tasks t JOIN projects p ON p.id = t.project_id
               WHERE t.id = ?""",
            (req["task_id"],),
        ).fetchone()
        if proj_row and proj_row[0] and os.path.isdir(proj_row[0]):
            real_workspace = os.path.realpath(proj_row[0])

        # ── Build dry-run report (honors strict_parent; pure simulation) ──
        warnings: list[str] = []
        predicted_failure = False
        stop_reason = None

        planned_file_actions: list[dict] = []
        for f in proposed_files:
            path = f.get("path", "<unknown>")
            operation = f.get("operation") or f.get("action") or "unknown"
            action = {
                "path": path,
                "operation": operation,
                "dry_run": True,
                "executed": False,
                "status": "ok",
            }

            if predicted_failure:
                # Fail-fast: a prior predicted failure stops the batch.
                action["status"] = "skipped"
                action["reason"] = "Would be skipped due to earlier predicted failure"
                planned_file_actions.append(action)
                continue

            # Parent-directory prediction — READ-ONLY (never creates dirs).
            if real_workspace and operation in ("create", "modify"):
                full_path = os.path.normpath(os.path.join(real_workspace, path))
                parent_dir = os.path.dirname(full_path)
                if not os.path.isdir(parent_dir):
                    real_parent = os.path.realpath(os.path.normpath(parent_dir))
                    within = (
                        real_parent == real_workspace
                        or real_parent.startswith(real_workspace + os.sep)
                    )
                    if strict_parent or not within:
                        # strict_parent → fail-fast; out-of-workspace always fails.
                        action["status"] = "failed"
                        action["reason"] = (
                            "Parent directory does not exist"
                            if within else "Parent directory outside workspace"
                        )
                        predicted_failure = True
                        stop_reason = f"Parent directory missing for '{path}'"
                    # else (not strict, within workspace): would be auto-created
                    # by the real executor → action stays "ok".

            planned_file_actions.append(action)

        planned_command_actions: list[dict] = []
        for c in proposed_commands:
            cmd = c if isinstance(c, str) else c.get("command", str(c))
            action = {
                "command": cmd,
                "dry_run": True,
                "executed": False,
                "status": "skipped" if predicted_failure else "ok",
            }
            if predicted_failure:
                action["reason"] = (
                    "Would be skipped due to predicted file execution failure"
                )
            planned_command_actions.append(action)

        if not proposed_files and not proposed_commands:
            warnings.append("Snapshot contains no proposed_files or proposed_commands")

        result_data = {
            "mode": "dry_run",
            "execution_request_id": execution_request_id,
            "snapshot_id": snap["id"],
            "snapshot_content_hash": snap["content_hash"],
            "summary": summary,
            "strict_parent": strict_parent,
            "planned_file_actions": planned_file_actions,
            "planned_command_actions": planned_command_actions,
            "stop_reason": stop_reason,
            "warnings": warnings,
        }

        # ── Create execution_results record ─────────────────────────
        result_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        status = "failed" if predicted_failure else "completed"

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
                "dry_run",
                status,
                json.dumps(result_data),
                now,   # started_at
                now,   # completed_at (dry-run is instant)
                now,
            ),
        )

        # ── Audit log ───────────────────────────────────────────────
        log_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO log_events
               (id, task_id, run_id, level, source, message, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id,
                req["task_id"],
                None,
                "info",
                "execution_result_service",
                f"Dry-run execution {status} for request {execution_request_id}",
                json.dumps({
                    "event_type": "execution_result:dry_run",
                    "execution_request_id": execution_request_id,
                    "execution_result_id": result_id,
                    "snapshot_id": snap["id"],
                    "status": status,
                    "file_actions": len(planned_file_actions),
                    "command_actions": len(planned_command_actions),
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
            "status": status,
            "result_data": json.dumps(result_data),
            "started_at": now,
            "completed_at": now,
            "created_at": now,
        }
    finally:
        conn.close()
