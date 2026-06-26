"""Builder malformed-JSON retry — the parse-retry GATE MATRIX, now THROUGH the tool loop.

B3 step 4 made Builder TOOL-ENABLED: it routes through _parse_retry_core with a tool-enabled
model_call (_call_model_with_tools), instead of the single-shot _call_model. So Builder's own
behavior legitimately changed (like ar_step1 in step 2): a "valid final" must now be wrapped in
the {"action":"final","result":{...}} envelope, and a malformed/non-envelope response is handled
FIRST by the tool loop (re-prompted up to max_rounds, then EXHAUSTED) before the parse-retry's
json-parse gate sees it. So call COUNTS change (an exhausting attempt = max_rounds provider calls),
but the parse-retry GATES are preserved and re-asserted here at the ATTEMPT level:
  - success-first-try (valid final envelope → 1 call, no retry),
  - schema failure → NO retry (1 call),
  - finish_reason=="length" → NO retry (tool loop exhausts once, gate fires),
  - json-parse failure → RETRY (a malformed final triggers a FRESH tool loop; builder escaping
    feedback is appended for the next attempt),
  - retries exhausted → success=False (BLOCKING; pipeline aborts).
The single-shot gates themselves are also covered byte-identically by planner_json_retry /
reviewer_json_retry (which still go through the single-shot _call_parse_retry).

Hermetic: throwaway RUNTIME_DB; a repeat-last ScriptedProvider; no real provider/app/net.

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

from agents.model_executor import ModelAgentExecutor, _TOOL_LOOP_MAX_ROUNDS  # noqa: E402
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


_SINGLE_PROPOSAL = {
    "change_summary": "x",
    "proposed_files": [{"path": "a.py", "action": "create", "reason": "r", "content": "c"}],
    "change_steps": [{"step": 1, "description": "d"}],
    "reasoning_summary": "why",
    "validation_plan": ["run tests"],
    "risk_notes": [],
}
# C2 real-model N: a valid Builder output is now a WRAPPER of 2-3 proposals.
VALID_DICT = {"proposals": [dict(_SINGLE_PROPOSAL), dict(_SINGLE_PROPOSAL)]}
SCHEMA_FAIL_DICT = {"change_summary": "x"}                  # not a wrapper (no 'proposals') → schema fail
RAW_MALFORMED = '{\n  "change_summary": "x" "oops"\n}'      # bad JSON + no "action" → tool-loop malformed

FEEDBACK_MARK = "IMPORTANT — your previous response was NOT valid JSON"
ESCAPE_MARK = "escape every newline as \\n"
MAXR = _TOOL_LOOP_MAX_ROUNDS  # 5


def final_env(obj, fr="stop"):
    return (json.dumps({"action": "final", "result": obj}), fr)


class ScriptedProvider:
    """Scripts (content, finish_reason) per call, REPEATING the last when exhausted (the tool loop
    calls up to max_rounds per attempt). Records full messages per call for feedback assertions."""
    def __init__(self, scripted):
        self.api_key = "fake"
        self._scripted = list(scripted)
        self.calls = []  # per call: list[(role, content)]

    async def list_models(self):
        return []

    async def complete(self, request):
        i = len(self.calls)
        self.calls.append([(m.role.value, m.content) for m in request.messages])
        content, fr = self._scripted[i] if i < len(self._scripted) else self._scripted[-1]
        return CompletionResponse(
            content=content, model="gemini-2.5-flash", provider="gemini",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20), finish_reason=fr,
        )


def run_builder(scripted):
    prov = ScriptedProvider(scripted)
    ex = ModelAgentExecutor()
    ex._resolve_provider = lambda role: (get_definition(AgentRole.BUILDER), prov, "gemini-2.5-flash")
    ctx = {"title": "t", "description": "d", "priority": "low", "previous_outputs": {}}  # no project_id → no pre-fetch
    result = asyncio.run(ex._execute_builder(ctx))
    return result, prov


# (a) SUCCESS-FIRST-TRY: valid final envelope → success in 1 call, no retry, no feedback
print("\n[a] success-first-try: valid final envelope")
res, prov = run_builder([final_env(VALID_DICT)])
check("a: success=True", res.success, getattr(res, "error_message", ""))
check("a: exactly 1 provider call (final on round 1, no retry)", len(prov.calls) == 1, str(len(prov.calls)))
check("a: no parse-retry feedback in the (only) call", FEEDBACK_MARK not in prov.calls[0][0][1])
check("a: wrapper has 2 proposals, each preserved + normalized (risk_level set per proposal)",
      len(res.output.get("proposals", [])) == 2
      and len(res.output["proposals"][0].get("proposed_files", [])) == 1
      and "risk_level" in res.output["proposals"][0] and "risk_level" in res.output["proposals"][1])


# (b) NO-RETRY-ON-SCHEMA: valid JSON, wrong shape → schema failure → NO retry, 1 call
print("\n[b] no-retry-on-schema")
res, prov = run_builder([final_env(SCHEMA_FAIL_DICT)])
check("b: success=False", res.success is False)
check("b: exactly 1 call (schema failure NOT retried)", len(prov.calls) == 1, str(len(prov.calls)))
check("b: failure_kind schema", getattr(res, "failure_kind", None) == "schema")


# (c) JSON-PARSE → RETRY: malformed exhausts attempt-1 tool loop → retry → valid final (attempt 2)
print("\n[c] json-parse-retry: malformed exhausts → retry → valid")
res, prov = run_builder([(RAW_MALFORMED, "stop")] * MAXR + [final_env(VALID_DICT)])
check("c: success=True (recovered on the 2nd attempt)", res.success, getattr(res, "error_message", ""))
check(f"c: {MAXR}+1 calls (attempt-1 tool loop exhausts {MAXR} rounds, then attempt-2 finals)",
      len(prov.calls) == MAXR + 1, str(len(prov.calls)))
# the BUILDER escaping feedback is appended to attempt-2's seed user_msg (call index MAXR)
attempt2_seed = prov.calls[MAXR][0][1]
check("c: 1st call had NO feedback (== base)", FEEDBACK_MARK not in prov.calls[0][0][1])
check("c: attempt-2 seed CONTAINS the builder feedback block", FEEDBACK_MARK in attempt2_seed, attempt2_seed[-200:])
check("c: attempt-2 seed contains the content-escaping guidance", ESCAPE_MARK in attempt2_seed)


# (d) RETRY-EXHAUSTED: malformed every attempt → success=False (BLOCKING), failure_kind json_parse
print("\n[d] retry-exhausted: malformed forever")
res, prov = run_builder([(RAW_MALFORMED, "stop")])
check("d: success=False (blocking → pipeline would abort)", res.success is False)
check("d: failure_kind json_parse", getattr(res, "failure_kind", None) == "json_parse")
check(f"d: 3 attempts x {MAXR} rounds = {3 * MAXR} calls (bounded, no infinite loop)",
      len(prov.calls) == 3 * MAXR, str(len(prov.calls)))
check("d: raw_output captured = last malformed text", res.raw_output == RAW_MALFORMED.strip(), repr(res.raw_output))


# (e) NO-RETRY-ON-LENGTH: finish_reason=length → tool loop exhausts ONCE → length-gate → no retry
print("\n[e] no-retry-on-length")
res, prov = run_builder([(RAW_MALFORMED, "length")])
check("e: success=False", res.success is False)
check(f"e: exactly {MAXR} calls (ONE exhausting attempt; truncation NOT retried)",
      len(prov.calls) == MAXR, str(len(prov.calls)))
check("e: finish_reason length preserved on usage", (res.token_usage or {}).get("finish_reason") == "length")


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  builder json-retry (tool-loop-aware): {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
