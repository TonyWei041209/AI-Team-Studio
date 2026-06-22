"""Reviewer malformed-JSON retry (shared _call_parse_retry) — cases a–e + rejection-path guard.

The Reviewer is a pipeline-BLOCKING role on PARSE failure: a malformed reviewer response
returns success=False and aborts at orchestrator §158 BEFORE the reviewer decision logic
(§172) runs — a separate code path from the request_changes rejection loop (which fires only
on a SUCCESSFUL parse). So the JSON retry recovers malformed-but-complete output WITHOUT
interfering with the rejection loop. It now wraps the shared _call_parse_retry (generic feedback).

Hermetic: ScriptedProvider returns scripted (content, finish_reason); throwaway RUNTIME_DB.

    python tests/reviewer_json_retry_test.py
"""
import asyncio
import json
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="reviewer_retry_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.model_executor import ModelAgentExecutor  # noqa: E402
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole, ReviewDecision  # noqa: E402
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
    "decision": "approve", "reason": "lgtm", "issues_found": [], "confidence": "high",
})
VALID_RC = json.dumps({
    "decision": "request_changes", "reason": "fix x",
    "issues_found": [{"severity": "major", "description": "bad"}], "confidence": "medium",
})
MALFORMED = '{\n  "decision": "approve" "oops"\n}'      # json.loads -> Expecting ',' delimiter
SCHEMA_FAIL = '{"decision": "approve"}'                 # parses, missing reason/issues_found/confidence

FEEDBACK_MARK = "IMPORTANT — your previous response was NOT valid JSON"
GENERIC_MARK = "inside all string values"


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


def run_reviewer(scripted):
    prov = ScriptedProvider(scripted)
    ex = ModelAgentExecutor()
    ex._resolve_provider = lambda role: (get_definition(AgentRole.REVIEWER), prov, "gemini-2.5-flash")
    ctx = {"title": "t", "description": "d", "priority": "low", "previous_outputs": {}}
    result = asyncio.run(ex._execute_reviewer(ctx))
    return result, prov


print("\n[a] retry-success: malformed(stop) -> valid")
res, prov = run_reviewer([(MALFORMED, "stop"), (VALID, "stop")])
check("a: success=True", res.success, getattr(res, "error_message", ""))
check("a: exactly 2 calls (1 retry)", len(prov.calls) == 2, str(len(prov.calls)))
check("a: 1st call had NO feedback", FEEDBACK_MARK not in prov.calls[0])
check("a: 2nd call CONTAINS feedback + GENERIC guidance", FEEDBACK_MARK in prov.calls[1] and GENERIC_MARK in prov.calls[1])
check("a: 2nd call has the parse-error detail", "Expecting" in prov.calls[1] and "delimiter" in prov.calls[1])
check("a: decision mapped (approve)", res.decision == ReviewDecision.APPROVE, str(res.decision))

print("\n[b] retry-exhausted: malformed(stop) x3  (would abort at orchestrator §158)")
res, prov = run_reviewer([(MALFORMED, "stop")] * 3)
check("b: success=False", res.success is False)
check("b: exactly 3 calls (1 + 2 retries)", len(prov.calls) == 3, str(len(prov.calls)))
check("b: failure_kind json_parse", getattr(res, "failure_kind", None) == "json_parse")
check("b: raw_output = last malformed (FAILED-path capture)", res.raw_output == MALFORMED.strip(), repr(res.raw_output))
check("b: decision is None (decision logic §172 never reached on parse-fail)", res.decision is None, str(res.decision))

print("\n[c] no-retry-on-length: malformed(length)")
res, prov = run_reviewer([(MALFORMED, "length")])
check("c: success=False", res.success is False)
check("c: exactly 1 call (truncation NOT retried)", len(prov.calls) == 1, str(len(prov.calls)))

print("\n[d] no-retry-on-schema: parses but fails schema")
res, prov = run_reviewer([(SCHEMA_FAIL, "stop")])
check("d: success=False", res.success is False)
check("d: exactly 1 call (schema failure NOT retried)", len(prov.calls) == 1, str(len(prov.calls)))
check("d: failure_kind schema", getattr(res, "failure_kind", None) == "schema")

print("\n[e] success-first-try: valid (approve)")
res, prov = run_reviewer([(VALID, "stop")])
check("e: success=True", res.success, getattr(res, "error_message", ""))
check("e: exactly 1 call (no retry)", len(prov.calls) == 1, str(len(prov.calls)))
check("e: no feedback appended (behavior-neutral)", FEEDBACK_MARK not in prov.calls[0])

print("\n[f] rejection-path UNAFFECTED: a SUCCESSFUL request_changes is not json-retried")
res, prov = run_reviewer([(VALID_RC, "stop")])
check("f: success=True", res.success)
check("f: decision REQUEST_CHANGES (rejection loop input intact)", res.decision == ReviewDecision.REQUEST_CHANGES, str(res.decision))
check("f: exactly 1 call (no json-retry on a valid parse)", len(prov.calls) == 1, str(len(prov.calls)))
check("f: no feedback appended", FEEDBACK_MARK not in prov.calls[0])


print()
print("-" * 60)
total = PASS + FAIL
print(f"  reviewer json-retry: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
