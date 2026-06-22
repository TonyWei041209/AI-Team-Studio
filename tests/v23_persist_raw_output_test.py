"""TASK 乙 tests: persist raw provider output + finish_reason (V23, write-side only).

Covers:
  (A) migration V22->V23: the additive nullable columns exist; re-running migrations is
      idempotent (safe twice); pre-existing rows are preserved across a re-run.
  (B) _call_model threads the provider finish_reason into the usage dict (metadata).
  (C) _update_run persists raw_output (FULL parsed output, lists NOT collapsed) and
      finish_reason, while output_summary stays the LOSSY summary (lists collapsed).

Hermetic: throwaway RUNTIME_DB; no real provider/app/real-DB.

    python tests/v23_persist_raw_output_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="v23_raw_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

import database  # noqa: E402
from database import get_connection, init_db  # noqa: E402
from agents.orchestrator import Orchestrator  # noqa: E402
from agents.model_executor import ModelAgentExecutor  # noqa: E402
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole, RunStatus  # noqa: E402
from providers.base import CompletionResponse, TokenUsage  # noqa: E402

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


def _insert_run(output_summary=None, raw=None, fr=None):
    """Insert project+task+agent_run; return run_id."""
    now = datetime.now(timezone.utc).isoformat()
    pid, tid, rid = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute("INSERT INTO projects (id,name,local_repo_path,default_branch,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (pid, "p", "/tmp/x", "main", "", now, now))
        conn.execute("INSERT INTO tasks (id,project_id,title,description,status,priority,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (tid, pid, "t", "", "in_progress", "medium", now, now))
        conn.execute("INSERT INTO agent_runs (id,task_id,role,model_provider,model_name,status,input_summary,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (rid, tid, "architect", "gemini", "gemini-2.5-flash", "pending", "{}", now))
        conn.commit()
    finally:
        conn.close()
    return rid


# ══════════════════════════════════════════════════════════════
# (A) migration: columns exist + nullable + idempotent + preserved
# ══════════════════════════════════════════════════════════════
print("\n[A] V23 migration")
ver = None
conn = get_connection()
try:
    ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
finally:
    conn.close()
check("A: schema_version reached 23", ver == 23, f"got {ver}")
cols = _cols("agent_runs")
check("A: agent_runs has raw_output column", "raw_output" in cols, str(sorted(cols)))
check("A: agent_runs has finish_reason column", "finish_reason" in cols, str(sorted(cols)))

# insert a row with the new columns left unset → proves they are nullable
rid = _insert_run()
conn = get_connection()
try:
    row = conn.execute("SELECT raw_output, finish_reason FROM agent_runs WHERE id=?", (rid,)).fetchone()
finally:
    conn.close()
check("A: new columns nullable (row inserts with them NULL)", row["raw_output"] is None and row["finish_reason"] is None)

# re-run migrations → idempotent (safe twice); row preserved; version still 23
database._ensure_schema()
conn = get_connection()
try:
    ver2 = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    still = conn.execute("SELECT COUNT(*) FROM agent_runs WHERE id=?", (rid,)).fetchone()[0]
finally:
    conn.close()
check("A: re-running migrations is idempotent (version still 23)", ver2 == 23, f"got {ver2}")
check("A: pre-existing row preserved across re-run", still == 1)


# ══════════════════════════════════════════════════════════════
# (B) _call_model threads finish_reason into the usage dict
# ══════════════════════════════════════════════════════════════
print("\n[B] _call_model captures finish_reason")


class FakeProvider:
    def __init__(self, fr):
        self.api_key = "fake"
        self._fr = fr

    async def complete(self, request):
        return CompletionResponse(
            content="{}", model="m", provider="p",
            usage=TokenUsage(prompt_tokens=11, completion_tokens=22),
            finish_reason=self._fr,
        )


executor = ModelAgentExecutor()
defn = get_definition(AgentRole.QA)  # QA is non-injectable → build_enhanced_system_prompt needs no DB
content, usage = asyncio.run(executor._call_model(defn, FakeProvider("length"), "m", "hi"))
check("B: content returned", content == "{}")
check("B: finish_reason captured in usage", usage.get("finish_reason") == "length", str(usage))
check("B: token usage still present", usage.get("completion_tokens") == 22 and usage.get("provider") == "p")
# and the 'stop' default flows through too
_, usage2 = asyncio.run(executor._call_model(defn, FakeProvider("stop"), "m", "hi"))
check("B: finish_reason='stop' threaded", usage2.get("finish_reason") == "stop")


# ══════════════════════════════════════════════════════════════
# (C) _update_run: raw_output FULL vs output_summary LOSSY + finish_reason
# ══════════════════════════════════════════════════════════════
print("\n[C] _update_run persists raw_output (full) + finish_reason; output_summary lossy")
OUT = {
    "design_summary": "D" * 2500,  # force full JSON > 2000 so _summarize_output collapses lists
    "components": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
    "key_decisions": [{"decision": "x", "rationale": "y"}],
    "interfaces_or_contracts": ["i1", "i2"],
    "risks_tradeoffs": ["r1"],
    "summary": "s",
}
rid = _insert_run()
Orchestrator._update_run(
    rid, RunStatus.COMPLETED,
    output_summary=Orchestrator._summarize_output(OUT),
    raw_output=json.dumps(OUT, ensure_ascii=False),
    finish_reason="length",
)
conn = get_connection()
try:
    row = conn.execute("SELECT output_summary, raw_output, finish_reason FROM agent_runs WHERE id=?", (rid,)).fetchone()
finally:
    conn.close()

raw_parsed = json.loads(row["raw_output"])
summ_parsed = json.loads(row["output_summary"])
check("C: finish_reason stored", row["finish_reason"] == "length", str(row["finish_reason"]))
check("C: raw_output is FULL (components list intact, 3 items)",
      isinstance(raw_parsed.get("components"), list) and len(raw_parsed["components"]) == 3, str(raw_parsed.get("components")))
check("C: raw_output preserves the full design_summary", raw_parsed.get("design_summary") == "D" * 2500)
check("C: output_summary is LOSSY (components collapsed to a count string)",
      summ_parsed.get("components") == "[3 items]", str(summ_parsed.get("components")))
check("C: raw_output != output_summary (audit-grade vs display)", row["raw_output"] != row["output_summary"])


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  v23 persist raw_output: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
