"""B3 step 3.5 — tool-loop SUMMARY persisted to the queryable log_events DB table.

Closes the step-3 gap: the [tool-loop] SUMMARY was Python-logging-only (stderr/console) and
UNRECOVERABLE after a run. Now _call_model_with_tools returns structured tool_loop_stats,
_execute_architect attaches them to its ExecutionResult, and the orchestrator's _execute_step
writes a greppable "tool-loop SUMMARY" row to log_events (with the structured payload).

Asserts:
  (1) EXECUTOR attaches stats — architect (tool→final) ExecutionResult.tool_loop_stats is the
      correct {rounds, tool_calls, requests:[{tool,path}], ended}; planner (single-shot) is None.
  (2) ORCHESTRATOR persists — _execute_step writes ONE queryable log_events "tool-loop SUMMARY"
      row for architect (message + structured payload), and NONE for a single-shot role (planner).

Hermetic: throwaway RUNTIME_DB; real temp workspace for read_file; scripted provider; no app/net.

    python tests/tool_loop_summary_persist_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="tl_summary_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

import agents.scoped_file_reader as _sfr  # noqa: E402
from agents.orchestrator import Orchestrator  # noqa: E402
from agents.model_executor import ModelAgentExecutor  # noqa: E402
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole  # noqa: E402
from providers.base import CompletionResponse, TokenUsage  # noqa: E402
from database import get_connection, init_db  # noqa: E402

init_db()

WS = tempfile.mkdtemp(prefix="tl_summary_ws_")
with open(os.path.join(WS, "X.txt"), "w", encoding="utf-8") as f:
    f.write("X-FILE-CONTENT")
_sfr.resolve_workspace_root = lambda project_id: WS  # both pre-fetch + tool loop point at WS

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


ARCH_RESULT = {
    "design_summary": "Add a CANCELLED state and a cancel method",
    "components": [{"name": "TaskQueue", "responsibility": "manage tasks"}],
    "key_decisions": [], "interfaces_or_contracts": [], "risks_tradeoffs": [], "summary": "s",
}
PLANNER_VALID = json.dumps({
    "goal_summary": "Implement cancellation",
    "task_breakdown": [{"step": 1, "description": "add cancel", "role": "builder"}],
    "acceptance_criteria": ["cancel works"],
})


def final_msg(result):
    return (json.dumps({"action": "final", "result": result}), "stop")


def tool_msg(tool, params):
    return (json.dumps({"action": "tool", "tool": tool, "params": params}), "stop")


class ScriptedProvider:
    def __init__(self, scripted):
        self.api_key = "fake"
        self._scripted = list(scripted)
        self.n = 0

    async def list_models(self):
        return []

    async def complete(self, request):
        i = self.n
        self.n += 1
        content, fr = self._scripted[i] if i < len(self._scripted) else self._scripted[-1]
        return CompletionResponse(
            content=content, model="gemini-2.5-flash", provider="gemini",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20), finish_reason=fr,
        )


def arch_executor():
    ex = ModelAgentExecutor()
    prov = ScriptedProvider([tool_msg("read_file", {"path": "X.txt"}), final_msg(ARCH_RESULT)])
    ex._resolve_provider = lambda role: (get_definition(AgentRole.ARCHITECT), prov, "gemini-2.5-flash")
    return ex


def planner_executor():
    ex = ModelAgentExecutor()
    prov = ScriptedProvider([(PLANNER_VALID, "stop")])
    ex._resolve_provider = lambda role: (get_definition(AgentRole.PLANNER), prov, "gemini-2.5-flash")
    return ex


def _make_task():
    pid, tid = str(_uuid.uuid4()), str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO projects (id,name,local_repo_path,default_branch,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (pid, "p", WS, "main", "", now, now))
        conn.execute("INSERT INTO tasks (id,project_id,title,description,status,priority,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (tid, pid, "Add task cancellation", "", "in_progress", "medium", now, now))
        conn.commit()
    finally:
        conn.close()
    return pid, tid


def summary_rows(run_id):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT source,message,payload FROM log_events WHERE run_id=? AND message LIKE '%tool-loop SUMMARY%'",
            (run_id,)).fetchall()
    finally:
        conn.close()


# ── (1) EXECUTOR attaches tool_loop_stats ─────────────────────────
print("\n[1] executor attaches tool_loop_stats")
ctx = {"task_id": "t", "title": "Add task cancellation", "description": "d",
       "project_id": "p1", "priority": "medium", "previous_outputs": {}}
arch_res = asyncio.run(arch_executor()._execute_architect(ctx))
st = arch_res.tool_loop_stats
check("1: architect ExecutionResult.tool_loop_stats is populated", isinstance(st, dict), str(st))
check("1: stats.rounds == 2", st and st.get("rounds") == 2, str(st))
check("1: stats.tool_calls == 1", st and st.get("tool_calls") == 1, str(st))
check("1: stats.ended == 'final'", st and st.get("ended") == "final", str(st))
check("1: stats.requests == [{tool:read_file, path:X.txt}]",
      st and st.get("requests") == [{"tool": "read_file", "path": "X.txt"}], str(st.get("requests") if st else None))

plan_res = asyncio.run(planner_executor().execute(AgentRole.PLANNER, ctx))
check("1: planner (single-shot) succeeded", plan_res.success, getattr(plan_res, "error_message", ""))
check("1: planner ExecutionResult.tool_loop_stats is None (no loop)",
      getattr(plan_res, "tool_loop_stats", "MISSING") is None, str(getattr(plan_res, "tool_loop_stats", "MISSING")))


# ── (2) ORCHESTRATOR persists the SUMMARY to log_events ───────────
print("\n[2] orchestrator persists SUMMARY → log_events (queryable)")
pid, tid = _make_task()
orch = Orchestrator(executors={AgentRole.ARCHITECT: arch_executor(),
                               AgentRole.PLANNER: planner_executor()})
octx = {"task_id": tid, "title": "Add task cancellation", "description": "d",
        "project_id": pid, "priority": "medium", "previous_outputs": {}}

astep = asyncio.run(orch._execute_step(tid, get_definition(AgentRole.ARCHITECT), octx))
check("2: architect step succeeded", astep["success"], str(astep))
rows = summary_rows(astep["run_id"])
check("2: EXACTLY ONE 'tool-loop SUMMARY' log_events row for architect", len(rows) == 1, str(len(rows)))
if rows:
    msg = rows[0]["message"]
    check("2: row source = architect", rows[0]["source"] == "architect", rows[0]["source"])
    check("2: message greppable + shows rounds=2", "tool-loop SUMMARY" in msg and "rounds=2" in msg, msg)
    check("2: message shows tool_calls=1", "tool_calls=1" in msg, msg)
    check("2: message records the requested path (read_file:X.txt)", "read_file:X.txt" in msg, msg)
    check("2: message shows ended=final", "ended=final" in msg, msg)
    payload = json.loads(rows[0]["payload"]).get("tool_loop", {})
    check("2: structured payload carries the full stats",
          payload.get("rounds") == 2 and payload.get("tool_calls") == 1
          and payload.get("ended") == "final"
          and payload.get("requests") == [{"tool": "read_file", "path": "X.txt"}],
          str(payload))

# single-shot role → NO summary row
pstep = asyncio.run(orch._execute_step(tid, get_definition(AgentRole.PLANNER), octx))
check("2: planner step succeeded", pstep["success"], str(pstep))
check("2: NO 'tool-loop SUMMARY' row for the single-shot planner step",
      len(summary_rows(pstep["run_id"])) == 0, str(len(summary_rows(pstep["run_id"]))))


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  tool-loop SUMMARY persist (B3 step 3.5): {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
