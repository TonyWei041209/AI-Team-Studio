"""Builder malformed-JSON retry (inner model-call layer) — cases a–e.

The retry is INSIDE _execute_builder, BELOW the orchestrator's reviewer-rejection loop.
It retries ONLY syntactic json.loads failures (not schema failures), ONLY when
finish_reason != "length" (truncation isn't re-promptable), up to _BUILDER_JSON_RETRY_MAX
(=2) retries, with appended feedback (parse error + escaping guidance).

Hermetic: a ScriptedProvider returns scripted (content, finish_reason) per call; no real
provider/app. Throwaway RUNTIME_DB (build_enhanced_system_prompt queries the skills table).

    python tests/builder_json_retry_test.py
"""
import asyncio
import json
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="builder_retry_"))
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
    "change_summary": "x",
    "proposed_files": [{"path": "a.py", "action": "create", "reason": "r", "content": "c"}],
    "change_steps": [{"step": 1, "description": "d"}],
    "reasoning_summary": "why",
    "validation_plan": ["run tests"],
    "risk_notes": [],
})
MALFORMED = '{\n  "change_summary": "x" "oops"\n}'        # json.loads -> Expecting ',' delimiter
SCHEMA_FAIL = '{"change_summary": "x"}'                   # parses, but missing proposed_files etc.

FEEDBACK_MARK = "IMPORTANT — your previous response was NOT valid JSON"
ESCAPE_MARK = "escape every newline as \\n"


class ScriptedProvider:
    """Returns scripted (content, finish_reason) responses across calls; records user_msgs."""
    def __init__(self, scripted):
        self.api_key = "fake"
        self._scripted = list(scripted)
        self.calls = []  # user_msg per call

    async def complete(self, request):
        self.calls.append(request.messages[0].content)
        content, fr = self._scripted.pop(0)
        return CompletionResponse(
            content=content, model="gemini-2.5-flash", provider="gemini",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20), finish_reason=fr,
        )


def run_builder(scripted):
    prov = ScriptedProvider(scripted)
    ex = ModelAgentExecutor()
    ex._resolve_provider = lambda role: (get_definition(AgentRole.BUILDER), prov, "gemini-2.5-flash")
    ctx = {"title": "t", "description": "d", "priority": "low", "previous_outputs": {}}
    result = asyncio.run(ex._execute_builder(ctx))
    return result, prov


# (a) RETRY-SUCCESS: malformed (stop) then valid → success, 2 calls, feedback in 2nd call
print("\n[a] retry-success: malformed(stop) -> valid")
res, prov = run_builder([(MALFORMED, "stop"), (VALID, "stop")])
check("a: success=True", res.success, getattr(res, "error_message", ""))
check("a: exactly 2 calls (1 retry)", len(prov.calls) == 2, str(len(prov.calls)))
check("a: 1st call had NO feedback (== base)", FEEDBACK_MARK not in prov.calls[0])
check("a: 2nd call CONTAINS the feedback block", FEEDBACK_MARK in prov.calls[1])
check("a: 2nd call contains the escaping guidance", ESCAPE_MARK in prov.calls[1])
check("a: 2nd call contains the parse-error detail", "Expecting" in prov.calls[1] and "delimiter" in prov.calls[1], prov.calls[1][-300:])


# (b) RETRY-EXHAUSTED: malformed (stop) x3 → fail after exactly 3 calls, raw_output = last malformed
print("\n[b] retry-exhausted: malformed(stop) x3")
res, prov = run_builder([(MALFORMED, "stop"), (MALFORMED, "stop"), (MALFORMED, "stop")])
check("b: success=False", res.success is False)
check("b: exactly 3 calls (1 + 2 retries)", len(prov.calls) == 3, str(len(prov.calls)))
check("b: failure_kind json_parse", getattr(res, "failure_kind", None) == "json_parse")
check("b: raw_output = last malformed text (FAILED-path capture)", res.raw_output == MALFORMED.strip(), repr(res.raw_output))


# (c) NO-RETRY-ON-LENGTH: malformed (length) → no retry, exactly 1 call
print("\n[c] no-retry-on-length: malformed(length)")
res, prov = run_builder([(MALFORMED, "length")])
check("c: success=False", res.success is False)
check("c: exactly 1 call (truncation NOT retried)", len(prov.calls) == 1, str(len(prov.calls)))
check("c: finish_reason length preserved on usage", (res.token_usage or {}).get("finish_reason") == "length")


# (d) NO-RETRY-ON-SCHEMA: valid JSON, wrong shape → no retry, exactly 1 call, failure_kind schema
print("\n[d] no-retry-on-schema: parses but fails schema")
res, prov = run_builder([(SCHEMA_FAIL, "stop")])
check("d: success=False", res.success is False)
check("d: exactly 1 call (schema failure NOT retried)", len(prov.calls) == 1, str(len(prov.calls)))
check("d: failure_kind schema", getattr(res, "failure_kind", None) == "schema")


# (e) SUCCESS-FIRST-TRY: valid JSON → success, exactly 1 call, NO feedback (behavior identical)
print("\n[e] success-first-try: valid")
res, prov = run_builder([(VALID, "stop")])
check("e: success=True", res.success, getattr(res, "error_message", ""))
check("e: exactly 1 call (no retry)", len(prov.calls) == 1, str(len(prov.calls)))
check("e: no feedback appended (1st call == base, behavior-neutral)", FEEDBACK_MARK not in prov.calls[0])
check("e: proposed_files preserved", len(res.output.get("proposed_files", [])) == 1)


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  builder json-retry: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
