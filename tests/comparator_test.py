"""C2 step 1: read-only mock Comparator role — selects ONE proposal among N.

The Comparator collapses N candidate Builder proposals down to 1 (C2 direction =
internal collapse N→1). It is DEFINED BUT UNWIRED here (not in AGENT_PIPELINE); this
suite exercises the role definition + the MockAgentExecutor selection logic against
synthetic N candidates injected via ctx["candidate_proposals"].

Selection rule (LOCKED): prefer requires_approval == False (auto-approvable); first-on-tie.
Fallback (LOCKED): on failure/malformed → lowest risk_level (missing → critical, sorts last;
all-unreadable → first); ALWAYS success=True (veto-safe — never aborts).

Cases:
  (a) one candidate requires_approval=false → that one selected
  (b) multiple requires_approval=false → FIRST of them selected
  (c) all requires_approval=true / absent → first selected
  (d) malformed candidate → fallback degrades to lowest risk_level; success=True
  (e) fallback with all risk_levels unreadable → first candidate (builder-shaped)
  (f) the chosen output is builder-shaped + cleanly extractable

Hermetic: throwaway RUNTIME_DB; mock executor only; no real provider/app/real-DB.

    python tests/comparator_test.py
"""
import asyncio
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="comparator_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.executor import MockAgentExecutor  # noqa: E402
from agents.definitions import COMPARATOR_DEFINITION, AGENT_PIPELINE  # noqa: E402
from models import AgentRole, TaskStatus  # noqa: E402

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"  -- {detail}" if detail else ""))


def _proposal(risk_level=None, requires_approval=None, summary="impl"):
    """A Builder-shaped proposal dict; optional risk_level / requires_approval."""
    p = {
        "change_summary": summary,
        "proposed_files": [{"path": "src/f.py", "action": "modify", "reason": "r", "content": "x"}],
        "change_steps": [{"step": 1, "description": "do"}],
        "reasoning_summary": "why",
        "validation_plan": ["run tests"],
        "risk_notes": [],
    }
    if risk_level is not None:
        p["risk_level"] = risk_level
    if requires_approval is not None:
        p["requires_approval"] = requires_approval
    return p


def _select(candidates):
    mock = MockAgentExecutor(delay_seconds=0)
    return asyncio.run(mock.execute(AgentRole.COMPARATOR, {
        "title": "T", "description": "d", "candidate_proposals": candidates,
    }))


# ══════════════════════════════════════════════════════════════
# Definition sanity (DEFINED BUT UNWIRED)
# ══════════════════════════════════════════════════════════════
print("\n[def] COMPARATOR_DEFINITION standalone + UNWIRED")
check("def: role is COMPARATOR", COMPARATOR_DEFINITION.role == AgentRole.COMPARATOR)
check("def: no-op status (required==success==IN_PROGRESS)",
      COMPARATOR_DEFINITION.required_task_status == TaskStatus.IN_PROGRESS
      and COMPARATOR_DEFINITION.success_task_status == TaskStatus.IN_PROGRESS)
check("def: read-only (allowed_tools == [])", COMPARATOR_DEFINITION.allowed_tools == [])
check("def: has a system prompt", bool(COMPARATOR_DEFINITION.system_prompt.strip()))
check("def: NOT inserted into AGENT_PIPELINE (unwired)",
      all(d.role != AgentRole.COMPARATOR for d in AGENT_PIPELINE),
      f"pipeline roles: {[d.role.value for d in AGENT_PIPELINE]}")


# ══════════════════════════════════════════════════════════════
# (a) one candidate requires_approval=false → that one selected
# ══════════════════════════════════════════════════════════════
print("\n[a] one auto-approvable → selected")
res = _select([
    _proposal(requires_approval=True, summary="needs-approval"),
    _proposal(requires_approval=False, summary="auto"),
    _proposal(requires_approval=True, summary="also-needs"),
])
check("a: success=True", res.success is True)
check("a: selected_index == 1 (the requires_approval=false one)", res.output["selected_index"] == 1, str(res.output))
check("a: chosen is the auto-approvable proposal", res.output["chosen"]["change_summary"] == "auto")


# ══════════════════════════════════════════════════════════════
# (b) multiple requires_approval=false → FIRST of them
# ══════════════════════════════════════════════════════════════
print("\n[b] multiple auto-approvable → first of them")
res = _select([
    _proposal(requires_approval=True, summary="x"),
    _proposal(requires_approval=False, summary="first-auto"),
    _proposal(requires_approval=False, summary="second-auto"),
])
check("b: selected_index == 1 (first false)", res.output["selected_index"] == 1, str(res.output["selected_index"]))
check("b: chosen is the FIRST auto-approvable", res.output["chosen"]["change_summary"] == "first-auto")


# ══════════════════════════════════════════════════════════════
# (c) all requires_approval=true / absent → first selected
# ══════════════════════════════════════════════════════════════
print("\n[c] none auto-approvable (true / absent) → first")
res = _select([
    _proposal(requires_approval=True, summary="first-true"),
    _proposal(summary="absent"),                       # field absent → treated as True
    _proposal(requires_approval=True, summary="third"),
])
check("c: selected_index == 0 (first overall)", res.output["selected_index"] == 0, str(res.output["selected_index"]))
check("c: chosen is the first candidate", res.output["chosen"]["change_summary"] == "first-true")
# sub-case: ALL absent → still first
res_abs = _select([_proposal(summary="a0"), _proposal(summary="a1")])
check("c: all-absent → first selected", res_abs.output["selected_index"] == 0 and res_abs.output["chosen"]["change_summary"] == "a0")


# ══════════════════════════════════════════════════════════════
# (d) malformed candidate → fallback degrades to lowest risk_level; veto-safe
# ══════════════════════════════════════════════════════════════
print("\n[d] malformed → fallback lowest risk_level (success=True)")
res = _select([
    "garbage-not-a-dict",                                          # forces primary to raise
    _proposal(risk_level="high", requires_approval=True, summary="high"),
    _proposal(risk_level="low", requires_approval=True, summary="low"),
])
check("d: success=True (veto-safe, never aborts)", res.success is True)
check("d: selection_basis == fallback_lowest_risk", res.output["selection_basis"] == "fallback_lowest_risk", str(res.output["selection_basis"]))
check("d: selected_index == 2 (the 'low' risk one)", res.output["selected_index"] == 2, str(res.output["selected_index"]))
check("d: chosen risk_level == 'low'", res.output["chosen"].get("risk_level") == "low")
# missing risk_level sorts last (critical): low beats a missing-risk candidate
res2 = _select([
    "garbage",
    _proposal(requires_approval=True, summary="no-risk"),         # missing risk_level → critical
    _proposal(risk_level="medium", requires_approval=True, summary="medium"),
])
check("d: missing risk_level treated as critical → 'medium' wins over missing",
      res2.output["selected_index"] == 2 and res2.output["chosen"].get("risk_level") == "medium", str(res2.output))
# N=0 → veto-safe empty fallback
res0 = _select([])
check("d: N=0 → success=True + no_candidates fallback", res0.success is True and res0.output["selection_basis"] == "fallback_no_candidates")
check("d: N=0 → selected_index is None", res0.output["selected_index"] is None)


# ══════════════════════════════════════════════════════════════
# (e) fallback with ALL risk_levels unreadable → FIRST candidate (builder-shaped)
# ══════════════════════════════════════════════════════════════
print("\n[e] fallback all-unreadable → first candidate")
# Direct fallback test (the locked rule) with clean builder-shaped dicts, all missing risk_level:
p_first = _proposal(summary="first-of-equals")
p_second = _proposal(summary="second")
fb = MockAgentExecutor._comparator_fallback([p_first, p_second], ValueError("forced"))
check("e: success=True", fb.success is True)
check("e: all risk unreadable → selected_index == 0 (first)", fb.output["selected_index"] == 0, str(fb.output["selected_index"]))
check("e: chosen is the FIRST candidate (builder-shaped)", fb.output["chosen"]["change_summary"] == "first-of-equals")


# ══════════════════════════════════════════════════════════════
# (f) chosen output is builder-shaped + cleanly extractable
# ══════════════════════════════════════════════════════════════
print("\n[f] chosen is builder-shaped + extractable")
winner = _proposal(requires_approval=False, summary="winner")
res = _select([_proposal(requires_approval=True, summary="loser"), winner])
chosen = res.output["chosen"]
check("f: chosen == the original winning proposal (intact)", chosen == winner)
check("f: chosen has proposed_files (builder-shaped)",
      isinstance(chosen.get("proposed_files"), list) and len(chosen["proposed_files"]) == 1)
check("f: chosen proposed_files[0] has path/action/reason/content",
      set(chosen["proposed_files"][0]) >= {"path", "action", "reason", "content"})
check("f: chosen has change_steps + change_summary (builder-shaped)",
      isinstance(chosen.get("change_steps"), list) and chosen.get("change_summary") == "winner")
check("f: selected_index points at the chosen (index 1)", res.output["selected_index"] == 1)


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  comparator (C2 step 1): {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
