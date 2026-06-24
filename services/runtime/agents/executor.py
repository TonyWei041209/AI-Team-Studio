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
    raw_output     Raw provider text on a parse/validation failure (the unparsed
                   string that failed json.loads). Used by the orchestrator to
                   persist audit-grade raw_output on the FAILED path. None on success.
    failure_kind   On failure, what KIND: "json_parse" (json.loads failed — syntactic)
                   or "schema" (parsed but failed the output schema). Lets the builder's
                   inner retry target syntactic JSON failures only. None on success.
    tool_loop_stats  Tool-enabled roles ONLY (currently architect via _call_model_with_tools):
                   the structured tool-loop audit record {rounds, tool_calls, requests:
                   [{tool, path}], ended}. The orchestrator persists it to log_events for an
                   queryable audit trail (B3 step 3.5). None for single-shot roles. Metadata
                   only — tool names + PATHS, never file contents.
    """

    def __init__(
        self,
        success: bool,
        output: dict,
        error_message: str | None = None,
        decision: str | ReviewDecision | None = None,
        token_usage: dict | None = None,
        raw_output: str | None = None,
        failure_kind: str | None = None,
        tool_loop_stats: dict | None = None,
    ):
        self.success = success
        self.output = output
        self.error_message = error_message
        self.token_usage = token_usage
        self.raw_output = raw_output
        self.failure_kind = failure_kind
        self.tool_loop_stats = tool_loop_stats
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

# Risk ordering for the Comparator's veto-safe fallback (C2 step 1). Lower = safer.
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


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
            AgentRole.ARCHITECT: self._architect,
            AgentRole.BUILDER: self._builder,
            AgentRole.QA: self._qa,
            AgentRole.SECURITY_REVIEWER: self._security_reviewer,
            AgentRole.REVIEWER: self._reviewer,
            AgentRole.DOCUMENTATION: self._documentation,
            AgentRole.COMPARATOR: self._comparator,
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
        # Retry detection keys off rejection_history — the live channel the real
        # builder reads (model_executor.py). The former rejection_feedback key was
        # removed as dead (its value duplicated rejection_history[-1]).
        is_retry = "rejection_history" in ctx
        output = {
            "change_summary": (
                f"{'Revised' if is_retry else 'Implemented'} the core logic for: {title}"
            ),
            "proposed_files": [
                {
                    "path": "src/feature.py",
                    "action": "create" if not is_retry else "modify",
                    "reason": "Core feature implementation",
                },
                {
                    "path": "src/utils.py",
                    "action": "modify",
                    "reason": "Utility updates for feature support",
                },
            ],
            "change_steps": [
                {"step": 1, "description": f"{'Revise' if is_retry else 'Create'} feature module"},
                {"step": 2, "description": "Update utility functions"},
            ],
            "reasoning_summary": (
                "Addressed reviewer feedback" if is_retry
                else "Followed the plan from the Planner step"
            ),
            "validation_plan": [
                "Run unit tests",
                "Check type annotations",
                "Verify acceptance criteria",
            ],
            "risk_notes": [],
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

    def _architect(self, title: str, desc: str, ctx: dict) -> ExecutionResult:
        output = {
            "design_summary": f"Straightforward technical design for: {title}",
            "components": [],
            "key_decisions": [],
            "interfaces_or_contracts": [],
            "risks_tradeoffs": [],
            "summary": "No notable architectural concerns; Builder may implement per the Planner's plan (mock design).",
        }
        return ExecutionResult(success=True, output=output)

    def _security_reviewer(self, title: str, desc: str, ctx: dict) -> ExecutionResult:
        output = {
            "review_scope": f"Static security review of the proposed changes for: {title}",
            "findings": [],
            "overall_risk": "low",
            "verdict": "pass",
            "summary": "No semantic security risks identified in the proposal (mock review).",
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

    def _documentation(self, title: str, desc: str, ctx: dict) -> ExecutionResult:
        # A "second Builder": emit a doc proposal with proposed_files INCLUDING content
        # (the execution chain / scoped file executor needs content to write the file).
        output = {
            "change_summary": f"Document the implementation of: {title}",
            "proposed_files": [
                {
                    "path": "README.md",
                    "action": "modify",
                    "reason": "Document the new feature for users",
                    "content": f"# {title}\n\n{desc or 'See task description.'}\n\n(Documentation generated by the Documentation agent — mock.)\n",
                },
            ],
            "change_steps": [
                {"step": 1, "description": "Add a section to README.md describing the feature"},
            ],
            "reasoning_summary": "Documentation grounded in the implemented changes (mock).",
            "validation_plan": ["A human confirms the README section matches the implemented behavior"],
        }
        return ExecutionResult(success=True, output=output)

    def _comparator(self, title: str, desc: str, ctx: dict) -> ExecutionResult:
        """C2 step 1 (mock): collapse N candidate Builder proposals down to ONE.

        Selection rule (LOCKED): prefer a candidate with ``requires_approval is False``
        (auto-approvable); on a tie — multiple False, or none False (all True / field
        absent, treated as True) — keep the FIRST in list order. requires_approval only.

        Fallback (LOCKED): on ANY failure (no candidates, malformed entry, exception)
        degrade to the lowest ``risk_level`` (low<medium<high<critical; missing/unreadable
        → treated as critical so it sorts last); if ALL risk_levels are unreadable, keep the
        FIRST candidate. ALWAYS returns success=True (veto-safe — never aborts the pipeline).

        Step-1 source: the N candidates are read from ``ctx["candidate_proposals"]`` (tests
        inject synthetic N). The real "Builder produces N" source is wired in step 2.
        """
        candidates = ctx.get("candidate_proposals")
        if candidates is None:
            # Live pipeline (step 1): no explicit candidate list is injected. Fall back to the
            # Builder's output as the candidate set — a single Builder proposal becomes a
            # 1-element list (no-op passthrough); step 2 feeds N explicitly via
            # candidate_proposals. Tests that inject candidate_proposals are unaffected.
            prev = ctx.get("previous_outputs")
            builder_out = prev.get("builder") if isinstance(prev, dict) else None
            if isinstance(builder_out, list):
                candidates = builder_out
            elif isinstance(builder_out, dict) and builder_out:
                candidates = [builder_out]
        try:
            if not isinstance(candidates, list) or len(candidates) == 0:
                raise ValueError("no candidate_proposals to select from")
            # PRIMARY: first candidate with requires_approval is False; else the first overall.
            chosen_index = next(
                (i for i, c in enumerate(candidates)
                 if isinstance(c, dict) and c.get("requires_approval") is False),
                0,
            )
            chosen = candidates[chosen_index]
            if not isinstance(chosen, dict):
                raise ValueError(f"chosen candidate at index {chosen_index} is not an object")
            auto_approvable = chosen.get("requires_approval") is False
            basis = (
                "requires_approval=false (auto-approvable)"
                if auto_approvable
                else "no auto-approvable candidate; first in order"
            )
            return ExecutionResult(success=True, output={
                "selected_index": chosen_index,
                "selection_basis": basis,
                "rationale": (
                    f"Selected proposal #{chosen_index} of {len(candidates)} ({basis})."
                ),
                "chosen": chosen,
            })
        except Exception as exc:
            return self._comparator_fallback(candidates, exc)

    @staticmethod
    def _comparator_fallback(candidates, exc) -> ExecutionResult:
        """Veto-safe degrade for the Comparator: lowest risk_level, fault-tolerant.

        Never returns success=False (mirrors the QA/SR VETO INVARIANT). Missing/unreadable
        risk_level sorts last (critical); strict ``<`` keeps the first among equal ranks, so
        all-unreadable → the FIRST candidate.
        """
        try:
            if not isinstance(candidates, list) or len(candidates) == 0:
                return ExecutionResult(success=True, output={
                    "selected_index": None,
                    "selection_basis": "fallback_no_candidates",
                    "rationale": f"Selection failed ({exc}); no candidates available.",
                    "chosen": {},
                })
            best_index, best_rank = 0, None
            for i, c in enumerate(candidates):
                rank = 3  # missing / unreadable / non-dict → treat as critical (sorts last)
                if isinstance(c, dict):
                    rl = c.get("risk_level")
                    if isinstance(rl, str) and rl.strip().lower() in _RISK_ORDER:
                        rank = _RISK_ORDER[rl.strip().lower()]
                if best_rank is None or rank < best_rank:
                    best_rank, best_index = rank, i
            chosen = candidates[best_index]
            return ExecutionResult(success=True, output={
                "selected_index": best_index,
                "selection_basis": "fallback_lowest_risk",
                "rationale": (
                    f"Primary selection failed ({exc}); degraded to lowest risk_level "
                    f"(rank={best_rank}; all-unreadable→first)."
                ),
                "chosen": chosen if isinstance(chosen, dict) else {},
            })
        except Exception as exc2:
            # Absolute last resort — still veto-safe.
            has_first = isinstance(candidates, list) and len(candidates) > 0
            first = candidates[0] if has_first else {}
            return ExecutionResult(success=True, output={
                "selected_index": 0 if has_first else None,
                "selection_basis": "fallback_first_safe",
                "rationale": f"Fallback also failed ({exc2}); defaulted to first candidate.",
                "chosen": first if isinstance(first, dict) else {},
            })
