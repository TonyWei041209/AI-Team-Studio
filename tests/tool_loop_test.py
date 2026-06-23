"""B3 step 1 — `_call_model_with_tools` A2 text-protocol tool loop (cases a–i).

Proves the SEPARATE multi-turn read-only tool loop in isolation (NOT wired to any role):
the model emits one JSON object per turn with an "action" discriminator ("tool" | "final");
the loop parses it from TEXT (fence/prose-tolerant), enforces tool access (role allowed_tools
via is_allowed + a defense-in-depth read-only allowlist), executes sandboxed read tools, feeds
bounded results back, accumulates token usage across rounds, bounds rounds, and NEVER raises.

Hermetic: a ScriptedProvider returns scripted (content, finish_reason) per round; a real temp
workspace backs the read tools (resolve_workspace_root monkeypatched → temp dir). No app, no net.

    python tests/tool_loop_test.py
"""
import asyncio
import json
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="tool_loop_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

import agents.scoped_file_reader as _sfr  # noqa: E402
from agents.model_executor import (  # noqa: E402
    ModelAgentExecutor,
    _TOOL_LOOP_MAX_ROUNDS,
    _TOOL_RESULT_CHAR_BUDGET,
    _STEP1_TOOL_NAMES,
)
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole  # noqa: E402
from providers.base import CompletionResponse, TokenUsage  # noqa: E402
from tools.base import get_registry as get_tool_registry, ToolResult  # noqa: E402
from database import init_db  # noqa: E402

init_db()  # build_enhanced_system_prompt queries the skills table

# ── Temp workspace backing the read tools ─────────────────────────
WS = tempfile.mkdtemp(prefix="tool_loop_ws_")
with open(os.path.join(WS, "X.txt"), "w", encoding="utf-8") as f:
    f.write("X-FILE-CONTENT")
with open(os.path.join(WS, "big.txt"), "w", encoding="utf-8") as f:
    f.write("A" * (_TOOL_RESULT_CHAR_BUDGET + 5000))
# Monkeypatch the lazy-imported workspace resolver so the loop's ToolContext points at WS
# (no DB project row needed; keeps the test hermetic).
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


# A schema-shaped architect result used as the "final" payload.
ARCH_RESULT = {
    "design_summary": "d", "components": [], "key_decisions": [],
    "interfaces_or_contracts": [], "risks_tradeoffs": [], "summary": "s",
}


def final_msg(result):
    return (json.dumps({"action": "final", "result": result}), "stop")


def tool_msg(tool, params):
    return (json.dumps({"action": "tool", "tool": tool, "params": params}), "stop")


class ScriptedProvider:
    """Returns scripted (content, finish_reason) per round; records the messages list per call.

    Repeats the LAST scripted entry once exhausted (so a single tool_msg drives every round).
    """
    def __init__(self, scripted, raise_on_call=None):
        self.api_key = "fake"
        self._scripted = list(scripted)
        self.calls = []  # per call: list[(role, content)] of the request messages
        self._raise_on_call = raise_on_call

    async def list_models(self):
        return []

    async def complete(self, request):
        idx = len(self.calls)
        self.calls.append([(m.role.value, m.content) for m in request.messages])
        if self._raise_on_call is not None and idx == self._raise_on_call:
            raise RuntimeError("boom in complete()")
        content, fr = self._scripted[idx] if idx < len(self._scripted) else self._scripted[-1]
        return CompletionResponse(
            content=content, model="gemini-2.5-flash", provider="gemini",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20), finish_reason=fr,
        )


def run_loop(scripted, role=AgentRole.ARCHITECT, task_context=None, raise_on_call=None):
    prov = ScriptedProvider(scripted, raise_on_call=raise_on_call)
    ex = ModelAgentExecutor()
    defn = get_definition(role)
    ctx = task_context if task_context is not None else {"project_id": "p1"}
    # B3 step 3.5: _call_model_with_tools now returns (content, usage, tool_loop_stats).
    content, usage, _stats = asyncio.run(ex._call_model_with_tools(
        defn, prov, "gemini-2.5-flash", "Do the task.",
        role=role, task_context=ctx,
    ))
    return content, usage, prov


# (a) FINAL first turn → unwrap envelope, 1 call, inner result handed to caller
print("\n[a] final-first-turn")
content, usage, prov = run_loop([final_msg(ARCH_RESULT)])
check("a: exactly 1 provider call", len(prov.calls) == 1, str(len(prov.calls)))
check("a: returned content is the UNWRAPPED inner result (ready for _parse_and_validate)",
      json.loads(content) == ARCH_RESULT, content[:200])


# (b) ONE TOOL THEN FINAL → read_file invoked, result appended, 2 calls
print("\n[b] one-tool-then-final")
content, usage, prov = run_loop([tool_msg("read_file", {"path": "X.txt"}), final_msg(ARCH_RESULT)])
check("b: exactly 2 provider calls", len(prov.calls) == 2, str(len(prov.calls)))
check("b: final returned after the tool round", json.loads(content) == ARCH_RESULT, content[:200])
r2 = prov.calls[1]
b_last_role, b_last = r2[-1]
check("b: tool result appended as a USER message", b_last_role == "user" and "tool_result" in b_last)
check("b: read_file invoked with the right path (echoed back)", '"path": "X.txt"' in b_last, b_last[:200])
check("b: file content present in the result text", "X-FILE-CONTENT" in b_last)
check("b: model's tool-request recorded as an ASSISTANT turn", any(rl == "assistant" for rl, _ in r2))


# (c) ENFORCEMENT — builder requests write_file → BLOCKED (defense-in-depth), never executed
print("\n[c] enforcement-blocks-write-tool (THE KEY SECURITY GUARD)")
_wf = get_tool_registry().get("write_file")
_wf_orig = _wf.execute
_wf_called = {"n": 0}


async def _wf_spy(params, context=None):
    _wf_called["n"] += 1
    return ToolResult(success=True, output={"path": "hacked"}, tool_name="write_file")


_wf.execute = _wf_spy
try:
    # Builder's allowed_tools include "write"/"edit", so is_allowed PASSES — only the read-only
    # advertised-set guard (canonical name) stops it. This is the defense-in-depth path.
    content, usage, prov = run_loop(
        [tool_msg("write_file", {"path": "C:/evil.txt", "content": "x"}), final_msg(ARCH_RESULT)],
        role=AgentRole.BUILDER,
    )
finally:
    _wf.execute = _wf_orig
check("c: write_file.execute was NEVER called", _wf_called["n"] == 0, str(_wf_called["n"]))
check("c: 2 provider calls (blocked round → final)", len(prov.calls) == 2, str(len(prov.calls)))
check("c: final still returned (loop continued past the block)", json.loads(content) == ARCH_RESULT)
c_last_role, c_last = prov.calls[1][-1]
check("c: a tool_error (NOT a tool_result) was appended", "tool_error" in c_last and "tool_result" not in c_last, c_last[:200])


# (d) MALFORMED then recover — prose / invalid JSON / JSON-without-action
print("\n[d] malformed-then-recover")
content, usage, prov = run_loop([("This is not JSON, just prose.", "stop"), final_msg(ARCH_RESULT)])
check("d: 2 provider calls (malformed → final)", len(prov.calls) == 2, str(len(prov.calls)))
check("d: final returned (recovered)", json.loads(content) == ARCH_RESULT)
d_last_role, d_last = prov.calls[1][-1]
check("d: protocol_error appended after the malformed turn", d_last_role == "user" and "protocol_error" in d_last, d_last[:200])
# JSON object WITHOUT an "action" key is also malformed-then-recoverable
content2, _u, prov2 = run_loop([(json.dumps({"foo": "bar"}), "stop"), final_msg(ARCH_RESULT)])
check("d: JSON-without-action → malformed → recovered to final", json.loads(content2) == ARCH_RESULT and len(prov2.calls) == 2)


# (e) FENCE/PROSE TOLERANCE — final wrapped in ```json``` fences with leading/trailing prose
print("\n[e] fence-and-prose-tolerance")
fenced = "Here is my answer:\n```json\n" + json.dumps({"action": "final", "result": ARCH_RESULT}) + "\n```\nDone."
content, usage, prov = run_loop([(fenced, "stop")])
check("e: fence+prose-wrapped final extracted", json.loads(content) == ARCH_RESULT, content[:200])
check("e: exactly 1 provider call", len(prov.calls) == 1)
pure_fenced = "```json\n" + json.dumps({"action": "final", "result": ARCH_RESULT}) + "\n```"
content2, _u, prov2 = run_loop([(pure_fenced, "stop")])
check("e: pure-fenced final via strip_code_fences", json.loads(content2) == ARCH_RESULT)


# (f) MAX_ROUNDS exhaustion — a tool request every round → stops at the bound
print("\n[f] max_rounds-bound")
content, usage, prov = run_loop([tool_msg("read_file", {"path": "X.txt"})])  # repeats forever
check(f"f: exactly _TOOL_LOOP_MAX_ROUNDS (={_TOOL_LOOP_MAX_ROUNDS}) provider calls",
      len(prov.calls) == _TOOL_LOOP_MAX_ROUNDS, str(len(prov.calls)))
check("f: returns the last content (does not loop forever / crash)",
      isinstance(content, str) and '"action": "tool"' in content, content[:120])


# (g) SIZE BUDGET — a large file is truncated in the appended result text
print("\n[g] per-result-size-budget")
content, usage, prov = run_loop([tool_msg("read_file", {"path": "big.txt"}), final_msg(ARCH_RESULT)])
g_role, g_last = prov.calls[1][-1]
check("g: large read result truncated with a note", "[truncated]" in g_last)
check("g: appended result text bounded near the budget",
      len(g_last) <= _TOOL_RESULT_CHAR_BUDGET + 300, str(len(g_last)))


# (h) TOKEN ACCUMULATION — sums across rounds (not just the last)
print("\n[h] token-accumulation")
content, usage, prov = run_loop([tool_msg("read_file", {"path": "X.txt"}), final_msg(ARCH_RESULT)])
check("h: prompt_tokens summed across 2 rounds (10+10)", usage["prompt_tokens"] == 20, str(usage))
check("h: completion_tokens summed (20+20)", usage["completion_tokens"] == 40, str(usage))
check("h: total_tokens summed (30+30)", usage["total_tokens"] == 60, str(usage))
check("h: provider/model set on the usage dict",
      usage["provider"] == "gemini" and usage["model"] == "gemini-2.5-flash", str(usage))


# (i) NEVER-RAISES — provider.complete raising returns gracefully
print("\n[i] never-raises")
content, usage, prov = run_loop([final_msg(ARCH_RESULT)], raise_on_call=0)
check("i: provider raise on 1st call → graceful return (no exception)", isinstance(content, str))
check("i: returns empty content when the very first call raises", content == "", repr(content))
check("i: usage dict still returned", isinstance(usage, dict) and "total_tokens" in usage)
content2, _u, prov2 = run_loop(
    [tool_msg("read_file", {"path": "X.txt"}), final_msg(ARCH_RESULT)], raise_on_call=1,
)
check("i: raise on 2nd call → returns last content from round 1 (the tool request)",
      isinstance(content2, str) and '"action": "tool"' in content2, content2[:120])


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  tool-loop (B3 step 1): {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
