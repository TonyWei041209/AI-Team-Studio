"""Dry-run execution service for Phase 6F-A Step 2.

Takes a confirmed execution request, reads its frozen snapshot,
and produces a simulated execution result.  No real file / shell / git
operations are performed — only a structured dry-run report is recorded.
"""

from __future__ import annotations

import hashlib
import json
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


def run_dry_execution(execution_request_id: str) -> dict:
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

    Returns:
        dict with execution result fields.

    Raises:
        ValueError: request not found, not confirmed, snapshot missing,
                    or hash mismatch.
    """
    conn = get_connection()
    try:
        # ── Idempotent: return existing result ──────────────────────
        existing = conn.execute(
            "SELECT * FROM execution_results WHERE execution_request_id = ?",
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

        # ── Build dry-run report ────────────────────────────────────
        warnings: list[str] = []

        planned_file_actions: list[dict] = []
        for f in proposed_files:
            action = {
                "path": f.get("path", "<unknown>"),
                "operation": f.get("operation", "unknown"),
                "dry_run": True,
                "executed": False,
            }
            planned_file_actions.append(action)

        planned_command_actions: list[dict] = []
        for c in proposed_commands:
            cmd = c if isinstance(c, str) else c.get("command", str(c))
            action = {
                "command": cmd,
                "dry_run": True,
                "executed": False,
            }
            planned_command_actions.append(action)

        if not proposed_files and not proposed_commands:
            warnings.append("Snapshot contains no proposed_files or proposed_commands")

        result_data = {
            "mode": "dry_run",
            "execution_request_id": execution_request_id,
            "snapshot_id": snap["id"],
            "snapshot_content_hash": snap["content_hash"],
            "summary": summary,
            "planned_file_actions": planned_file_actions,
            "planned_command_actions": planned_command_actions,
            "warnings": warnings,
        }

        # ── Create execution_results record ─────────────────────────
        result_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        status = "completed"

        conn.execute(
            """INSERT INTO execution_results
               (id, execution_request_id, task_id, snapshot_id,
                snapshot_content_hash, status, result_data,
                started_at, completed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result_id,
                execution_request_id,
                req["task_id"],
                snap["id"],
                snap["content_hash"],
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
                f"Dry-run execution completed for request {execution_request_id}",
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
