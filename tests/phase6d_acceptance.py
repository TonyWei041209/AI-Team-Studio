#!/usr/bin/env python3
"""Phase 6D acceptance tests -- Builder plan-only real model integration.

Sections
--------
1. BuilderOutputSchema validation (unit tests)
2. Builder definition updates
3. Builder role-model PATCH -- allowed
4. Builder role-model PATCH -- QA still blocked
5. Builder executor routing (mock fallback)
6. No-tool-execution guarantee
7. AgentRun output_summary is concise
8. QA still mock
9. Conditional real-provider test (gated on TEST_ANTHROPIC_KEY)

Run
---
  cd services/runtime
  set RUNTIME_DB=<temp_db_path>
  set RUNTIME_PORT=<port>
  python -m uvicorn main:app --port %RUNTIME_PORT%

  set TEST_API_BASE=http://127.0.0.1:<port>/api
  python ../../tests/phase6d_acceptance.py
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
# Section 1: BuilderOutputSchema validation (unit tests)
# ==============================================================
print("\n=== Section 1: BuilderOutputSchema validation ===")

# Import schema directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "runtime"))
from agents.model_executor import BuilderOutputSchema

VALID_BUILDER = {
    "change_summary": "Add user authentication module",
    "proposed_files": [
        {"path": "src/auth.py", "action": "create", "reason": "New authentication module"},
        {"path": "src/main.py", "action": "modify", "reason": "Import and wire auth"},
    ],
    "change_steps": [
        {"step": 1, "description": "Create auth.py with login/logout", "target_file": "src/auth.py"},
        {"step": 2, "description": "Update main.py imports"},
    ],
    "reasoning_summary": "Separate auth into its own module for maintainability",
    "validation_plan": ["Run unit tests", "Test login flow manually"],
    "risk_notes": ["Breaking change if session format changes"],
}

ok, err = BuilderOutputSchema._validate_single_proposal(VALID_BUILDER)
check("Valid complete output passes", ok, err)

# Missing change_summary
bad = {**VALID_BUILDER, "change_summary": ""}
ok, err = BuilderOutputSchema._validate_single_proposal(bad)
check("Empty change_summary fails", not ok)
check("Error mentions change_summary", "change_summary" in err)

# Missing proposed_files
bad = {k: v for k, v in VALID_BUILDER.items() if k != "proposed_files"}
ok, err = BuilderOutputSchema._validate_single_proposal(bad)
check("Missing proposed_files fails", not ok)

# Empty proposed_files
bad = {**VALID_BUILDER, "proposed_files": []}
ok, err = BuilderOutputSchema._validate_single_proposal(bad)
check("Empty proposed_files fails", not ok)

# proposed_files item missing path
bad = {**VALID_BUILDER, "proposed_files": [{"action": "create", "reason": "x"}]}
ok, err = BuilderOutputSchema._validate_single_proposal(bad)
check("proposed_files missing path fails", not ok)
check("Error mentions path", "path" in err)

# Invalid action
bad = {**VALID_BUILDER, "proposed_files": [{"path": "a.py", "action": "rename", "reason": "x"}]}
ok, err = BuilderOutputSchema._validate_single_proposal(bad)
check("Invalid action fails", not ok)
check("Error mentions action", "action" in err)

# Valid actions: create, modify, delete
for action in ("create", "modify", "delete"):
    test_data = {**VALID_BUILDER, "proposed_files": [{"path": "a.py", "action": action, "reason": "test"}]}
    ok, _ = BuilderOutputSchema._validate_single_proposal(test_data)
    check(f"Action '{action}' accepted", ok)

# Missing change_steps
bad = {k: v for k, v in VALID_BUILDER.items() if k != "change_steps"}
ok, err = BuilderOutputSchema._validate_single_proposal(bad)
check("Missing change_steps fails", not ok)

# change_steps item missing step
bad = {**VALID_BUILDER, "change_steps": [{"description": "do something"}]}
ok, err = BuilderOutputSchema._validate_single_proposal(bad)
check("change_steps missing step fails", not ok)

# Missing reasoning_summary
bad = {**VALID_BUILDER, "reasoning_summary": ""}
ok, err = BuilderOutputSchema._validate_single_proposal(bad)
check("Empty reasoning_summary fails", not ok)

# Missing validation_plan
bad = {**VALID_BUILDER, "validation_plan": []}
ok, err = BuilderOutputSchema._validate_single_proposal(bad)
check("Empty validation_plan fails", not ok)

# risk_notes optional (missing tolerated)
good = {k: v for k, v in VALID_BUILDER.items() if k != "risk_notes"}
ok, err = BuilderOutputSchema._validate_single_proposal(good)
check("Missing risk_notes tolerated", ok)

# risk_notes can be empty
good = {**VALID_BUILDER, "risk_notes": []}
ok, err = BuilderOutputSchema._validate_single_proposal(good)
check("Empty risk_notes allowed", ok)

# Extra fields tolerated
good = {**VALID_BUILDER, "extra_field": "extra"}
ok, err = BuilderOutputSchema._validate_single_proposal(good)
check("Extra fields tolerated", ok)

# Non-dict input
ok, err = BuilderOutputSchema._validate_single_proposal("string")
check("String input fails", not ok)

ok, err = BuilderOutputSchema._validate_single_proposal([1, 2])
check("List input fails", not ok)


# ==============================================================
# Section 2: Builder definition updates
# ==============================================================
print("\n=== Section 2: Builder definition updates ===")

from agents.definitions import get_definition
from models import AgentRole

builder_def = get_definition(AgentRole.BUILDER)
check("Builder model_provider is anthropic", builder_def.model_provider == "anthropic")
check("Builder has system_prompt", bool(builder_def.system_prompt))
check("Builder system_prompt mentions JSON", "JSON" in builder_def.system_prompt)
check("Builder system_prompt mentions plan-only", "plan-only" in builder_def.system_prompt.lower() or "plan only" in builder_def.system_prompt.lower() or "MUST NOT execute" in builder_def.system_prompt)
check("Builder system_prompt forbids file modifications", "file modification" in builder_def.system_prompt.lower() or "MUST NOT execute" in builder_def.system_prompt)
check("Builder system_prompt forbids fabrication", "fabricat" in builder_def.system_prompt.lower() or "MUST NOT claim" in builder_def.system_prompt)
check("Builder output_sections includes change_summary", "change_summary" in builder_def.output_sections)
check("Builder output_sections includes proposed_files", "proposed_files" in builder_def.output_sections)
check("Builder output_sections has at least 6 core items", len(builder_def.output_sections) >= 6 and all(
    f in builder_def.output_sections for f in ["change_summary", "proposed_files", "change_steps", "reasoning_summary", "validation_plan", "risk_notes"]
))

# QA-Real: QA is now a real role (system_prompt wired). Its seed model_provider
# stays "mock" — runtime provider/model come from role_model_settings (DB).
qa_def = get_definition(AgentRole.QA)
check("QA seed model_provider is still mock (DB is runtime truth)", qa_def.model_provider == "mock")
check("QA now has a system_prompt (real role)", len(qa_def.system_prompt) > 100)

# Planner/Reviewer unchanged
planner_def = get_definition(AgentRole.PLANNER)
check("Planner still has system_prompt", bool(planner_def.system_prompt))
reviewer_def = get_definition(AgentRole.REVIEWER)
check("Reviewer still has system_prompt", bool(reviewer_def.system_prompt))


# ==============================================================
# Section 3: Builder role-model PATCH -- allowed
# ==============================================================
print("\n=== Section 3: Builder role-model PATCH -- allowed ===")

code, data = GET("/settings/role-models")
check("GET role-models returns 200", code == 200)

# Builder should now be configurable
code, data = PATCH("/settings/role-models", {
    "role_models": [
        {"role": "builder", "provider": "anthropic", "model": "claude-sonnet-4-20250514", "enabled": True}
    ]
})
check("Builder enable real -> 200 (allowed in 6D)", code == 200)
rm = data.get("role_models", {})
check("Builder provider updated", rm.get("builder", {}).get("provider") == "anthropic")
check("Builder model updated", rm.get("builder", {}).get("model") == "claude-sonnet-4-20250514")
check("Builder enabled", rm.get("builder", {}).get("enabled") is True)

# Disable builder back for orchestration tests
PATCH("/settings/role-models", {
    "role_models": [{"role": "builder", "provider": "mock", "model": "", "enabled": False}]
})


# ==============================================================
# Section 4: Builder role-model PATCH -- QA allowed (static review)
# ==============================================================
print("\n=== Section 4: QA allowed (static review) ===")

code, data = PATCH("/settings/role-models", {
    "role_models": [{"role": "qa", "provider": "anthropic", "model": "claude-sonnet-4-20250514", "enabled": True}]
})
check("QA enable real -> 200 (static-review allowed)", code == 200)
check("QA now enabled with real provider",
      data.get("role_models", {}).get("qa", {}).get("enabled") is True)
# Reset QA to mock so later sections see the disabled seed default
PATCH("/settings/role-models", {
    "role_models": [{"role": "qa", "provider": "mock", "model": "", "enabled": False}]
})


# ==============================================================
# Section 5: Builder executor routing (mock fallback)
# ==============================================================
print("\n=== Section 5: Builder executor routing (mock fallback) ===")

# Ensure builder is disabled -> should use mock
PATCH("/settings/role-models", {
    "role_models": [{"role": "builder", "provider": "mock", "model": "", "enabled": False}]
})

# Create project + task
code, proj = POST("/projects", {"name": "6D-test", "local_repo_path": "/tmp/6d"})
if code not in (200, 201):
    print(f"  !! Could not create project (code={code})")
else:
    pid = proj["id"]
    code, task = POST(f"/projects/{pid}/tasks", {"title": "6D acceptance task"})
    if code in (200, 201):
        tid = task["id"]
        code, result = POST(f"/tasks/{tid}/orchestrate", {"delay_seconds": 0.1})
        check("Orchestration completes (mock)", code == 200)
        check("Task reaches terminal state", result.get("final_status") in ("done", "failed"))
        steps = result.get("steps", [])
        check("Has 4 pipeline steps", len(steps) >= 4)

        # Builder step should be from mock
        if len(steps) >= 2:
            builder_step = steps[1]
            check("Builder step completed", builder_step.get("success") is True or builder_step.get("status") in ("completed", "failed"))
    else:
        print(f"  !! Could not create task (code={code})")


# ==============================================================
# Section 6: No-tool-execution guarantee
# ==============================================================
print("\n=== Section 6: No-tool-execution guarantee ===")

# After orchestration above, check:
# 1. No ApprovalRequests were created (no tool execution triggers approvals)
code, approvals = GET("/approvals/pending")
check("No pending approvals from orchestration", code == 200 and len(approvals) == 0,
      f"got {len(approvals) if isinstance(approvals, list) else 'error'} approvals")

# 2. No tool_audit log events from builder
code, logs = GET("/logs/recent?limit=100")
if code == 200:
    tool_audit_logs = [l for l in logs if l.get("source") == "tool_audit"]
    check("No tool_audit log events", len(tool_audit_logs) == 0,
          f"found {len(tool_audit_logs)} tool_audit events")
else:
    check("Could read logs", False, f"code={code}")


# ==============================================================
# Section 7: AgentRun output_summary is concise
# ==============================================================
print("\n=== Section 7: AgentRun output_summary is concise ===")

# Check from the orchestration above
if 'tid' in dir() or 'tid' in locals():
    code, status_data = GET(f"/tasks/{tid}/orchestration-status")
    if code == 200:
        runs = status_data.get("runs", [])
        for run in runs:
            summary = run.get("output_summary", "")
            check(f"Run {run.get('role', '?')} summary <= 2000 chars",
                  len(summary) <= 2000,
                  f"len={len(summary)}")
    else:
        check("Could read orchestration status", False)


# ==============================================================
# Section 8: QA still mock
# ==============================================================
print("\n=== Section 8: QA still mock ===")

code, data = GET("/settings/role-models")
if code == 200:
    qa_cfg = data.get("role_models", {}).get("qa", {})
    check("QA provider is mock", qa_cfg.get("provider") == "mock")
    check("QA enabled is false", qa_cfg.get("enabled") is False)


# ==============================================================
# Section 9: ModelAgentExecutor Builder support check
# ==============================================================
print("\n=== Section 9: ModelAgentExecutor Builder support ===")

from agents.model_executor import ModelAgentExecutor

check("ModelAgentExecutor._SUPPORTED_ROLES includes BUILDER",
      AgentRole.BUILDER in ModelAgentExecutor._SUPPORTED_ROLES)
check("ModelAgentExecutor._SUPPORTED_ROLES includes PLANNER",
      AgentRole.PLANNER in ModelAgentExecutor._SUPPORTED_ROLES)
check("ModelAgentExecutor._SUPPORTED_ROLES includes REVIEWER",
      AgentRole.REVIEWER in ModelAgentExecutor._SUPPORTED_ROLES)

# QA-Real: QA is now a supported real role (no longer raises NotImplementedError)
check("ModelAgentExecutor._SUPPORTED_ROLES includes QA",
      AgentRole.QA in ModelAgentExecutor._SUPPORTED_ROLES)


# ==============================================================
# Section 10: Conditional real-provider test
# ==============================================================
print("\n=== Section 10: Real-provider integration (conditional) ===")

test_key = os.environ.get("TEST_ANTHROPIC_KEY", "")
if not test_key:
    print("  - Skipped -- TEST_ANTHROPIC_KEY not set")
else:
    # Configure anthropic provider with real key
    PATCH("/settings/providers", {
        "providers": [{"provider_name": "anthropic", "api_key": test_key, "enabled": True}]
    })

    # Enable builder with real model
    PATCH("/settings/role-models", {
        "role_models": [
            {"role": "planner", "provider": "anthropic", "model": "claude-3-5-haiku-20241022", "enabled": True},
            {"role": "builder", "provider": "anthropic", "model": "claude-3-5-haiku-20241022", "enabled": True},
            {"role": "reviewer", "provider": "anthropic", "model": "claude-3-5-haiku-20241022", "enabled": True},
        ]
    })

    # Create project + task for real orchestration
    code, proj = POST("/projects", {
        "name": "6D-real-test", "local_repo_path": "/tmp/6d-real"
    })
    if code not in (200, 201):
        print(f"  !! Could not create project (code={code})")
    else:
        pid = proj["id"]
        code, task = POST(f"/projects/{pid}/tasks", {
            "title": "Builder plan-only test",
            "description": "Test task for Phase 6D Builder plan-only mode."
        })
        if code not in (200, 201):
            print(f"  !! Could not create task (code={code})")
        else:
            tid = task["id"]
            code, result = POST(f"/tasks/{tid}/orchestrate", {"delay_seconds": 0.1})
            check("Real orchestration completes", code == 200)
            final = result.get("final_status", "")
            check("Real orchestration -> done or failed", final in ("done", "failed"))

            steps = result.get("steps", [])
            if len(steps) >= 2:
                builder_step = steps[1]
                builder_output = builder_step.get("output", {})
                check("Builder step completed",
                      builder_step.get("success") is True or builder_step.get("status") in ("completed", "failed"))

                # If builder succeeded with real model, check schema
                if builder_step.get("success"):
                    check("Builder output has change_summary",
                          isinstance(builder_output.get("change_summary"), str) and bool(builder_output["change_summary"]))
                    check("Builder output has proposed_files",
                          isinstance(builder_output.get("proposed_files"), list) and len(builder_output["proposed_files"]) > 0)
                    check("Builder output has change_steps",
                          isinstance(builder_output.get("change_steps"), list) and len(builder_output["change_steps"]) > 0)
                    check("Builder output has reasoning_summary",
                          isinstance(builder_output.get("reasoning_summary"), str) and bool(builder_output["reasoning_summary"]))
                    check("Builder output has validation_plan",
                          isinstance(builder_output.get("validation_plan"), list) and len(builder_output["validation_plan"]) > 0)

            # Verify no tool execution happened
            code2, approvals2 = GET("/approvals/pending")
            check("No approvals from real orchestration", code2 == 200 and len(approvals2) == 0)


# ==============================================================
# Summary
# ==============================================================
print(f"\n{'=' * 60}")
print(f"Phase 6D Acceptance: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
print(f"{'=' * 60}")
sys.exit(1 if FAIL else 0)
