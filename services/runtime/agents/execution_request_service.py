"""Execution request service for Phase 6E-C/D.

Creates execution requests bound to frozen snapshots.
Supports status confirmation gate (requested -> confirmed/rejected).
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


_VALID_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"confirmed", "rejected"},
}


def update_execution_request_status(
    request_id: str,
    new_status: str,
    *,
    reason: str | None = None,
) -> dict:
    """Transition an execution request to a new status.

    Allowed transitions:
        requested -> confirmed
        requested -> rejected

    confirmed and rejected are terminal — no further changes allowed.

    Args:
        request_id: The execution request ID.
        new_status: Target status ('confirmed' or 'rejected').
        reason: Optional human-readable reason (stored in audit log only).

    Returns:
        dict with updated execution request fields.

    Raises:
        ValueError: request not found, invalid status, or illegal transition.
    """
    if new_status not in ("confirmed", "rejected"):
        raise ValueError(
            f"Invalid status '{new_status}': must be 'confirmed' or 'rejected'"
        )

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM execution_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Execution request not found: {request_id}")

        cols = [d[0] for d in conn.execute(
            "SELECT * FROM execution_requests LIMIT 0"
        ).description]
        req = dict(zip(cols, row))

        old_status = req["status"]
        allowed = _VALID_TRANSITIONS.get(old_status)
        if allowed is None or new_status not in allowed:
            raise ValueError(
                f"Cannot transition from '{old_status}' to '{new_status}'"
            )

        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "UPDATE execution_requests SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, request_id),
        )

        # Audit log
        payload: dict = {
            "event_type": "execution_request:status_change",
            "request_id": request_id,
            "snapshot_id": req["snapshot_id"],
            "old_status": old_status,
            "new_status": new_status,
        }
        if reason:
            payload["reason"] = reason

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
                "execution_request_service",
                f"Execution request {request_id} status: {old_status} -> {new_status}",
                json.dumps(payload),
                now,
            ),
        )

        conn.commit()

        req["status"] = new_status
        req["updated_at"] = now
        return req
    finally:
        conn.close()
