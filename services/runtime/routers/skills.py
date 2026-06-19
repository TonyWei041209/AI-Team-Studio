"""Skills CRUD endpoints (Phase 14-1)."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from database import get_connection
from models import Skill, SkillCreate, SkillUpdate

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Valid agent roles for scope_type=agent
_VALID_ROLES = {"planner", "builder", "qa", "security_reviewer", "reviewer"}


def _validate_skill_body(body: SkillCreate | SkillUpdate, *, is_create: bool = False) -> None:
    """Validate scope_type / agent_role consistency."""
    scope = getattr(body, "scope_type", None)
    role = getattr(body, "agent_role", None)

    if is_create:
        if scope == "agent" and not role:
            raise HTTPException(
                status_code=422,
                detail="agent_role is required when scope_type is 'agent'",
            )
        if scope == "global" and role is not None:
            raise HTTPException(
                status_code=422,
                detail="agent_role must be null when scope_type is 'global'",
            )
    # For updates, only validate if both fields are being changed
    if role is not None and role not in _VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"agent_role must be one of {sorted(_VALID_ROLES)}, got '{role}'",
        )


@router.post("", response_model=Skill, status_code=201)
async def create_skill(body: SkillCreate):
    _validate_skill_body(body, is_create=True)
    skill_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO skills (id, name, description, content, scope_type, agent_role, is_enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                skill_id,
                body.name,
                body.description,
                body.content,
                body.scope_type.value if body.scope_type else "global",
                body.agent_role,
                1 if body.is_enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        return _row_to_skill(row)
    finally:
        conn.close()


@router.get("", response_model=list[Skill])
async def list_skills(
    scope_type: Optional[str] = Query(None, description="Filter by scope_type: global or agent"),
    agent_role: Optional[str] = Query(None, description="Filter by agent_role"),
    enabled_only: bool = Query(False, description="If true, only return enabled skills"),
):
    conn = get_connection()
    try:
        clauses: list[str] = []
        params: list[str | int] = []

        if scope_type:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if agent_role:
            clauses.append("agent_role = ?")
            params.append(agent_role)
        if enabled_only:
            clauses.append("is_enabled = 1")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM skills{where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [_row_to_skill(r) for r in rows]
    finally:
        conn.close()


@router.get("/{skill_id}", response_model=Skill)
async def get_skill(skill_id: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Skill not found")
        return _row_to_skill(row)
    finally:
        conn.close()


@router.patch("/{skill_id}", response_model=Skill)
async def update_skill(skill_id: str, body: SkillUpdate):
    _validate_skill_body(body)
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Skill not found")

        updates = body.model_dump(exclude_none=True)
        if not updates:
            return _row_to_skill(existing)

        # Convert enum values to strings for SQL
        if "scope_type" in updates:
            updates["scope_type"] = updates["scope_type"].value if hasattr(updates["scope_type"], "value") else updates["scope_type"]
        if "is_enabled" in updates:
            updates["is_enabled"] = 1 if updates["is_enabled"] else 0

        # Validate scope/role consistency after merge with existing
        final_scope = updates.get("scope_type", existing["scope_type"])
        final_role = updates.get("agent_role", existing["agent_role"])
        if final_scope == "agent" and not final_role:
            raise HTTPException(
                status_code=422,
                detail="agent_role is required when scope_type is 'agent'",
            )
        if final_scope == "global" and final_role is not None:
            raise HTTPException(
                status_code=422,
                detail="agent_role must be null when scope_type is 'global'",
            )

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [skill_id]
        conn.execute(f"UPDATE skills SET {set_clause} WHERE id = ?", values)
        conn.commit()

        row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        return _row_to_skill(row)
    finally:
        conn.close()


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(skill_id: str):
    conn = get_connection()
    try:
        existing = conn.execute("SELECT id FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Skill not found")
        conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        conn.commit()
    finally:
        conn.close()


def _row_to_skill(row: object) -> dict:
    """Convert a sqlite3.Row to a dict with bool coercion for is_enabled."""
    d = dict(row)  # type: ignore[arg-type]
    d["is_enabled"] = bool(d["is_enabled"])
    return d
