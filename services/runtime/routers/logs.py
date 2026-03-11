"""LogEvent CRUD endpoints."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter

from database import get_connection
from models import LogEvent, LogEventCreate, LogLevel

router = APIRouter(prefix="/api", tags=["logs"])


@router.post("/logs", response_model=LogEvent, status_code=201)
async def create_log(body: LogEventCreate):
    conn = get_connection()
    try:
        log_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT INTO log_events (id, task_id, run_id, level, source, message, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (log_id, body.task_id, body.run_id, body.level.value, body.source,
             body.message, body.payload, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM log_events WHERE id = ?", (log_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.get("/tasks/{task_id}/logs", response_model=list[LogEvent])
async def list_logs_for_task(task_id: str, level: LogLevel | None = None, limit: int = 100):
    conn = get_connection()
    try:
        if level:
            rows = conn.execute(
                "SELECT * FROM log_events WHERE task_id = ? AND level = ? ORDER BY created_at DESC LIMIT ?",
                (task_id, level.value, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM log_events WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/logs/recent", response_model=list[LogEvent])
async def list_recent_logs(level: LogLevel | None = None, limit: int = 50):
    conn = get_connection()
    try:
        if level:
            rows = conn.execute(
                "SELECT * FROM log_events WHERE level = ? ORDER BY created_at DESC LIMIT ?",
                (level.value, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM log_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
