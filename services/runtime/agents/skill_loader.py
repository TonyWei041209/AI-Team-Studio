"""Skill loader for agent prompt injection (Phase 14-3).

Loads enabled skills from the database and formats them for injection
into agent system prompts.

Rules:
- Global skills → injected into all supported roles (planner, builder, reviewer)
- Per-agent skills → injected only into the matching role
- Disabled skills → never injected
- QA → not injected (mock executor, no LLM calls)
- Skills only add context; they never grant tools, bypass approval, or change execution semantics
"""

from __future__ import annotations

from models import AgentRole

# Roles that receive skill injection (generative roles). QA + Security Reviewer are
# excluded (read-only reviewers); Architect + Documentation ARE included (generative —
# Documentation is a "second Builder" that produces file proposals).
_INJECTABLE_ROLES = {AgentRole.PLANNER, AgentRole.ARCHITECT, AgentRole.BUILDER, AgentRole.REVIEWER, AgentRole.DOCUMENTATION}


def load_skills_for_role(role: AgentRole) -> list[dict]:
    """Load enabled skills applicable to a given role.

    Returns a list of dicts with keys: name, description, content, scope_type, agent_role.
    Returns empty list if role is not injectable or on any DB error.
    """
    if role not in _INJECTABLE_ROLES:
        return []

    try:
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT name, description, content, scope_type, agent_role
                   FROM skills
                   WHERE is_enabled = 1
                     AND (scope_type = 'global' OR (scope_type = 'agent' AND agent_role = ?))
                   ORDER BY scope_type ASC, created_at ASC""",
                (role.value,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []  # Never break orchestration due to skill loading


def format_skills_block(skills: list[dict]) -> str:
    """Format a list of skills into a text block for system prompt injection.

    Returns empty string if no skills.
    """
    if not skills:
        return ""

    lines = ["\n\n--- Additional Skills ---"]
    for i, skill in enumerate(skills, 1):
        lines.append(f"\n[Skill {i}: {skill['name']}]")
        if skill.get("description"):
            lines.append(f"Description: {skill['description']}")
        if skill.get("content"):
            lines.append(skill["content"])
    lines.append("\n--- End Skills ---")
    return "\n".join(lines)


def build_enhanced_system_prompt(base_prompt: str, role: AgentRole) -> str:
    """Build the final system prompt by appending applicable skills.

    This is the main entry point used by ModelAgentExecutor.
    Returns the base prompt unchanged if no skills apply.
    """
    skills = load_skills_for_role(role)
    if not skills:
        return base_prompt
    block = format_skills_block(skills)
    return base_prompt + block
