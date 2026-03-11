"""AgentRun CRUD endpoints."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from database import get_connection
from models import AgentRun, AgentRunCreate, AgentRunUpdate, RunStatus

router = APIRouter(prefix="/api", tags=["agent_runs"])


@router.post("/tasks/{task_id}/runs", response_model=AgentRun, status_code=201)
async def create_run(task_id: str, body: AgentRunCreate):
    conn = get_connection()
    try:
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT INTO agent_runs (id, task_id, role, model_provider, model_name, status, input_summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, task_id, body.role.value, body.model_provider, body.model_name,
             RunStatus.PENDING.value, body.input_summary, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.get("/tasks/{task_id}/runs", response_model=list[AgentRun])
async def list_runs(task_id: str):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/runs/{run_id}", response_model=AgentRun)
async def get_run(run_id: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="AgentRun not found")
        return dict(row)
    finally:
        conn.close()


@router.patch("/runs/{run_id}", response_model=AgentRun)
async def update_run(run_id: str, body: AgentRunUpdate):
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="AgentRun not found")

        now = datetime.now(timezone.utc).isoformat()
        updates: dict = {}

        if body.status is not None:
            updates["status"] = body.status.value
            if body.status == RunStatus.RUNNING:
                updates["started_at"] = now
            elif body.status in (RunStatus.COMPLETED, RunStatus.FAILED):
                updates["ended_at"] = now
        if body.output_summary is not None:
            updates["output_summary"] = body.output_summary

        if not updates:
            return dict(existing)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [run_id]
        conn.execute(f"UPDATE agent_runs SET {set_clause} WHERE id = ?", values)
        conn.commit()

        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()
