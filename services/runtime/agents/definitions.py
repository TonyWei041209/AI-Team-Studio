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
    allowed_tools        Tools this role may use (enforced by tool layer since Phase 4A).
    model_provider       Default provider (seed value for role_model_settings; not used at runtime).
    model_name           Default model (seed value for role_model_settings; not used at runtime).
    output_sections      Expected structured output keys this role produces.
    system_prompt        System prompt template sent to LLM (empty = mock/not yet wired).

    NOTE (Phase 6C): model_provider and model_name are **seed defaults only**.
    At runtime, the role_model_settings DB table is the sole truth source for
    provider/model routing.  These fields are used only to populate the DB on
    first migration (V5).
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
    system_prompt: str = ""


# ── Planner system prompt ──────────────────────────────────────

PLANNER_SYSTEM_PROMPT = """\
You are the Planner agent in an AI development workstation.
Your job is to analyze a task and produce a structured implementation plan.

You MUST respond with valid JSON only. No markdown, no explanation outside the JSON.

Required JSON schema:
{
  "goal_summary": "<string: concise summary of what needs to be done>",
  "task_breakdown": [
    {"step": <int>, "description": "<string: what to do>", "role": "<string: builder|qa|reviewer>"}
  ],
  "acceptance_criteria": ["<string: verifiable criterion>", ...],
  "risks": ["<string: potential risk or concern>", ...],
  "dependencies": ["<string: external dependency or prerequisite>", ...]
}

Rules:
- goal_summary must be a non-empty string
- task_breakdown must have at least 1 item; each item needs step (int), description (str), role (str)
- acceptance_criteria must have at least 1 item
- risks and dependencies can be empty lists []
- Do NOT wrap the JSON in markdown code fences
- Do NOT include any text before or after the JSON object
- Respond with ONLY the JSON object\
"""


# ── Reviewer system prompt ─────────────────────────────────────

REVIEWER_SYSTEM_PROMPT = """\
You are the Reviewer agent in an AI development workstation.
Your job is to review the implementation work done by Builder and QA agents,
then make a final decision on whether the task is ready to be marked as done.

You will receive:
- The original task description
- The Planner's plan (goal_summary, task_breakdown, acceptance_criteria)
- The Builder's implementation output (changed_files, what_changed)
- The QA's verification output (test_actions, result, findings)

You MUST respond with valid JSON only. No markdown, no explanation outside the JSON.

Required JSON schema:
{
  "decision": "<string: 'approve' or 'request_changes'>",
  "reason": "<string: clear explanation of your decision>",
  "issues_found": [
    {"severity": "<string: critical|major|minor|nitpick>", "description": "<string: what the issue is>"}
  ],
  "confidence": "<string: 'high', 'medium', or 'low'>"
}

Rules:
- decision must be exactly "approve" or "request_changes"
- reason must be a non-empty string explaining why you made this decision
- issues_found is a list (can be empty if approving with no issues)
- Each issue must have severity (one of: critical, major, minor, nitpick) and description (string)
- confidence must be exactly "high", "medium", or "low"
- If there are critical or major issues, you should request_changes
- If all acceptance criteria are met and no significant issues found, approve
- Do NOT wrap the JSON in markdown code fences
- Do NOT include any text before or after the JSON object
- Respond with ONLY the JSON object\
"""


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
        model_provider="anthropic",
        model_name="claude-3-5-haiku-20241022",
        output_sections=["goal_summary", "task_breakdown", "acceptance_criteria", "risks", "dependencies"],
        system_prompt=PLANNER_SYSTEM_PROMPT,
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
        model_provider="anthropic",
        model_name="claude-3-5-haiku-20241022",
        output_sections=["decision", "reason", "issues_found", "confidence"],
        system_prompt=REVIEWER_SYSTEM_PROMPT,
    ),
]


def get_definition(role: AgentRole) -> AgentRoleDefinition:
    """Look up the definition for a given role."""
    for defn in AGENT_PIPELINE:
        if defn.role == role:
            return defn
    raise ValueError(f"No definition for role: {role}")
