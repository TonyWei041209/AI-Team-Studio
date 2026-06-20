"""Project CRUD endpoints."""

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from database import get_connection
from models import Project, ProjectCreate, ProjectUpdate, ProjectParticipant, ProjectParticipantsUpdate

router = APIRouter(prefix="/api/projects", tags=["projects"])

# Default system roles for auto-seeding
_DEFAULT_ROLES = ["planner", "architect", "builder", "qa", "security_reviewer", "reviewer", "documentation"]
# Mandatory roles that cannot be disabled
_MANDATORY_ROLES = {"planner", "builder"}


def _ensure_participants(conn, project_id: str) -> list[dict]:
    """Ensure project has participant records, auto-seeding defaults if needed."""
    rows = conn.execute(
        "SELECT role_name, is_enabled FROM project_role_participants WHERE project_id = ? ORDER BY role_name",
        (project_id,),
    ).fetchall()
    if rows:
        return [{"role_name": r["role_name"], "is_enabled": bool(r["is_enabled"])} for r in rows]
    # Auto-seed defaults
    now = datetime.now(timezone.utc).isoformat()
    for role in _DEFAULT_ROLES:
        conn.execute(
            """INSERT OR IGNORE INTO project_role_participants (id, project_id, role_name, is_enabled, created_at, updated_at)
               VALUES (?, ?, ?, 1, ?, ?)""",
            (str(uuid.uuid4()), project_id, role, now, now),
        )
    conn.commit()
    rows = conn.execute(
        "SELECT role_name, is_enabled FROM project_role_participants WHERE project_id = ? ORDER BY role_name",
        (project_id,),
    ).fetchall()
    return [{"role_name": r["role_name"], "is_enabled": bool(r["is_enabled"])} for r in rows]


@router.post("", response_model=Project, status_code=201)
async def create_project(body: ProjectCreate):
    project_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
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

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
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
        try:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.commit()
        except Exception:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete project with existing tasks. Delete tasks first.",
            )
    finally:
        conn.close()


@router.get("/{project_id}/participants", response_model=list[ProjectParticipant])
async def get_project_participants(project_id: str):
    conn = get_connection()
    try:
        # Verify project exists
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        return _ensure_participants(conn, project_id)
    finally:
        conn.close()


@router.put("/{project_id}/participants", response_model=list[ProjectParticipant])
async def update_project_participants(project_id: str, body: ProjectParticipantsUpdate):
    conn = get_connection()
    try:
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        # Ensure defaults exist first
        _ensure_participants(conn, project_id)
        # Validate mandatory roles
        for p in body.participants:
            if p.role_name in _MANDATORY_ROLES and not p.is_enabled:
                raise HTTPException(
                    status_code=422,
                    detail=f"Role '{p.role_name}' is mandatory and cannot be disabled",
                )
        # Apply updates
        now = datetime.now(timezone.utc).isoformat()
        for p in body.participants:
            conn.execute(
                """UPDATE project_role_participants
                   SET is_enabled = ?, updated_at = ?
                   WHERE project_id = ? AND role_name = ?""",
                (1 if p.is_enabled else 0, now, project_id, p.role_name),
            )
        conn.commit()
        return _ensure_participants(conn, project_id)
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────
#  Godot project detection  (Phase 5.8)
# ──────────────────────────────────────────────────────────────


def _parse_godot_project(repo_path: str) -> dict | None:
    """Detect and parse minimal Godot project metadata.

    Returns None if not a Godot project. Read-only — never writes.
    """
    godot_file = Path(repo_path) / "project.godot"
    if not godot_file.is_file():
        return None

    result: dict = {
        "is_godot": True,
        "project_godot_path": str(godot_file),
        "project_name": None,
        "config_version": None,
        "godot_version": None,
        "renderer": None,
    }

    try:
        content = godot_file.read_text(encoding="utf-8", errors="replace")
        # Parse key=value pairs from INI-like format (max 200 lines for safety)
        lines = content.split("\n")[:200]
        for line in lines:
            line = line.strip()
            if line.startswith("config/name="):
                val = line.split("=", 1)[1].strip().strip('"')
                result["project_name"] = val
            elif line.startswith("config_version="):
                result["config_version"] = line.split("=", 1)[1].strip()
            elif line.startswith("run/main_scene="):
                pass  # Available but not surfaced in v1
            elif "rendering/renderer" in line and "=" in line:
                val = line.split("=", 1)[1].strip().strip('"')
                result["renderer"] = val
    except (OSError, UnicodeDecodeError):
        pass  # File unreadable — still report as Godot project

    return result


@router.get("/{project_id}/godot-info")
async def get_godot_info(project_id: str):
    """Read-only Godot project detection and metadata.

    Returns { is_godot: false } if not a Godot project.
    Returns { is_godot: true, ... } with minimal parsed metadata if detected.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT local_repo_path FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Project not found")

        repo_path = row["local_repo_path"]
        if not repo_path or not Path(repo_path).is_dir():
            return {"is_godot": False, "reason": "repo_path_not_found"}

        info = _parse_godot_project(repo_path)
        if info is None:
            return {"is_godot": False}
        return info
    finally:
        conn.close()
