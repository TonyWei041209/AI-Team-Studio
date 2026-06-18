"""Security Reviewer SR-1 unit test — ModelAgentExecutor._execute_security_reviewer in isolation.

Verifies the new _execute_security_reviewer / _build_security_reviewer_user_message
and SecurityReviewerOutputSchema WITHOUT a network, API key, or DB. A FakeProvider
returns canned JSON and _resolve_provider is stubbed.

SR-1 scope: SECURITY_REVIEWER is NOT in the AgentRole enum or AGENT_PIPELINE yet
(that is SR-3); _execute_security_reviewer is wired to nothing and reachable only by
this test. It enforces the VETO INVARIANT (always success=True; malformed/failed
responses degrade to verdict="concerns"). This is a model_executor unit test and
lives OUTSIDE scripts/run-all-regression.py; run it directly:

    python tests/sr_step1_execute_security_reviewer_test.py
"""
import asyncio
import json
import os
import sys

# Ensure services/runtime is on sys.path for direct imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "runtime"))

from agents.model_executor import (
    ModelAgentExecutor,
    SecurityReviewerOutputSchema,
    _VALID_SEC_VERDICTS,
)
from agents.definitions import get_definition, SECURITY_REVIEWER_SYSTEM_PROMPT
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
            model="fake-sec-model",
            provider="fake",
            usage=TokenUsage(prompt_tokens=13, completion_tokens=21),
        )


def run_sr(canned_content, task_context):
    """Invoke _execute_security_reviewer with a fake provider, bypassing DB/registry.

    SECURITY_REVIEWER has no AgentRole/definition yet (SR-3), so the stub returns
    the QA definition as a stand-in — QA is non-injectable, so build_enhanced_system_prompt
    short-circuits to [] with no DB access — and _execute_security_reviewer overrides
    the system prompt via replace() to SECURITY_REVIEWER_SYSTEM_PROMPT regardless.
    """
    executor = ModelAgentExecutor()
    fake = FakeProvider(canned_content)
    executor._resolve_provider = lambda role: (get_definition(AgentRole.QA), fake, "fake-sec-model")
    result = asyncio.run(executor._execute_security_reviewer(task_context))
    return result, fake


TASK_CTX = {
    "title": "Add API key handling",
    "description": "Store and use a third-party API key",
    "priority": "high",
    "previous_outputs": {
        "planner": {"goal_summary": "Integrate API", "acceptance_criteria": ["calls succeed"]},
        "builder": {
            "change_summary": "add config module",
            "proposed_files": [{"path": "config.py", "action": "create", "reason": "config"}],
        },
    },
}

VALID_SR = {
    "review_scope": "Reviewed proposed config.py against the plan for security risks",
    "findings": [
        {"severity": "high", "category": "hardcoded_secret",
         "description": "config.py appears to embed an API key literal"},
    ],
    "overall_risk": "high",
    "verdict": "concerns",
    "summary": "One hardcoded-secret concern; externalize the key.",
}


# ── (a) valid security-review JSON → success, parsed verdict passed through ──
print("\n=== (a) valid security-review JSON -> ExecutionResult(success=True) ===")
res, fake = run_sr(json.dumps(VALID_SR), TASK_CTX)
check("valid: success is True", res.success, getattr(res, "error_message", ""))
check("valid: verdict == 'concerns'", res.output.get("verdict") == "concerns",
      f"got: {res.output.get('verdict')}")
check("valid: overall_risk == 'high' (genuine, not fallback's 'medium')",
      res.output.get("overall_risk") == "high")
check("valid: finding category preserved (hardcoded_secret, not fallback 'other')",
      res.output.get("findings", [{}])[0].get("category") == "hardcoded_secret")
check("valid: review_scope preserved", bool(res.output.get("review_scope")))
check("valid: no decision (informs, not a Reviewer)", res.decision is None)
check("valid: token_usage captured from provider",
      bool(res.token_usage) and res.token_usage.get("provider") == "fake")
# Confirm SECURITY_REVIEWER_SYSTEM_PROMPT reached the model (injected via replace()).
# The stand-in defn's role is QA (non-injectable) so no skills append -> exact equality.
sp = fake.last_request.system_prompt
check("valid: SECURITY_REVIEWER_SYSTEM_PROMPT injected to model", sp == SECURITY_REVIEWER_SYSTEM_PROMPT,
      f"system_prompt head: {(sp or '')[:80]!r}")
check("valid: prompt carries anti-fabrication framing",
      "MUST NOT fabricate vulnerabilities" in (sp or ""))
check("valid: user message included Builder proposal",
      "Builder proposal" in fake.last_request.messages[0].content)


# ── (b) malformed/failed → DEGRADES to concerns, NOT a veto ──
print("\n=== (b) malformed/failed -> success=True, verdict='concerns' (no veto) ===")


def expect_concerns(name, res):
    check(f"{name}: success is True (no veto)", res.success, getattr(res, "error_message", ""))
    check(f"{name}: verdict == 'concerns'", res.output.get("verdict") == "concerns",
          f"got: {res.output.get('verdict')}")
    check(f"{name}: fallback overall_risk == 'medium'", res.output.get("overall_risk") == "medium")
    findings = res.output.get("findings", [])
    check(f"{name}: a finding records the degradation",
          len(findings) >= 1 and bool(findings[0].get("description")))


# b1: invalid verdict value
res, _ = run_sr(json.dumps(dict(VALID_SR, verdict="BLOCK")), TASK_CTX)
expect_concerns("invalid verdict", res)

# b2: invalid severity
bad_sev = json.loads(json.dumps(VALID_SR))
bad_sev["findings"][0]["severity"] = "showstopper"
res, _ = run_sr(json.dumps(bad_sev), TASK_CTX)
expect_concerns("invalid severity", res)

# b3: empty category (non-empty rule)
bad_cat = json.loads(json.dumps(VALID_SR))
bad_cat["findings"][0]["category"] = "  "
res, _ = run_sr(json.dumps(bad_cat), TASK_CTX)
expect_concerns("empty category", res)

# b4: missing summary
res, _ = run_sr(json.dumps({k: v for k, v in VALID_SR.items() if k != "summary"}), TASK_CTX)
expect_concerns("missing summary", res)

# b5: non-JSON
res, _ = run_sr("not json {{{", TASK_CTX)
expect_concerns("non-JSON", res)

# b6: provider/resolution EXCEPTION must also degrade to concerns (never propagate)
print("\n=== (b6) provider exception -> success=True, verdict='concerns' ===")


def run_sr_raising(task_context):
    executor = ModelAgentExecutor()

    def boom(role):
        raise ValueError("security_reviewer is not enabled for real model calls")

    executor._resolve_provider = boom
    return asyncio.run(executor._execute_security_reviewer(task_context))


res = run_sr_raising(TASK_CTX)
expect_concerns("resolve exception", res)
check("exception: summary mentions could-not-complete",
      "could not be completed" in (res.output.get("summary") or "").lower(),
      res.output.get("summary"))


# ── (c) schema enforceability ──
print("\n=== (c) SecurityReviewerOutputSchema rejects malformed output ===")
ok, err = SecurityReviewerOutputSchema.validate(VALID_SR)
check("schema: valid output accepted", ok, err)
ok, _ = SecurityReviewerOutputSchema.validate({"verdict": "pass"})
check("schema: missing required fields rejected", not ok)
ok, _ = SecurityReviewerOutputSchema.validate(dict(VALID_SR, overall_risk="catastrophic"))
check("schema: bad overall_risk rejected", not ok)
ok, _ = SecurityReviewerOutputSchema.validate(
    dict(VALID_SR, findings=[{"severity": "high", "category": "", "description": "x"}]))
check("schema: empty finding category rejected", not ok)
ok, err = SecurityReviewerOutputSchema.validate(dict(VALID_SR, verdict="pass", findings=[]))
check("schema: empty findings + verdict 'pass' accepted", ok, err)
check("schema: 'BLOCK' not a valid verdict", "BLOCK" not in _VALID_SEC_VERDICTS)


# ── (d) graceful degradation of the message builder ──
print("\n=== (d) _build_security_reviewer_user_message graceful degradation ===")
msg_empty = ModelAgentExecutor._build_security_reviewer_user_message({"title": "T", "description": "D"})
check("degrade: builds without previous_outputs (no crash)",
      isinstance(msg_empty, str) and "T" in msg_empty)
msg_planner_only = ModelAgentExecutor._build_security_reviewer_user_message(
    {"title": "T", "previous_outputs": {"planner": {"goal_summary": "g"}}})
check("degrade: planner-only message has Planner but not Builder",
      "Planner plan" in msg_planner_only and "Builder proposal" not in msg_planner_only)


# ── summary ──
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
