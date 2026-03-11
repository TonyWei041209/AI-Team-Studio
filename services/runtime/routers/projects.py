"""Project CRUD endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from database import get_connection
from models import Project, ProjectCreate, ProjectUpdate

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=Project, status_code=201)
async def create_project(body: ProjectCreate):
    project_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO projects (id, name, local_repo_path, default_branch, description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, body.name, body.local_repo_path, body.default_branch, body.description, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.get("", response_model=list[Project])
async def list_projects():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Project not found")
        return dict(row)
    finally:
        conn.close()


@router.patch("/{project_id}", response_model=Project)
async def update_project(project_id: str, body: ProjectUpdate):
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Project not found")

        updates = body.model_dump(exclude_none=True)
        if not updates:
            return dict(existing)

        updates["updated_at"] = datetime.utcnow().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [project_id]
        conn.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", values)
        conn.commit()

        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str):
    conn = get_connection()
    try:
        existing = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Project not found")
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    finally:
        conn.close()
