"""Per-model max_tokens clamp test.

_call_model clamps the desired output budget (_DESIRED_MAX_OUTPUT_TOKENS=16384) to each
model's real hard API limit (ModelInfo.max_tokens), so high-capacity models (Gemini, now
65536) get 16384 while hard-limited models (Anthropic 8192) are NEVER exceeded.

Asserts (helper math AND the actual CompletionRequest built by _call_model):
  (a) Gemini (ModelInfo 65536) -> 16384   (the unlock)
  (b) Anthropic Haiku/Sonnet (8192) -> 8192  (regression guard: never exceed the hard limit)
  (c) unknown model_name -> 8192   (safe degradation, NOT 16384)
Plus: Gemini ModelInfo.max_tokens is now 65536; Anthropic's is still 8192 (untouched).

Hermetic: FakeProv with list_models()/complete(); throwaway RUNTIME_DB (skill lookup).

    python tests/max_tokens_clamp_test.py
"""
import asyncio
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="clamp_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.model_executor import ModelAgentExecutor, _DESIRED_MAX_OUTPUT_TOKENS  # noqa: E402
from agents.definitions import get_definition  # noqa: E402
from models import AgentRole  # noqa: E402
from providers.base import CompletionResponse, TokenUsage, ModelInfo  # noqa: E402
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


class FakeProv:
    """Provider stub exposing list_models() (for the clamp lookup) + recording complete()."""
    def __init__(self, models):
        self.api_key = "fake"
        self._models = list(models)
        self.last_request = None

    async def list_models(self):
        return list(self._models)

    async def complete(self, request):
        self.last_request = request
        return CompletionResponse(content="{}", model=request.model, provider="fake",
                                  usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
                                  finish_reason="stop")


GEMINI = ModelInfo(id="gemini-2.5-flash", display_name="g", provider="gemini", max_tokens=65536)
HAIKU = ModelInfo(id="claude-3-5-haiku-20241022", display_name="h", provider="anthropic", max_tokens=8192)


print(f"\n_DESIRED_MAX_OUTPUT_TOKENS = {_DESIRED_MAX_OUTPUT_TOKENS}")

print("\n[helper math] _resolve_max_tokens(provider, model_name)")
# (a) Gemini → 16384
eff = asyncio.run(ModelAgentExecutor._resolve_max_tokens(FakeProv([GEMINI]), "gemini-2.5-flash"))
check("a: Gemini (65536) -> 16384 (unlocked)", eff == 16384, str(eff))
# (b) Anthropic Haiku → 8192 (regression guard)
eff = asyncio.run(ModelAgentExecutor._resolve_max_tokens(FakeProv([HAIKU]), "claude-3-5-haiku-20241022"))
check("b: Haiku (8192) -> 8192 (REGRESSION GUARD: never exceed hard limit)", eff == 8192, str(eff))
# (c) unknown model → 8192 (safe fallback, NOT 16384)
eff = asyncio.run(ModelAgentExecutor._resolve_max_tokens(FakeProv([GEMINI]), "mystery-model-x"))
check("c: unknown model -> 8192 (safe degradation, not 16384)", eff == 8192, str(eff))
# also: provider whose list_models raises → 8192
class BrokenProv(FakeProv):
    async def list_models(self):
        raise RuntimeError("boom")
eff = asyncio.run(ModelAgentExecutor._resolve_max_tokens(BrokenProv([]), "anything"))
check("c': list_models error -> 8192 (never raises, conservative)", eff == 8192, str(eff))


print("\n[end-to-end] the ACTUAL CompletionRequest.max_tokens built by _call_model")
ex = ModelAgentExecutor()
defn = get_definition(AgentRole.BUILDER)

prov = FakeProv([GEMINI])
asyncio.run(ex._call_model(defn, prov, "gemini-2.5-flash", "hi"))
check("a-e2e: Gemini request.max_tokens == 16384", prov.last_request.max_tokens == 16384, str(prov.last_request.max_tokens))

prov = FakeProv([HAIKU])
asyncio.run(ex._call_model(defn, prov, "claude-3-5-haiku-20241022", "hi"))
check("b-e2e: Haiku request.max_tokens == 8192 (NEVER >8192)", prov.last_request.max_tokens == 8192, str(prov.last_request.max_tokens))

prov = FakeProv([GEMINI])
asyncio.run(ex._call_model(defn, prov, "unknown-xyz", "hi"))
check("c-e2e: unknown request.max_tokens == 8192", prov.last_request.max_tokens == 8192, str(prov.last_request.max_tokens))


print("\n[metadata] Gemini corrected to 65536; Anthropic still 8192 (untouched)")
from providers.gemini_provider import _GEMINI_MODELS  # noqa: E402
from providers.anthropic_provider import _CLAUDE_MODELS  # noqa: E402
gem = {m.id: m.max_tokens for m in _GEMINI_MODELS}
ana = {m.id: m.max_tokens for m in _CLAUDE_MODELS}
check("gemini metadata all 65536", all(v == 65536 for v in gem.values()), str(gem))
check("gemini covers the 3 known models", set(gem) == {"gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.1-pro-preview"}, str(set(gem)))
check("anthropic metadata still all 8192 (untouched hard limit)", all(v == 8192 for v in ana.values()), str(ana))


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  max_tokens clamp: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
