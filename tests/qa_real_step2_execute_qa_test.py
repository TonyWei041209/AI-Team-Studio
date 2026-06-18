"""QA-Real Step 2 unit test — ModelAgentExecutor._execute_qa in isolation.

Verifies the new _execute_qa / _build_qa_user_message and QaOutputSchema WITHOUT
a network, API key, or DB. The real provider call is replaced by a FakeProvider
that returns canned JSON, and _resolve_provider is stubbed so no
role_model_settings row or API key is required.

This method is NOT wired into the live pipeline (that is Step 3) — it is
reachable only by this test. This file is a model_executor unit test and lives
OUTSIDE scripts/run-all-regression.py (which runs phase 6E-8B API suites); run
it directly:

    python tests/qa_real_step2_execute_qa_test.py
"""
import asyncio
import json
import os
import sys

# Ensure services/runtime is on sys.path for direct imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "runtime"))

from agents.model_executor import ModelAgentExecutor, QaOutputSchema, _VALID_QA_RESULTS
from agents.definitions import get_definition, QA_SYSTEM_PROMPT
from models import AgentRole
from providers.base import CompletionResponse, TokenUsage

results = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((name, status))
    mark = "PASS" if condition else "FAIL"
    suffix = f" -- {detail}" if detail and not condition else ""
    print(f"  [{mark}] {name}{suffix}")


# ── Fake provider (no network / no API key) ───────────────────

class FakeProvider:
    """Returns a canned completion; records the request it received."""

    def __init__(self, canned_content):
        self._canned = canned_content
        self.api_key = "fake-test-key"
        self.last_request = None

    async def complete(self, request):
        self.last_request = request
        return CompletionResponse(
            content=self._canned,
            model="fake-qa-model",
            provider="fake",
            usage=TokenUsage(prompt_tokens=11, completion_tokens=22),
        )


def run_qa(canned_content, task_context):
    """Invoke _execute_qa with a fake provider, bypassing DB/registry.

    Only _resolve_provider (DB row + registered provider + API key) is stubbed;
    _build_qa_user_message, _call_model, _parse_and_validate and QaOutputSchema
    all run for real.
    """
    executor = ModelAgentExecutor()
    fake = FakeProvider(canned_content)
    executor._resolve_provider = lambda role: (get_definition(role), fake, "fake-qa-model")
    result = asyncio.run(executor._execute_qa(task_context))
    return result, fake


TASK_CTX = {
    "title": "Add JWT auth",
    "description": "Implement login/logout endpoints",
    "priority": "high",
    "previous_outputs": {
        "planner": {"goal_summary": "Auth", "acceptance_criteria": ["login works"]},
        "builder": {
            "change_summary": "add auth",
            "proposed_files": [{"path": "auth.py", "action": "create", "reason": "core"}],
        },
    },
}

VALID_QA = {
    "validation_scope": "Reviewed proposed auth.py against the Planner's acceptance criteria",
    "review_findings": [
        {"severity": "minor", "description": "No explicit token-expiry handling mentioned"},
    ],
    "acceptance_criteria_assessment": [
        {"criterion": "login works", "met": True, "rationale": "Proposal adds a login endpoint"},
    ],
    "result": "concerns",
    "summary": "Proposal broadly addresses the plan; one minor gap noted.",
}


# ── (a) valid JSON → success ──────────────────────────────────
print("\n=== (a) valid QA JSON -> ExecutionResult(success=True) ===")
res, fake = run_qa(json.dumps(VALID_QA), TASK_CTX)
check("valid: success is True", res.success, getattr(res, "error_message", ""))
check("valid: output result == 'concerns'", res.output.get("result") == "concerns",
      f"got: {res.output.get('result')}")
check("valid: validation_scope preserved", bool(res.output.get("validation_scope")))
check("valid: review_findings preserved (1)", len(res.output.get("review_findings", [])) == 1)
check("valid: acceptance_criteria_assessment preserved (1)",
      len(res.output.get("acceptance_criteria_assessment", [])) == 1)
check("valid: no decision (QA is not Reviewer)", res.decision is None)
check("valid: token_usage captured from provider",
      bool(res.token_usage) and res.token_usage.get("provider") == "fake")
# Confirm QA_SYSTEM_PROMPT actually reached the model, injected via dataclasses.replace.
# QA receives no skill injection (load_skills_for_role short-circuits QA to []), so
# build_enhanced_system_prompt returns the base prompt verbatim — exact equality proves
# the replace() injection worked end-to-end (without it defn.system_prompt would be "").
sp = fake.last_request.system_prompt
check("valid: exact QA_SYSTEM_PROMPT injected via replace()", sp == QA_SYSTEM_PROMPT,
      f"system_prompt head: {(sp or '')[:80]!r}")
check("valid: injected prompt carries anti-fabrication framing",
      "MUST NOT claim you ran tests" in (sp or ""))
# Confirm the user message included the Builder proposal QA reviews
check("valid: user message included Builder proposal",
      "Builder proposal" in fake.last_request.messages[0].content)


# ── (b) invalid JSON → failure ────────────────────────────────
print("\n=== (b) invalid QA JSON -> ExecutionResult(success=False) ===")

# b1: legacy mock value result="PASS" (uppercase) is now invalid
bad_pass = dict(VALID_QA, result="PASS")
res, _ = run_qa(json.dumps(bad_pass), TASK_CTX)
check("invalid: result='PASS' -> success False", not res.success)
check("invalid: error mentions result schema",
      "result must be one of" in (res.error_message or ""), res.error_message)

# b2: missing summary
bad_missing = {k: v for k, v in VALID_QA.items() if k != "summary"}
res, _ = run_qa(json.dumps(bad_missing), TASK_CTX)
check("invalid: missing summary -> success False", not res.success)
check("invalid: error mentions summary", "summary" in (res.error_message or ""), res.error_message)

# b3: whitespace-only criterion (Part C tightening)
bad_crit = json.loads(json.dumps(VALID_QA))
bad_crit["acceptance_criteria_assessment"][0]["criterion"] = "   "
res, _ = run_qa(json.dumps(bad_crit), TASK_CTX)
check("invalid: whitespace criterion -> success False (Part C)", not res.success)
check("invalid: error mentions criterion non-empty",
      "criterion must be a non-empty string" in (res.error_message or ""), res.error_message)

# b3b: criterion key omitted entirely (also caught by the non-empty check)
bad_crit_missing = json.loads(json.dumps(VALID_QA))
del bad_crit_missing["acceptance_criteria_assessment"][0]["criterion"]
res, _ = run_qa(json.dumps(bad_crit_missing), TASK_CTX)
check("invalid: missing criterion key -> success False", not res.success)
check("invalid: error mentions criterion (missing key)",
      "criterion must be a non-empty string" in (res.error_message or ""), res.error_message)

# b3c: empty-string rationale (Part C tightening — the other tightened field)
bad_rat = json.loads(json.dumps(VALID_QA))
bad_rat["acceptance_criteria_assessment"][0]["rationale"] = ""
res, _ = run_qa(json.dumps(bad_rat), TASK_CTX)
check("invalid: empty rationale -> success False (Part C)", not res.success)
check("invalid: error mentions rationale non-empty",
      "rationale must be a non-empty string" in (res.error_message or ""), res.error_message)

# b4: malformed (not JSON at all)
res, _ = run_qa("not json {{{", TASK_CTX)
check("invalid: non-JSON -> success False", not res.success)
check("invalid: error mentions invalid JSON", "invalid JSON" in (res.error_message or ""), res.error_message)


# ── (c) schema enforceability (anti-fabrication is structural) ─
print("\n=== (c) QaOutputSchema rejects malformed output ===")
ok, err = QaOutputSchema.validate(VALID_QA)
check("schema: valid output accepted", ok, err)
ok, _ = QaOutputSchema.validate({"result": "pass"})
check("schema: missing required fields rejected", not ok)
ok, _ = QaOutputSchema.validate(dict(VALID_QA, review_findings=[{"severity": "bogus", "description": "x"}]))
check("schema: bad severity rejected", not ok)
ok, err = QaOutputSchema.validate(dict(VALID_QA, result="pass"))
check("schema: result 'pass' accepted", ok, err)
check("schema: 'PASS' not a valid result (distinct from legacy mock)", "PASS" not in _VALID_QA_RESULTS)


# ── (d) graceful degradation of _build_qa_user_message ────────
print("\n=== (d) _build_qa_user_message graceful degradation ===")
msg_empty = ModelAgentExecutor._build_qa_user_message({"title": "T", "description": "D"})
check("degrade: builds without previous_outputs (no crash)",
      isinstance(msg_empty, str) and "T" in msg_empty)
msg_planner_only = ModelAgentExecutor._build_qa_user_message(
    {"title": "T", "previous_outputs": {"planner": {"goal_summary": "g"}}})
check("degrade: planner-only message has Planner but not Builder",
      "Planner plan" in msg_planner_only and "Builder proposal" not in msg_planner_only)


# ── summary ───────────────────────────────────────────────────
print("\n" + "=" * 70)
passed = sum(1 for _, s in results if s == "PASS")
failed = sum(1 for _, s in results if s == "FAIL")
print(f"TOTAL: {len(results)}  |  PASS: {passed}  |  FAIL: {failed}")
print("=" * 70)
if failed > 0:
    print("\nFailed tests:")
    for name, status in results:
        if status == "FAIL":
            print(f"  - {name}")
    sys.exit(1)
sys.exit(0)
