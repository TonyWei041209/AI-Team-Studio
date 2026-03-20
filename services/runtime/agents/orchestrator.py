"""Orchestration engine: drives a task through the four-role agent pipeline.

Usage
-----
    orchestrator = Orchestrator()                  # uses MockAgentExecutor
    result = await orchestrator.run(task_id)       # returns OrchestrationResult
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from database import get_connection
from models import (
    AgentRole, LogLevel, ReviewDecision, RunStatus, TaskStatus,
    is_valid_transition,
)
from agents.definitions import AGENT_PIPELINE, AgentRoleDefinition
from agents.executor import AgentExecutor, ExecutionResult, MockAgentExecutor


# Maximum Reviewer rejections before the task fails
MAX_REJECTIONS = 3


# ── Result container ───────────────────────────────────────────


class OrchestrationResult:
    """Final outcome of running the full pipeline on a task."""

    def __init__(
        self,
        task_id: str,
        final_status: str,
        steps: list[dict],
        error: str | None = None,
    ):
        self.task_id = task_id
        self.final_status = final_status
        self.steps = steps
        self.error = error

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "final_status": self.final_status,
            "steps": self.steps,
            "error": self.error,
        }


# ── Orchestrator ───────────────────────────────────────────────


class Orchestrator:
    """Drives a pending task through Planner → Builder → QA → Reviewer."""

    def __init__(
        self,
        executor: AgentExecutor | None = None,
        executors: dict[AgentRole, AgentExecutor] | None = None,
    ):
        self._default_executor: AgentExecutor = executor or MockAgentExecutor()
        self._executors: dict[AgentRole, AgentExecutor] = executors or {}

    # Backward-compat property so existing code using `self.executor` still works
    @property
    def executor(self) -> AgentExecutor:
        return self._default_executor

    def _get_executor(self, role: AgentRole) -> AgentExecutor:
        """Return the executor for a role, falling back to the default."""
        return self._executors.get(role, self._default_executor)

    async def run(self, task_id: str) -> OrchestrationResult:
        """Execute the full orchestration pipeline for a task.

        Returns an OrchestrationResult with the final task status,
        a list of step records, and any error message.
        """
        steps: list[dict] = []
        rejection_count = 0

        # ── 1. Load and validate (atomic claim) ───────────────
        task = self._load_task(task_id)
        if task is None:
            return OrchestrationResult(
                task_id=task_id,
                final_status="error",
                steps=steps,
                error="Task not found",
            )

        current_status = TaskStatus(task["status"])
        if current_status != TaskStatus.PENDING:
            return OrchestrationResult(
                task_id=task_id,
                final_status=current_status.value,
                steps=steps,
                error=(
                    f"Task must be in 'pending' status to start orchestration, "
                    f"currently '{current_status.value}'"
                ),
            )

        # Atomic claim: UPDATE ... WHERE status='pending' to prevent races
        if not self._claim_task(task_id):
            return OrchestrationResult(
                task_id=task_id,
                final_status="error",
                steps=steps,
                error="Task was already claimed by another orchestrator",
            )

        self._log(
            task_id, None, "info", "orchestrator",
            f"Orchestration started for task: {task['title']}",
        )

        # ── 2. Build initial context ─────────────────────────
        task_context: dict = {
            "task_id": task_id,
            "title": task["title"],
            "description": task["description"],
            "project_id": task["project_id"],
            "priority": task["priority"],
            "previous_outputs": {},
        }

        # ── 2b. Load project participant config ────────────
        enabled_roles = self._get_enabled_roles(task_context["project_id"])
        # Filter pipeline: keep only enabled roles
        active_pipeline = [
            defn for defn in AGENT_PIPELINE
            if defn.role.value in enabled_roles
        ]
        if not active_pipeline:
            # Safety: if somehow all roles disabled, use full pipeline
            active_pipeline = list(AGENT_PIPELINE)

        self._log(
            task_id, None, "info", "orchestrator",
            f"Active pipeline roles: {[d.role.value for d in active_pipeline]}",
        )

        # ── 3. Pipeline loop ─────────────────────────────────
        idx = 0
        while idx < len(active_pipeline):
            defn = active_pipeline[idx]

            step = await self._execute_step(task_id, defn, task_context)
            steps.append(step)

            # Agent failed → task fails immediately
            if not step["success"]:
                self._update_task_status(task_id, TaskStatus.FAILED)
                self._log(
                    task_id, step.get("run_id"), "error", "orchestrator",
                    f"{defn.display_name} failed: {step.get('error', 'unknown')}",
                )
                return OrchestrationResult(
                    task_id=task_id,
                    final_status=TaskStatus.FAILED.value,
                    steps=steps,
                    error=step.get("error"),
                )

            # ── Reviewer-specific logic ───────────────────────
            if defn.role == AgentRole.REVIEWER:
                decision = step.get("decision")

                if decision == ReviewDecision.REQUEST_CHANGES.value:
                    rejection_count += 1
                    self._log(
                        task_id, step.get("run_id"), "warn", "orchestrator",
                        f"Reviewer requested changes (rejection #{rejection_count})",
                    )
                    if rejection_count >= MAX_REJECTIONS:
                        self._update_task_status(task_id, TaskStatus.FAILED)
                        self._log(
                            task_id, None, "error", "orchestrator",
                            f"Task failed: max rejections ({MAX_REJECTIONS}) reached",
                        )
                        return OrchestrationResult(
                            task_id=task_id,
                            final_status=TaskStatus.FAILED.value,
                            steps=steps,
                            error=f"Exceeded maximum rejections ({MAX_REJECTIONS})",
                        )
                    # Loop back to Builder
                    self._update_task_status(task_id, TaskStatus.IN_PROGRESS)
                    # Accumulate rejection history (not just latest)
                    if "rejection_history" not in task_context:
                        task_context["rejection_history"] = []
                    task_context["rejection_history"].append(step.get("output", {}))
                    # Compress older rejections to save tokens (Phase 12-3)
                    self._compress_rejection_history(task_context["rejection_history"])
                    task_context["rejection_feedback"] = step.get("output", {})
                    builder_idx = next(
                        (i for i, d in enumerate(active_pipeline) if d.role == AgentRole.BUILDER),
                        1,
                    )
                    idx = builder_idx
                    continue

                if decision == ReviewDecision.BLOCK.value:
                    self._update_task_status(task_id, TaskStatus.FAILED)
                    self._log(
                        task_id, step.get("run_id"), "error", "orchestrator",
                        "Reviewer blocked the task",
                    )
                    return OrchestrationResult(
                        task_id=task_id,
                        final_status=TaskStatus.FAILED.value,
                        steps=steps,
                        error="Reviewer blocked the task",
                    )

                # decision == "APPROVE" → fall through to advance status

            # ── Advance task status (skip if already there, e.g. after claim) ──
            current = self._get_task_status(task_id)
            if current != defn.success_task_status:
                self._update_task_status(task_id, defn.success_task_status)

            # Store output for the next agent
            task_context["previous_outputs"][defn.role.value] = step.get("output", {})

            idx += 1

        # ── 4. Pipeline completed successfully ────────────────
        self._log(
            task_id, None, "info", "orchestrator",
            f"Orchestration completed successfully for task: {task['title']}",
        )
        return OrchestrationResult(
            task_id=task_id,
            final_status=TaskStatus.DONE.value,
            steps=steps,
        )

    # ── Single-step execution ──────────────────────────────────

    async def _execute_step(
        self,
        task_id: str,
        defn: AgentRoleDefinition,
        task_context: dict,
    ) -> dict:
        """Create an AgentRun, execute the agent, update the run."""

        # 1. Create AgentRun (pending)
        run_id = self._create_run(
            task_id=task_id,
            role=defn.role,
            model_provider=defn.model_provider,
            model_name=defn.model_name,
            input_summary=json.dumps({
                "task_title": task_context["title"],
                "role": defn.role.value,
                "has_previous": list(task_context["previous_outputs"].keys()),
            }),
        )

        self._log(task_id, run_id, "info", defn.role.value,
                  f"{defn.display_name} started")

        # 2. Mark running
        self._update_run(run_id, RunStatus.RUNNING)

        # 3. Execute (per-role executor dispatch)
        executor = self._get_executor(defn.role)
        try:
            result: ExecutionResult = await executor.execute(
                role=defn.role,
                task_context=task_context,
            )
        except Exception as exc:
            self._update_run(
                run_id, RunStatus.FAILED,
                output_summary=json.dumps({"error": str(exc)}),
            )
            self._log(task_id, run_id, "error", defn.role.value,
                      f"{defn.display_name} raised exception: {exc}")
            return {
                "run_id": run_id,
                "role": defn.role.value,
                "success": False,
                "error": str(exc),
                "output": {},
                "decision": None,
            }

        # 4. Record result
        if result.success:
            self._update_run(
                run_id, RunStatus.COMPLETED,
                output_summary=self._summarize_output(result.output),
            )
            self._log(task_id, run_id, "info", defn.role.value,
                      f"{defn.display_name} completed successfully")

            # 5. Create execution proposal after Builder succeeds (Phase 6E-A)
            if defn.role == AgentRole.BUILDER:
                self._create_execution_proposal(task_id, run_id, result.output)
        else:
            self._update_run(
                run_id, RunStatus.FAILED,
                output_summary=json.dumps({
                    "error": result.error_message,
                    "output": result.output,
                }),
            )
            self._log(task_id, run_id, "error", defn.role.value,
                      f"{defn.display_name} failed: {result.error_message}")

        # 6. Patch agent_runs with actual runtime provider/model
        self._update_run_model_info(run_id, result.token_usage)

        # 7. Record token usage (Phase 12-1)
        self._record_token_usage(task_id, run_id, defn.role.value, result)

        return {
            "run_id": run_id,
            "role": defn.role.value,
            "success": result.success,
            "error": result.error_message,
            "output": result.output,
            "decision": result.decision,
        }

    # ── Execution proposal creation (Phase 6E-A) ───────────────

    def _create_execution_proposal(
        self,
        task_id: str,
        run_id: str,
        builder_output: dict,
    ) -> str | None:
        """Persist Builder output as an execution proposal.

        If the proposal requires approval, also creates an ApprovalRequest
        linked via proposal_id.  Returns proposal_id or None on error.
        Does NOT execute any part of the proposal.
        """
        try:
            from tools.proposal_validator import ProposalValidator

            validator = ProposalValidator()
            risk_report = validator.generate_risk_report(builder_output)

            risk_level = builder_output.get("risk_level", risk_report["overall_risk"])
            requires_approval = builder_output.get(
                "requires_approval", risk_report["approval_required"]
            )
            approval_reasons = builder_output.get(
                "approval_reasons", risk_report["reasons"]
            )

            proposal_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()

            conn = get_connection()
            try:
                conn.execute(
                    """INSERT INTO execution_proposals
                       (id, task_id, run_id, role, proposal_data, risk_level,
                        requires_approval, approval_reasons, status,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        proposal_id, task_id, run_id, "builder",
                        json.dumps(builder_output),
                        risk_level,
                        1 if requires_approval else 0,
                        json.dumps(approval_reasons),
                        "pending" if requires_approval else "approved",
                        now, now,
                    ),
                )

                # Create linked ApprovalRequest if approval needed
                if requires_approval:
                    approval_id = str(uuid.uuid4())
                    payload = json.dumps({
                        "proposal_id": proposal_id,
                        "risk_level": risk_level,
                        "approval_reasons": approval_reasons,
                        "change_summary": builder_output.get("change_summary", ""),
                        "files_affected": len(builder_output.get("proposed_files", [])),
                        "commands_count": len(builder_output.get("proposed_commands", [])),
                    })
                    conn.execute(
                        """INSERT INTO approval_requests
                           (id, task_id, run_id, action_type, action_payload,
                            status, proposal_id, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            approval_id, task_id, run_id,
                            "proposal:builder", payload,
                            "pending", proposal_id, now,
                        ),
                    )

                conn.commit()

                self._log(
                    task_id, run_id, "info", "orchestrator",
                    f"Execution proposal created "
                    f"(risk={risk_level}, "
                    f"approval={'required' if requires_approval else 'not required'})",
                )
                return proposal_id
            except Exception as exc:
                conn.rollback()
                self._log(
                    task_id, run_id, "error", "orchestrator",
                    f"Failed to create execution proposal: {exc}",
                )
                return None
            finally:
                conn.close()
        except Exception as exc:
            self._log(
                task_id, run_id, "error", "orchestrator",
                f"Proposal validation error: {exc}",
            )
            return None

    # ── Database helpers (direct SQL, same patterns as routers) ─

    @staticmethod
    def _load_task(task_id: str) -> dict | None:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def _get_task_status(task_id: str) -> TaskStatus | None:
        """Read the current task status from the database."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,),
            ).fetchone()
            return TaskStatus(row["status"]) if row else None
        finally:
            conn.close()

    @staticmethod
    def _claim_task(task_id: str) -> bool:
        """Atomically claim a pending task for orchestration.

        Returns True if the claim succeeded (row was updated), False if
        another orchestrator already claimed it.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        try:
            cursor = conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? "
                "WHERE id = ? AND status = ?",
                (TaskStatus.PLANNING.value, now, task_id, TaskStatus.PENDING.value),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    @staticmethod
    def _update_task_status(task_id: str, status: TaskStatus) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        try:
            # Validate state transition against the task state machine
            row = conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,),
            ).fetchone()
            if row is not None:
                current = TaskStatus(row["status"])
                if not is_valid_transition(current, status):
                    raise ValueError(
                        f"Invalid task transition: {current.value} → {status.value}"
                    )
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _create_run(
        task_id: str,
        role: AgentRole,
        model_provider: str,
        model_name: str,
        input_summary: str,
    ) -> str:
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO agent_runs
                   (id, task_id, role, model_provider, model_name,
                    status, input_summary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, task_id, role.value, model_provider, model_name,
                 RunStatus.PENDING.value, input_summary, now),
            )
            conn.commit()
            return run_id
        finally:
            conn.close()

    # Columns that _update_run is allowed to SET — prevents SQL injection
    # if a future caller ever passes untrusted keys into the dict.
    _ALLOWED_RUN_COLUMNS = frozenset({
        "status", "started_at", "ended_at", "output_summary",
        "model_provider", "model_name",
    })

    @staticmethod
    def _update_run(
        run_id: str,
        status: RunStatus,
        output_summary: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        try:
            updates: dict[str, str] = {"status": status.value}
            if status == RunStatus.RUNNING:
                updates["started_at"] = now
            elif status in (RunStatus.COMPLETED, RunStatus.FAILED):
                updates["ended_at"] = now
            if output_summary is not None:
                updates["output_summary"] = output_summary

            # Validate all column names against allowlist
            for col in updates:
                if col not in Orchestrator._ALLOWED_RUN_COLUMNS:
                    raise ValueError(f"Disallowed column in _update_run: {col}")

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [run_id]
            conn.execute(
                f"UPDATE agent_runs SET {set_clause} WHERE id = ?", values,
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _update_run_model_info(run_id: str, token_usage: dict | None) -> None:
        """Patch agent_runs with the actual runtime provider/model.

        Called after execution so the logged provider/model reflects what
        was really used, not the seed defaults from definitions.py.
        """
        if not token_usage:
            return
        provider = token_usage.get("provider", "")
        model = token_usage.get("model", "")
        if not provider and not model:
            return
        conn = get_connection()
        try:
            updates = []
            values: list[str] = []
            if provider:
                updates.append("model_provider = ?")
                values.append(provider)
            if model:
                updates.append("model_name = ?")
                values.append(model)
            values.append(run_id)
            conn.execute(
                f"UPDATE agent_runs SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _summarize_output(output: dict) -> str:
        """Create a concise JSON summary for output_summary storage.

        Avoids storing full large structured plans (e.g. Builder change plans)
        in the database.  Keeps top-level scalar keys and truncates lists.
        """
        MAX_SUMMARY_LEN = 2000
        full = json.dumps(output)
        if len(full) <= MAX_SUMMARY_LEN:
            return full
        # Build a concise summary: keep scalar values, summarize lists
        summary = {}
        for k, v in output.items():
            if isinstance(v, str):
                summary[k] = v[:200] + "..." if len(v) > 200 else v
            elif isinstance(v, list):
                summary[k] = f"[{len(v)} items]"
            else:
                summary[k] = v
        return json.dumps(summary)

    def _record_token_usage(
        self, task_id: str, run_id: str, role: str, result: ExecutionResult,
    ) -> None:
        """Persist token usage from an execution step (Phase 12-1)."""
        usage = result.token_usage
        if usage is None:
            # Mock executor or no usage data — skip silently
            return
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO token_usage_log
                   (id, task_id, run_id, role, provider, model,
                    prompt_tokens, completion_tokens, total_tokens, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    task_id,
                    run_id,
                    role,
                    usage.get("provider", ""),
                    usage.get("model", ""),
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("total_tokens", 0),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        except Exception:
            pass  # Never fail orchestration due to usage logging
        finally:
            conn.close()

    @staticmethod
    def _compress_rejection_history(history: list[dict]) -> None:
        """Compress older rejections to one-line summaries (Phase 12-3).

        Keeps the most recent rejection (last element) in full.
        All earlier entries are replaced with a compact summary containing
        only the rejection number, decision, and a truncated review summary.
        Mutates *history* in-place.
        """
        if len(history) <= 1:
            return
        for i in range(len(history) - 1):
            entry = history[i]
            summary_text = entry.get("review_summary", entry.get("reason", ""))
            if len(summary_text) > 120:
                summary_text = summary_text[:117] + "..."
            history[i] = {
                "rejection_number": i + 1,
                "decision": entry.get("decision", "request_changes"),
                "summary": summary_text,
            }

    @staticmethod
    def _log(
        task_id: str,
        run_id: str | None,
        level: str | LogLevel,
        source: str,
        message: str,
        payload: dict | None = None,
    ) -> None:
        # Normalise to LogLevel enum; accept raw strings for convenience
        if isinstance(level, str):
            level = LogLevel(level)
        log_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO log_events
                   (id, task_id, run_id, level, source, message, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (log_id, task_id, run_id, level.value, source, message,
                 json.dumps(payload or {}), now),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Project participant config (Phase 15-3) ────────────────

    @staticmethod
    def _get_enabled_roles(project_id: str) -> set[str]:
        """Return set of enabled role names for a project.

        If no participant config exists, returns all 4 default roles.
        """
        if not project_id:
            return {"planner", "builder", "qa", "reviewer"}
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT role_name FROM project_role_participants WHERE project_id = ? AND is_enabled = 1",
                (project_id,),
            ).fetchall()
            if not rows:
                # No config yet → all defaults enabled
                return {"planner", "builder", "qa", "reviewer"}
            return {r["role_name"] for r in rows}
        finally:
            conn.close()

    # ── Tool access check (Phase 4A enforcement seam) ─────────

    @staticmethod
    def _check_tool_access(role: AgentRole, tool_name: str) -> bool:
        """Check whether *role* is allowed to use *tool_name*.

        Uses the role definition's ``allowed_tools`` list together with
        the ``ToolRegistry`` alias resolution.  Phase 6 executor will
        call this before each tool invocation.
        """
        from agents.definitions import get_definition
        from tools.base import get_registry

        defn = get_definition(role)
        registry = get_registry()
        return registry.is_allowed(tool_name, defn.allowed_tools)
