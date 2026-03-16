"""Snapshot service for freezing approved execution proposals.

Phase 6E-B: Creates immutable snapshots from approved proposals.
No update or delete operations are provided.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from database import get_connection


def _canonical_json(data: str) -> str:
    """Parse and re-serialize JSON with sorted keys for stable hashing."""
    return json.dumps(json.loads(data), sort_keys=True, separators=(",", ":"))


def _content_hash(snapshot_data: str) -> str:
    """SHA-256 of canonical JSON representation."""
    canonical = _canonical_json(snapshot_data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def freeze_snapshot(proposal_id: str) -> dict:
    """Freeze an approved proposal into an immutable execution snapshot.

    Rules:
    - Proposal must exist and have status 'approved'.
    - A matching approval_request with status 'approved' must exist.
    - If a snapshot already exists for this proposal, return it (idempotent).
    - Snapshot is immutable once created — no update/delete.

    Returns:
        dict with snapshot fields.

    Raises:
        ValueError: proposal not found, not approved, or no matching approval.
    """
    conn = get_connection()
    try:
        # Check for existing snapshot (idempotent)
        existing = conn.execute(
            "SELECT * FROM execution_snapshots WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if existing:
            cols = [d[0] for d in conn.execute(
                "SELECT * FROM execution_snapshots LIMIT 0"
            ).description]
            return dict(zip(cols, existing))

        # Fetch proposal
        proposal = conn.execute(
            "SELECT * FROM execution_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not proposal:
            raise ValueError(f"Proposal not found: {proposal_id}")

        prop_cols = [d[0] for d in conn.execute(
            "SELECT * FROM execution_proposals LIMIT 0"
        ).description]
        prop_dict = dict(zip(prop_cols, proposal))

        if prop_dict["status"] != "approved":
            raise ValueError(
                f"Proposal not approved (status={prop_dict['status']})"
            )

        task_id = prop_dict["task_id"]

        # Find matching approval (not required for auto-approved low-risk proposals)
        approval = conn.execute(
            "SELECT * FROM approval_requests WHERE proposal_id = ? AND status = 'approved'",
            (proposal_id,),
        ).fetchone()
        approval_id_value = None
        if approval:
            appr_cols = [d[0] for d in conn.execute(
                "SELECT * FROM approval_requests LIMIT 0"
            ).description]
            appr_dict = dict(zip(appr_cols, approval))
            approval_id_value = appr_dict["id"]
        elif prop_dict.get("requires_approval"):
            # High-risk proposal must have an approval record
            raise ValueError(
                f"No approved approval_request for proposal {proposal_id}"
            )
        else:
            # Low-risk auto-approved proposal — create synthetic approval record
            approval_id_value = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO approval_requests
                   (id, task_id, run_id, action_type, action_payload,
                    status, proposal_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    approval_id_value, task_id, prop_dict.get("run_id"),
                    "auto_approve", json.dumps({"proposal_id": proposal_id, "auto": True}),
                    "approved", proposal_id, datetime.now(timezone.utc).isoformat(),
                ),
            )

        # Freeze
        snapshot_id = str(uuid.uuid4())
        snapshot_data = prop_dict["proposal_data"]
        content_hash = _content_hash(snapshot_data)
        risk_level = prop_dict["risk_level"]
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT INTO execution_snapshots
               (id, proposal_id, approval_id, task_id, snapshot_data,
                content_hash, risk_level, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'frozen', ?)""",
            (
                snapshot_id, proposal_id, approval_id_value, task_id,
                snapshot_data, content_hash, risk_level, now,
            ),
        )

        # Audit log
        log_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO log_events
               (id, task_id, run_id, level, source, message, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id, task_id, prop_dict.get("run_id"),
                "info", "snapshot_service",
                f"Execution snapshot frozen for proposal {proposal_id}",
                json.dumps({
                    "event_type": "snapshot:freeze",
                    "snapshot_id": snapshot_id,
                    "proposal_id": proposal_id,
                    "risk_level": risk_level,
                    "content_hash": content_hash,
                }),
                now,
            ),
        )

        conn.commit()

        return {
            "id": snapshot_id,
            "proposal_id": proposal_id,
            "approval_id": approval_id_value,
            "task_id": task_id,
            "snapshot_data": snapshot_data,
            "content_hash": content_hash,
            "risk_level": risk_level,
            "status": "frozen",
            "created_at": now,
        }
    finally:
        conn.close()
