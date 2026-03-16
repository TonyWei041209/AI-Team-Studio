"""ApprovalRequest CRUD endpoints."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from database import get_connection
from models import ApprovalRequest, ApprovalRequestCreate, ApprovalResolve, ApprovalStatus

router = APIRouter(prefix="/api", tags=["approvals"])


@router.post("/tasks/{task_id}/approvals", response_model=ApprovalRequest, status_code=201)
async def create_approval(task_id: str, body: ApprovalRequestCreate):
    conn = get_connection()
    try:
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        approval_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT INTO approval_requests (id, task_id, run_id, action_type, action_payload, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, task_id, body.run_id, body.action_type, body.action_payload,
             ApprovalStatus.PENDING.value, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.get("/tasks/{task_id}/approvals", response_model=list[ApprovalRequest])
async def list_approvals_for_task(task_id: str):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM approval_requests WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/approvals/pending", response_model=list[ApprovalRequest])
async def list_pending_approvals():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM approval_requests WHERE status = ? ORDER BY created_at ASC",
            (ApprovalStatus.PENDING.value,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.patch("/approvals/{approval_id}", response_model=ApprovalRequest)
async def resolve_approval(approval_id: str, body: ApprovalResolve):
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM approval_requests WHERE id = ?", (approval_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="ApprovalRequest not found")
        if existing["status"] == ApprovalStatus.CONSUMED.value:
            raise HTTPException(
                status_code=400,
                detail="Approval has been consumed and cannot be modified",
            )
        if existing["status"] != ApprovalStatus.PENDING.value:
            raise HTTPException(status_code=400, detail="Approval already resolved")
        if body.status == ApprovalStatus.PENDING:
            raise HTTPException(status_code=400, detail="Cannot set status back to pending")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE approval_requests SET status = ?, reviewer_comment = ?, resolved_at = ? WHERE id = ?",
            (body.status.value, body.reviewer_comment, now, approval_id),
        )
        # Phase 9-4: Cascade approval status to linked execution_proposal
        if existing["proposal_id"]:
            proposal_status = "approved" if body.status == ApprovalStatus.APPROVED else "rejected"
            conn.execute(
                "UPDATE execution_proposals SET status = ?, updated_at = ? WHERE id = ?",
                (proposal_status, now, existing["proposal_id"]),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()
