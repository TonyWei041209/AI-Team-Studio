"""Reviewer receives the Architect's design (B2 #1) — prompt-assembly + system-prompt wiring.

B2 #1 wires the Architect's technical design into the Reviewer's context so the final
acceptance gate can judge build-vs-design consistency (the B3 blind spot: the reviewer
approved an unrequested threading.Lock praising "thread safety" with no design baseline).

This is a reviewer-only ADDITIVE change:
  - 1 data line in _build_reviewer_user_message: an OPTIONAL "Architect design:" block,
    placed after the planner block and before the builder block (planner -> architect ->
    builder order), byte-identical to the builder/documentation injection convention.
  - 2 REVIEWER_SYSTEM_PROMPT edits: the architect design (+ the already-injected security
    reviewer) added to the "You will receive:" list, and a Rules directive telling the
    reviewer to check the build against the design and flag scope-creep / unrequested
    additions in issues_found.

Covers:
  (a) architect output present -> the "Architect design:" block IS in the assembled message
      AND it appears in planner -> architect -> builder ORDER; content is injected with the
      exact compact-json convention (separators=(',',':')).
  (b) NO architect output (degraded run) -> the block is OMITTED gracefully (the
      `if prev.get("architect")` guard), no crash, other optional blocks still present.
  (c) REVIEWER_SYSTEM_PROMPT gained the architect + security-reviewer input lines and the
      consistency/scope-creep rule, while the original reviewer instructions remain intact.

Hermetic: no provider/network/app — _build_reviewer_user_message is a pure static method;
a throwaway RUNTIME_DB is set defensively for the definitions import path.

    python tests/reviewer_architect_context_test.py
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="reviewer_arch_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.model_executor import ModelAgentExecutor  # noqa: E402
from agents.definitions import REVIEWER_SYSTEM_PROMPT  # noqa: E402

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


# Representative upstream outputs. The architect design carries a unique marker + a
# declared interface/contract so we can assert real content reaches the reviewer.
ARCH_MARKER = "ARCH_DESIGN_MARKER_zzz"
IOC_MARKER = "TaskQueue.cancel(task_id) -> bool"
ARCHITECT = {
    "design_summary": f"single-threaded design {ARCH_MARKER}",
    "components": [{"name": "TaskQueue", "responsibility": "manage tasks", "interfaces": "cancel()"}],
    "key_decisions": [{"decision": "no locking", "rationale": "single-threaded runtime"}],
    "interfaces_or_contracts": [IOC_MARKER],
    "risks_tradeoffs": ["none"],
    "summary": "implement cancel without threading primitives",
}
PLANNER = {
    "goal_summary": "add cancel", "task_breakdown": [{"step": 1, "description": "x", "role": "builder"}],
    "acceptance_criteria": ["cancel works"], "risks": [], "dependencies": [],
}
BUILDER = {
    "change_summary": "add cancel", "proposed_files": [{"path": "src/q.py", "action": "modify", "reason": "x"}],
    "change_steps": [{"step": 1, "description": "x"}], "reasoning_summary": "r", "validation_plan": ["v"],
}
QA = {
    "validation_scope": "s", "review_findings": [], "acceptance_criteria_assessment": [],
    "result": "pass", "summary": "ok",
}
SR = {"review_scope": "s", "findings": [], "overall_risk": "none", "verdict": "pass", "summary": "ok"}


def build(prev):
    ctx = {"title": "t", "description": "d", "priority": "low", "previous_outputs": prev}
    return ModelAgentExecutor._build_reviewer_user_message(ctx)


# ══════════════════════════════════════════════════════════════
# (a) architect present -> block injected, planner->architect->builder order
# ══════════════════════════════════════════════════════════════
print("\n[a] architect output present -> 'Architect design:' block in correct order")
msg = build({"planner": PLANNER, "architect": ARCHITECT, "builder": BUILDER, "qa": QA, "security_reviewer": SR})

check("a: 'Architect design:' block present", "Architect design:" in msg)
check("a: architect content injected (design_summary marker reaches reviewer)", ARCH_MARKER in msg)
check("a: architect interfaces_or_contracts injected (the contract the builder must honor)", IOC_MARKER in msg)

i_plan = msg.find("Planner output:")
i_arch = msg.find("Architect design:")
i_build = msg.find("Builder output:")
check("a: order planner -> architect (architect after planner)", i_plan != -1 and i_arch != -1 and i_plan < i_arch,
      f"plan={i_plan} arch={i_arch}")
check("a: order architect -> builder (architect before builder)", i_arch != -1 and i_build != -1 and i_arch < i_build,
      f"arch={i_arch} build={i_build}")

# byte-identical convention: labeled header + compact json.dumps(separators=(',',':'))
expected_block = "\nArchitect design:\n" + json.dumps(ARCHITECT, ensure_ascii=False, separators=(",", ":"))
check("a: block matches builder/doc convention exactly (compact json, no spaces)", expected_block in msg)


# ══════════════════════════════════════════════════════════════
# (b) no architect -> block omitted gracefully, other blocks intact
# ══════════════════════════════════════════════════════════════
print("\n[b] NO architect output (degraded run) -> block omitted gracefully")
msg2 = build({"planner": PLANNER, "builder": BUILDER, "qa": QA, "security_reviewer": SR})

check("b: 'Architect design:' block OMITTED (guard skips it)", "Architect design:" not in msg2)
check("b: no crash + planner block still present", "Planner output:" in msg2)
check("b: builder block still present", "Builder output:" in msg2)
check("b: qa + security_reviewer blocks still present", "QA output:" in msg2 and "Security Reviewer output:" in msg2)

# empty previous_outputs (e.g. all-degraded) also assembles without crashing
msg3 = build({})
check("b: empty previous_outputs assembles (task framing only, no crash)",
      "Task: t" in msg3 and "Architect design:" not in msg3)


# ══════════════════════════════════════════════════════════════
# (c) REVIEWER_SYSTEM_PROMPT gained the inputs + the consistency rule
# ══════════════════════════════════════════════════════════════
print("\n[c] REVIEWER_SYSTEM_PROMPT: architect+SR inputs + consistency/scope-creep rule")
check("c: architect added to 'You will receive' list (real field names)",
      "The Architect's technical design (design_summary, components, interfaces_or_contracts, key_decisions)"
      in REVIEWER_SYSTEM_PROMPT)
check("c: security_reviewer added to input list (corrects existing drift)",
      "The Security Reviewer's findings (findings, overall_risk, verdict)" in REVIEWER_SYSTEM_PROMPT)
check("c: consistency rule present (check build vs design)",
      "Check the Builder's implementation against the Architect's design" in REVIEWER_SYSTEM_PROMPT)
check("c: rule flags scope-creep / unrequested additions",
      "scope-creep" in REVIEWER_SYSTEM_PROMPT and "issues_found" in REVIEWER_SYSTEM_PROMPT)
# original reviewer instructions remain intact (additive change, nothing removed)
check("c: original reviewer identity intact", "You are the Reviewer agent" in REVIEWER_SYSTEM_PROMPT)
check("c: original approve/request_changes criteria intact",
      "If all acceptance criteria are met and no significant issues found, approve" in REVIEWER_SYSTEM_PROMPT)


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  reviewer architect-context: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
