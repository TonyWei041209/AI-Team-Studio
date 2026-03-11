"""Agent executor protocol and mock implementation.

The AgentExecutor Protocol is the Phase 6 upgrade seam.
Swap MockAgentExecutor for a real ModelAgentExecutor later
without touching the Orchestrator.
"""

from __future__ import annotations

import asyncio
import random
from typing import Protocol, runtime_checkable

from models import AgentRole, ReviewDecision


# ── Result container ───────────────────────────────────────────


class ExecutionResult:
    """Outcome of a single agent execution.

    Attributes
    ----------
    success        Whether the agent finished its work without errors.
    output         Structured dict of the agent's output sections.
    error_message  Human-readable error (only when success is False).
    decision       Reviewer-only: ReviewDecision enum value.
    """

    def __init__(
        self,
        success: bool,
        output: dict,
        error_message: str | None = None,
        decision: str | ReviewDecision | None = None,
    ):
        self.success = success
        self.output = output
        self.error_message = error_message
        # Normalise to ReviewDecision enum when provided
        if isinstance(decision, str):
            self.decision = ReviewDecision(decision).value
        elif isinstance(decision, ReviewDecision):
            self.decision = decision.value
        else:
            self.decision = None


# ── Protocol (the upgrade seam) ────────────────────────────────


@runtime_checkable
class AgentExecutor(Protocol):
    """Interface that any agent executor must satisfy.

    Phase 3: MockAgentExecutor
    Phase 6: ModelAgentExecutor (real API calls)
    """

    async def execute(
        self,
        role: AgentRole,
        task_context: dict,
    ) -> ExecutionResult: ...


# ── Mock implementation ────────────────────────────────────────


class MockAgentExecutor:
    """Simulates agent work with configurable delays, failures, and rejections.

    Parameters
    ----------
    delay_seconds   Simulated work time per agent step.
    failure_rate    Probability [0, 1] that any agent step fails.
    rejection_rate  Probability [0, 1] that Reviewer returns REQUEST_CHANGES.
    """

    def __init__(
        self,
        delay_seconds: float = 0.5,
        failure_rate: float = 0.0,
        rejection_rate: float = 0.0,
    ):
        self.delay_seconds = delay_seconds
        self.failure_rate = failure_rate
        self.rejection_rate = rejection_rate

    async def execute(
        self,
        role: AgentRole,
        task_context: dict,
    ) -> ExecutionResult:
        """Simulate agent execution with a delay and mock output."""
        await asyncio.sleep(self.delay_seconds)

        # Simulate random failure
        if random.random() < self.failure_rate:
            return ExecutionResult(
                success=False,
                output={},
                error_message=f"Mock {role.value} agent encountered a simulated error",
            )

        title = task_context.get("title", "Untitled task")
        desc = task_context.get("description", "")

        generators = {
            AgentRole.PLANNER: self._planner,
            AgentRole.BUILDER: self._builder,
            AgentRole.QA: self._qa,
            AgentRole.REVIEWER: self._reviewer,
        }
        return generators[role](title, desc, task_context)

    # ── Per-role mock output generators ────────────────────────

    def _planner(self, title: str, desc: str, ctx: dict) -> ExecutionResult:
        output = {
            "goal_summary": (
                f"Implement: {title}. "
                f"{desc[:120] if desc else 'No description provided.'}"
            ),
            "task_breakdown": [
                {"title": "Analyze requirements", "owner": "planner", "priority": "high"},
                {"title": "Implement core logic", "owner": "builder", "priority": "high"},
                {"title": "Write tests", "owner": "builder", "priority": "medium"},
                {"title": "Verify functionality", "owner": "qa", "priority": "high"},
            ],
            "acceptance_criteria": [
                "Core functionality works as described",
                "No regressions in existing tests",
                "Code follows project conventions",
            ],
            "risks": [
                "Requirements may need clarification",
            ],
        }
        return ExecutionResult(success=True, output=output)

    def _builder(self, title: str, desc: str, ctx: dict) -> ExecutionResult:
        is_retry = "rejection_feedback" in ctx
        output = {
            "changed_files": [
                "src/feature.py (created)" if not is_retry else "src/feature.py (revised)",
                "src/utils.py (modified)",
            ],
            "what_changed": (
                f"{'Revised' if is_retry else 'Implemented'} the core logic for: {title}"
            ),
            "why": (
                "Addressed reviewer feedback" if is_retry
                else "Followed the plan from the Planner step"
            ),
            "validation": {
                "lint": "pass",
                "typecheck": "pass",
                "tests": "pass",
            },
            "open_issues": [],
        }
        return ExecutionResult(success=True, output=output)

    def _qa(self, title: str, desc: str, ctx: dict) -> ExecutionResult:
        output = {
            "validation_scope": f"Verified implementation of: {title}",
            "test_actions": [
                "Ran unit tests — all pass",
                "Checked type annotations — clean",
                "Verified acceptance criteria — met",
            ],
            "result": "PASS",
            "findings": [],
        }
        return ExecutionResult(success=True, output=output)

    def _reviewer(self, title: str, desc: str, ctx: dict) -> ExecutionResult:
        # Simulate rejection
        if random.random() < self.rejection_rate:
            output = {
                "review_summary": f"Changes for '{title}' need revision",
                "alignment_check": "Partially meets acceptance criteria",
                "risk_review": {"stability": "medium"},
                "decision": ReviewDecision.REQUEST_CHANGES.value,
                "reason": "Implementation needs improvements before approval",
            }
            return ExecutionResult(
                success=True, output=output,
                decision=ReviewDecision.REQUEST_CHANGES,
            )

        output = {
            "review_summary": f"Changes for '{title}' look good",
            "alignment_check": "All acceptance criteria met",
            "risk_review": {"stability": "low", "security": "low"},
            "decision": ReviewDecision.APPROVE.value,
            "reason": "Implementation matches plan, tests pass, no significant risks",
        }
        return ExecutionResult(
            success=True, output=output,
            decision=ReviewDecision.APPROVE,
        )
