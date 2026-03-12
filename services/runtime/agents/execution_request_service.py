"""Execution request service for Phase 6E-C.

Creates execution requests bound to frozen snapshots.
No actual execution occurs — this records intent only.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from database import get_connection


def create_execution_request(snapshot_id: str) -> dict:
    """Create an execution request for a frozen snapshot.

    Rules:
    - Snapshot must exist and have status 'frozen'.
    - If a request already exists for this snapshot, return it (idempotent).
    - Inherits task_id, proposal_id, approval_id, content_hash, risk_level
      from the snapshot.

    Returns:
        dict with execution request fields.

    Raises:
        ValueError: snapshot not found or not frozen.
    """
    conn = get_connection()
    try:
        # Idempotent: check for existing request
        existing = conn.execute(
            "SELECT * FROM execution_requests WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if existing:
            cols = [d[0] for d in conn.execute(
                "SELECT * FROM execution_requests LIMIT 0"
            ).description]
            return dict(zip(cols, existing))

        # Fetch snapshot
        snapshot = conn.execute(
            "SELECT * FROM execution_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        if not snapshot:
            raise ValueError(f"Snapshot not found: {snapshot_id}")

        snap_cols = [d[0] for d in conn.execute(
            "SELECT * FROM execution_snapshots LIMIT 0"
        ).description]
        snap_dict = dict(zip(snap_cols, snapshot))

        if snap_dict["status"] != "frozen":
            raise ValueError(
                f"Snapshot not frozen (status={snap_dict['status']})"
            )

        # Create request
        request_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT INTO execution_requests
               (id, task_id, proposal_id, approval_id, snapshot_id,
                snapshot_content_hash, risk_level, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'requested', ?, ?)""",
            (
                request_id,
                snap_dict["task_id"],
                snap_dict["proposal_id"],
                snap_dict["approval_id"],
                snapshot_id,
                snap_dict["content_hash"],
                snap_dict["risk_level"],
                now,
                now,
            ),
        )

        # Audit log
        log_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO log_events
               (id, task_id, run_id, level, source, message, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id,
                snap_dict["task_id"],
                None,
                "info",
                "execution_request_service",
                f"Execution request created for snapshot {snapshot_id}",
                json.dumps({
                    "event_type": "execution_request:create",
                    "request_id": request_id,
                    "snapshot_id": snapshot_id,
                    "snapshot_content_hash": snap_dict["content_hash"],
                    "risk_level": snap_dict["risk_level"],
                    "proposal_id": snap_dict["proposal_id"],
                    "approval_id": snap_dict["approval_id"],
                }),
                now,
            ),
        )

        conn.commit()

        return {
            "id": request_id,
            "task_id": snap_dict["task_id"],
            "proposal_id": snap_dict["proposal_id"],
            "approval_id": snap_dict["approval_id"],
            "snapshot_id": snapshot_id,
            "snapshot_content_hash": snap_dict["content_hash"],
            "risk_level": snap_dict["risk_level"],
            "status": "requested",
            "created_at": now,
            "updated_at": now,
        }
    finally:
        conn.close()
