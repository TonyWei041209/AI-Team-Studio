"""Planner malformed-JSON retry (shared _call_parse_retry) — cases a–e.

The Planner is a pipeline-BLOCKING role: a parse failure returns success=False and aborts
the whole pipeline (it is step 0). It now wraps the shared _call_parse_retry with the GENERIC
feedback (no large content field). Mirrors the builder retry cases for the new role.

Hermetic: a ScriptedProvider returns scripted (content, finish_reason) per call; no real
provider/app. Throwaway RUNTIME_DB (build_enhanced_system_prompt queries the skills table).

    python tests/planner_json_retry_test.py
"""
import asyncio
import json
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="planner_retry_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.model_executor import ModelAgentExecutor  # noqa: E402
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole  # noqa: E402
from providers.base import CompletionResponse, TokenUsage  # noqa: E402
from database import init_db  # noqa: E402

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


VALID = json.dumps({
    "goal_summary": "g",
    "task_breakdown": [{"step": 1, "description": "d", "role": "builder"}],
    "acceptance_criteria": ["ac"],
})
MALFORMED = '{\n  "goal_summary": "g" "oops"\n}'       # json.loads -> Expecting ',' delimiter
SCHEMA_FAIL = '{"goal_summary": "g"}'                  # parses, missing task_breakdown etc.

FEEDBACK_MARK = "IMPORTANT — your previous response was NOT valid JSON"
GENERIC_MARK = "inside all string values"              # unique to the generic feedback (not builder's)


class ScriptedProvider:
    def __init__(self, scripted):
        self.api_key = "fake"
        self._scripted = list(scripted)
        self.calls = []

    async def complete(self, request):
        self.calls.append(request.messages[0].content)
        content, fr = self._scripted.pop(0)
        return CompletionResponse(
            content=content, model="gemini-2.5-flash", provider="gemini",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20), finish_reason=fr,
        )


def run_planner(scripted):
    prov = ScriptedProvider(scripted)
    ex = ModelAgentExecutor()
    ex._resolve_provider = lambda role: (get_definition(AgentRole.PLANNER), prov, "gemini-2.5-flash")
    ctx = {"title": "t", "description": "d", "priority": "low", "previous_outputs": {}}
    result = asyncio.run(ex._execute_planner(ctx))
    return result, prov


print("\n[a] retry-success: malformed(stop) -> valid")
res, prov = run_planner([(MALFORMED, "stop"), (VALID, "stop")])
check("a: success=True", res.success, getattr(res, "error_message", ""))
check("a: exactly 2 calls (1 retry)", len(prov.calls) == 2, str(len(prov.calls)))
check("a: 1st call had NO feedback", FEEDBACK_MARK not in prov.calls[0])
check("a: 2nd call CONTAINS feedback + GENERIC guidance", FEEDBACK_MARK in prov.calls[1] and GENERIC_MARK in prov.calls[1])
check("a: 2nd call has the parse-error detail", "Expecting" in prov.calls[1] and "delimiter" in prov.calls[1])

print("\n[b] retry-exhausted: malformed(stop) x3")
res, prov = run_planner([(MALFORMED, "stop")] * 3)
check("b: success=False", res.success is False)
check("b: exactly 3 calls (1 + 2 retries)", len(prov.calls) == 3, str(len(prov.calls)))
check("b: failure_kind json_parse", getattr(res, "failure_kind", None) == "json_parse")
check("b: raw_output = last malformed (FAILED-path capture)", res.raw_output == MALFORMED.strip(), repr(res.raw_output))

print("\n[c] no-retry-on-length: malformed(length)")
res, prov = run_planner([(MALFORMED, "length")])
check("c: success=False", res.success is False)
check("c: exactly 1 call (truncation NOT retried)", len(prov.calls) == 1, str(len(prov.calls)))

print("\n[d] no-retry-on-schema: parses but fails schema")
res, prov = run_planner([(SCHEMA_FAIL, "stop")])
check("d: success=False", res.success is False)
check("d: exactly 1 call (schema failure NOT retried)", len(prov.calls) == 1, str(len(prov.calls)))
check("d: failure_kind schema", getattr(res, "failure_kind", None) == "schema")

print("\n[e] success-first-try: valid")
res, prov = run_planner([(VALID, "stop")])
check("e: success=True", res.success, getattr(res, "error_message", ""))
check("e: exactly 1 call (no retry)", len(prov.calls) == 1, str(len(prov.calls)))
check("e: no feedback appended (behavior-neutral)", FEEDBACK_MARK not in prov.calls[0])
check("e: post-success normalize (risks/dependencies present)",
      res.output.get("risks") == [] and res.output.get("dependencies") == [])


print()
print("-" * 60)
total = PASS + FAIL
print(f"  planner json-retry: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
