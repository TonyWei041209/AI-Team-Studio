"""Task CRUD endpoints with state machine."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from database import get_connection
from models import (
    Task, TaskCreate, TaskStatus, TaskStatusUpdate,
    TASK_TRANSITIONS, is_valid_transition,
)

router = APIRouter(prefix="/api", tags=["tasks"])


@router.post("/projects/{project_id}/tasks", response_model=Task, status_code=201)
async def create_task(project_id: str, body: TaskCreate):
    conn = get_connection()
    try:
        # Verify project exists
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")

        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        role = body.assigned_agent_role.value if body.assigned_agent_role else None

        conn.execute(
            """INSERT INTO tasks (id, project_id, title, description, status, priority, assigned_agent_role, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, project_id, body.title, body.description, TaskStatus.PENDING.value,
             body.priority.value, role, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.get("/projects/{project_id}/tasks", response_model=list[Task])
async def list_tasks(project_id: str, status: TaskStatus | None = None):
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? AND status = ? ORDER BY created_at DESC",
                (project_id, status.value),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/tasks/{task_id}", response_model=Task)
async def get_task(task_id: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        return dict(row)
    finally:
        conn.close()


@router.patch("/tasks/{task_id}/status", response_model=Task)
async def update_task_status(task_id: str, body: TaskStatusUpdate):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")

        current_status = TaskStatus(row["status"])
        new_status = body.status

        if not is_valid_transition(current_status, new_status):
            allowed = [s.value for s in TASK_TRANSITIONS.get(current_status, [])]
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from '{current_status.value}' to '{new_status.value}'. "
                       f"Allowed: {allowed}",
            )

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (new_status.value, now, task_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()
