"""B3 step 2 — Architect wired to the A2 tool loop (_call_model_with_tools), cases a–e.

Proves _execute_architect now routes through _call_model_with_tools (advertising read_file
only) while keeping its pre-fetch (augment, not replace) and its VETO-SAFE invariant. Stub
provider scripts the action-protocol turns; a real temp workspace backs read_file.

  (a) clean final envelope → success=True with the unwrapped+parsed design (1 round)
  (b) one read_file tool call then final → architect reads actively; usage accumulates
  (c) malformed every round (max_rounds) → STILL success=True via _arch_concerns_fallback
  (d) provider.complete raises → STILL success=True via fallback (loop never-raises)
  (e) end-of-loop SUMMARY LogEvent emitted with round/tool-call counts + requested path

    python tests/architect_tools_test.py
"""
import asyncio
import json
import logging as pylog
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="arch_tools_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

import agents.scoped_file_reader as _sfr  # noqa: E402
from agents import model_executor as _me  # noqa: E402
from agents.model_executor import ModelAgentExecutor, ArchitectOutputSchema  # noqa: E402
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole  # noqa: E402
from providers.base import CompletionResponse, TokenUsage  # noqa: E402
from database import init_db  # noqa: E402

init_db()  # build_enhanced_system_prompt queries the skills table

# Temp workspace backing read_file; resolve_workspace_root monkeypatched so both the
# pre-fetch AND the tool loop's ToolContext point at it (no DB project row needed).
WS = tempfile.mkdtemp(prefix="arch_tools_ws_")
with open(os.path.join(WS, "X.txt"), "w", encoding="utf-8") as f:
    f.write("X-FILE-CONTENT")
_sfr.resolve_workspace_root = lambda project_id: WS

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


VALID_ARCH = {
    "design_summary": "Thin adapter over the existing auth module",
    "components": [{"name": "Adapter", "responsibility": "wraps the client"}],
    "key_decisions": [{"decision": "reuse auth router", "rationale": "least change"}],
    "interfaces_or_contracts": ["GET /auth/callback"],
    "risks_tradeoffs": ["token refresh"],
    "summary": "Builder implements the adapter behind the existing router.",
}


def final_msg(result):
    return (json.dumps({"action": "final", "result": result}), "stop")


def tool_msg(tool, params):
    return (json.dumps({"action": "tool", "tool": tool, "params": params}), "stop")


class ScriptedProvider:
    """Scripts (content, finish_reason) per round; records messages per call; can raise."""
    def __init__(self, scripted, raise_on_call=None):
        self.api_key = "fake"
        self._scripted = list(scripted)
        self.calls = []
        self.system_prompts = []
        self._raise_on_call = raise_on_call

    async def list_models(self):
        return []

    async def complete(self, request):
        idx = len(self.calls)
        self.calls.append([(m.role.value, m.content) for m in request.messages])
        self.system_prompts.append(request.system_prompt)
        if self._raise_on_call is not None and idx == self._raise_on_call:
            raise RuntimeError("boom in complete()")
        content, fr = self._scripted[idx] if idx < len(self._scripted) else self._scripted[-1]
        return CompletionResponse(
            content=content, model="gemini-2.5-flash", provider="gemini",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20), finish_reason=fr,
        )


def run_arch(scripted, task_context=None, raise_on_call=None):
    ex = ModelAgentExecutor()
    prov = ScriptedProvider(scripted, raise_on_call=raise_on_call)
    defn = get_definition(AgentRole.ARCHITECT)  # real architect defn (allowed_tools=[read,grep,glob])
    ex._resolve_provider = lambda role: (defn, prov, "gemini-2.5-flash")
    ctx = task_context if task_context is not None else {
        "project_id": "p1", "title": "Add OAuth", "description": "Google sign-in", "priority": "high",
    }
    result = asyncio.run(ex._execute_architect(ctx))
    return result, prov


# (a) clean final envelope → success with unwrapped+parsed design, 1 round
print("\n[a] clean-final-envelope")
res, prov = run_arch([final_msg(VALID_ARCH)])
check("a: success is True", res.success, getattr(res, "error_message", ""))
check("a: design unwrapped + parsed (design_summary preserved)",
      res.output.get("design_summary", "").startswith("Thin adapter"), str(res.output)[:160])
check("a: components preserved", len(res.output.get("components", [])) == 1)
check("a: exactly 1 provider call (clean final)", len(prov.calls) == 1, str(len(prov.calls)))
check("a: token_usage captured (provider=gemini)",
      bool(res.token_usage) and res.token_usage.get("provider") == "gemini", str(res.token_usage))
check("a: architect's prefetch still seeds turn 0 (structure block present)",
      "Project structure" in prov.calls[0][0][1], prov.calls[0][0][1][:120])
check("a: tool-use preamble reached the model (read_file advertised, augment-not-replace)",
      "TOOL-USE PROTOCOL (read-only)" in (prov.system_prompts[0] or "")
      and "read_file" in (prov.system_prompts[0] or ""),
      (prov.system_prompts[0] or "")[-200:])


# (b) one read_file tool call then final → architect actively reads; usage accumulates
print("\n[b] one-read_file-then-final")
res, prov = run_arch([tool_msg("read_file", {"path": "X.txt"}), final_msg(VALID_ARCH)])
check("b: success is True", res.success, getattr(res, "error_message", ""))
check("b: design parsed after the tool round", res.output.get("design_summary", "").startswith("Thin adapter"))
check("b: exactly 2 provider calls", len(prov.calls) == 2, str(len(prov.calls)))
r2 = prov.calls[1]
b_last_role, b_last = r2[-1]
check("b: read_file result fed back as a user message", b_last_role == "user" and "tool_result" in b_last)
check("b: the actual file content was read + appended", "X-FILE-CONTENT" in b_last, b_last[:160])
check("b: usage ACCUMULATED across 2 rounds reaches token_usage (total=60)",
      (res.token_usage or {}).get("total_tokens") == 60, str(res.token_usage))


# (c) VETO-SAFE on exhaustion: malformed every round → success=True via fallback (KEY GUARD)
print("\n[c] veto-safe-preserved-on-exhaustion (KEY GUARD)")
res, prov = run_arch([("garbage, not a protocol object", "stop")])  # repeats every round
check("c: success is True (NOT a veto, despite a misbehaving tool loop)", res.success is True)
check("c: degraded to the minimal fallback design",
      res.output.get("design_summary") == "Technical design could not be produced normally.", str(res.output)[:160])
ok, err = ArchitectOutputSchema.validate(res.output)
check("c: fallback design is itself schema-valid", ok, err)
check("c: loop ran exactly max_rounds then stopped",
      len(prov.calls) == _me._TOOL_LOOP_MAX_ROUNDS, str(len(prov.calls)))


# (d) VETO-SAFE on exception: provider.complete raises → success=True via fallback
print("\n[d] veto-safe-on-exception")
res, prov = run_arch([final_msg(VALID_ARCH)], raise_on_call=0)
check("d: success is True (loop never-raises; architect degrades)", res.success is True)
check("d: degraded to the minimal fallback design",
      res.output.get("design_summary") == "Technical design could not be produced normally.", str(res.output)[:160])
ok, err = ArchitectOutputSchema.validate(res.output)
check("d: fallback design is itself schema-valid", ok, err)


# (e) end-of-loop SUMMARY LogEvent with round/tool-call counts + requested path
print("\n[e] end-of-loop-observability-summary")


class _Cap(pylog.Handler):
    def __init__(self):
        super().__init__()
        self.msgs = []

    def emit(self, record):
        self.msgs.append(record.getMessage())


cap = _Cap()
_prev_level = _me.logger.level
_me.logger.addHandler(cap)
_me.logger.setLevel(pylog.INFO)
try:
    res, prov = run_arch([tool_msg("read_file", {"path": "X.txt"}), final_msg(VALID_ARCH)])
finally:
    _me.logger.removeHandler(cap)
    _me.logger.setLevel(_prev_level)
summaries = [m for m in cap.msgs if "SUMMARY" in m]
check("e: an end-of-loop SUMMARY LogEvent was emitted", len(summaries) >= 1, str(cap.msgs[-4:]))
s = summaries[-1] if summaries else ""
check("e: SUMMARY shows rounds=2", "rounds=2" in s, s)
check("e: SUMMARY shows tool_calls=1", "tool_calls=1" in s, s)
check("e: SUMMARY shows ended=final", "ended=final" in s, s)
check("e: SUMMARY records the requested PATH (metadata: read_file:X.txt)", "read_file:X.txt" in s, s)


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  architect-tools (B3 step 2): {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
