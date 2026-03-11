"""Phase 6A acceptance test suite — Provider Abstraction & Settings.

Covers:
  - GET /api/providers (list all providers)
  - GET /api/models (list all models, with optional filter)
  - POST /api/provider-test (test connectivity)
  - GET /api/settings/providers (read settings, masked keys)
  - PATCH /api/settings/providers (save settings)
  - API key masking safety
  - Unknown provider handling
  - Idempotent settings updates
"""
import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")
results = []


# ── Test isolation: reset provider settings before running ────────
def _reset_provider_settings():
    """Clear all provider settings to ensure a clean slate."""
    try:
        rq = urllib.request.Request(
            BASE + "/settings/providers",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(rq) as resp:
            current = json.loads(resp.read().decode())
        resets = []
        for p in current.get("providers", []):
            resets.append({
                "provider_name": p["provider_name"],
                "api_key": "",
                "base_url": "",
                "enabled": False,
            })
        if resets:
            data = json.dumps({"providers": resets}).encode()
            rq2 = urllib.request.Request(
                BASE + "/settings/providers",
                data=data,
                method="PATCH",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(rq2)
    except Exception:
        pass  # first run — table may be empty

_reset_provider_settings()


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    rq = urllib.request.Request(url, data=data, method=method)
    rq.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(rq) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((name, status))
    mark = "PASS" if condition else "FAIL"
    suffix = f" -- {detail}" if detail and not condition else ""
    print(f"  [{mark}] {name}{suffix}")


# ── 1. GET /api/providers ────────────────────────────────────────

print("\n=== Provider listing ===")

code, data = req("GET", "/providers")
check("GET /providers returns 200", code == 200)
check("Returns a list", isinstance(data, list))
check("At least 6 providers", len(data) >= 6, f"got {len(data)}")

names = [p["name"] for p in data]
for expected in ["anthropic", "openai", "deepseek", "gemini"]:
    check(f"Provider '{expected}' present", expected in names)

# Each provider has name + display_name
for p in data:
    check(
        f"Provider '{p['name']}' has display_name",
        "display_name" in p and len(p["display_name"]) > 0,
    )

# ── 2. GET /api/models ────────────────────────────────────────────

print("\n=== Model listing ===")

code, models = req("GET", "/models")
check("GET /models returns 200", code == 200)
check("Returns a list", isinstance(models, list))
check("At least 10 models", len(models) >= 10, f"got {len(models)}")

# Each model has required fields
for m in models[:3]:  # spot check first 3
    check(
        f"Model '{m['id']}' has all fields",
        all(k in m for k in ("id", "display_name", "provider", "max_tokens")),
    )

# Filter by provider
code, filtered = req("GET", "/models?provider=anthropic")
check("Filter by provider returns 200", code == 200)
check(
    "All filtered models are anthropic",
    all(m["provider"] == "anthropic" for m in filtered),
)
check("Anthropic has at least 2 models", len(filtered) >= 2, f"got {len(filtered)}")

# ── 3. GET /api/settings/providers (initial state) ───────────────

print("\n=== Settings — initial state ===")

code, settings = req("GET", "/settings/providers")
check("GET /settings/providers returns 200", code == 200)
check("Has 'providers' key", "providers" in settings)

provs = settings["providers"]
check("Settings for all registered providers", len(provs) >= 6, f"got {len(provs)}")

# All should be unconfigured initially
for ps in provs:
    check(
        f"  {ps['provider_name']} key is '(not set)'",
        ps["api_key_masked"] == "(not set)",
    )
    check(
        f"  {ps['provider_name']} disabled initially",
        ps["enabled"] is False,
    )

# ── 4. PATCH /api/settings/providers ──────────────────────────────

print("\n=== Settings — save & mask ===")

# Save a fake key for anthropic
code, patched = req("PATCH", "/settings/providers", {
    "providers": [
        {
            "provider_name": "anthropic",
            "api_key": "sk-ant-fake-key-ABCDEFGH12345678",
            "enabled": True,
        }
    ]
})
check("PATCH returns 200", code == 200)

anth = [p for p in patched["providers"] if p["provider_name"] == "anthropic"][0]
check("Anthropic now enabled", anth["enabled"] is True)
check(
    "API key is masked (not raw)",
    "sk-ant-fake-key-ABCDEFGH12345678" not in anth["api_key_masked"],
    f"got: {anth['api_key_masked']}",
)
check(
    "Masked key shows partial",
    anth["api_key_masked"] != "(not set)" and "****" in anth["api_key_masked"],
    f"got: {anth['api_key_masked']}",
)

# Other providers unchanged
oai = [p for p in patched["providers"] if p["provider_name"] == "openai"][0]
check("OpenAI still unconfigured", oai["api_key_masked"] == "(not set)")

# ── 5. Settings persistence — re-read ────────────────────────────

print("\n=== Settings — persistence ===")

code, reread = req("GET", "/settings/providers")
anth2 = [p for p in reread["providers"] if p["provider_name"] == "anthropic"][0]
check("Re-read shows saved state", anth2["enabled"] is True)
check("Re-read key still masked", "****" in anth2["api_key_masked"])

# ── 6. Partial update — only change enabled, not key ─────────────

print("\n=== Settings — partial update ===")

code, partial = req("PATCH", "/settings/providers", {
    "providers": [
        {"provider_name": "anthropic", "enabled": False}
    ]
})
anth3 = [p for p in partial["providers"] if p["provider_name"] == "anthropic"][0]
check("Partial update: disabled", anth3["enabled"] is False)
check(
    "Partial update: key preserved (still masked)",
    anth3["api_key_masked"] != "(not set)" and "****" in anth3["api_key_masked"],
)

# ── 7. Save settings for a second provider ────────────────────────

print("\n=== Settings — multi-provider ===")

code, multi = req("PATCH", "/settings/providers", {
    "providers": [
        {"provider_name": "openai", "api_key": "sk-openai-test-key-999", "enabled": True},
        {"provider_name": "anthropic", "enabled": True},
    ]
})
check("Multi-provider patch returns 200", code == 200)
oai2 = [p for p in multi["providers"] if p["provider_name"] == "openai"][0]
anth4 = [p for p in multi["providers"] if p["provider_name"] == "anthropic"][0]
check("OpenAI now configured", oai2["api_key_masked"] != "(not set)")
check("Anthropic re-enabled", anth4["enabled"] is True)

# ── 8. POST /api/provider-test ────────────────────────────────────

print("\n=== Provider test ===")

# Test configured provider (fake key — should return ok=false with a message)
code, test_result = req("POST", "/provider-test", {"provider": "anthropic"})
check("Test returns 200", code == 200)
check("Test result has ok field", "ok" in test_result)
check("Test result has message", "message" in test_result)
check("Test result has provider", test_result.get("provider") == "anthropic")
# With fake key, should fail gracefully
check("Fake key gives ok=false", test_result["ok"] is False)
check(
    "Error message is descriptive",
    len(test_result.get("message", "")) > 0,
    f"got: '{test_result.get('message', '')}'",
)

# Test unconfigured provider
code2, test2 = req("POST", "/provider-test", {"provider": "gemini"})
check("Unconfigured provider test returns 200", code2 == 200)
check("Unconfigured gives ok=false", test2["ok"] is False)
check(
    "Unconfigured message mentions key",
    "key" in test2.get("message", "").lower() or "not configured" in test2.get("message", "").lower(),
)

# ── 9. Unknown provider ──────────────────────────────────────────

print("\n=== Error handling ===")

code, err = req("POST", "/provider-test", {"provider": "nonexistent"})
check("Unknown provider returns 404", code == 404)

# ── 10. API key safety ────────────────────────────────────────────

print("\n=== API key safety ===")

# Ensure raw key never appears in any response
code, all_settings = req("GET", "/settings/providers")
raw_response = json.dumps(all_settings)
check(
    "Raw key 'sk-ant-fake-key-ABCDEFGH12345678' NOT in response",
    "sk-ant-fake-key-ABCDEFGH12345678" not in raw_response,
)
check(
    "Raw key 'sk-openai-test-key-999' NOT in response",
    "sk-openai-test-key-999" not in raw_response,
)


# ══════════════════════════════════════════════════════════════════
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
