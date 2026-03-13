"""Orchestration API endpoints.

POST /api/tasks/{task_id}/orchestrate           Start the full pipeline
GET  /api/tasks/{task_id}/orchestration-status   Inspect current state
GET  /api/tasks/{task_id}/proposals              List execution proposals
GET  /api/proposals/{proposal_id}                Get single proposal
POST /api/proposals/{proposal_id}/freeze         Freeze approved proposal
GET  /api/snapshots/{snapshot_id}                Get single snapshot
GET  /api/snapshots/{snapshot_id}/execution-request   Get execution request for snapshot
POST /api/snapshots/{snapshot_id}/request-execution  Create execution request
GET  /api/execution-requests/{request_id}        Get single execution request
PATCH /api/execution-requests/{request_id}       Confirm or reject execution request
"""

import json
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


# ── Execution proposals (Phase 6E-A) ───────────────────────────


@router.get("/tasks/{task_id}/proposals")
async def get_task_proposals(task_id: str):
    """List all execution proposals for a task."""
    conn = get_connection()
    try:
        task = conn.execute(
            "SELECT id FROM tasks WHERE id = ?", (task_id,),
        ).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        rows = conn.execute(
            """SELECT * FROM execution_proposals
               WHERE task_id = ?
               ORDER BY created_at DESC""",
            (task_id,),
        ).fetchall()

        proposals = []
        for row in rows:
            p = dict(row)
            p["requires_approval"] = bool(p.get("requires_approval", 1))
            try:
                p["proposal_data_parsed"] = json.loads(p.get("proposal_data", "{}"))
            except (json.JSONDecodeError, TypeError):
                p["proposal_data_parsed"] = {}
            try:
                p["approval_reasons_parsed"] = json.loads(p.get("approval_reasons", "[]"))
            except (json.JSONDecodeError, TypeError):
                p["approval_reasons_parsed"] = []
            proposals.append(p)

        return {"proposals": proposals}
    finally:
        conn.close()


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str):
    """Get a single execution proposal by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM execution_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Proposal not found")

        p = dict(row)
        p["requires_approval"] = bool(p.get("requires_approval", 1))
        try:
            p["proposal_data_parsed"] = json.loads(p.get("proposal_data", "{}"))
        except (json.JSONDecodeError, TypeError):
            p["proposal_data_parsed"] = {}
        try:
            p["approval_reasons_parsed"] = json.loads(p.get("approval_reasons", "[]"))
        except (json.JSONDecodeError, TypeError):
            p["approval_reasons_parsed"] = []

        return p
    finally:
        conn.close()


# ── Snapshot endpoints (Phase 6E-B) ──────────────────────────────


@router.post("/proposals/{proposal_id}/freeze", status_code=201)
async def freeze_proposal(proposal_id: str):
    """Freeze an approved proposal into an immutable execution snapshot.

    Idempotent: if snapshot already exists, returns it with 200.
    """
    from agents.snapshot_service import freeze_snapshot

    try:
        snapshot = freeze_snapshot(proposal_id)
    except ValueError as e:
        msg = str(e).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(e))
        elif "not approved" in msg:
            raise HTTPException(status_code=409, detail=str(e))
        elif "no approved approval" in msg:
            raise HTTPException(status_code=409, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))

    # Parse snapshot_data for convenience
    try:
        snapshot["snapshot_data_parsed"] = json.loads(
            snapshot.get("snapshot_data", "{}")
        )
    except (json.JSONDecodeError, TypeError):
        snapshot["snapshot_data_parsed"] = {}

    return snapshot


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    """Get a single execution snapshot by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM execution_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Snapshot not found")

        s = dict(row)
        try:
            s["snapshot_data_parsed"] = json.loads(s.get("snapshot_data", "{}"))
        except (json.JSONDecodeError, TypeError):
            s["snapshot_data_parsed"] = {}
        return s
    finally:
        conn.close()


@router.get("/snapshots/{snapshot_id}/execution-request")
async def get_snapshot_execution_request(snapshot_id: str):
    """Get the execution request linked to a snapshot, if any."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM execution_requests WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail="No execution request for this snapshot",
            )
        return dict(row)
    finally:
        conn.close()


@router.post("/snapshots/{snapshot_id}/request-execution", status_code=201)
async def request_execution(snapshot_id: str):
    """Create an execution request for a frozen snapshot."""
    from agents.execution_request_service import create_execution_request

    try:
        result = create_execution_request(snapshot_id)
    except ValueError as e:
        msg = str(e).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(e))
        elif "not frozen" in msg:
            raise HTTPException(status_code=409, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/execution-requests/{request_id}")
async def get_execution_request(request_id: str):
    """Get a single execution request by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM execution_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404, detail="Execution request not found"
            )
        return dict(row)
    finally:
        conn.close()


class _StatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None


@router.patch("/execution-requests/{request_id}")
async def update_execution_request(request_id: str, body: _StatusUpdate):
    """Confirm or reject an execution request."""
    from agents.execution_request_service import update_execution_request_status

    try:
        result = update_execution_request_status(
            request_id, body.status, reason=body.reason
        )
    except ValueError as e:
        msg = str(e).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(e))
        elif "cannot transition" in msg:
            raise HTTPException(status_code=409, detail=str(e))
        elif "invalid status" in msg:
            raise HTTPException(status_code=400, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))
    return result
