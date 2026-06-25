"""ITEM 1 (C-series pre-work): the dead `rejection_feedback` key is removed.

Confirms:
  (A) The MockAgentExecutor builder keys its retry detection off `rejection_history`
      (the live channel the real builder reads in model_executor.py), NOT the removed
      `rejection_feedback` key. The legacy key alone no longer triggers a retry.
  (B) The orchestrator no longer writes task_context["rejection_feedback"] on a Reviewer
      request_changes — the re-running builder receives `rejection_history` and NO
      `rejection_feedback`. Control flow (the rejection rewind) is UNCHANGED: the builder
      still re-runs exactly once after a single rejection, and the task reaches DONE.

Hermetic: throwaway RUNTIME_DB; mock executors; no real provider/app/real-DB.

    python tests/rejection_feedback_removed_test.py
"""
import asyncio
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="rej_fb_rm_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.executor import MockAgentExecutor, ExecutionResult  # noqa: E402
from agents.orchestrator import Orchestrator  # noqa: E402
from models import AgentRole, ReviewDecision  # noqa: E402
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


# ══════════════════════════════════════════════════════════════
# (A) Mock builder retry detection now keys off rejection_history
# (C2 step 2: the mock builder emits a WRAPPER {"proposals":[...]}; the is_retry wording
#  now lives on each proposal, so read proposals[0].)
# ══════════════════════════════════════════════════════════════
print("\n[A] MockAgentExecutor builder keys is_retry off rejection_history")
mock = MockAgentExecutor(delay_seconds=0)


def _p0(res):
    return res.output["proposals"][0]


# rejection_history present → treated as a retry
r_hist = asyncio.run(mock.execute(AgentRole.BUILDER, {
    "title": "T", "description": "d",
    "rejection_history": [{"decision": "request_changes", "reason": "x"}],
}))
check("A: rejection_history present -> 'Revised' wording",
      _p0(r_hist)["change_summary"].startswith("Revised"), _p0(r_hist)["change_summary"])
check("A: rejection_history present -> first file action 'modify'",
      _p0(r_hist)["proposed_files"][0]["action"] == "modify")

# fresh context → not a retry
r_fresh = asyncio.run(mock.execute(AgentRole.BUILDER, {"title": "T", "description": "d"}))
check("A: no rejection_history -> 'Implemented' wording",
      _p0(r_fresh)["change_summary"].startswith("Implemented"), _p0(r_fresh)["change_summary"])
check("A: no rejection_history -> first file action 'create'",
      _p0(r_fresh)["proposed_files"][0]["action"] == "create")

# the removed key ALONE must NOT trigger a retry (proves the repoint away from rejection_feedback)
r_legacy = asyncio.run(mock.execute(AgentRole.BUILDER, {
    "title": "T", "description": "d",
    "rejection_feedback": {"decision": "request_changes"},
}))
check("A: legacy rejection_feedback alone does NOT trigger retry (key is dead)",
      _p0(r_legacy)["change_summary"].startswith("Implemented"), _p0(r_legacy)["change_summary"])


# ══════════════════════════════════════════════════════════════
# (B) Orchestrator omits rejection_feedback; builder re-run sees rejection_history only
# ══════════════════════════════════════════════════════════════
print("\n[B] Orchestrator no longer writes rejection_feedback on a rejection")


class RecordingBuilder:
    """Records the set of ctx keys present each time the builder is invoked."""

    def __init__(self):
        self.seen_keys = []

    async def execute(self, role, task_context):
        self.seen_keys.append(set(task_context.keys()))
        return ExecutionResult(success=True, output={
            "change_summary": "impl",
            "proposed_files": [{"path": "src/f.py", "action": "modify",
                                "reason": "r", "content": "x"}],
            "change_steps": [{"step": 1, "description": "do"}],
            "reasoning_summary": "why",
            "validation_plan": ["run tests"],
            "risk_notes": [],
        })


class RejectOnceReviewer:
    """REQUEST_CHANGES on the 1st call, APPROVE on the 2nd — deterministic rejection cycle."""

    def __init__(self):
        self.calls = 0

    async def execute(self, role, task_context):
        self.calls += 1
        if self.calls == 1:
            return ExecutionResult(
                success=True,
                output={"review_summary": "needs work", "decision": "request_changes",
                        "reason": "fix it"},
                decision=ReviewDecision.REQUEST_CHANGES,
            )
        return ExecutionResult(
            success=True,
            output={"review_summary": "ok", "decision": "approve", "reason": "lgtm"},
            decision=ReviewDecision.APPROVE,
        )


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


pid, tid = _make_task()
rec = RecordingBuilder()
orch = Orchestrator(
    executor=MockAgentExecutor(delay_seconds=0),
    executors={AgentRole.BUILDER: rec, AgentRole.REVIEWER: RejectOnceReviewer()},
)
result = asyncio.run(orch.run(tid))

check("B: task reached DONE (one rejection then approve)", result.final_status == "done", result.final_status)
check("B: builder invoked twice (first pass + one re-run)", len(rec.seen_keys) == 2, str(len(rec.seen_keys)))
if len(rec.seen_keys) == 2:
    first, second = rec.seen_keys[0], rec.seen_keys[1]
    check("B: first builder pass has NO rejection_history", "rejection_history" not in first, str(first))
    check("B: re-run builder DOES see rejection_history", "rejection_history" in second, str(second))
    check("B: re-run builder does NOT see rejection_feedback (key removed)",
          "rejection_feedback" not in second, str(second))
    check("B: rejection_feedback absent on the first pass too", "rejection_feedback" not in first, str(first))


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  rejection_feedback removed: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
