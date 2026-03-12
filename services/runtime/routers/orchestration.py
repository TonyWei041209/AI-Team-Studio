"""Orchestration API endpoints.

POST /api/tasks/{task_id}/orchestrate        Start the full pipeline
GET  /api/tasks/{task_id}/orchestration-status  Inspect current state
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import get_connection
from models import AgentRole, TaskStatus, RunStatus
from agents.orchestrator import Orchestrator
from agents.executor import AgentExecutor, MockAgentExecutor
from agents.model_executor import ModelAgentExecutor
from providers.registry import get_registry
from routers.settings import load_role_model_settings

router = APIRouter(prefix="/api", tags=["orchestration"])


# ── Request / response schemas ─────────────────────────────────


class OrchestrationRequest(BaseModel):
    """Optional body for POST /orchestrate — lets callers tune mock behaviour."""
    failure_rate: float = Field(0.0, ge=0.0, le=1.0)
    rejection_rate: float = Field(0.0, ge=0.0, le=1.0)
    delay_seconds: float = Field(0.5, ge=0.0, le=30.0)


class OrchestrationResponse(BaseModel):
    task_id: str
    final_status: str
    steps: list[dict]
    error: Optional[str] = None


class OrchestrationStatusResponse(BaseModel):
    task_id: str
    task_status: str
    runs: list[dict]
    is_complete: bool
    current_role: Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────


@router.post(
    "/tasks/{task_id}/orchestrate",
    response_model=OrchestrationResponse,
)
async def orchestrate_task(
    task_id: str,
    body: OrchestrationRequest | None = None,
):
    """Start the full orchestration pipeline for a task.

    The task must be in ``pending`` status.  Runs synchronously through
    Planner → Builder → QA → Reviewer and returns the complete result.
    """

    # Pre-validate so we can give a clear HTTP error
    conn = get_connection()
    try:
        task = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,),
        ).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["status"] != TaskStatus.PENDING.value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Task must be in 'pending' status to start orchestration, "
                    f"currently '{task['status']}'"
                ),
            )
    finally:
        conn.close()

    # Build executor(s)
    params = body or OrchestrationRequest()
    mock_executor = MockAgentExecutor(
        delay_seconds=params.delay_seconds,
        failure_rate=params.failure_rate,
        rejection_rate=params.rejection_rate,
    )

    # Phase 6C+6D: Per-role executor dispatch based on role_model_settings DB.
    # The DB table is the sole truth source for model routing.
    # Orchestrator only decides mock vs model executor; the ModelAgentExecutor
    # internally resolves the specific provider/model from the same DB.
    # Phase 6D adds Builder (plan-only mode).
    executors: dict[AgentRole, AgentExecutor] = {}
    registry = get_registry()
    role_configs = load_role_model_settings()
    for role_enum in (AgentRole.PLANNER, AgentRole.BUILDER, AgentRole.REVIEWER):
        cfg = role_configs.get(role_enum.value, {})
        if cfg.get("enabled") and cfg.get("provider", "mock") != "mock":
            provider = registry.get(cfg["provider"])
            if provider and getattr(provider, "api_key", ""):
                executors[role_enum] = ModelAgentExecutor(registry=registry)

    orchestrator = Orchestrator(executor=mock_executor, executors=executors)
    result = await orchestrator.run(task_id)
    return result.to_dict()


@router.get(
    "/tasks/{task_id}/orchestration-status",
    response_model=OrchestrationStatusResponse,
)
async def get_orchestration_status(task_id: str):
    """Inspect the orchestration state of a task.

    Returns the task status, all agent runs in chronological order,
    whether the task has reached a terminal state, and the role of
    the currently-running agent (if any).
    """

    conn = get_connection()
    try:
        task = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,),
        ).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        runs = conn.execute(
            "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()

        runs_list = [dict(r) for r in runs]
        task_status = task["status"]
        is_complete = task_status in (TaskStatus.DONE.value, TaskStatus.FAILED.value)

        # Find the currently-active agent (if any)
        current_role: str | None = None
        for run in reversed(runs_list):
            if run["status"] in (RunStatus.RUNNING.value, RunStatus.PENDING.value):
                current_role = run["role"]
                break

        return {
            "task_id": task_id,
            "task_status": task_status,
            "runs": runs_list,
            "is_complete": is_complete,
            "current_role": current_role,
        }
    finally:
        conn.close()
