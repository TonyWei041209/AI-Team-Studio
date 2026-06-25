"""C2 step 1 WIRING: comparator inserted live between builder and qa + Option-A collapse.

Verifies the wiring (NOT the selection logic — that's comparator_test.py):
  (a) WIRING: the comparator runs between builder and qa; the pipeline completes (done).
  (b) COLLAPSE: after the comparator, previous_outputs["builder"] holds the comparator's
      CHOSEN proposal (step 1: builder produces 1, sourced via the mock's previous_outputs
      fallback → chosen == that 1). Downstream (QA) receives a builder-shaped dict via
      previous_outputs["builder"].
  (c) AUDIT SLOT: previous_outputs["comparator"] holds the comparator's full output.
  (d) FAULT TOLERANCE: a comparator output lacking a usable `chosen` leaves
      previous_outputs["builder"] = the builder's original (downstream not broken); done.
  (e) DOWNSTREAM UNCHANGED: QA reads previous_outputs["builder"] (the slot the 4 real
      readers use; their isolated tests stay green — Option A).
  (f) PROPOSAL 1:1 (C2 step 2): ZERO execution_proposals from the builder (wrapper fails
      the gate); EXACTLY 1 from the comparator (the transfer). Safety-chain 1:1 intact.
  (g) FILTER-EXEMPTION: the comparator runs even for a project WITH participant config
      (it is always-on mandatory infra, not a disableable participant).

Hermetic: throwaway RUNTIME_DB; mock executors; no real provider/app/real-DB.

    python tests/comparator_wiring_test.py
"""
import asyncio
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="comp_wire_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.executor import MockAgentExecutor, ExecutionResult  # noqa: E402
from agents.orchestrator import Orchestrator  # noqa: E402
from models import AgentRole  # noqa: E402
from database import get_connection, init_db  # noqa: E402

init_db()

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


# A known single Builder proposal (auto-approvable, builder-shaped).
BUILDER_PROPOSAL = {
    "change_summary": "the-one-proposal",
    "proposed_files": [{"path": "src/f.py", "action": "modify", "reason": "r", "content": "x"}],
    "change_steps": [{"step": 1, "description": "do"}],
    "reasoning_summary": "why",
    "validation_plan": ["run tests"],
    "risk_notes": [],
    "risk_level": "low",
    "requires_approval": False,
}


class KnownBuilder:
    """Builder that emits a fixed, known proposal wrapped as N=1 (C2 step 2 shape).

    The wrapper {"proposals":[...]} has no top-level proposed_files → the builder creates
    0 proposals; the comparator selects the single candidate and creates the 1 proposal.
    """
    async def execute(self, role, task_context):
        return ExecutionResult(success=True, output={"proposals": [dict(BUILDER_PROPOSAL)]})


class RecordingQA:
    """Captures what QA receives via previous_outputs (the downstream collapse target)."""
    def __init__(self):
        self.seen_builder = None
        self.seen_comparator = None
        self.called = False

    async def execute(self, role, task_context):
        self.called = True
        prev = task_context.get("previous_outputs", {})
        self.seen_builder = prev.get("builder")
        self.seen_comparator = prev.get("comparator")
        return ExecutionResult(success=True, output={
            "validation_scope": "x", "review_findings": [],
            "acceptance_criteria_assessment": [], "result": "pass", "summary": "ok"})


class MalformedComparator:
    """Comparator whose output lacks a usable `chosen` — exercises the collapse fault tolerance."""
    async def execute(self, role, task_context):
        return ExecutionResult(success=True, output={"selected_index": None, "rationale": "no chosen here"})


def _make_task(participant_roles=None):
    """Create a project + pending task. If participant_roles given, seed project_role_participants."""
    pid, tid = str(_uuid.uuid4()), str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO projects (id,name,local_repo_path,default_branch,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (pid, "p", "/tmp/x", "main", "", now, now))
        conn.execute("INSERT INTO tasks (id,project_id,title,description,status,priority,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (tid, pid, "T", "", "pending", "medium", now, now))
        if participant_roles is not None:
            for r in participant_roles:
                conn.execute(
                    "INSERT INTO project_role_participants (id, project_id, role_name, is_enabled, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (str(_uuid.uuid4()), pid, r, now, now),
                )
        conn.commit()
    finally:
        conn.close()
    return pid, tid


# ══════════════════════════════════════════════════════════════
# Main run: KnownBuilder + RecordingQA, default mock for the rest (incl. real _comparator)
# ══════════════════════════════════════════════════════════════
print("\n[a-f] comparator wired live; collapse into previous_outputs['builder']")
pid, tid = _make_task()
qa = RecordingQA()
orch = Orchestrator(
    executor=MockAgentExecutor(delay_seconds=0),
    executors={AgentRole.BUILDER: KnownBuilder(), AgentRole.QA: qa},
)
result = asyncio.run(orch.run(tid))

step_roles = [s["role"] for s in result.steps]

# (a) WIRING
check("a: pipeline completed (done)", result.final_status == "done", result.final_status)
check("a: comparator runs between builder and qa",
      step_roles == ["planner", "architect", "builder", "comparator", "qa", "security_reviewer", "reviewer", "documentation"],
      str(step_roles))

# (b) COLLAPSE + source-fallback (mock sourced previous_outputs['builder'] as N=1)
check("b: QA was reached", qa.called is True)
check("b: previous_outputs['builder'] == the chosen proposal (== builder's 1)", qa.seen_builder == BUILDER_PROPOSAL, str(qa.seen_builder))
check("b: chosen is builder-shaped (has proposed_files) for downstream",
      isinstance(qa.seen_builder, dict) and isinstance(qa.seen_builder.get("proposed_files"), list))

# (c) COMPARATOR AUDIT SLOT preserved
check("c: previous_outputs['comparator'] present (audit)", isinstance(qa.seen_comparator, dict))
check("c: comparator audit has selected_index/rationale/chosen",
      qa.seen_comparator is not None
      and set(qa.seen_comparator) >= {"selected_index", "rationale", "chosen"}, str(qa.seen_comparator))
check("c: comparator selected_index == 0 (single candidate)", (qa.seen_comparator or {}).get("selected_index") == 0)
check("c: comparator.chosen == builder's proposal", (qa.seen_comparator or {}).get("chosen") == BUILDER_PROPOSAL)

# (f) PROPOSAL 1:1 — builder makes exactly 1; comparator makes 0
conn = get_connection()
try:
    builder_props = conn.execute("SELECT COUNT(*) FROM execution_proposals WHERE task_id=? AND role='builder'", (tid,)).fetchone()[0]
    comp_props = conn.execute("SELECT COUNT(*) FROM execution_proposals WHERE task_id=? AND role='comparator'", (tid,)).fetchone()[0]
finally:
    conn.close()
# C2 step 2: proposal creation transferred — builder (wrapper) creates 0, comparator creates 1.
check("f: ZERO execution_proposals from builder (wrapper fails the gate)", builder_props == 0, f"got {builder_props}")
check("f: EXACTLY 1 execution_proposal from comparator (the transfer)", comp_props == 1, f"got {comp_props}")


# ══════════════════════════════════════════════════════════════
# (d) FAULT TOLERANCE: malformed comparator output → builder slot intact
# ══════════════════════════════════════════════════════════════
print("\n[d] malformed comparator output → previous_outputs['builder'] intact")
pid2, tid2 = _make_task()
qa2 = RecordingQA()
orch2 = Orchestrator(
    executor=MockAgentExecutor(delay_seconds=0),
    executors={AgentRole.BUILDER: KnownBuilder(), AgentRole.COMPARATOR: MalformedComparator(), AgentRole.QA: qa2},
)
result2 = asyncio.run(orch2.run(tid2))
check("d: pipeline still completes (veto-safe)", result2.final_status == "done", result2.final_status)
check("d: builder slot retains the builder's ORIGINAL output (the wrapper, no overwrite with garbage)",
      qa2.seen_builder == {"proposals": [BUILDER_PROPOSAL]}, str(qa2.seen_builder))


# ══════════════════════════════════════════════════════════════
# (e) DOWNSTREAM UNCHANGED — QA read previous_outputs['builder'] (the real readers' slot)
# ══════════════════════════════════════════════════════════════
print("\n[e] downstream reads previous_outputs['builder'] (Option A; 4 real readers unchanged)")
check("e: QA sourced its input from previous_outputs['builder']", qa.seen_builder is not None)


# ══════════════════════════════════════════════════════════════
# (g) FILTER-EXEMPTION — comparator runs even WITH participant config
# ══════════════════════════════════════════════════════════════
print("\n[g] comparator is always-on: runs for a project WITH participant config")
# Seed the 7 user-facing participant roles (NO comparator) — it must still run (exempt).
pid3, tid3 = _make_task(participant_roles=[
    "planner", "architect", "builder", "qa", "security_reviewer", "reviewer", "documentation",
])
orch3 = Orchestrator(executor=MockAgentExecutor(delay_seconds=0))
result3 = asyncio.run(orch3.run(tid3))
roles3 = [s["role"] for s in result3.steps]
check("g: pipeline completes with participant config", result3.final_status == "done", result3.final_status)
check("g: comparator STILL runs (filter-exemption) despite config without it",
      "comparator" in roles3, str(roles3))
check("g: comparator still between builder and qa under config",
      "comparator" in roles3 and roles3.index("comparator") == roles3.index("builder") + 1
      and roles3.index("comparator") + 1 == roles3.index("qa"), str(roles3))


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  comparator wiring (C2 step 1): {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
