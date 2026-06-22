"""Architect AR-1 unit test — ModelAgentExecutor._execute_architect in isolation.

Verifies the new _execute_architect / _build_architect_user_message and
ArchitectOutputSchema WITHOUT a network, API key, or DB. A FakeProvider returns
canned JSON and _resolve_provider is stubbed.

AR-1 scope: ARCHITECT is NOT in the AgentRole enum or AGENT_PIPELINE yet (that is
AR-3); _execute_architect is wired to nothing and reachable only by this test. It is
veto-safe (always success=True; malformed/failed responses degrade to a minimal valid
design). This is a model_executor unit test and lives OUTSIDE
scripts/run-all-regression.py; run it directly:

    python tests/ar_step1_execute_architect_test.py
"""
import asyncio
import json
import os
import sys

# Ensure services/runtime is on sys.path for direct imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "runtime"))

from agents.model_executor import ModelAgentExecutor, ArchitectOutputSchema
from dataclasses import replace
from agents.definitions import get_definition, ARCHITECT_SYSTEM_PROMPT
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
            model="fake-arch-model",
            provider="fake",
            usage=TokenUsage(prompt_tokens=12, completion_tokens=20),
        )


def run_arch(canned_content, task_context):
    """Invoke _execute_architect with a fake provider, bypassing DB/registry.

    _execute_architect now reads the system prompt from the resolved definition (AR-3
    dropped the in-method replace()). To stay hermetic (no DB), the stub returns the QA
    definition — non-injectable, so build_enhanced_system_prompt short-circuits to []
    with no DB access — but carrying ARCHITECT_SYSTEM_PROMPT (injected here via replace())
    so the assertion that the architect prompt reaches the model still holds.
    """
    executor = ModelAgentExecutor()
    fake = FakeProvider(canned_content)
    arch_defn = replace(get_definition(AgentRole.QA), system_prompt=ARCHITECT_SYSTEM_PROMPT)
    executor._resolve_provider = lambda role: (arch_defn, fake, "fake-arch-model")
    result = asyncio.run(executor._execute_architect(task_context))
    return result, fake


TASK_CTX = {
    "title": "Add OAuth login",
    "description": "Support Google OAuth sign-in",
    "priority": "high",
    "previous_outputs": {
        "planner": {
            "goal_summary": "Add Google OAuth",
            "task_breakdown": [{"step": 1, "description": "wire oauth", "role": "builder"}],
            "acceptance_criteria": ["login works"],
        },
    },
}

VALID_ARCH = {
    "design_summary": "Add an OAuth2 authorization-code flow with a thin provider adapter",
    "components": [
        {"name": "OAuthAdapter", "responsibility": "Wraps the Google OAuth client", "interfaces": "exchange_code(code)->token"},
        {"name": "SessionStore", "responsibility": "Persists authenticated sessions"},
    ],
    "key_decisions": [
        {"decision": "Use authorization-code flow", "rationale": "Most secure for server-side apps", "alternatives": "implicit flow"},
    ],
    "interfaces_or_contracts": ["GET /auth/callback returns a session cookie"],
    "risks_tradeoffs": ["Token refresh adds complexity"],
    "summary": "Builder implements OAuthAdapter + SessionStore behind the existing auth router.",
}


# ── (a) valid architect JSON → success, parsed design passed through ──
# B3 step 2: the Architect now routes through the A2 tool loop. The model must emit the
# {"action":"final","result":{...}} envelope; the loop unwraps it and hands the inner result
# to _parse_and_validate exactly as before. (The malformed/degradation cases below deliberately
# return non-protocol JSON to exercise the veto-safe fallback.)
print("\n=== (a) valid architect JSON -> ExecutionResult(success=True) ===")
res, fake = run_arch(json.dumps({"action": "final", "result": VALID_ARCH}), TASK_CTX)
check("valid: success is True", res.success, getattr(res, "error_message", ""))
check("valid: design_summary preserved", res.output.get("design_summary", "").startswith("Add an OAuth2"))
check("valid: components preserved (2)", len(res.output.get("components", [])) == 2)
check("valid: key_decisions preserved (1)", len(res.output.get("key_decisions", [])) == 1)
check("valid: no decision (informs, not a Reviewer)", res.decision is None)
check("valid: token_usage captured from provider",
      bool(res.token_usage) and res.token_usage.get("provider") == "fake")
# Confirm ARCHITECT_SYSTEM_PROMPT reached the model (injected via replace()).
# B3 step 2: the tool loop APPENDS the read-only tool-use protocol preamble to the base prompt,
# so the base prompt is now the PREFIX (was exact equality under single-shot _call_model).
sp = fake.last_request.system_prompt
check("valid: ARCHITECT_SYSTEM_PROMPT is the system-prompt prefix",
      (sp or "").startswith(ARCHITECT_SYSTEM_PROMPT),
      f"system_prompt head: {(sp or '')[:80]!r}")
check("valid: read-only tool-use protocol preamble appended (step-2 wiring)",
      "TOOL-USE PROTOCOL (read-only)" in (sp or "") and "read_file" in (sp or ""))
check("valid: prompt carries anti-fabrication framing",
      "MUST NOT fabricate existing code structure" in (sp or ""))
check("valid: prompt carries Planner-boundary framing (HOW not WHAT / no re-decompose)",
      "do NOT re-decompose tasks" in (sp or "") and "do NOT write file contents or code" in (sp or ""))
check("valid: user message included the Planner plan",
      "Planner plan" in fake.last_request.messages[0].content)


# ── (b) malformed/failed → DEGRADES to a minimal valid design, NOT a veto ──
print("\n=== (b) malformed/failed -> success=True with fallback design (no veto) ===")


def expect_fallback(name, res):
    check(f"{name}: success is True (no veto)", res.success, getattr(res, "error_message", ""))
    check(f"{name}: fallback design_summary",
          res.output.get("design_summary") == "Technical design could not be produced normally.")
    ok, err = ArchitectOutputSchema.validate(res.output)
    check(f"{name}: fallback is itself schema-valid", ok, err)


# b1: missing summary
res, _ = run_arch(json.dumps({k: v for k, v in VALID_ARCH.items() if k != "summary"}), TASK_CTX)
expect_fallback("missing summary", res)

# b2: component missing responsibility
bad_comp = json.loads(json.dumps(VALID_ARCH))
del bad_comp["components"][0]["responsibility"]
res, _ = run_arch(json.dumps(bad_comp), TASK_CTX)
expect_fallback("bad component", res)

# b3: interfaces_or_contracts has an empty string
bad_contract = json.loads(json.dumps(VALID_ARCH))
bad_contract["interfaces_or_contracts"] = ["  "]
res, _ = run_arch(json.dumps(bad_contract), TASK_CTX)
expect_fallback("empty contract string", res)

# b4: non-JSON
res, _ = run_arch("not json {{{", TASK_CTX)
expect_fallback("non-JSON", res)

# b5: provider/resolution EXCEPTION must also degrade (never propagate)
print("\n=== (b5) provider exception -> success=True fallback ===")


def run_arch_raising(task_context):
    executor = ModelAgentExecutor()

    def boom(role):
        raise ValueError("architect is not enabled for real model calls")

    executor._resolve_provider = boom
    return asyncio.run(executor._execute_architect(task_context))


res = run_arch_raising(TASK_CTX)
expect_fallback("resolve exception", res)
check("exception: summary mentions could-not-be-completed",
      "could not be completed" in (res.output.get("summary") or "").lower(),
      res.output.get("summary"))


# ── (c) schema enforceability ──
print("\n=== (c) ArchitectOutputSchema rejects malformed output ===")
ok, err = ArchitectOutputSchema.validate(VALID_ARCH)
check("schema: valid output accepted", ok, err)
ok, _ = ArchitectOutputSchema.validate({"summary": "x"})
check("schema: missing required fields rejected", not ok)
ok, _ = ArchitectOutputSchema.validate(dict(VALID_ARCH, components=[{"name": "X"}]))
check("schema: component missing responsibility rejected", not ok)
ok, _ = ArchitectOutputSchema.validate(dict(VALID_ARCH, risks_tradeoffs=[""]))
check("schema: empty risk string rejected", not ok)
ok, err = ArchitectOutputSchema.validate(
    dict(VALID_ARCH, components=[], key_decisions=[], interfaces_or_contracts=[], risks_tradeoffs=[]))
check("schema: all-empty lists accepted (design_summary + summary present)", ok, err)
# optional fields omitted (no interfaces / no alternatives) -> valid
ok, err = ArchitectOutputSchema.validate(dict(
    VALID_ARCH,
    components=[{"name": "C", "responsibility": "R"}],
    key_decisions=[{"decision": "D", "rationale": "Why"}]))
check("schema: optional interfaces/alternatives omitted -> accepted", ok, err)


# ── (d) graceful degradation of the message builder ──
print("\n=== (d) _build_architect_user_message graceful degradation ===")
msg_empty = ModelAgentExecutor._build_architect_user_message({"title": "T", "description": "D"})
check("degrade: builds without previous_outputs (no crash)",
      isinstance(msg_empty, str) and "T" in msg_empty)
msg_planner = ModelAgentExecutor._build_architect_user_message(
    {"title": "T", "previous_outputs": {"planner": {"goal_summary": "g"}}})
check("degrade: includes Planner plan when present", "Planner plan" in msg_planner)
check("degrade: does NOT include Builder output (Architect runs before the Builder)",
      "Builder" not in msg_planner)


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
