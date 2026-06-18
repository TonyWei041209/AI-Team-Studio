"""Phase 6B Round 2 acceptance test suite -- Reviewer real model integration.

Covers:
  - ReviewerOutputSchema validation (unit tests)
  - Reviewer definition updates
  - ModelAgentExecutor supports Reviewer role
  - Orchestrator wires Reviewer to ModelAgentExecutor
  - Orchestration mock fallback (no API key → mock for all)
  - Conditional real-provider end-to-end test (gated on TEST_ANTHROPIC_KEY)
"""
import json
import os
import sys
import urllib.request
import urllib.error

# Ensure services/runtime is on sys.path for direct imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "runtime"))

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")
results = []


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


# ══════════════════════════════════════════════════════════════════
# Section 1: ReviewerOutputSchema validation (pure unit tests)
# ══════════════════════════════════════════════════════════════════

print("\n=== ReviewerOutputSchema validation ===")

from agents.model_executor import ReviewerOutputSchema

# Valid complete output
valid_output = {
    "decision": "approve",
    "reason": "All acceptance criteria met, tests pass, no issues found.",
    "issues_found": [],
    "confidence": "high",
}
ok, err = ReviewerOutputSchema.validate(valid_output)
check("Valid approve output passes", ok, err)

# Valid with issues
valid_with_issues = {
    "decision": "request_changes",
    "reason": "Found critical bug in authentication logic.",
    "issues_found": [
        {"severity": "critical", "description": "SQL injection vulnerability in login"},
        {"severity": "minor", "description": "Missing docstring on helper function"},
    ],
    "confidence": "high",
}
ok, err = ReviewerOutputSchema.validate(valid_with_issues)
check("Valid request_changes with issues passes", ok, err)

# Missing decision
bad1 = {**valid_output}
del bad1["decision"]
ok, err = ReviewerOutputSchema.validate(bad1)
check("Missing decision fails", not ok)
check("Error mentions decision", "decision" in err, err)

# Invalid decision value
bad2 = {**valid_output, "decision": "block"}
ok, err = ReviewerOutputSchema.validate(bad2)
check("Invalid decision 'block' fails", not ok)

bad2b = {**valid_output, "decision": "APPROVE"}
ok, err = ReviewerOutputSchema.validate(bad2b)
check("Uppercase decision 'APPROVE' fails", not ok)

# Missing reason
bad3 = {**valid_output}
del bad3["reason"]
ok, err = ReviewerOutputSchema.validate(bad3)
check("Missing reason fails", not ok)

# Empty reason
bad4 = {**valid_output, "reason": "  "}
ok, err = ReviewerOutputSchema.validate(bad4)
check("Empty reason fails", not ok)

# Missing issues_found
bad5 = {**valid_output}
del bad5["issues_found"]
ok, err = ReviewerOutputSchema.validate(bad5)
check("Missing issues_found fails", not ok)

# issues_found not a list
bad6 = {**valid_output, "issues_found": "none"}
ok, err = ReviewerOutputSchema.validate(bad6)
check("issues_found as string fails", not ok)

# Issue missing severity
bad7 = {**valid_output, "issues_found": [{"description": "some problem"}]}
ok, err = ReviewerOutputSchema.validate(bad7)
check("Issue missing severity fails", not ok)

# Issue with invalid severity
bad8 = {**valid_output, "issues_found": [{"severity": "blocker", "description": "x"}]}
ok, err = ReviewerOutputSchema.validate(bad8)
check("Issue with invalid severity fails", not ok)

# Issue missing description
bad9 = {**valid_output, "issues_found": [{"severity": "major"}]}
ok, err = ReviewerOutputSchema.validate(bad9)
check("Issue missing description fails", not ok)

# Issue with empty description
bad10 = {**valid_output, "issues_found": [{"severity": "minor", "description": "  "}]}
ok, err = ReviewerOutputSchema.validate(bad10)
check("Issue with empty description fails", not ok)

# Missing confidence
bad11 = {**valid_output}
del bad11["confidence"]
ok, err = ReviewerOutputSchema.validate(bad11)
check("Missing confidence fails", not ok)

# Invalid confidence
bad12 = {**valid_output, "confidence": "very_high"}
ok, err = ReviewerOutputSchema.validate(bad12)
check("Invalid confidence fails", not ok)

# Non-object input
ok, err = ReviewerOutputSchema.validate("not a dict")
check("String input fails", not ok)
ok, err = ReviewerOutputSchema.validate([1, 2, 3])
check("List input fails", not ok)

# Extra fields tolerated
extra = {**valid_output, "summary": "all good", "notes": "extra info"}
ok, err = ReviewerOutputSchema.validate(extra)
check("Extra fields tolerated", ok, err)

# All valid severities accepted
for sev in ("critical", "major", "minor", "nitpick"):
    test = {**valid_output, "issues_found": [{"severity": sev, "description": f"test {sev}"}]}
    ok, err = ReviewerOutputSchema.validate(test)
    check(f"Severity '{sev}' accepted", ok, err)

# All valid confidences accepted
for conf in ("high", "medium", "low"):
    test = {**valid_output, "confidence": conf}
    ok, err = ReviewerOutputSchema.validate(test)
    check(f"Confidence '{conf}' accepted", ok, err)


# ══════════════════════════════════════════════════════════════════
# Section 2: Reviewer definition updates
# ══════════════════════════════════════════════════════════════════

print("\n=== Reviewer definition updates ===")

from agents.definitions import AGENT_PIPELINE, get_definition
from models import AgentRole

reviewer_def = get_definition(AgentRole.REVIEWER)
check("Reviewer model_provider is anthropic", reviewer_def.model_provider == "anthropic")
check("Reviewer model_name is haiku", "haiku" in reviewer_def.model_name)
check("Reviewer has system_prompt", len(reviewer_def.system_prompt) > 100)
check("Reviewer system_prompt mentions JSON", "JSON" in reviewer_def.system_prompt)
check("Reviewer system_prompt mentions decision", "decision" in reviewer_def.system_prompt)
check("Reviewer system_prompt mentions approve", "approve" in reviewer_def.system_prompt)
check("Reviewer system_prompt mentions request_changes",
      "request_changes" in reviewer_def.system_prompt)
check("Reviewer output_sections has 4 items",
      len(reviewer_def.output_sections) == 4,
      f"got {len(reviewer_def.output_sections)}: {reviewer_def.output_sections}")
check("Reviewer output_sections includes decision",
      "decision" in reviewer_def.output_sections)
check("Reviewer output_sections includes confidence",
      "confidence" in reviewer_def.output_sections)

# Builder and QA are now real roles (system prompts wired)
for role_enum in (AgentRole.BUILDER, AgentRole.QA):
    defn = get_definition(role_enum)
    check(f"{defn.display_name} has a system_prompt (real role)",
          len(defn.system_prompt) > 100)

# Planner still has anthropic (from Round 1)
planner_def = get_definition(AgentRole.PLANNER)
check("Planner still uses anthropic", planner_def.model_provider == "anthropic")
check("Planner still has system_prompt", len(planner_def.system_prompt) > 100)


# ══════════════════════════════════════════════════════════════════
# Section 3: ModelAgentExecutor supports Reviewer
# ══════════════════════════════════════════════════════════════════

print("\n=== ModelAgentExecutor Reviewer support ===")

from agents.executor import AgentExecutor
from agents.model_executor import ModelAgentExecutor

executor = ModelAgentExecutor()
check("ModelAgentExecutor is runtime_checkable AgentExecutor",
      isinstance(executor, AgentExecutor))

# REVIEWER should be in supported roles
check("REVIEWER in supported roles",
      AgentRole.REVIEWER in ModelAgentExecutor._SUPPORTED_ROLES)
check("PLANNER in supported roles",
      AgentRole.PLANNER in ModelAgentExecutor._SUPPORTED_ROLES)

# BUILDER and QA are now supported real roles (no longer raise NotImplementedError)
check("BUILDER in supported roles",
      AgentRole.BUILDER in ModelAgentExecutor._SUPPORTED_ROLES)
check("QA in supported roles",
      AgentRole.QA in ModelAgentExecutor._SUPPORTED_ROLES)


# ══════════════════════════════════════════════════════════════════
# Section 4: Orchestration with mock fallback (no API key)
# ══════════════════════════════════════════════════════════════════

print("\n=== Orchestration mock fallback ===")

# Ensure anthropic provider has NO key → all roles fall back to mock
req("PATCH", "/settings/providers", {
    "providers": [{"provider_name": "anthropic", "api_key": "", "enabled": False}]
})

# Create project + task
code, proj = req("POST", "/projects", {
    "name": "6B-R2-Test-Project",
    "local_repo_path": "/tmp/test-r2",
})
check("Create test project", code == 201 or code == 200, f"code={code}")
pid = proj["id"]

code, task = req("POST", f"/projects/{pid}/tasks", {
    "title": "6B-R2-Test-Task",
    "description": "Test task for Phase 6B Round 2 acceptance",
})
check("Create test task", code == 201 or code == 200, f"code={code}")
tid = task["id"]

# Orchestrate — should succeed using MockAgentExecutor for all roles
code, orch = req("POST", f"/tasks/{tid}/orchestrate", {
    "delay_seconds": 0.05,
    "failure_rate": 0.0,
    "rejection_rate": 0.0,
})
check("Orchestration returns 200", code == 200, f"code={code}")
check("Orchestration succeeds (mock fallback)", orch["final_status"] == "done",
      f"got: {orch.get('final_status')}")
check("4 steps executed", len(orch["steps"]) == 4, f"got {len(orch['steps'])}")

# Verify runs
code, status = req("GET", f"/tasks/{tid}/orchestration-status")
runs = status["runs"]

# Planner run should show anthropic (from definition) even though executor was mock
planner_run = [r for r in runs if r["role"] == "planner"][0]
check("Planner run model_provider is 'anthropic' (from definition)",
      planner_run["model_provider"] == "anthropic")

# Builder run shows 'anthropic' from its definition (executor was mock — no key).
# QA run shows 'mock' (QA keeps the mock seed provider; runtime truth is the DB).
builder_run = [r for r in runs if r["role"] == "builder"][0]
check("builder run model_provider is 'anthropic' (from definition)",
      builder_run["model_provider"] == "anthropic")
qa_run = [r for r in runs if r["role"] == "qa"][0]
check("qa run model_provider is 'mock' (from definition)",
      qa_run["model_provider"] == "mock")

# Reviewer run should show anthropic (from definition) even though executor was mock
reviewer_run = [r for r in runs if r["role"] == "reviewer"][0]
check("Reviewer run model_provider is 'anthropic' (from definition)",
      reviewer_run["model_provider"] == "anthropic")

# Cleanup
req("DELETE", f"/projects/{pid}")


# ══════════════════════════════════════════════════════════════════
# Section 5: PlannerOutputSchema still works (Round 1 regression check)
# ══════════════════════════════════════════════════════════════════

print("\n=== PlannerOutputSchema regression check ===")

from agents.model_executor import PlannerOutputSchema

valid_planner = {
    "goal_summary": "Implement feature X",
    "task_breakdown": [
        {"step": 1, "description": "Build it", "role": "builder"},
    ],
    "acceptance_criteria": ["It works"],
    "risks": [],
    "dependencies": [],
}
ok, err = PlannerOutputSchema.validate(valid_planner)
check("PlannerOutputSchema still validates correctly", ok, err)

bad_planner = {"task_breakdown": [], "acceptance_criteria": []}
ok, err = PlannerOutputSchema.validate(bad_planner)
check("PlannerOutputSchema still rejects invalid", not ok)


# ══════════════════════════════════════════════════════════════════
# Section 6: Reviewer decision mapping
# ══════════════════════════════════════════════════════════════════

print("\n=== Reviewer decision mapping ===")

from models import ReviewDecision

# Test that ReviewDecision enum maps correctly
check("ReviewDecision.APPROVE exists", hasattr(ReviewDecision, "APPROVE"))
check("ReviewDecision.REQUEST_CHANGES exists", hasattr(ReviewDecision, "REQUEST_CHANGES"))

# Test ExecutionResult decision normalization
from agents.executor import ExecutionResult

result_approve = ExecutionResult(
    success=True, output={}, decision=ReviewDecision.APPROVE,
)
check("ExecutionResult normalizes APPROVE",
      result_approve.decision == ReviewDecision.APPROVE.value)

result_rc = ExecutionResult(
    success=True, output={}, decision=ReviewDecision.REQUEST_CHANGES,
)
check("ExecutionResult normalizes REQUEST_CHANGES",
      result_rc.decision == ReviewDecision.REQUEST_CHANGES.value)


# ══════════════════════════════════════════════════════════════════
# Section 7: Conditional real-provider test
# ══════════════════════════════════════════════════════════════════

ANTHROPIC_KEY = os.environ.get("TEST_ANTHROPIC_KEY", "")

if ANTHROPIC_KEY:
    print("\n=== Real provider test (TEST_ANTHROPIC_KEY set) ===")

    # Configure anthropic with real key
    req("PATCH", "/settings/providers", {
        "providers": [{
            "provider_name": "anthropic",
            "api_key": ANTHROPIC_KEY,
            "enabled": True,
        }]
    })

    # Create project + task
    code, proj = req("POST", "/projects", {
        "name": "6B-R2-Real-Provider-Test",
        "local_repo_path": "/tmp/real-test-r2",
    })
    pid = proj["id"]
    code, task = req("POST", f"/projects/{pid}/tasks", {
        "title": "Add user authentication",
        "description": "Implement JWT-based authentication with login and logout endpoints",
    })
    tid = task["id"]

    # Orchestrate — Planner and Reviewer use real model, Builder/QA use mock
    code, orch = req("POST", f"/tasks/{tid}/orchestrate", {
        "delay_seconds": 0.05,
        "failure_rate": 0.0,
        "rejection_rate": 0.0,
    })
    check("Real provider orchestration returns 200", code == 200, f"code={code}")
    check("Real provider orchestration succeeds",
          orch["final_status"] == "done",
          f"status={orch.get('final_status')}, error={orch.get('error')}")

    if orch["final_status"] == "done":
        # Validate Planner output
        planner_step = orch["steps"][0]
        check("Planner step success", planner_step["success"])
        planner_output = planner_step["output"]
        ok, err = PlannerOutputSchema.validate(planner_output)
        check("Planner real output validates against schema", ok, err)

        # Validate Reviewer output
        reviewer_step = orch["steps"][3]
        check("Reviewer step success", reviewer_step["success"])
        reviewer_output = reviewer_step["output"]
        ok, err = ReviewerOutputSchema.validate(reviewer_output)
        check("Reviewer real output validates against schema", ok, err)
        check("Reviewer decision is valid",
              reviewer_output.get("decision") in ("approve", "request_changes"),
              f"got: {reviewer_output.get('decision')}")
        check("Reviewer reason is non-empty",
              bool(reviewer_output.get("reason", "").strip()))
        check("Reviewer confidence is valid",
              reviewer_output.get("confidence") in ("high", "medium", "low"),
              f"got: {reviewer_output.get('confidence')}")

        # Builder/QA should still be mock
        for i, role_name in enumerate(["builder", "qa"]):
            step = orch["steps"][i + 1]
            check(f"Real: {role_name} used mock (step success)",
                  step["success"])

    # Cleanup
    req("PATCH", "/settings/providers", {
        "providers": [{"provider_name": "anthropic", "api_key": "", "enabled": False}]
    })
    req("DELETE", f"/projects/{pid}")
else:
    print("\n=== Real provider test SKIPPED (TEST_ANTHROPIC_KEY not set) ===")


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
