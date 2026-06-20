"""Documentation DOC-1 unit test — ModelAgentExecutor._execute_documentation in isolation.

Verifies the new _execute_documentation / _build_documentation_user_message,
DocumentationOutputSchema, and _doc_skip_fallback WITHOUT a network, API key, or DB.
A FakeProvider returns canned JSON and _resolve_provider is stubbed.

DOC-1 scope: DOCUMENTATION is NOT in the AgentRole enum or AGENT_PIPELINE yet (that is
DOC-3); _execute_documentation is wired to nothing and reachable only by this test.

The Documentation role is modeled on the BUILDER (it proposes documentation file
changes that flow the execution chain), with HYBRID semantics: ALWAYS success=True, a
real proposal on success, and a no-proposal skip-fallback on any failure path. This is
a model_executor unit test and lives OUTSIDE scripts/run-all-regression.py for now
(DOC-4 registers it); run it directly:

    python tests/doc_step1_execute_documentation_test.py
"""
import asyncio
import json
import os
import sys

# Ensure services/runtime is on sys.path for direct imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "runtime"))

from agents.model_executor import ModelAgentExecutor, DocumentationOutputSchema
from agents.definitions import get_definition, DOCUMENTATION_SYSTEM_PROMPT
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
            model="fake-doc-model",
            provider="fake",
            usage=TokenUsage(prompt_tokens=14, completion_tokens=22),
        )


def run_doc(canned_content, task_context):
    """Invoke _execute_documentation with a fake provider, bypassing DB/registry.

    _execute_documentation injects DOCUMENTATION_SYSTEM_PROMPT via replace() internally
    (DOC-1 pre-activation step). To stay hermetic (no DB), the stub returns the QA
    definition — QA is non-injectable, so build_enhanced_system_prompt short-circuits
    with no DB access — and the method overwrites its system_prompt with
    DOCUMENTATION_SYSTEM_PROMPT, so the assertion that the doc prompt reaches the model
    still holds.
    """
    executor = ModelAgentExecutor()
    fake = FakeProvider(canned_content)
    executor._resolve_provider = lambda role: (get_definition(AgentRole.QA), fake, "fake-doc-model")
    result = asyncio.run(executor._execute_documentation(task_context))
    return result, fake


TASK_CTX = {
    "title": "Add OAuth login",
    "description": "Support Google OAuth sign-in",
    "priority": "high",
    "previous_outputs": {
        "planner": {"goal_summary": "Add Google OAuth", "acceptance_criteria": ["login works"]},
        "architect": {"design_summary": "OAuth2 auth-code flow with a thin adapter"},
        "builder": {
            "change_summary": "Implemented OAuthAdapter + SessionStore",
            "proposed_files": [{"path": "src/auth/oauth.py", "action": "create", "reason": "core"}],
        },
    },
}

VALID_DOC = {
    "change_summary": "Document the new Google OAuth login flow in the README and docs/auth.md",
    "proposed_files": [
        {
            "path": "docs/auth.md",
            "action": "create",
            "reason": "New auth subsystem needs end-user + developer docs",
            "content": "# Authentication\n\nThis app supports Google OAuth2 (authorization-code flow).\n",
        },
        {
            "path": "README.md",
            "action": "modify",
            "reason": "Add an Authentication section pointer",
            "content": "## Authentication\n\nSee docs/auth.md for the OAuth login flow.\n",
        },
    ],
    "change_steps": [
        {"step": 1, "description": "Create docs/auth.md describing the OAuth flow"},
        {"step": 2, "description": "Link it from the README"},
    ],
    "reasoning_summary": "Docs grounded in the Builder's OAuthAdapter/SessionStore changes",
    "validation_plan": ["A human checks docs/auth.md matches the implemented flow"],
}


# ── (a) valid documentation JSON → success, parsed proposal passed through ──
print("\n=== (a) valid documentation JSON -> ExecutionResult(success=True) with proposal ===")
res, fake = run_doc(json.dumps(VALID_DOC), TASK_CTX)
check("valid: success is True", res.success, getattr(res, "error_message", ""))
check("valid: change_summary preserved", res.output.get("change_summary", "").startswith("Document the new"))
check("valid: proposed_files preserved (2)", len(res.output.get("proposed_files", [])) == 2)
check("valid: each proposed file has non-empty content",
      all(isinstance(f.get("content"), str) and f["content"].strip() for f in res.output.get("proposed_files", [])))
check("valid: NOT a skip (real proposal)", res.output.get("skipped") is not True)
check("valid: no decision (Documentation is a Builder-like producer, not a verdict role)",
      res.decision is None)
check("valid: token_usage captured from provider",
      bool(res.token_usage) and res.token_usage.get("provider") == "fake")
# Confirm DOCUMENTATION_SYSTEM_PROMPT reached the model.
sp = fake.last_request.system_prompt
check("valid: DOCUMENTATION_SYSTEM_PROMPT injected to model", sp == DOCUMENTATION_SYSTEM_PROMPT,
      f"system_prompt head: {(sp or '')[:80]!r}")
check("valid: prompt carries anti-fabrication framing",
      "MUST NOT fabricate APIs, function signatures" in (sp or ""))
check("valid: prompt carries proposal-discipline framing (propose, don't write; full content)",
      "do NOT write them to disk yourself" in (sp or "") and "FULL intended content" in (sp or ""))
check("valid: prompt states docs never delete files",
      "Documentation never deletes files" in (sp or ""))
# User message documents what was built (planner + architect + builder all present).
um = fake.last_request.messages[0].content
check("valid: user message includes Planner plan", "Planner plan" in um)
check("valid: user message includes Architect design", "Architect design" in um)
check("valid: user message includes Builder proposal (documents what was built)",
      "Builder proposal" in um)


# ── (b) malformed/failed → DEGRADES to skip fallback (no proposal), NOT a veto ──
print("\n=== (b) malformed/failed -> success=True with skip fallback (no proposed_files) ===")


def expect_skip(name, res):
    check(f"{name}: success is True (no task failure)", res.success, getattr(res, "error_message", ""))
    check(f"{name}: marked skipped", res.output.get("skipped") is True)
    check(f"{name}: NO proposed_files in fallback", "proposed_files" not in res.output)


# b1: a proposed_files item with EMPTY content (the key improvement: must be rejected)
empty_content = json.loads(json.dumps(VALID_DOC))
empty_content["proposed_files"][0]["content"] = "   "
res, _ = run_doc(json.dumps(empty_content), TASK_CTX)
expect_skip("empty content", res)

# b2: missing content key entirely
no_content = json.loads(json.dumps(VALID_DOC))
del no_content["proposed_files"][1]["content"]
res, _ = run_doc(json.dumps(no_content), TASK_CTX)
expect_skip("missing content", res)

# b3: action = delete (not allowed for docs)
del_action = json.loads(json.dumps(VALID_DOC))
del_action["proposed_files"][0]["action"] = "delete"
res, _ = run_doc(json.dumps(del_action), TASK_CTX)
expect_skip("delete action", res)

# b4: zero proposed_files
zero_files = json.loads(json.dumps(VALID_DOC))
zero_files["proposed_files"] = []
res, _ = run_doc(json.dumps(zero_files), TASK_CTX)
expect_skip("zero proposed_files", res)

# b5: non-JSON
res, _ = run_doc("not json {{{", TASK_CTX)
expect_skip("non-JSON", res)


# ── (c) provider/resolution exception → success=True skip fallback (never propagate) ──
print("\n=== (c) provider exception -> success=True skip fallback ===")


def run_doc_raising(task_context):
    executor = ModelAgentExecutor()

    def boom(role):
        raise ValueError("documentation is not enabled for real model calls")

    executor._resolve_provider = boom
    return asyncio.run(executor._execute_documentation(task_context))


res = run_doc_raising(TASK_CTX)
expect_skip("resolve exception", res)
check("exception: reason mentions could-not-be-produced",
      "could not be produced" in (res.output.get("reason") or "").lower(),
      res.output.get("reason"))


# ── (d) DocumentationOutputSchema enforceability ──
print("\n=== (d) DocumentationOutputSchema rejects malformed output ===")
ok, err = DocumentationOutputSchema.validate(VALID_DOC)
check("schema: valid output accepted", ok, err)
ok, _ = DocumentationOutputSchema.validate(dict(VALID_DOC, proposed_files=[]))
check("schema: zero proposed_files rejected", not ok)
ok, _ = DocumentationOutputSchema.validate(
    dict(VALID_DOC, proposed_files=[{"path": "x.md", "action": "create", "reason": "r", "content": "  "}]))
check("schema: empty content rejected (improvement over BuilderOutputSchema)", not ok)
ok, _ = DocumentationOutputSchema.validate(
    dict(VALID_DOC, proposed_files=[{"path": "x.md", "action": "create", "reason": "r"}]))
check("schema: missing content rejected", not ok)
ok, _ = DocumentationOutputSchema.validate(
    dict(VALID_DOC, proposed_files=[{"path": "x.md", "action": "delete", "reason": "r", "content": "c"}]))
check("schema: action=delete rejected (docs never delete)", not ok)
ok, err = DocumentationOutputSchema.validate(
    dict(VALID_DOC, proposed_files=[{"path": "x.md", "action": "modify", "reason": "r", "content": "c"}]))
check("schema: single valid modify-with-content accepted", ok, err)
ok, _ = DocumentationOutputSchema.validate({"change_summary": "x"})
check("schema: missing required fields rejected", not ok)
ok, _ = DocumentationOutputSchema.validate(dict(VALID_DOC, validation_plan=[""]))
check("schema: empty validation_plan string rejected", not ok)


# ── (e) graceful degradation of the message builder ──
print("\n=== (e) _build_documentation_user_message graceful degradation ===")
msg_empty = ModelAgentExecutor._build_documentation_user_message({"title": "T", "description": "D"})
check("degrade: builds without previous_outputs (no crash)",
      isinstance(msg_empty, str) and "T" in msg_empty)
msg_full = ModelAgentExecutor._build_documentation_user_message(TASK_CTX)
check("degrade: includes Planner plan when present", "Planner plan" in msg_full)
check("degrade: includes Architect design when present", "Architect design" in msg_full)
check("degrade: includes Builder proposal when present", "Builder proposal" in msg_full)
msg_planner_only = ModelAgentExecutor._build_documentation_user_message(
    {"title": "T", "previous_outputs": {"planner": {"goal_summary": "g"}}})
check("degrade: omits Architect/Builder when absent",
      "Architect design" not in msg_planner_only and "Builder proposal" not in msg_planner_only)


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
