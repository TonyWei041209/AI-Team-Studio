"""Role registry CRUD endpoints (Phase 15-1)."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from database import get_connection
from models import Role, RoleCreate, RoleUpdate

router = APIRouter(prefix="/api/roles", tags=["roles"])


def _row_to_role(row: object) -> dict:
    """Convert a sqlite3.Row to a dict with bool coercion."""
    d = dict(row)  # type: ignore[arg-type]
    d["is_system"] = bool(d["is_system"])
    d["is_enabled"] = bool(d["is_enabled"])
    return d


@router.post("", response_model=Role, status_code=201)
async def create_role(body: RoleCreate):
    """Create a custom role. System roles can only be created via migration."""
    # Validate name format: lowercase, alphanumeric + underscore
    if not body.name or not body.name.replace("_", "").isalnum() or not body.name[0].isalpha():
        raise HTTPException(
            status_code=422,
            detail="name must be non-empty, start with a letter, and contain only lowercase letters, digits, and underscores",
        )
    if body.name != body.name.lower():
        raise HTTPException(status_code=422, detail="name must be lowercase")

    conn = get_connection()
    try:
        # Check uniqueness
        existing = conn.execute("SELECT id FROM roles WHERE name = ?", (body.name,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail=f"Role with name '{body.name}' already exists")

        role_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO roles (id, name, display_name, description, department, is_system, is_enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            (role_id, body.name, body.display_name, body.description, body.department,
             1 if body.is_enabled else 0, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
        return _row_to_role(row)
    finally:
        conn.close()


@router.get("", response_model=list[Role])
async def list_roles(
    department: Optional[str] = Query(None, description="Filter by department"),
    is_system: Optional[bool] = Query(None, description="Filter by system/custom"),
    enabled_only: bool = Query(False, description="If true, only return enabled roles"),
):
    conn = get_connection()
    try:
        clauses: list[str] = []
        params: list[str | int] = []

        if department:
            clauses.append("department = ?")
            params.append(department)
        if is_system is not None:
            clauses.append("is_system = ?")
            params.append(1 if is_system else 0)
        if enabled_only:
            clauses.append("is_enabled = 1")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM roles{where} ORDER BY is_system DESC, name ASC",
            params,
        ).fetchall()
        return [_row_to_role(r) for r in rows]
    finally:
        conn.close()


@router.get("/{role_id}", response_model=Role)
async def get_role(role_id: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Role not found")
        return _row_to_role(row)
    finally:
        conn.close()


@router.patch("/{role_id}", response_model=Role)
async def update_role(role_id: str, body: RoleUpdate):
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Role not found")

        # System role protection: only allow updating description and is_enabled
        if existing["is_system"]:
            disallowed = []
            if body.display_name is not None:
                disallowed.append("display_name")
            if body.department is not None:
                disallowed.append("department")
            if disallowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot modify {', '.join(disallowed)} on system roles",
                )

        updates = body.model_dump(exclude_none=True)
        if not updates:
            return _row_to_role(existing)

        if "is_enabled" in updates:
            updates["is_enabled"] = 1 if updates["is_enabled"] else 0

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [role_id]
        conn.execute(f"UPDATE roles SET {set_clause} WHERE id = ?", values)
        conn.commit()

        row = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
        return _row_to_role(row)
    finally:
        conn.close()


@router.delete("/{role_id}", status_code=204)
async def delete_role(role_id: str):
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Role not found")

        if existing["is_system"]:
            raise HTTPException(status_code=403, detail="Cannot delete system roles")

        conn.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        conn.commit()
    finally:
        conn.close()
