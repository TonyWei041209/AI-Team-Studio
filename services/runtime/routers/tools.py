"""Tool execution endpoints (Phase 4A + Phase 4B audit & consume).

POST /api/tools/execute            Execute a tool (with risk check + approval gating)
POST /api/tools/execute-approved   Re-execute a previously blocked tool after approval
GET  /api/tools                    List all registered tools

Phase 4B additions:
- Every tool invocation produces ``source='tool_audit'`` log_events
- ``execute-approved`` atomically *consumes* the approval (single-use)
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import get_connection
from tools.approval_gate import ApprovalGate
from tools.audit import log_tool_event
from tools.base import ToolContext, get_registry
from tools.safety import RiskClassifier

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


def _common_audit_kwargs(body: ToolExecuteRequest | ToolExecuteApprovedRequest) -> dict:
    """Extract common audit context fields from a request body."""
    return {
        "project_id": body.project_id,
        "task_id": body.task_id,
        "run_id": body.run_id,
        "role": body.role,
    }


def _extract_previews(result_output: Any) -> tuple[str | None, str | None]:
    """Extract stdout/stderr from a shell tool result output dict."""
    if not isinstance(result_output, dict):
        return None, None
    return result_output.get("stdout"), result_output.get("stderr")


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
        # 404 — not a tool-level event, no audit
        raise HTTPException(
            status_code=404,
            detail=(
                f"Tool not found: '{body.tool_name}'. "
                f"Available: {registry.list_names()}"
            ),
        )

    audit_kw = _common_audit_kwargs(body)

    # ── Audit: request ────────────────────────────────────────
    log_tool_event(
        event="tool.execute.request",
        tool_name=body.tool_name,
        level="info",
        params=body.params,
        **audit_kw,
    )

    # ── Role permission check ─────────────────────────────────
    if body.role:
        from agents.definitions import get_definition
        from models import AgentRole

        try:
            defn = get_definition(AgentRole(body.role))
            if not registry.is_allowed(body.tool_name, defn.allowed_tools):
                # Audit: role denied
                log_tool_event(
                    event="tool.execute.role_denied",
                    tool_name=body.tool_name,
                    level="warn",
                    params=body.params,
                    **audit_kw,
                )
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
        # 422 — param-level error, no audit
        raise HTTPException(status_code=422, detail=validation_error)

    # ── Build context ─────────────────────────────────────────
    context = _build_context(body)

    # ── Risk check + approval gating ──────────────────────────
    classifier = RiskClassifier()
    risk_level, risk_reason = classifier.classify(body.tool_name, body.params)

    # Audit: risk classified
    log_tool_event(
        event="tool.execute.risk_classified",
        tool_name=body.tool_name,
        level="warn" if classifier.needs_approval(risk_level) else "info",
        params=body.params,
        risk_level=risk_level.value,
        risk_reason=risk_reason,
        **audit_kw,
    )

    if classifier.needs_approval(risk_level):
        gate = ApprovalGate(classifier=classifier)
        blocked_result = gate.check(body.tool_name, body.params, context)
        if blocked_result is not None:
            # Audit: blocked
            log_tool_event(
                event="tool.execute.blocked",
                tool_name=body.tool_name,
                level="warn",
                params=body.params,
                risk_level=risk_level.value,
                risk_reason=risk_reason,
                blocked=True,
                approval_id=blocked_result.approval_id,
                **audit_kw,
            )
            return blocked_result.to_dict()

    # ── Execute ───────────────────────────────────────────────
    result = await tool.execute(body.params, context)

    stdout_preview, stderr_preview = _extract_previews(result.output)

    if result.success:
        log_tool_event(
            event="tool.execute.success",
            tool_name=body.tool_name,
            level="info",
            params=body.params,
            risk_level=risk_level.value,
            success=True,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            **audit_kw,
        )
    else:
        log_tool_event(
            event="tool.execute.failed",
            tool_name=body.tool_name,
            level="error",
            params=body.params,
            risk_level=risk_level.value,
            success=False,
            error_summary=result.error,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            **audit_kw,
        )

    return result.to_dict()


@router.post("/tools/execute-approved", response_model=ToolExecuteResponse)
async def execute_approved_tool(body: ToolExecuteApprovedRequest):
    """Re-execute a previously blocked tool after approval has been granted.

    The caller must supply the ``approval_id`` that was returned when the
    tool was initially blocked.  The approval is *consumed* atomically —
    it cannot be reused (Phase 4B).
    """
    registry = get_registry()
    tool = registry.get(body.tool_name)
    if tool is None:
        # 404 — not a tool-level event, no audit
        raise HTTPException(
            status_code=404,
            detail=f"Tool not found: '{body.tool_name}'",
        )

    audit_kw = _common_audit_kwargs(body)

    # ── Audit: request ────────────────────────────────────────
    log_tool_event(
        event="tool.execute_approved.request",
        tool_name=body.tool_name,
        level="info",
        params=body.params,
        approval_id=body.approval_id,
        **audit_kw,
    )

    # ── Param validation ──────────────────────────────────────
    validation_error = tool.validate_params(body.params)
    if validation_error:
        # 422 — param-level error, no audit
        raise HTTPException(status_code=422, detail=validation_error)

    # ── Consume approval (atomic CAS) ─────────────────────────
    # This is placed after param validation, right before execution.
    # Once consumed, the approval is permanently spent even if the
    # tool execution subsequently fails.
    gate = ApprovalGate()
    consumed_ok, consume_reason = gate.consume_approval(body.approval_id)

    if not consumed_ok:
        if consume_reason == "consumed":
            # Already consumed — specific audit event
            log_tool_event(
                event="tool.execute_approved.already_consumed",
                tool_name=body.tool_name,
                level="warn",
                params=body.params,
                approval_id=body.approval_id,
                error_summary=f"Approval already consumed: {body.approval_id}",
                **audit_kw,
            )
            raise HTTPException(
                status_code=409,
                detail=f"Approval '{body.approval_id}' has already been consumed",
            )
        else:
            # not_found / pending / rejected
            log_tool_event(
                event="tool.execute_approved.denied",
                tool_name=body.tool_name,
                level="warn",
                params=body.params,
                approval_id=body.approval_id,
                error_summary=f"Approval denied: {consume_reason}",
                **audit_kw,
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Approval '{body.approval_id}' cannot be used: "
                    f"status is '{consume_reason}'"
                ),
            )

    # ── Execute (bypass risk check — approved + consumed) ─────
    context = _build_context(body)
    result = await tool.execute(body.params, context)

    stdout_preview, stderr_preview = _extract_previews(result.output)

    if result.success:
        log_tool_event(
            event="tool.execute_approved.success",
            tool_name=body.tool_name,
            level="info",
            params=body.params,
            approval_id=body.approval_id,
            success=True,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            **audit_kw,
        )
    else:
        log_tool_event(
            event="tool.execute_approved.failed",
            tool_name=body.tool_name,
            level="error",
            params=body.params,
            approval_id=body.approval_id,
            success=False,
            error_summary=result.error,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            **audit_kw,
        )

    return result.to_dict()
