"""Data-driven definitions for the four agent roles and pipeline sequence."""

from __future__ import annotations

from dataclasses import dataclass, field

from models import AgentRole, TaskStatus


@dataclass(frozen=True)
class AgentRoleDefinition:
    """Declarative metadata for one agent role.

    Fields
    ------
    role                 Which AgentRole enum value this represents.
    display_name         Human-readable label (e.g. "Planner").
    description          One-line description of the role's purpose.
    required_task_status Task status that must hold before this role can run.
    success_task_status  Task status to set after this role succeeds.
    allowed_tools        Informational: tools this role may use (enforced in Phase 6).
    model_provider       Default provider string (overridden per-call in Phase 6).
    model_name           Default model string (overridden per-call in Phase 6).
    output_sections      Expected structured output keys this role produces.
    """

    role: AgentRole
    display_name: str
    description: str
    required_task_status: TaskStatus
    success_task_status: TaskStatus
    allowed_tools: list[str] = field(default_factory=list)
    model_provider: str = "mock"
    model_name: str = "mock-v1"
    output_sections: list[str] = field(default_factory=list)


# ── Canonical pipeline sequence ────────────────────────────────
# The orchestrator iterates this list in order.

AGENT_PIPELINE: list[AgentRoleDefinition] = [
    AgentRoleDefinition(
        role=AgentRole.PLANNER,
        display_name="Planner",
        description="Breaks down the task into subtasks and sets acceptance criteria",
        required_task_status=TaskStatus.PENDING,
        success_task_status=TaskStatus.PLANNING,
        allowed_tools=["read", "grep", "glob"],
        output_sections=["goal_summary", "task_breakdown", "acceptance_criteria", "risks"],
    ),
    AgentRoleDefinition(
        role=AgentRole.BUILDER,
        display_name="Builder",
        description="Implements the planned changes",
        required_task_status=TaskStatus.PLANNING,
        success_task_status=TaskStatus.IN_PROGRESS,
        allowed_tools=["read", "edit", "write", "bash", "grep", "glob"],
        output_sections=["changed_files", "what_changed", "why", "validation", "open_issues"],
    ),
    AgentRoleDefinition(
        role=AgentRole.QA,
        display_name="QA",
        description="Validates the implementation against acceptance criteria",
        required_task_status=TaskStatus.IN_PROGRESS,
        success_task_status=TaskStatus.REVIEWING,
        allowed_tools=["read", "bash", "grep", "glob"],
        output_sections=["validation_scope", "test_actions", "result", "findings", "repro_steps"],
    ),
    AgentRoleDefinition(
        role=AgentRole.REVIEWER,
        display_name="Reviewer",
        description="Final review and approve/reject decision",
        required_task_status=TaskStatus.REVIEWING,
        success_task_status=TaskStatus.DONE,
        allowed_tools=["read", "grep", "glob"],
        output_sections=["review_summary", "alignment_check", "risk_review", "decision", "reason"],
    ),
]


def get_definition(role: AgentRole) -> AgentRoleDefinition:
    """Look up the definition for a given role."""
    for defn in AGENT_PIPELINE:
        if defn.role == role:
            return defn
    raise ValueError(f"No definition for role: {role}")
