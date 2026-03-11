"""Tool execution endpoints.

POST /api/tools/execute            Execute a tool (with risk check + approval gating)
POST /api/tools/execute-approved   Re-execute a previously blocked tool after approval
GET  /api/tools                    List all registered tools
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import get_connection
from tools.approval_gate import ApprovalGate
from tools.base import ToolContext, get_registry

router = APIRouter(prefix="/api", tags=["tools"])


# ── Request / Response schemas ────────────────────────────────


class ToolExecuteRequest(BaseModel):
    tool_name: str
    params: dict = Field(default_factory=dict)
    # Context fields (optional for direct invocation, set by orchestrator)
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    role: Optional[str] = None


class ToolExecuteApprovedRequest(BaseModel):
    approval_id: str
    tool_name: str
    params: dict = Field(default_factory=dict)
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    role: Optional[str] = None


class ToolExecuteResponse(BaseModel):
    success: bool
    output: Optional[Any] = None
    error: Optional[str] = None
    tool_name: str
    risk_level: str = "safe"
    blocked: bool = False
    approval_id: Optional[str] = None


class ToolInfo(BaseModel):
    name: str
    description: str
    category: str


# ── Helpers ───────────────────────────────────────────────────


def _build_context(
    body: ToolExecuteRequest | ToolExecuteApprovedRequest,
) -> ToolContext | None:
    """Build a ToolContext, looking up ``working_dir`` from the project table."""
    if not body.project_id:
        return None

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT local_repo_path FROM projects WHERE id = ?",
            (body.project_id,),
        ).fetchone()
        working_dir = row["local_repo_path"] if row else ""
    finally:
        conn.close()

    return ToolContext(
        project_id=body.project_id,
        task_id=body.task_id,
        run_id=body.run_id,
        role=body.role,
        working_dir=working_dir,
    )


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/tools", response_model=list[ToolInfo])
async def list_tools():
    """List all registered tools."""
    registry = get_registry()
    return registry.list_tools()


@router.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(body: ToolExecuteRequest):
    """Execute a tool with risk classification and approval gating.

    If the invocation is classified as HIGH / CRITICAL risk, execution
    is blocked and an ``ApprovalRequest`` is created.  The response will
    have ``blocked=True`` and an ``approval_id``.
    """
    registry = get_registry()
    tool = registry.get(body.tool_name)
    if tool is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Tool not found: '{body.tool_name}'. "
                f"Available: {registry.list_names()}"
            ),
        )

    # ── Role permission check ─────────────────────────────────
    if body.role:
        from agents.definitions import get_definition
        from models import AgentRole

        try:
            defn = get_definition(AgentRole(body.role))
            if not registry.is_allowed(body.tool_name, defn.allowed_tools):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Tool '{body.tool_name}' not allowed for role '{body.role}'. "
                        f"Allowed: {defn.allowed_tools}"
                    ),
                )
        except ValueError:
            pass  # unknown role string — skip check

    # ── Param validation ──────────────────────────────────────
    validation_error = tool.validate_params(body.params)
    if validation_error:
        raise HTTPException(status_code=422, detail=validation_error)

    # ── Build context ─────────────────────────────────────────
    context = _build_context(body)

    # ── Risk check + approval gating ──────────────────────────
    gate = ApprovalGate()
    blocked_result = gate.check(body.tool_name, body.params, context)
    if blocked_result is not None:
        return blocked_result.to_dict()

    # ── Execute ───────────────────────────────────────────────
    result = await tool.execute(body.params, context)
    return result.to_dict()


@router.post("/tools/execute-approved", response_model=ToolExecuteResponse)
async def execute_approved_tool(body: ToolExecuteApprovedRequest):
    """Re-execute a previously blocked tool after approval has been granted.

    The caller must supply the ``approval_id`` that was returned when the
    tool was initially blocked.
    """
    registry = get_registry()
    tool = registry.get(body.tool_name)
    if tool is None:
        raise HTTPException(
            status_code=404,
            detail=f"Tool not found: '{body.tool_name}'",
        )

    # ── Verify approval ───────────────────────────────────────
    gate = ApprovalGate()
    if not gate.check_pre_approved(body.approval_id):
        raise HTTPException(
            status_code=403,
            detail=f"Approval '{body.approval_id}' has not been approved",
        )

    # ── Param validation ──────────────────────────────────────
    validation_error = tool.validate_params(body.params)
    if validation_error:
        raise HTTPException(status_code=422, detail=validation_error)

    # ── Execute (bypass risk check — already approved) ────────
    context = _build_context(body)
    result = await tool.execute(body.params, context)
    return result.to_dict()
