"""ITEM 2 (C-series pre-work): attempt_number re-run tracking on agent_runs (V24).

Additive, nullable column mirroring the V23 template. attempt_number records which
rejection round produced each agent_runs row (sourced from the orchestrator's
rejection_count): first pass = 0, after the 1st rejection = 1, etc.

Covers:
  (A) Migration V23->V24: schema_version reaches 24; agent_runs.attempt_number exists and
      is nullable; re-running migrations is idempotent (safe twice); pre-existing rows are
      preserved across a re-run.
  (B) Set-point (direct): _execute_step threads attempt_number into the agent_runs row.
      Default (no arg) -> 0; explicit value -> stored verbatim. Existing callers stay safe.
  (C) Payoff (integration): a full pipeline with ONE reviewer rejection writes the re-run
      builder/qa/sr/reviewer rows with attempt_number=1 while the first pass is 0 — so the
      builder's first vs second attempt is queryable directly, not just by created_at.

Hermetic: throwaway RUNTIME_DB; mock executors; no real provider/app/real-DB.

    python tests/v24_attempt_number_test.py
"""
import asyncio
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="v24_attempt_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

import database  # noqa: E402
from database import get_connection, init_db  # noqa: E402
from agents.executor import MockAgentExecutor, ExecutionResult  # noqa: E402
from agents.orchestrator import Orchestrator  # noqa: E402
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole, ReviewDecision  # noqa: E402

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


def _cols(table):
    conn = get_connection()
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def _make_task(status="pending"):
    pid, tid = str(_uuid.uuid4()), str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO projects (id,name,local_repo_path,default_branch,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (pid, "p", "/tmp/x", "main", "", now, now))
        conn.execute("INSERT INTO tasks (id,project_id,title,description,status,priority,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (tid, pid, "T", "", status, "medium", now, now))
        conn.commit()
    finally:
        conn.close()
    return pid, tid


# ══════════════════════════════════════════════════════════════
# (A) migration: column exists + nullable + idempotent + preserved
# ══════════════════════════════════════════════════════════════
print("\n[A] V24 migration")
conn = get_connection()
try:
    ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
finally:
    conn.close()
# >= 24 (not == 24): keeps this test robust when a later additive migration advances
# MAX(version). The substantive V24 assertion is the attempt_number column check below.
check("A: schema_version reached >= 24", ver >= 24, f"got {ver}")
cols = _cols("agent_runs")
check("A: agent_runs has attempt_number column", "attempt_number" in cols, str(sorted(cols)))

# insert a row WITHOUT attempt_number → proves it is nullable
_pid, _tid = _make_task(status="in_progress")
rid = str(_uuid.uuid4())
now = datetime.now(timezone.utc).isoformat()
conn = get_connection()
try:
    conn.execute("INSERT INTO agent_runs (id,task_id,role,model_provider,model_name,status,input_summary,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (rid, _tid, "planner", "mock", "mock-v1", "pending", "{}", now))
    conn.commit()
    row = conn.execute("SELECT attempt_number FROM agent_runs WHERE id=?", (rid,)).fetchone()
finally:
    conn.close()
check("A: attempt_number nullable (row inserts with it NULL)", row["attempt_number"] is None, str(row["attempt_number"]))

# re-run migrations → idempotent; version still 24; row preserved
database._ensure_schema()
conn = get_connection()
try:
    ver2 = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    still = conn.execute("SELECT COUNT(*) FROM agent_runs WHERE id=?", (rid,)).fetchone()[0]
finally:
    conn.close()
check("A: re-running migrations is idempotent (version unchanged)", ver2 == ver, f"got {ver2}, expected {ver}")
check("A: pre-existing row preserved across re-run", still == 1)


# ══════════════════════════════════════════════════════════════
# (B) set-point (direct): _execute_step threads attempt_number into the row
# ══════════════════════════════════════════════════════════════
print("\n[B] _execute_step threads attempt_number into agent_runs (default 0)")
pid_b, tid_b = _make_task(status="in_progress")
orch_b = Orchestrator(executor=MockAgentExecutor(delay_seconds=0))
planner_defn = get_definition(AgentRole.PLANNER)
ctx_b = {"task_id": tid_b, "title": "T", "description": "d", "project_id": pid_b,
         "priority": "medium", "previous_outputs": {}}

# default (no attempt_number arg) → 0; explicit value → stored verbatim
step_default = asyncio.run(orch_b._execute_step(tid_b, planner_defn, ctx_b))
step_two = asyncio.run(orch_b._execute_step(tid_b, planner_defn, ctx_b, attempt_number=2))

conn = get_connection()
try:
    an0 = conn.execute("SELECT attempt_number FROM agent_runs WHERE id=?", (step_default["run_id"],)).fetchone()["attempt_number"]
    an2 = conn.execute("SELECT attempt_number FROM agent_runs WHERE id=?", (step_two["run_id"],)).fetchone()["attempt_number"]
finally:
    conn.close()
check("B: default call records attempt_number=0", an0 == 0, str(an0))
check("B: explicit attempt_number=2 recorded verbatim", an2 == 2, str(an2))


# ══════════════════════════════════════════════════════════════
# (C) payoff (integration): a single reviewer rejection labels the re-run tail with 1
# ══════════════════════════════════════════════════════════════
print("\n[C] full pipeline w/ one rejection: re-run tail carries attempt_number=1")


class RejectOnceReviewer:
    def __init__(self):
        self.calls = 0

    async def execute(self, role, task_context):
        self.calls += 1
        if self.calls == 1:
            return ExecutionResult(
                success=True,
                output={"review_summary": "needs work", "decision": "request_changes", "reason": "fix"},
                decision=ReviewDecision.REQUEST_CHANGES,
            )
        return ExecutionResult(
            success=True,
            output={"review_summary": "ok", "decision": "approve", "reason": "ok"},
            decision=ReviewDecision.APPROVE,
        )


pid_c, tid_c = _make_task(status="pending")
orch_c = Orchestrator(
    executor=MockAgentExecutor(delay_seconds=0),
    executors={AgentRole.REVIEWER: RejectOnceReviewer()},
)
result_c = asyncio.run(orch_c.run(tid_c))
check("C: task reached DONE", result_c.final_status == "done", result_c.final_status)

conn = get_connection()
try:
    rows = conn.execute(
        "SELECT role, attempt_number FROM agent_runs WHERE task_id=? ORDER BY created_at",
        (tid_c,),
    ).fetchall()
finally:
    conn.close()

by_role: dict[str, list] = {}
for r in rows:
    by_role.setdefault(r["role"], []).append(r["attempt_number"])

check("C: builder ran twice with attempt_number [0, 1]", sorted(by_role.get("builder", [])) == [0, 1], str(by_role.get("builder")))
check("C: reviewer ran twice with attempt_number [0, 1]", sorted(by_role.get("reviewer", [])) == [0, 1], str(by_role.get("reviewer")))
check("C: qa ran twice with attempt_number [0, 1]", sorted(by_role.get("qa", [])) == [0, 1], str(by_role.get("qa")))
check("C: security_reviewer ran twice with attempt_number [0, 1]", sorted(by_role.get("security_reviewer", [])) == [0, 1], str(by_role.get("security_reviewer")))
check("C: planner ran once with attempt_number [0] (never re-run)", by_role.get("planner") == [0], str(by_role.get("planner")))
check("C: architect ran once with attempt_number [0] (never re-run)", by_role.get("architect") == [0], str(by_role.get("architect")))
check("C: documentation ran once with attempt_number [1] (after the approved round)", by_role.get("documentation") == [1], str(by_role.get("documentation")))


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  v24 attempt_number: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
