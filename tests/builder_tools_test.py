"""B3 step 4 — Builder wired to the A2 tool loop via _parse_retry_core(model_call), cases a–f.

Builder is TOOL-ENABLED and BLOCKING (non-veto-safe). The tool loop is the INNER loop; the
parse-retry (_parse_retry_core) is the OUTER loop. Builder advertises ONLY read_file.

  (a) final envelope → success, proposal normalized, envelope unwrapped/parsed
  (b) read_file then final → success, read_file invoked, tool_loop_stats attached (rounds=2)
  (c) write_file requested → BLOCKED by the canonical advertised-set guard; execute NEVER called
  (d) blocking semantics: malformed every round AND every retry → success=False (NOT a fallback)
  (e) stats on the FAILURE path: the failure ExecutionResult carries tool_loop_stats (SUMMARY persists)
  (f) inner/outer composition: a malformed final exhausts attempt-1's tool loop → parse-retry runs a
      FRESH tool loop (attempt 2) → valid final → success

Hermetic: throwaway RUNTIME_DB; real temp workspace for read_file; repeat-last scripted provider.

    python tests/builder_tools_test.py
"""
import asyncio
import json
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="builder_tools_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

import agents.scoped_file_reader as _sfr  # noqa: E402
from agents.model_executor import ModelAgentExecutor, _TOOL_LOOP_MAX_ROUNDS  # noqa: E402
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole  # noqa: E402
from providers.base import CompletionResponse, TokenUsage  # noqa: E402
from tools.base import get_registry as get_tool_registry, ToolResult  # noqa: E402
from database import init_db  # noqa: E402

init_db()

WS = tempfile.mkdtemp(prefix="builder_tools_ws_")
with open(os.path.join(WS, "X.txt"), "w", encoding="utf-8") as f:
    f.write("X-FILE-CONTENT")
_sfr.resolve_workspace_root = lambda project_id: WS

PASS = 0
FAIL = 0
MAXR = _TOOL_LOOP_MAX_ROUNDS  # 5


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"  -- {detail}" if detail else ""))


VALID_DICT = {
    "change_summary": "Add a CANCELLED status and cancel method",
    "proposed_files": [{"path": "src/models.py", "action": "modify", "reason": "add CANCELLED", "content": "..."}],
    "change_steps": [{"step": 1, "description": "add enum value"}],
    "reasoning_summary": "grounded in the real enum",
    "validation_plan": ["run tests"],
    "risk_notes": [],
}
RAW_MALFORMED = '{\n  "change_summary": "x" "oops"\n}'      # bad JSON + no "action"


def final_env(obj):
    return (json.dumps({"action": "final", "result": obj}), "stop")


def tool_msg(tool, params):
    return (json.dumps({"action": "tool", "tool": tool, "params": params}), "stop")


class ScriptedProvider:
    def __init__(self, scripted):
        self.api_key = "fake"
        self._scripted = list(scripted)
        self.calls = []

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
    ctx = {"project_id": "p1", "title": "Add cancellation", "description": "d",
           "priority": "medium", "previous_outputs": {}}
    result = asyncio.run(ex._execute_builder(ctx))
    return result, prov


# (a) final envelope → success, normalized, unwrapped
print("\n[a] final-envelope")
res, prov = run_builder([final_env(VALID_DICT)])
check("a: success=True", res.success, getattr(res, "error_message", ""))
check("a: design unwrapped + parsed (change_summary preserved)",
      res.output.get("change_summary", "").startswith("Add a CANCELLED"))
check("a: proposal normalized (risk_level computed by _normalize_builder_proposal)",
      "risk_level" in res.output, str(res.output.keys()))
check("a: exactly 1 provider call", len(prov.calls) == 1, str(len(prov.calls)))
check("a: tool_loop_stats attached (ended=final)",
      isinstance(res.tool_loop_stats, dict) and res.tool_loop_stats.get("ended") == "final", str(res.tool_loop_stats))


# (b) read_file then final → success + read happened + stats
print("\n[b] read_file-then-final")
res, prov = run_builder([tool_msg("read_file", {"path": "X.txt"}), final_env(VALID_DICT)])
check("b: success=True after the tool round", res.success, getattr(res, "error_message", ""))
check("b: 2 provider calls", len(prov.calls) == 2, str(len(prov.calls)))
b_last_role, b_last = prov.calls[1][-1]
check("b: read_file result fed back (file content present)",
      b_last_role == "user" and "X-FILE-CONTENT" in b_last, b_last[:160])
st = res.tool_loop_stats
check("b: stats rounds=2 / tool_calls=1 / read_file:X.txt",
      st and st.get("rounds") == 2 and st.get("tool_calls") == 1
      and st.get("requests") == [{"tool": "read_file", "path": "X.txt"}], str(st))


# (c) write_file requested → BLOCKED (canonical advertised-set guard), execute NEVER called
print("\n[c] enforcement-blocks-write-tool (KEY SECURITY GUARD, builder path)")
_wf = get_tool_registry().get("write_file")
_wf_orig = _wf.execute
_wf_called = {"n": 0}


async def _wf_spy(params, context=None):
    _wf_called["n"] += 1
    return ToolResult(success=True, output={"path": "hacked"}, tool_name="write_file")


_wf.execute = _wf_spy
try:
    # Builder's allowed_tools include write/edit → is_allowed PASSES; only the read-only
    # advertised-set guard stops write_file. This is the defense-in-depth path in builder's route.
    res, prov = run_builder([tool_msg("write_file", {"path": "C:/evil.txt", "content": "x"}), final_env(VALID_DICT)])
finally:
    _wf.execute = _wf_orig
check("c: write_file.execute was NEVER called", _wf_called["n"] == 0, str(_wf_called["n"]))
check("c: builder still reached final (loop continued past the block)", res.success is True)
c_last_role, c_last = prov.calls[1][-1]
check("c: a tool_error (NOT a tool_result) was appended", "tool_error" in c_last and "tool_result" not in c_last, c_last[:160])


# (d) BLOCKING: malformed every round + every retry → success=False (NOT a veto-safe fallback)
# (e) stats on the FAILURE path
print("\n[d/e] blocking-semantics-preserved + stats-on-failure")
res, prov = run_builder([(RAW_MALFORMED, "stop")])
check("d: success=False (BLOCKING — pipeline would abort; NO architect-style fallback)", res.success is False)
check("d: output is empty (not a fabricated fallback design)", res.output == {}, str(res.output))
check(f"d: bounded — 3 attempts x {MAXR} rounds = {3 * MAXR} calls (no hang)",
      len(prov.calls) == 3 * MAXR, str(len(prov.calls)))
check("e: tool_loop_stats ATTACHED to the FAILURE result (SUMMARY persists on builder failure)",
      isinstance(res.tool_loop_stats, dict), str(res.tool_loop_stats))
check("e: failure stats ended=max_rounds_exhausted",
      (res.tool_loop_stats or {}).get("ended") == "max_rounds_exhausted", str(res.tool_loop_stats))


# (f) inner/outer composition: malformed final exhausts attempt 1 → parse-retry → valid attempt 2
print("\n[f] inner(tool)/outer(parse-retry) composition")
res, prov = run_builder([(RAW_MALFORMED, "stop")] * MAXR + [final_env(VALID_DICT)])
check("f: success=True (recovered by the parse-retry running a FRESH tool loop)", res.success, getattr(res, "error_message", ""))
check(f"f: {MAXR}+1 calls (attempt-1 exhausts {MAXR} rounds, attempt-2 finals on round 1)",
      len(prov.calls) == MAXR + 1, str(len(prov.calls)))
check("f: final design parsed", res.output.get("change_summary", "").startswith("Add a CANCELLED"))


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  builder-tools (B3 step 4): {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
