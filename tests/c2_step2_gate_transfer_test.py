"""C2 step 2 (mock-first): proposal creation TRANSFERS from builder to comparator.

THE SAFETY-CHAIN TEST. The mock Builder now produces N proposals under a WRAPPER
{"proposals":[...]} with NO top-level proposed_files → the Builder creates 0 proposals
(it fails the gate). The Comparator selects ONE and ITS chosen (a Builder-shaped dict WITH
proposed_files) becomes the SINGLE execution_proposal. 1:1 is enforced at the single gate
site (execution_proposals has no UNIQUE(task_id) — N proposals would mean N approvable chains).

Asserts:
  (1) BUILDER CREATES 0 (wrapper fails the gate).
  (2) COMPARATOR CREATES EXACTLY 1 — the chosen proposal (proposal_data == chosen).
  (3) TOTAL from builder->comparator flow == 1 (NOT N). 1:1 preserved.
  (4) N-1 leave no trace (no extra proposals; no snapshots).
  (5) CHOSEN is correct (selection rule: requires_approval==False, first-on-tie → option 2).
  (6) Comparator sourced the N from previous_outputs["builder"]["proposals"] (selected_index==1).
  (7) Collapse still works: previous_outputs["builder"] == the single chosen (downstream unchanged).
  (8) AUDIT: builder's agent_runs.raw_output contains the full N (the wrapper).
  (9) The created proposal's role == "comparator" (kept, not overridden).

Hermetic: throwaway RUNTIME_DB; mock executors; no real provider/app/real-DB.

    python tests/c2_step2_gate_transfer_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="c2s2_"))
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


class RecordingQA:
    """Captures what QA receives downstream of the comparator collapse."""
    def __init__(self):
        self.seen_builder = None
        self.seen_comparator = None

    async def execute(self, role, task_context):
        prev = task_context.get("previous_outputs", {})
        self.seen_builder = prev.get("builder")
        self.seen_comparator = prev.get("comparator")
        return ExecutionResult(success=True, output={
            "validation_scope": "x", "review_findings": [],
            "acceptance_criteria_assessment": [], "result": "pass", "summary": "ok"})


def _make_task():
    pid, tid = str(_uuid.uuid4()), str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO projects (id,name,local_repo_path,default_branch,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (pid, "p", "/tmp/x", "main", "", now, now))
        conn.execute("INSERT INTO tasks (id,project_id,title,description,status,priority,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (tid, pid, "T", "", "pending", "medium", now, now))
        conn.commit()
    finally:
        conn.close()
    return pid, tid


# ── Run the full mock pipeline (default mock Builder now emits the N-wrapper) ──
print("\n[run] full mock pipeline — builder wrapper N, comparator transfers proposal creation")
pid, tid = _make_task()
qa = RecordingQA()
orch = Orchestrator(executor=MockAgentExecutor(delay_seconds=0), executors={AgentRole.QA: qa})
result = asyncio.run(orch.run(tid))
check("pipeline completed (done)", result.final_status == "done", result.final_status)

conn = get_connection()
try:
    rows = conn.execute("SELECT role, proposal_data FROM execution_proposals WHERE task_id=?", (tid,)).fetchall()
    builder_props = [dict(r) for r in rows if r["role"] == "builder"]
    comp_props = [dict(r) for r in rows if r["role"] == "comparator"]
    doc_props = [dict(r) for r in rows if r["role"] == "documentation"]
    snap_count = conn.execute("SELECT COUNT(*) FROM execution_snapshots WHERE task_id=?", (tid,)).fetchone()[0]
    builder_run = conn.execute("SELECT raw_output FROM agent_runs WHERE task_id=? AND role='builder'", (tid,)).fetchone()
finally:
    conn.close()

chosen = (qa.seen_comparator or {}).get("chosen")

# (1) BUILDER CREATES 0
check("(1) builder created 0 execution_proposals (wrapper fails the gate)", len(builder_props) == 0, f"got {len(builder_props)}")

# (2) COMPARATOR CREATES EXACTLY 1 = the chosen
check("(2) comparator created EXACTLY 1 execution_proposal", len(comp_props) == 1, f"got {len(comp_props)}")
if comp_props:
    pdata = json.loads(comp_props[0]["proposal_data"])
    check("(2) the proposal_data == the comparator's chosen proposal", pdata == chosen, "proposal_data != chosen")

# (3) TOTAL from builder->comparator flow == 1 (NOT N=3)
check("(3) builder+comparator proposals == 1 (1:1 preserved, NOT N)", len(builder_props) + len(comp_props) == 1)

# (4) N-1 leave no trace: only 1 proposal from the flow; nothing frozen
check("(4) exactly 1 flow-proposal (N-1 non-chosen created none)", len(comp_props) == 1)
check("(4) no snapshots created (nothing frozen in this test)", snap_count == 0, f"got {snap_count}")

# (5) CHOSEN is correct per the selection rule (option 2: first requires_approval==False, risk low)
check("(5) chosen is a builder-shaped dict with proposed_files", isinstance(chosen, dict) and isinstance(chosen.get("proposed_files"), list))
check("(5) chosen.requires_approval == False (auto-approvable, the rule's pick)", (chosen or {}).get("requires_approval") is False, str((chosen or {}).get("requires_approval")))
check("(5) chosen.risk_level == 'low' (option 2)", (chosen or {}).get("risk_level") == "low")
check("(5) chosen is option 2 (change_summary mentions 'option 2')", "option 2" in (chosen or {}).get("change_summary", ""), (chosen or {}).get("change_summary"))

# (6) Comparator sourced N from previous_outputs["builder"]["proposals"] → selected_index == 1
check("(6) comparator selected_index == 1 (picked among the 3 wrapper proposals, not 1)",
      (qa.seen_comparator or {}).get("selected_index") == 1, str((qa.seen_comparator or {}).get("selected_index")))

# (7) Collapse still works: previous_outputs["builder"] == single chosen (downstream unchanged)
check("(7) previous_outputs['builder'] collapsed to the single chosen dict", qa.seen_builder == chosen)
check("(7) collapsed builder slot is NOT the wrapper (no 'proposals' key)", isinstance(qa.seen_builder, dict) and "proposals" not in qa.seen_builder)

# (8) AUDIT: builder raw_output has the full N (the wrapper)
raw = builder_run["raw_output"] if builder_run else None
raw_parsed = json.loads(raw) if raw else {}
check("(8) builder raw_output persisted the full N wrapper", isinstance(raw_parsed.get("proposals"), list) and len(raw_parsed["proposals"]) == 3, str(type(raw_parsed)))

# (9) ROLE kept as comparator
check("(9) created proposal role == 'comparator' (kept, not overridden)", bool(comp_props) and comp_props[0]["role"] == "comparator")

# Sanity: documentation still creates its own proposal (unchanged behavior)
check("(sanity) documentation still creates its own proposal (separate from the flow)", len(doc_props) == 1, f"got {len(doc_props)}")


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  c2 step2 gate transfer: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
