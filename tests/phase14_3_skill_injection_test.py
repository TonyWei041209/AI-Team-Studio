"""Phase 14-3 acceptance tests — skill injection into agent prompts.

Covers:
- T1: No skills → base prompt unchanged
- T2: Global enabled skill visible to planner/builder/reviewer
- T3: Per-agent enabled skill visible only to matching role
- T4: Disabled skill never injected
- T5: Global + per-agent skills can coexist
- T6: QA role never receives skills
- T7: format_skills_block output structure
- T8: build_enhanced_system_prompt preserves base prompt
"""

import os
import sys
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone

# Ensure service root on path
SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
sys.path.insert(0, SERVICE_ROOT)

# Use a temp file DB for tests (in-memory doesn't persist across connections)
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["RUNTIME_DB"] = _tmp_db.name

import database
from models import AgentRole
from agents.skill_loader import load_skills_for_role, format_skills_block, build_enhanced_system_prompt

# ── Helpers ────────────────────────────────────────────────

_pass = 0
_fail = 0


def check(label: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  PASS  {label}")
    else:
        _fail += 1
        print(f"  FAIL  {label}  {detail}")


def _reset_db():
    """Ensure schema is applied and clear skills table for a clean test state."""
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM skills")
        conn.commit()
    finally:
        conn.close()


def _insert_skill(
    name: str,
    scope_type: str = "global",
    agent_role: str | None = None,
    is_enabled: bool = True,
    description: str = "",
    content: str = "",
):
    conn = database.get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO skills (id, name, description, content, scope_type, agent_role, is_enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), name, description, content, scope_type, agent_role,
             1 if is_enabled else 0, now, now),
        )
        conn.commit()
    finally:
        conn.close()


# ── T1: No skills → empty list, base prompt unchanged ─────

def test_no_skills():
    print("\n[T1] No skills — base prompt unchanged")
    _reset_db()

    skills = load_skills_for_role(AgentRole.PLANNER)
    check("T1.1 load returns empty list", skills == [])

    base = "You are the Planner agent."
    enhanced = build_enhanced_system_prompt(base, AgentRole.PLANNER)
    check("T1.2 enhanced == base when no skills", enhanced == base)


# ── T2: Global enabled skill visible to all LLM roles ─────

def test_global_skill():
    print("\n[T2] Global enabled skill → planner/builder/reviewer")
    _reset_db()
    _insert_skill("Code Style", scope_type="global", description="Follow PEP8", content="Use 4 spaces.")

    for role in [AgentRole.PLANNER, AgentRole.BUILDER, AgentRole.REVIEWER]:
        skills = load_skills_for_role(role)
        check(f"T2.{role.value} gets global skill", len(skills) == 1 and skills[0]["name"] == "Code Style")


# ── T3: Per-agent skill visible only to matching role ──────

def test_per_agent_skill():
    print("\n[T3] Per-agent skill → only matching role")
    _reset_db()
    _insert_skill("Builder Hint", scope_type="agent", agent_role="builder", content="Focus on tests.")

    check("T3.1 builder sees skill", len(load_skills_for_role(AgentRole.BUILDER)) == 1)
    check("T3.2 planner does not see skill", len(load_skills_for_role(AgentRole.PLANNER)) == 0)
    check("T3.3 reviewer does not see skill", len(load_skills_for_role(AgentRole.REVIEWER)) == 0)


# ── T4: Disabled skill never injected ─────────────────────

def test_disabled_skill():
    print("\n[T4] Disabled skill → never injected")
    _reset_db()
    _insert_skill("Disabled Global", scope_type="global", is_enabled=False)
    _insert_skill("Disabled Agent", scope_type="agent", agent_role="planner", is_enabled=False)

    for role in [AgentRole.PLANNER, AgentRole.BUILDER, AgentRole.REVIEWER]:
        skills = load_skills_for_role(role)
        check(f"T4.{role.value} gets nothing", len(skills) == 0)


# ── T5: Global + per-agent coexist ────────────────────────

def test_mixed_skills():
    print("\n[T5] Global + per-agent skills coexist")
    _reset_db()
    _insert_skill("Global Tip", scope_type="global", content="Be concise.")
    _insert_skill("Planner Tip", scope_type="agent", agent_role="planner", content="Plan carefully.")
    _insert_skill("Builder Tip", scope_type="agent", agent_role="builder", content="Write tests.")

    planner_skills = load_skills_for_role(AgentRole.PLANNER)
    check("T5.1 planner gets 2 skills", len(planner_skills) == 2)
    names = {s["name"] for s in planner_skills}
    check("T5.2 planner sees Global Tip + Planner Tip", names == {"Global Tip", "Planner Tip"})

    builder_skills = load_skills_for_role(AgentRole.BUILDER)
    check("T5.3 builder gets 2 skills", len(builder_skills) == 2)
    names = {s["name"] for s in builder_skills}
    check("T5.4 builder sees Global Tip + Builder Tip", names == {"Global Tip", "Builder Tip"})

    reviewer_skills = load_skills_for_role(AgentRole.REVIEWER)
    check("T5.5 reviewer gets 1 skill", len(reviewer_skills) == 1)
    check("T5.6 reviewer sees only Global Tip", reviewer_skills[0]["name"] == "Global Tip")


# ── T6: QA never receives skills ──────────────────────────

def test_qa_excluded():
    print("\n[T6] QA role — never receives skills")
    _reset_db()
    _insert_skill("Global Tip", scope_type="global", content="Be concise.")
    _insert_skill("QA Tip", scope_type="agent", agent_role="qa", content="Check coverage.")

    skills = load_skills_for_role(AgentRole.QA)
    check("T6.1 QA gets empty list", len(skills) == 0)


# ── T7: format_skills_block output structure ──────────────

def test_format_block():
    print("\n[T7] format_skills_block output structure")

    check("T7.1 empty list → empty string", format_skills_block([]) == "")

    skills = [
        {"name": "Tip A", "description": "Desc A", "content": "Content A"},
        {"name": "Tip B", "description": "", "content": "Content B"},
    ]
    block = format_skills_block(skills)
    check("T7.2 block contains header", "--- Additional Skills ---" in block)
    check("T7.3 block contains footer", "--- End Skills ---" in block)
    check("T7.4 block contains skill 1 name", "[Skill 1: Tip A]" in block)
    check("T7.5 block contains skill 2 name", "[Skill 2: Tip B]" in block)
    check("T7.6 block contains content", "Content A" in block)
    check("T7.7 block contains description", "Description: Desc A" in block)


# ── T8: build_enhanced_system_prompt preserves base ───────

def test_enhanced_prompt():
    print("\n[T8] build_enhanced_system_prompt preserves base")
    _reset_db()
    _insert_skill("Style", scope_type="global", content="Follow PEP8.")

    base = "You are the Planner agent.\nRespond with JSON only."
    enhanced = build_enhanced_system_prompt(base, AgentRole.PLANNER)
    check("T8.1 starts with base prompt", enhanced.startswith(base))
    check("T8.2 contains skill content", "Follow PEP8." in enhanced)
    check("T8.3 contains skills header", "--- Additional Skills ---" in enhanced)
    check("T8.4 base is strict prefix", enhanced[:len(base)] == base)


# ── Main ──────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Phase 14-3: Skill injection acceptance tests")
    print("=" * 60)

    test_no_skills()
    test_global_skill()
    test_per_agent_skill()
    test_disabled_skill()
    test_mixed_skills()
    test_qa_excluded()
    test_format_block()
    test_enhanced_prompt()

    print("\n" + "=" * 60)
    total = _pass + _fail
    print(f"  Result: {_pass}/{total} PASS, {_fail} FAIL")
    print("=" * 60)
    sys.exit(1 if _fail > 0 else 0)
