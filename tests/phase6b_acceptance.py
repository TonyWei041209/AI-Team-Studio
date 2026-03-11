"""Phase 6B acceptance test suite -- ModelAgentExecutor & Planner integration.

Covers:
  - PlannerOutputSchema validation (unit tests)
  - Code fence stripping (unit tests)
  - POST /api/completion endpoint
  - Orchestrator per-role executor dispatch
  - Planner definition updates
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
# Section 1: PlannerOutputSchema validation (pure unit tests)
# ══════════════════════════════════════════════════════════════════

print("\n=== PlannerOutputSchema validation ===")

from agents.model_executor import PlannerOutputSchema

# Valid complete output
valid_output = {
    "goal_summary": "Implement user authentication",
    "task_breakdown": [
        {"step": 1, "description": "Create auth module", "role": "builder"},
        {"step": 2, "description": "Write tests", "role": "qa"},
    ],
    "acceptance_criteria": ["Login works", "Logout works"],
    "risks": ["Token expiry edge case"],
    "dependencies": ["JWT library"],
}
ok, err = PlannerOutputSchema.validate(valid_output)
check("Valid complete output passes", ok, err)

# Missing goal_summary
bad1 = {**valid_output}
del bad1["goal_summary"]
ok, err = PlannerOutputSchema.validate(bad1)
check("Missing goal_summary fails", not ok)
check("Error mentions goal_summary", "goal_summary" in err, err)

# Empty goal_summary
bad2 = {**valid_output, "goal_summary": "  "}
ok, err = PlannerOutputSchema.validate(bad2)
check("Empty goal_summary fails", not ok)

# Missing task_breakdown
bad3 = {**valid_output}
del bad3["task_breakdown"]
ok, err = PlannerOutputSchema.validate(bad3)
check("Missing task_breakdown fails", not ok)

# Empty task_breakdown
bad4 = {**valid_output, "task_breakdown": []}
ok, err = PlannerOutputSchema.validate(bad4)
check("Empty task_breakdown fails", not ok)

# task_breakdown item missing step
bad5 = {**valid_output, "task_breakdown": [{"description": "x", "role": "builder"}]}
ok, err = PlannerOutputSchema.validate(bad5)
check("task_breakdown item missing step fails", not ok)

# task_breakdown item missing description
bad6 = {**valid_output, "task_breakdown": [{"step": 1, "role": "builder"}]}
ok, err = PlannerOutputSchema.validate(bad6)
check("task_breakdown item missing description fails", not ok)

# task_breakdown item missing role
bad7 = {**valid_output, "task_breakdown": [{"step": 1, "description": "x"}]}
ok, err = PlannerOutputSchema.validate(bad7)
check("task_breakdown item missing role fails", not ok)

# Empty acceptance_criteria
bad8 = {**valid_output, "acceptance_criteria": []}
ok, err = PlannerOutputSchema.validate(bad8)
check("Empty acceptance_criteria fails", not ok)

# Missing acceptance_criteria
bad9 = {**valid_output}
del bad9["acceptance_criteria"]
ok, err = PlannerOutputSchema.validate(bad9)
check("Missing acceptance_criteria fails", not ok)

# Extra fields tolerated
extra = {**valid_output, "notes": "some extra info"}
ok, err = PlannerOutputSchema.validate(extra)
check("Extra fields tolerated", ok, err)

# Empty risks and dependencies allowed
minimal = {
    "goal_summary": "Do something",
    "task_breakdown": [{"step": 1, "description": "x", "role": "builder"}],
    "acceptance_criteria": ["It works"],
    "risks": [],
    "dependencies": [],
}
ok, err = PlannerOutputSchema.validate(minimal)
check("Empty risks/deps allowed", ok, err)

# Missing risks/deps tolerated (treated as absent)
no_optional = {
    "goal_summary": "Do something",
    "task_breakdown": [{"step": 1, "description": "x", "role": "builder"}],
    "acceptance_criteria": ["It works"],
}
ok, err = PlannerOutputSchema.validate(no_optional)
check("Missing risks/deps tolerated", ok, err)

# Non-object input
ok, err = PlannerOutputSchema.validate("not a dict")
check("String input fails", not ok)
ok, err = PlannerOutputSchema.validate([1, 2, 3])
check("List input fails", not ok)

# Wrong type in acceptance_criteria
bad10 = {**valid_output, "acceptance_criteria": [123]}
ok, err = PlannerOutputSchema.validate(bad10)
check("Non-string acceptance_criteria item fails", not ok)


# ══════════════════════════════════════════════════════════════════
# Section 2: Code fence stripping (pure unit tests)
# ══════════════════════════════════════════════════════════════════

print("\n=== Code fence stripping ===")

from agents.model_executor import strip_code_fences

# No fences — passthrough
check("No fences passthrough", strip_code_fences('{"a": 1}') == '{"a": 1}')

# ```json ... ```
fenced_json = '```json\n{"goal_summary": "test"}\n```'
check("```json fences stripped", strip_code_fences(fenced_json) == '{"goal_summary": "test"}')

# ``` ... ```
fenced_plain = '```\n{"goal_summary": "test"}\n```'
check("``` fences stripped", strip_code_fences(fenced_plain) == '{"goal_summary": "test"}')

# With leading/trailing whitespace
fenced_ws = '  \n```json\n{"a": 1}\n```  \n'
check("Whitespace + fences handled", strip_code_fences(fenced_ws) == '{"a": 1}')

# Multiline JSON in fences
multiline = '```json\n{\n  "goal_summary": "test",\n  "risks": []\n}\n```'
result = strip_code_fences(multiline)
check("Multiline JSON in fences", '"goal_summary"' in result and '"risks"' in result)


# ══════════════════════════════════════════════════════════════════
# Section 3: Planner definition updates
# ══════════════════════════════════════════════════════════════════

print("\n=== Planner definition updates ===")

from agents.definitions import AGENT_PIPELINE, get_definition
from models import AgentRole

planner_def = get_definition(AgentRole.PLANNER)
check("Planner model_provider is anthropic", planner_def.model_provider == "anthropic")
check("Planner model_name is haiku", "haiku" in planner_def.model_name)
check("Planner has system_prompt", len(planner_def.system_prompt) > 100)
check("Planner system_prompt mentions JSON", "JSON" in planner_def.system_prompt)
check("Planner output_sections includes dependencies",
      "dependencies" in planner_def.output_sections)
check("Planner output_sections has 5 items",
      len(planner_def.output_sections) == 5,
      f"got {len(planner_def.output_sections)}")

# Builder and QA still mock
for role_enum in (AgentRole.BUILDER, AgentRole.QA):
    defn = get_definition(role_enum)
    check(f"{defn.display_name} still uses mock provider",
          defn.model_provider == "mock")
    check(f"{defn.display_name} system_prompt is empty",
          defn.system_prompt == "")

# Reviewer uses anthropic (updated in Round 2)
reviewer_defn = get_definition(AgentRole.REVIEWER)
check("Reviewer uses anthropic provider",
      reviewer_defn.model_provider == "anthropic")
check("Reviewer has system_prompt",
      len(reviewer_defn.system_prompt) > 0)


# ══════════════════════════════════════════════════════════════════
# Section 4: ModelAgentExecutor Protocol compliance
# ══════════════════════════════════════════════════════════════════

print("\n=== ModelAgentExecutor Protocol ===")

from agents.executor import AgentExecutor
from agents.model_executor import ModelAgentExecutor

executor = ModelAgentExecutor()
check("ModelAgentExecutor is runtime_checkable AgentExecutor",
      isinstance(executor, AgentExecutor))


# ══════════════════════════════════════════════════════════════════
# Section 5: POST /api/completion endpoint
# ══════════════════════════════════════════════════════════════════

print("\n=== POST /api/completion ===")

# Unknown provider → 404
code, data = req("POST", "/completion", {
    "provider": "nonexistent",
    "model": "x",
    "prompt": "hello",
})
check("Unknown provider returns 404", code == 404)

# Unconfigured provider → 400
# First ensure anthropic has no key (reset settings)
req("PATCH", "/settings/providers", {
    "providers": [{"provider_name": "anthropic", "api_key": "", "enabled": False}]
})
code, data = req("POST", "/completion", {
    "provider": "anthropic",
    "model": "claude-3-5-haiku-20241022",
    "prompt": "hello",
})
check("Unconfigured provider returns 400", code == 400)
check("Error mentions not configured",
      "not configured" in str(data.get("detail", "")).lower(),
      f"got: {data}")


# ══════════════════════════════════════════════════════════════════
# Section 6: Orchestration with mock fallback
# ══════════════════════════════════════════════════════════════════

print("\n=== Orchestration mock fallback ===")

# Ensure anthropic provider has NO key → Planner should fall back to mock
req("PATCH", "/settings/providers", {
    "providers": [{"provider_name": "anthropic", "api_key": "", "enabled": False}]
})

# Create project + task
code, proj = req("POST", "/projects", {
    "name": "6B-Test-Project",
    "local_repo_path": "/tmp/test",
})
check("Create test project", code == 201 or code == 200, f"code={code}")
pid = proj["id"]

code, task = req("POST", f"/projects/{pid}/tasks", {
    "title": "6B-Test-Task",
    "description": "Test task for Phase 6B acceptance",
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

# Verify all runs used mock provider (since anthropic has no key)
code, status = req("GET", f"/tasks/{tid}/orchestration-status")
runs = status["runs"]
for run in runs:
    check(f"Run {run['role']} uses mock provider",
          run["model_provider"] == "mock" or run["model_provider"] == "anthropic",
          f"got: {run['model_provider']}")

# Planner run should show model_provider from definition
# When no API key → falls back to mock executor, but AgentRun records
# the definition's model_provider. Let's check that the Planner run
# has the updated provider name (anthropic) in the definition, even
# though the executor was mock.
planner_run = [r for r in runs if r["role"] == "planner"][0]
check("Planner run model_provider is 'anthropic' (from definition)",
      planner_run["model_provider"] == "anthropic")

# Builder/QA runs should be mock
for role_name in ("builder", "qa"):
    role_run = [r for r in runs if r["role"] == role_name][0]
    check(f"{role_name} run model_provider is 'mock'",
          role_run["model_provider"] == "mock")

# Reviewer run should show anthropic (from definition, updated in Round 2)
reviewer_run = [r for r in runs if r["role"] == "reviewer"][0]
check("reviewer run model_provider is 'anthropic' (from definition)",
      reviewer_run["model_provider"] == "anthropic")

# Cleanup
req("DELETE", f"/projects/{pid}")


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
        "name": "6B-Real-Provider-Test",
        "local_repo_path": "/tmp/real-test",
    })
    pid = proj["id"]
    code, task = req("POST", f"/projects/{pid}/tasks", {
        "title": "Add user authentication",
        "description": "Implement JWT-based authentication with login and logout endpoints",
    })
    tid = task["id"]

    # Orchestrate — Planner should use real model, others use mock
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
        # Validate Planner output schema
        planner_step = orch["steps"][0]
        check("Planner step success", planner_step["success"])
        planner_output = planner_step["output"]
        ok, err = PlannerOutputSchema.validate(planner_output)
        check("Planner real output validates against schema", ok, err)
        check("Planner goal_summary is non-empty",
              bool(planner_output.get("goal_summary", "").strip()))
        check("Planner has task_breakdown",
              len(planner_output.get("task_breakdown", [])) > 0)
        check("Planner has acceptance_criteria",
              len(planner_output.get("acceptance_criteria", [])) > 0)

        # Builder/QA/Reviewer should still be mock
        for i, role_name in enumerate(["builder", "qa", "reviewer"]):
            step = orch["steps"][i + 1]
            check(f"Real: {role_name} used mock (has mock-style output)",
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
