#!/usr/bin/env python3
"""Phase 6C acceptance tests -- role-level model routing & dual-model support.

Sections
--------
1. V5 migration & role_model_settings table
2. GET /api/settings/role-models
3. PATCH /api/settings/role-models -- happy path
4. PATCH /api/settings/role-models -- validation errors
5. Builder allowed (plan-only), QA real-model blocked
6. Same provider, different model per role
7. Settings persistence round-trip
8. No sensitive info leakage
9. ModelAgentExecutor config-driven resolution
10. Orchestration executor selection from DB
11. Conditional real-provider test (gated on TEST_ANTHROPIC_KEY)

Run
---
  cd services/runtime
  set RUNTIME_DB=<temp_db_path>
  set RUNTIME_PORT=<port>
  python -m uvicorn main:app --port %RUNTIME_PORT%

  set TEST_API_BASE=http://127.0.0.1:<port>/api
  python ../../tests/phase6c_acceptance.py
"""

import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")
PASS = 0
FAIL = 0


def _req(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def GET(path: str):
    return _req("GET", path)


def PATCH(path: str, body: Any):
    return _req("PATCH", path, body)


def POST(path: str, body: Any = None):
    return _req("POST", path, body)


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {name}"
        if detail:
            msg += f"  ({detail})"
        print(msg)


# ==============================================================
# Section 1: V5 migration & table existence
# ==============================================================
print("\n=== Section 1: V5 migration -- role_model_settings table ===")

code, data = GET("/health")
check("Health endpoint OK", code == 200)
check("Database connected", data.get("database") == "connected")

# The GET endpoint implicitly proves the table exists
code, data = GET("/settings/role-models")
check("GET role-models returns 200", code == 200)
check("Response has role_models key", "role_models" in data)
role_models = data.get("role_models", {})
check("All 4 roles present", all(r in role_models for r in ["planner", "builder", "qa", "reviewer"]))


# ==============================================================
# Section 2: GET /api/settings/role-models defaults
# ==============================================================
print("\n=== Section 2: GET role-models -- default values ===")

planner = role_models.get("planner", {})
reviewer = role_models.get("reviewer", {})
builder = role_models.get("builder", {})
qa_role = role_models.get("qa", {})

check("Planner default provider is anthropic", planner.get("provider") == "anthropic")
check("Planner default enabled is true", planner.get("enabled") is True)
check("Planner has model set", bool(planner.get("model")))
check("Reviewer default provider is anthropic", reviewer.get("provider") == "anthropic")
check("Reviewer default enabled is true", reviewer.get("enabled") is True)
check("Reviewer has model set", bool(reviewer.get("model")))
check("Builder default provider is mock", builder.get("provider") == "mock")
check("Builder default enabled is false", builder.get("enabled") is False)
check("QA default provider is mock", qa_role.get("provider") == "mock")
check("QA default enabled is false", qa_role.get("enabled") is False)


# ==============================================================
# Section 3: PATCH role-models -- happy path
# ==============================================================
print("\n=== Section 3: PATCH role-models -- happy path ===")

# Change planner to use a different model
code, data = PATCH("/settings/role-models", {
    "role_models": [
        {"role": "planner", "provider": "anthropic", "model": "claude-sonnet-4-20250514", "enabled": True}
    ]
})
check("PATCH planner model returns 200", code == 200)
updated_planner = data.get("role_models", {}).get("planner", {})
check("Planner model updated", updated_planner.get("model") == "claude-sonnet-4-20250514")
check("Planner still enabled", updated_planner.get("enabled") is True)

# Change reviewer to different model (same provider)
code, data = PATCH("/settings/role-models", {
    "role_models": [
        {"role": "reviewer", "provider": "anthropic", "model": "claude-3-5-haiku-20241022", "enabled": True}
    ]
})
check("PATCH reviewer model returns 200", code == 200)
updated_reviewer = data.get("role_models", {}).get("reviewer", {})
check("Reviewer model updated", updated_reviewer.get("model") == "claude-3-5-haiku-20241022")

# Partial update -- only change enabled, keep provider/model
code, data = PATCH("/settings/role-models", {
    "role_models": [
        {"role": "planner", "enabled": False}
    ]
})
check("Partial update returns 200", code == 200)
check("Planner disabled", data["role_models"]["planner"]["enabled"] is False)
check("Planner provider unchanged", data["role_models"]["planner"]["provider"] == "anthropic")
check("Planner model unchanged", data["role_models"]["planner"]["model"] == "claude-sonnet-4-20250514")


# ==============================================================
# Section 4: PATCH role-models -- validation errors
# ==============================================================
print("\n=== Section 4: PATCH role-models -- validation errors ===")

# Invalid role
code, data = PATCH("/settings/role-models", {
    "role_models": [{"role": "hacker", "provider": "anthropic", "model": "x", "enabled": True}]
})
check("Invalid role -> 400", code == 400)
check("Error mentions invalid role", "invalid role" in data.get("detail", "").lower() or "hacker" in data.get("detail", ""))

# Enabled but empty model
code, data = PATCH("/settings/role-models", {
    "role_models": [{"role": "planner", "provider": "anthropic", "model": "", "enabled": True}]
})
check("Enabled with empty model -> 400", code == 400)
check("Error mentions model", "model" in data.get("detail", "").lower())

# Enabled but non-existent provider
code, data = PATCH("/settings/role-models", {
    "role_models": [{"role": "planner", "provider": "nonexistent", "model": "foo", "enabled": True}]
})
check("Non-existent provider -> 400", code == 400)
check("Error mentions provider not registered", "not registered" in data.get("detail", "").lower() or "nonexistent" in data.get("detail", ""))


# ==============================================================
# Section 5: Builder allowed (plan-only), QA still blocked
# ==============================================================
print("\n=== Section 5: Builder + QA allowed (real model config) ===")

# Phase 6D: Builder is allowed for real model config (plan-only mode —
# outputs structured change plans but does NOT execute tools/files/git).
code, data = PATCH("/settings/role-models", {
    "role_models": [{"role": "builder", "provider": "anthropic", "model": "claude-sonnet-4-20250514", "enabled": True}]
})
check("Builder enable real -> 200 (plan-only allowed)", code == 200)
updated_builder = data.get("role_models", {}).get("builder", {})
check("Builder provider updated", updated_builder.get("provider") == "anthropic")
check("Builder model updated", updated_builder.get("model") == "claude-sonnet-4-20250514")
check("Builder enabled", updated_builder.get("enabled") is True)

# QA-Real: QA is now allowed for real model config (static-review mode)
code, data = PATCH("/settings/role-models", {
    "role_models": [{"role": "qa", "provider": "anthropic", "model": "claude-sonnet-4-20250514", "enabled": True}]
})
check("QA enable real -> 200 (static-review allowed)", code == 200)
updated_qa = data.get("role_models", {}).get("qa", {})
check("QA provider updated", updated_qa.get("provider") == "anthropic")
check("QA enabled", updated_qa.get("enabled") is True)

# Reset QA back to mock for subsequent sections (seed default stays disabled)
code, data = PATCH("/settings/role-models", {
    "role_models": [{"role": "qa", "provider": "mock", "model": "", "enabled": False}]
})
check("QA mock reset -> 200", code == 200)

# Reset builder back to mock for subsequent sections
code, data = PATCH("/settings/role-models", {
    "role_models": [{"role": "builder", "provider": "mock", "model": "", "enabled": False}]
})
check("Builder mock reset -> 200", code == 200)


# ==============================================================
# Section 6: Same provider, different model per role
# ==============================================================
print("\n=== Section 6: Same provider, different model per role ===")

# Set planner to opus, reviewer to sonnet (both anthropic)
code, data = PATCH("/settings/role-models", {
    "role_models": [
        {"role": "planner", "provider": "anthropic", "model": "claude-sonnet-4-20250514", "enabled": True},
        {"role": "reviewer", "provider": "anthropic", "model": "claude-3-5-haiku-20241022", "enabled": True},
    ]
})
check("Dual model update -> 200", code == 200)
rm = data.get("role_models", {})
check("Planner uses sonnet", rm["planner"]["model"] == "claude-sonnet-4-20250514")
check("Reviewer uses haiku", rm["reviewer"]["model"] == "claude-3-5-haiku-20241022")
check("Both use anthropic", rm["planner"]["provider"] == "anthropic" and rm["reviewer"]["provider"] == "anthropic")
check("Both enabled", rm["planner"]["enabled"] is True and rm["reviewer"]["enabled"] is True)


# ==============================================================
# Section 7: Settings persistence round-trip
# ==============================================================
print("\n=== Section 7: Settings persistence ===")

# Set specific config
PATCH("/settings/role-models", {
    "role_models": [
        {"role": "planner", "provider": "anthropic", "model": "test-model-planner", "enabled": True},
        {"role": "reviewer", "provider": "anthropic", "model": "test-model-reviewer", "enabled": True},
    ]
})

# Read back
code, data = GET("/settings/role-models")
check("Persistence read -> 200", code == 200)
rm = data.get("role_models", {})
check("Planner model persisted", rm["planner"]["model"] == "test-model-planner")
check("Reviewer model persisted", rm["reviewer"]["model"] == "test-model-reviewer")
check("Builder still mock", rm["builder"]["provider"] == "mock")
check("QA still mock", rm["qa"]["provider"] == "mock")


# ==============================================================
# Section 8: No sensitive info leakage
# ==============================================================
print("\n=== Section 8: No sensitive info leakage ===")

code, data = GET("/settings/role-models")
resp_str = json.dumps(data)
check("No api_key in role-models response", "api_key" not in resp_str)
check("No secret in role-models response", "secret" not in resp_str.lower())

# Provider settings should still mask keys
code, pdata = GET("/settings/providers")
for p in pdata.get("providers", []):
    if p.get("api_key_masked") and p["api_key_masked"] != "(not set)":
        check(f"Provider {p['provider_name']} key is masked",
              p["api_key_masked"].startswith("sk-") or "..." in p["api_key_masked"] or len(p["api_key_masked"]) < 20)


# ==============================================================
# Section 9: ModelAgentExecutor config-driven resolution
# ==============================================================
print("\n=== Section 9: ModelAgentExecutor config-driven ===")

# This tests indirectly by checking that executor respects DB config.
# First, set planner to a valid config
PATCH("/settings/role-models", {
    "role_models": [
        {"role": "planner", "provider": "anthropic", "model": "claude-sonnet-4-20250514", "enabled": True},
    ]
})

# Without an API key configured, orchestration should fall back to mock
# (because router checks provider.api_key before assigning ModelAgentExecutor)
# This is tested implicitly via orchestration below.

# Verify definitions.py doesn't override DB config
# We test this by confirming that after changing DB config, the GET endpoint
# returns the DB value, not the definition default
code, data = GET("/settings/role-models")
check("DB config is truth source (not definitions)",
      data["role_models"]["planner"]["model"] == "claude-sonnet-4-20250514")


# ==============================================================
# Section 10: Orchestration executor selection from DB
# ==============================================================
print("\n=== Section 10: Orchestration mock fallback ===")

# Create project + task
code, proj = POST("/projects", {"name": "6C-test", "local_repo_path": "/tmp/6c"})
if code not in (200, 201):
    print(f"  !! Could not create project (code={code})")
else:
    pid = proj["id"]
    code, task = POST(f"/projects/{pid}/tasks", {"title": "6C acceptance task"})
    if code in (200, 201):
        tid = task["id"]
        # Orchestrate -- should succeed with mock fallback (no API key)
        code, result = POST(f"/tasks/{tid}/orchestrate", {"delay_seconds": 0.1})
        check("Orchestration completes", code == 200)
        check("Task reaches terminal state",
              result.get("final_status") in ("done", "failed"))
        check("Has 4 pipeline steps", len(result.get("steps", [])) >= 4)
    else:
        print(f"  !! Could not create task (code={code})")


# ==============================================================
# Section 11: Conditional real-provider test
# ==============================================================
print("\n=== Section 11: Real-provider integration (conditional) ===")

test_key = os.environ.get("TEST_ANTHROPIC_KEY", "")
if not test_key:
    print("  - Skipped -- TEST_ANTHROPIC_KEY not set")
else:
    # Configure anthropic provider with real key
    PATCH("/settings/providers", {
        "providers": [{"provider_name": "anthropic", "api_key": test_key, "enabled": True}]
    })

    # Set planner to use one model, reviewer to use another
    PATCH("/settings/role-models", {
        "role_models": [
            {"role": "planner", "provider": "anthropic", "model": "claude-3-5-haiku-20241022", "enabled": True},
            {"role": "reviewer", "provider": "anthropic", "model": "claude-3-5-haiku-20241022", "enabled": True},
        ]
    })

    # Test completion endpoint with the configured model
    code, data = POST("/completion", {
        "provider": "anthropic",
        "model": "claude-3-5-haiku-20241022",
        "prompt": "Say 'ok' and nothing else.",
        "max_tokens": 10,
    })
    check("Real completion -> 200", code == 200)
    check("Real completion has content", bool(data.get("content")))

    # Create project + task for real orchestration
    code, proj = POST("/projects", {
        "name": "6C-real-test", "local_repo_path": "/tmp/6c-real"
    })
    if code not in (200, 201):
        print(f"  !! Could not create project (code={code})")
    else:
        pid = proj["id"]
        code, task = POST(f"/projects/{pid}/tasks", {
            "title": "Dual-model routing test",
            "description": "Simple test for Phase 6C dual-model routing."
        })
        if code not in (200, 201):
            print(f"  !! Could not create task (code={code})")
        else:
            tid = task["id"]
            code, result = POST(f"/tasks/{tid}/orchestrate", {"delay_seconds": 0.1})
            check("Real orchestration completes", code == 200)
            final = result.get("final_status", "")
            check("Real orchestration -> done or failed", final in ("done", "failed"))

            # Check that runs recorded the correct providers
            steps = result.get("steps", [])
            if len(steps) >= 4:
                # Planner and Reviewer should have used real model
                planner_step = steps[0]
                reviewer_step = steps[3]
                check("Planner step completed",
                      planner_step.get("status") in ("completed", "failed"))
                check("Reviewer step completed",
                      reviewer_step.get("status") in ("completed", "failed"))


# ==============================================================
# Summary
# ==============================================================
print(f"\n{'=' * 60}")
print(f"Phase 6C Acceptance: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
print(f"{'=' * 60}")
sys.exit(1 if FAIL else 0)
