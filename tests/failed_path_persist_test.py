"""FAILED-path persistence test: raw_output + finish_reason on step FAILURE.

Closes the V23 FAILED-path gap. Simulates a builder step whose provider returns
malformed JSON that breaks PAST char 200 (like the real "Add cancel method" failure
at char ~302), records it through the REAL orchestrator failure path
(_execute_step -> _update_run with FAILED), and asserts:
  (a) the FAILED agent_runs row has raw_output = the FULL malformed text (NOT truncated
      to 200 — the part past char 200 is present);
  (b) finish_reason is persisted (provider returns "stop" -> stored);
  (c) the existing 200-char human-readable error preview is still produced unchanged
      (and does NOT contain the past-200 marker).

Hermetic: throwaway RUNTIME_DB; FakeProvider; no real provider/app/real-DB.

    python tests/failed_path_persist_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="failed_path_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.orchestrator import Orchestrator  # noqa: E402
from agents.model_executor import ModelAgentExecutor  # noqa: E402
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole  # noqa: E402
from providers.base import CompletionResponse, TokenUsage  # noqa: E402
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


# Malformed JSON: well-formed through proposed_files[0].reason (>200 chars), then a
# structural break ("ok" "<marker>" — two strings with no comma) PAST char 200, so the
# 200-char preview cannot reach the marker (mirrors the real char-302 failure).
PAST200_MARKER = "BREAK_PAST_200_MARKER_zzz"
MALFORMED = (
    "{\n"
    '  "change_summary": "Add a CANCELLED status to TaskStatus and implement '
    'TaskQueue.cancel(task_id) which marks a pending or running task cancelled and '
    'removes it from the pending deque.",\n'
    '  "proposed_files": [\n'
    "    {\n"
    '      "path": "src/models.py",\n'
    '      "action": "modify",\n'
    '      "reason": "add CANCELLED enum value to TaskStatus",\n'
    '      "content": "ok" "' + PAST200_MARKER + '"\n'
    "    }\n"
    "  ]\n"
    "}\n"
)
# sanity: the marker really is past char 200 in the raw text
assert MALFORMED.index(PAST200_MARKER) > 200, MALFORMED.index(PAST200_MARKER)


class FakeProvider:
    def __init__(self):
        self.api_key = "fake"

    async def complete(self, request):
        return CompletionResponse(
            content=MALFORMED, model="gemini-2.5-flash", provider="gemini",
            usage=TokenUsage(prompt_tokens=20, completion_tokens=60),
            finish_reason="stop",  # malformed-but-complete (NOT truncated)
        )


def _make_task():
    pid, tid = str(_uuid.uuid4()), str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO projects (id,name,local_repo_path,default_branch,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (pid, "p", "/tmp/x", "main", "", now, now))
        conn.execute("INSERT INTO tasks (id,project_id,title,description,status,priority,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (tid, pid, "Add cancel method to TaskQueue", "", "in_progress", "medium", now, now))
        conn.commit()
    finally:
        conn.close()
    return pid, tid


print("\n[failure path] builder returns malformed JSON breaking past char 200")
pid, tid = _make_task()
builder_defn = get_definition(AgentRole.BUILDER)

# A real ModelAgentExecutor with the provider stubbed → exercises _execute_builder ->
# _call_model -> _parse_and_validate (json.loads fails) -> ExecutionResult(success=False,
# raw_output=<full text>, token_usage=usage{finish_reason}).
stub = ModelAgentExecutor()
stub._resolve_provider = lambda role: (builder_defn, FakeProvider(), "gemini-2.5-flash")

orch = Orchestrator(executors={AgentRole.BUILDER: stub})
ctx = {"task_id": tid, "title": "Add cancel method to TaskQueue", "description": "d",
       "project_id": pid, "priority": "medium", "previous_outputs": {}}
step = asyncio.run(orch._execute_step(tid, builder_defn, ctx))

check("builder step failed (semantics unchanged)", step["success"] is False, str(step))

conn = get_connection()
try:
    row = conn.execute("SELECT status, output_summary, raw_output, finish_reason FROM agent_runs WHERE id=?",
                       (step["run_id"],)).fetchone()
finally:
    conn.close()

check("row status = failed", row["status"] == "failed", str(row["status"]))
# (a) full raw_output captured, including the part PAST char 200
check("(a) raw_output is NOT NULL", row["raw_output"] is not None)
check("(a) raw_output contains the PAST-200 marker (full text, not truncated)",
      bool(row["raw_output"]) and PAST200_MARKER in row["raw_output"])
check("(a) raw_output is the full fence-stripped provider text json.loads received",
      row["raw_output"] == MALFORMED.strip(), "raw_output != MALFORMED.strip()")
# (b) finish_reason persisted
check("(b) finish_reason persisted = 'stop'", row["finish_reason"] == "stop", str(row["finish_reason"]))
# (c) the 200-char human-readable error preview is unchanged + does not reach the marker
err = json.loads(row["output_summary"]).get("error", "")
check("(c) error still says 'returned invalid JSON'", "returned invalid JSON" in err)
check("(c) error still carries the 200-char 'Response preview:'", "Response preview:" in err)
check("(c) error preview does NOT contain the past-200 marker (still truncated at 200)",
      PAST200_MARKER not in err)


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  failed-path persist: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
