"""Orchestration API endpoints.

POST /api/tasks/{task_id}/orchestrate           Start the full pipeline
GET  /api/tasks/{task_id}/orchestration-status   Inspect current state
GET  /api/tasks/{task_id}/proposals              List execution proposals
GET  /api/tasks/{task_id}/audit-trail            Aggregated audit timeline
GET  /api/proposals/{proposal_id}                Get single proposal
POST /api/proposals/{proposal_id}/freeze         Freeze approved proposal
GET  /api/snapshots/{snapshot_id}                Get single snapshot
GET  /api/snapshots/{snapshot_id}/execution-request   Get execution request for snapshot
POST /api/snapshots/{snapshot_id}/request-execution  Create execution request
GET  /api/execution-requests/{request_id}        Get single execution request
PATCH /api/execution-requests/{request_id}       Confirm or reject execution request
POST /api/execution-requests/{request_id}/dry-run  Dry-run execution (Phase 6F-A)
GET  /api/execution-requests/{request_id}/dry-run   Read existing dry-run result (Phase 6F-B)
GET  /api/execution-requests/{request_id}/real-run   Read existing real-run result (Phase 7B-1)
GET  /api/execution-requests/{request_id}/action-plan  Action plan with policy decisions (Phase 6G-A)
POST /api/execution-requests/{request_id}/execute    Real file execution (Phase 7A)
GET  /api/execution-requests/{request_id}/rollback   Read existing rollback result (Phase 7D-1)
POST /api/execution-results/{result_id}/rollback     Rollback real execution (Phase 7C-3)
"""

import json
from typing import Optional

import os

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


# ── Audit trail (Phase 6E-E) ───────────────────────────────────

# Fixed event order for stable sorting when timestamps are equal.
_EVENT_ORDER: dict[str, int] = {
    "proposal:created": 0,
    "approval:decided": 1,
    "snapshot:frozen": 2,
    "execution_request:created": 3,
    "execution_request:finalized": 4,
    "execution_result:completed": 5,
    "execution_result:failed": 5,
    "execution_result:rollback_completed": 6,
    "execution_result:rollback_failed": 6,
}


def _build_task_audit_trail(task_id: str) -> list[dict]:
    """Aggregate the full object chain for a task into a timeline.

    Collects events from: execution_proposals, approval_requests,
    execution_snapshots, execution_requests, execution_results.

    Each event has top-level fields consumable by Step 2 UI:
      event_type, object_type, object_id, status, timestamp,
      summary, related_ids, detail (supplementary).

    Sort: timestamp ASC, then _EVENT_ORDER for tie-breaking.
    Only includes approvals with decided status (approved/rejected).
    """
    conn = get_connection()
    try:
        events: list[dict] = []

        # ── Proposals ──
        rows = conn.execute(
            "SELECT * FROM execution_proposals WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            events.append({
                "event_type": "proposal:created",
                "object_type": "proposal",
                "object_id": d["id"],
                "status": d["status"],
                "timestamp": d["created_at"],
                "summary": f"Proposal created (risk={d['risk_level']})",
                "related_ids": {"task_id": task_id},
                "detail": {
                    "role": d.get("role", "builder"),
                    "risk_level": d["risk_level"],
                },
            })

        # ── Approvals (only decided: approved / rejected) ──
        rows = conn.execute(
            """SELECT * FROM approval_requests
               WHERE task_id = ? AND status IN ('approved', 'rejected')""",
            (task_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            ts = d.get("resolved_at") or d["created_at"]
            events.append({
                "event_type": "approval:decided",
                "object_type": "approval",
                "object_id": d["id"],
                "status": d["status"],
                "timestamp": ts,
                "summary": f"Approval {d['status']}",
                "related_ids": {
                    "task_id": task_id,
                    "proposal_id": d.get("proposal_id", ""),
                },
                "detail": {
                    "action_type": d.get("action_type", ""),
                    "reviewer_comment": d.get("reviewer_comment", ""),
                },
            })

        # ── Snapshots ──
        rows = conn.execute(
            "SELECT * FROM execution_snapshots WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            events.append({
                "event_type": "snapshot:frozen",
                "object_type": "snapshot",
                "object_id": d["id"],
                "status": d["status"],
                "timestamp": d["created_at"],
                "summary": f"Snapshot frozen (hash={d['content_hash'][:8]})",
                "related_ids": {
                    "task_id": task_id,
                    "proposal_id": d["proposal_id"],
                    "approval_id": d["approval_id"],
                },
                "detail": {
                    "content_hash": d["content_hash"],
                    "risk_level": d["risk_level"],
                },
            })

        # ── Execution requests ──
        rows = conn.execute(
            "SELECT * FROM execution_requests WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            # creation event
            events.append({
                "event_type": "execution_request:created",
                "object_type": "execution_request",
                "object_id": d["id"],
                "status": "requested",
                "timestamp": d["created_at"],
                "summary": f"Execution request created (risk={d['risk_level']})",
                "related_ids": {
                    "task_id": task_id,
                    "snapshot_id": d["snapshot_id"],
                    "proposal_id": d["proposal_id"],
                    "approval_id": d["approval_id"],
                },
                "detail": {
                    "snapshot_content_hash": d["snapshot_content_hash"],
                    "risk_level": d["risk_level"],
                },
            })
            # finalized event (only if terminal)
            if d["status"] in ("confirmed", "rejected"):
                events.append({
                    "event_type": "execution_request:finalized",
                    "object_type": "execution_request",
                    "object_id": d["id"],
                    "status": d["status"],
                    "timestamp": d["updated_at"],
                    "summary": f"Execution request {d['status']}",
                    "related_ids": {
                        "task_id": task_id,
                        "snapshot_id": d["snapshot_id"],
                    },
                    "detail": {
                        "old_status": "requested",
                        "new_status": d["status"],
                    },
                })

        # ── Execution results (Phase 6F-C) ──
        rows = conn.execute(
            """SELECT * FROM execution_results
               WHERE task_id = ? AND status IN ('completed', 'failed')""",
            (task_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            ts = d.get("completed_at") or d["created_at"]
            result_data = d.get("result_data", "{}")
            if isinstance(result_data, str):
                try:
                    rd = json.loads(result_data)
                except (json.JSONDecodeError, TypeError):
                    rd = {}
            else:
                rd = result_data
            mode = d.get("mode", rd.get("mode", "dry_run"))
            detail: dict[str, object] = {"mode": mode}

            if mode == "rollback":
                # Rollback: summary from file_results statuses
                file_results = rd.get("file_results", [])
                restored_count = sum(
                    1 for f in file_results
                    if f.get("status") in ("restored", "deleted")
                )
                failed_count = sum(
                    1 for f in file_results
                    if f.get("status") == "rollback_failed"
                )
                skipped_count = sum(
                    1 for f in file_results
                    if f.get("status") not in (
                        "restored", "deleted", "rollback_failed",
                    )
                )
                summary = (
                    f"Rollback {d['status']}: "
                    f"{restored_count} restored, {failed_count} failed"
                )
                detail["restored_count"] = restored_count
                detail["failed_count"] = failed_count
                detail["skipped_count"] = skipped_count
                event_type = f"execution_result:rollback_{d['status']}"
            elif mode == "real_run":
                # Real execution: summary from file_results + command_results
                file_results = rd.get("file_results", [])
                success_count = sum(
                    1 for f in file_results if f.get("status") == "success"
                )
                fail_count = sum(
                    1 for f in file_results if f.get("status") == "failed"
                )
                command_results = rd.get("command_results", [])
                cmd_success = sum(
                    1 for c in command_results if c.get("status") == "success"
                )
                cmd_fail = sum(
                    1 for c in command_results
                    if c.get("status") not in ("success", "pending", None)
                )
                summary = (
                    f"Real execution {d['status']}: "
                    f"{success_count} written, {fail_count} failed"
                )
                if command_results:
                    summary += f", {cmd_success} command(s) run"
                    if cmd_fail > 0:
                        summary += f", {cmd_fail} command(s) failed"
                detail["success_count"] = success_count
                detail["fail_count"] = fail_count
                detail["cmd_success"] = cmd_success
                detail["cmd_fail"] = cmd_fail
                detail["stopped_at"] = rd.get("stopped_at")
                event_type = f"execution_result:{d['status']}"
            else:
                # Dry-run: summary from planned actions
                file_count = len(rd.get("planned_file_actions", []))
                cmd_count = len(rd.get("planned_command_actions", []))
                warnings = rd.get("warnings", [])
                summary = (
                    f"Dry-run {d['status']}: {file_count} file action(s), "
                    f"{cmd_count} command action(s)"
                )
                detail["warnings_count"] = len(warnings)
                event_type = f"execution_result:{d['status']}"

            events.append({
                "event_type": event_type,
                "object_type": "execution_result",
                "object_id": d["id"],
                "status": d["status"],
                "timestamp": ts,
                "summary": summary,
                "related_ids": {
                    "task_id": task_id,
                    "execution_request_id": d["execution_request_id"],
                    "snapshot_id": d["snapshot_id"],
                },
                "detail": detail,
            })

        # Stable sort: timestamp ASC, then fixed event order
        events.sort(key=lambda e: (
            e["timestamp"],
            _EVENT_ORDER.get(e["event_type"], 99),
        ))

        return events
    finally:
        conn.close()


@router.get("/tasks/{task_id}/audit-trail")
async def get_task_audit_trail(task_id: str):
    """Aggregated audit timeline for a task's execution pipeline."""
    conn = get_connection()
    try:
        task = conn.execute(
            "SELECT id FROM tasks WHERE id = ?", (task_id,),
        ).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
    finally:
        conn.close()

    events = _build_task_audit_trail(task_id)
    return {"task_id": task_id, "events": events, "count": len(events)}


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


class _ExecuteOptions(BaseModel):
    """Optional body for execute / dry-run (route-3).

    strict_parent (default True): a file action whose parent directory is
    missing fails fast instead of auto-creating the parent. The body is
    optional so existing bodyless POSTs keep working (default = True).
    """

    strict_parent: bool = True


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


@router.post("/execution-requests/{request_id}/dry-run")
async def dry_run_execution(request_id: str):
    """Run a dry-run execution for a confirmed execution request.

    Returns 200 with the execution result (idempotent on repeat calls).
    Returns 404 if request not found.
    Returns 409 if request not confirmed, snapshot missing, or hash mismatch.
    """
    from agents.execution_result_service import run_dry_execution

    try:
        result = run_dry_execution(request_id)
    except ValueError as e:
        msg = str(e).lower()
        if "not found" in msg and "execution request" in msg:
            raise HTTPException(status_code=404, detail=str(e))
        else:
            # not confirmed, snapshot missing, hash mismatch → 409
            raise HTTPException(status_code=409, detail=str(e))

    # Parse result_data from JSON string to object
    rd = result.get("result_data", "{}")
    if isinstance(rd, str):
        try:
            result["result_data"] = json.loads(rd)
        except (json.JSONDecodeError, TypeError):
            result["result_data"] = {}

    return result


def _parse_result_data(row: dict) -> dict:
    """Parse result_data JSON string to object in-place and return."""
    rd = row.get("result_data", "{}")
    if isinstance(rd, str):
        try:
            row["result_data"] = json.loads(rd)
        except (json.JSONDecodeError, TypeError):
            row["result_data"] = {}
    return row


@router.get("/execution-requests/{request_id}/dry-run")
async def get_dry_run_result(request_id: str):
    """Read an existing dry-run result for an execution request.

    Returns 200 with the execution result if it exists.
    Returns 404 if the execution request or its dry-run result is not found.
    """
    conn = get_connection()
    try:
        # Check execution request exists
        req_row = conn.execute(
            "SELECT id FROM execution_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not req_row:
            raise HTTPException(
                status_code=404,
                detail=f"Execution request not found: {request_id}",
            )

        # Fetch dry-run result
        row = conn.execute(
            "SELECT * FROM execution_results WHERE execution_request_id = ? AND mode = 'dry_run'",
            (request_id,),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No dry-run result for execution request: {request_id}",
            )

        return _parse_result_data(dict(row))
    finally:
        conn.close()


@router.get("/execution-requests/{request_id}/real-run")
async def get_real_run_result(request_id: str):
    """Read an existing real-run result for an execution request.

    Returns 200 with the execution result if it exists.
    Returns 404 if the execution request or its real-run result is not found.
    """
    conn = get_connection()
    try:
        req_row = conn.execute(
            "SELECT id FROM execution_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not req_row:
            raise HTTPException(
                status_code=404,
                detail=f"Execution request not found: {request_id}",
            )

        row = conn.execute(
            "SELECT * FROM execution_results WHERE execution_request_id = ? AND mode = 'real_run'",
            (request_id,),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No real-run result for execution request: {request_id}",
            )

        return _parse_result_data(dict(row))
    finally:
        conn.close()


@router.get("/execution-requests/{request_id}/action-plan")
async def get_action_plan(request_id: str):
    """Compile a read-only action plan with policy decisions.

    Returns 200 with the action plan computed from the linked snapshot.
    Returns 404 if the execution request is not found.
    Returns 409 if the linked snapshot is missing or snapshot_data is invalid.
    """
    from agents.action_policy_service import build_action_plan

    conn = get_connection()
    try:
        # Fetch execution request
        req_row = conn.execute(
            "SELECT * FROM execution_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not req_row:
            raise HTTPException(
                status_code=404,
                detail=f"Execution request not found: {request_id}",
            )
        req = dict(req_row)

        # Fetch linked snapshot
        snap_row = conn.execute(
            "SELECT * FROM execution_snapshots WHERE id = ?",
            (req["snapshot_id"],),
        ).fetchone()
        if not snap_row:
            raise HTTPException(
                status_code=409,
                detail=f"Snapshot not found for execution request: {request_id}",
            )
        snap = dict(snap_row)

        # Compile action plan (pure computation, no side effects)
        try:
            plan = build_action_plan(snap["snapshot_data"])
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            raise HTTPException(
                status_code=409,
                detail=f"Invalid snapshot data: {e}",
            )

        return {
            "execution_request_id": request_id,
            "snapshot_id": snap["id"],
            "snapshot_content_hash": snap["content_hash"],
            **plan,
        }
    finally:
        conn.close()


@router.post("/execution-requests/{request_id}/execute")
async def execute_scoped(request_id: str, body: Optional[_ExecuteOptions] = None):
    """Trigger real (scoped) execution for a confirmed request.

    Optional JSON body (route-3): {"strict_parent": <bool>}. When omitted,
    strict_parent defaults to True (missing parent dir → fail-fast).

    Pre-conditions checked by API layer:
    1. Execution request exists → 404
    2. Status is 'confirmed' → 409
    3. Project workspace_root exists and is a directory → 422
    4. Eligibility gate passes → 409 + blocked_reasons

    Execution semantics (Phase 7A + 8B-1):
    - file_create / file_modify: atomic writes, fail-fast
    - command_run: restricted executor (Phase 8A whitelist + env allowlist)
    - Order: files first, then commands; file failure skips all commands
    - Fail-fast: first failure stops, no rollback
    - Idempotent: repeat calls return existing result with is_new=false
    - result_data contains both file_results and command_results

    Returns 200 with execution result (status=completed or failed).
    """
    from agents.execution_eligibility_service import check_execution_eligibility
    from agents.scoped_file_executor import execute_scoped_files

    conn = get_connection()
    try:
        # 1. Request exists?
        req_row = conn.execute(
            "SELECT * FROM execution_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not req_row:
            raise HTTPException(
                status_code=404,
                detail=f"Execution request not found: {request_id}",
            )
        req = dict(req_row)

        # 2. Status confirmed?
        if req["status"] != "confirmed":
            raise HTTPException(
                status_code=409,
                detail=f"Execution request status is '{req['status']}', must be 'confirmed'",
            )

        # 3. Workspace exists?
        task_row = conn.execute(
            "SELECT project_id FROM tasks WHERE id = ?",
            (req["task_id"],),
        ).fetchone()
        if not task_row:
            raise HTTPException(
                status_code=409,
                detail="Task not found for execution request",
            )
        proj_row = conn.execute(
            "SELECT local_repo_path FROM projects WHERE id = ?",
            (dict(task_row)["project_id"],),
        ).fetchone()
        if not proj_row:
            raise HTTPException(
                status_code=409,
                detail="Project not found for execution request",
            )
        workspace_root = dict(proj_row)["local_repo_path"]
        if not workspace_root or not os.path.isdir(workspace_root):
            raise HTTPException(
                status_code=422,
                detail=f"Project workspace_root is not a valid directory: {workspace_root}",
            )
    finally:
        conn.close()

    # 4. Eligibility gate
    elig = check_execution_eligibility(request_id)
    if not elig["eligible"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Execution request is not eligible",
                "blocked_reasons": elig["blocked_reasons"],
                "summary": elig["summary"],
            },
        )

    # Check for existing real_run result (idempotent)
    conn = get_connection()
    try:
        existing_row = conn.execute(
            "SELECT * FROM execution_results WHERE execution_request_id = ? AND mode = 'real_run'",
            (request_id,),
        ).fetchone()
    finally:
        conn.close()

    if existing_row:
        result = _parse_result_data(dict(existing_row))
        result["is_new"] = False
        return result

    # Execute
    strict_parent = body.strict_parent if body is not None else True
    try:
        raw_result = execute_scoped_files(
            request_id, workspace_root, strict_parent=strict_parent
        )
    except ValueError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
        )

    result = _parse_result_data(dict(raw_result))
    result["is_new"] = True
    return result


@router.get("/execution-requests/{request_id}/rollback")
async def get_rollback_result(request_id: str):
    """Read an existing rollback result for an execution request.

    Returns 200 with the rollback result if it exists.
    Returns 404 if the execution request or its rollback result is not found.
    """
    conn = get_connection()
    try:
        req_row = conn.execute(
            "SELECT id FROM execution_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not req_row:
            raise HTTPException(
                status_code=404,
                detail=f"Execution request not found: {request_id}",
            )

        row = conn.execute(
            "SELECT * FROM execution_results WHERE execution_request_id = ? AND mode = 'rollback'",
            (request_id,),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No rollback result for execution request: {request_id}",
            )

        return _parse_result_data(dict(row))
    finally:
        conn.close()


@router.post("/execution-results/{result_id}/rollback")
async def rollback_result(result_id: str):
    """Rollback a real_run execution result.

    Pre-conditions checked by API layer:
    1. Execution result exists → 404
    2. Mode is 'real_run' → 409
    3. Status is 'completed' or 'failed' → 409
    4. Project workspace_root exists and is a directory → 422

    Rollback semantics:
    - file_modify: restore original content from backup
    - file_create: delete the created file
    - Best-effort: continues on individual file failure
    - Idempotent: repeat calls return existing rollback result with is_new=false

    Returns 200 with rollback result (status=completed or failed).
    """
    from agents.rollback_service import rollback_execution

    conn = get_connection()
    try:
        # 1. Result exists?
        row = conn.execute(
            "SELECT * FROM execution_results WHERE id = ?",
            (result_id,),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Execution result not found: {result_id}",
            )
        er = dict(row)

        # 2. Mode is real_run?
        if er.get("mode") != "real_run":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Only real_run results can be rolled back",
                    "reason": f"Result mode is '{er.get('mode')}'",
                },
            )

        # 3. Status is terminal?
        if er["status"] not in ("completed", "failed"):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Cannot rollback result with non-terminal status",
                    "reason": f"Result status is '{er['status']}'",
                },
            )

        # 4. Check for existing rollback (idempotent — before workspace check)
        existing_row = conn.execute(
            "SELECT * FROM execution_results WHERE execution_request_id = ? AND mode = 'rollback'",
            (er["execution_request_id"],),
        ).fetchone()
        if existing_row:
            result = _parse_result_data(dict(existing_row))
            result["is_new"] = False
            return result

        # 5. Workspace exists?
        task_row = conn.execute(
            "SELECT project_id FROM tasks WHERE id = ?",
            (er["task_id"],),
        ).fetchone()
        if not task_row:
            raise HTTPException(
                status_code=409,
                detail="Task not found for execution result",
            )
        proj_row = conn.execute(
            "SELECT local_repo_path FROM projects WHERE id = ?",
            (dict(task_row)["project_id"],),
        ).fetchone()
        if not proj_row:
            raise HTTPException(
                status_code=409,
                detail="Project not found for execution result",
            )
        workspace_root = dict(proj_row)["local_repo_path"]
        if not workspace_root or not os.path.isdir(workspace_root):
            raise HTTPException(
                status_code=422,
                detail=f"Project workspace_root is not a valid directory: {workspace_root}",
            )
    finally:
        conn.close()

    # Execute rollback
    try:
        raw_result = rollback_execution(result_id, workspace_root)
    except ValueError as e:
        raise HTTPException(
            status_code=409,
            detail={"message": str(e)},
        )

    result = _parse_result_data(dict(raw_result))
    result["is_new"] = True
    return result


@router.get("/tasks/{task_id}/token-usage")
async def get_task_token_usage(task_id: str):
    """Return token usage records and totals for a task (Phase 12-1)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM token_usage_log WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        if not rows:
            return {
                "task_id": task_id,
                "records": [],
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
            }
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM token_usage_log LIMIT 0"
        ).description]
        records = [dict(zip(cols, row)) for row in rows]
        total_prompt = sum(r["prompt_tokens"] for r in records)
        total_completion = sum(r["completion_tokens"] for r in records)
        return {
            "task_id": task_id,
            "records": records,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
        }
    finally:
        conn.close()
