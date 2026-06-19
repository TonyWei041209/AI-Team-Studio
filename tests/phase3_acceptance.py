"""Phase 3 acceptance test suite — Agent & Orchestrator."""
import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")
results = []


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    rq = urllib.request.Request(url, data=data, method=method)
    rq.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(rq)
        code = resp.getcode()
        text = resp.read().decode()
        return code, json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        code = e.code
        text = e.read().decode()
        try:
            return code, json.loads(text)
        except Exception:
            return code, {"raw": text}


def test(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append((name, status, detail))
    extra = ""
    if not passed and detail:
        extra = f" -- {detail}"
    print(f"  [{status}] {name}{extra}")


print("=" * 70)
print("PHASE 3 ACCEPTANCE TEST SUITE")
print("=" * 70)

# ── Setup: create a project ──
code, proj = req("POST", "/projects", {
    "name": "Phase3 Test Project",
    "local_repo_path": "/tmp/p3-test",
})
PID = proj["id"]

# ================================================================
# TEST 1: Happy path — full pipeline completes with done
# ================================================================
print("\n-- 1. Happy Path (Planner -> Builder -> QA -> Reviewer APPROVE) --")

code, task1 = req("POST", f"/projects/{PID}/tasks", {
    "title": "Implement user login",
    "description": "Add login form with validation",
    "priority": "high",
})
TID1 = task1["id"]
test("Create pending task", code == 201 and task1["status"] == "pending")

# Orchestrate with zero delay for speed
code, orch = req("POST", f"/tasks/{TID1}/orchestrate", {
    "delay_seconds": 0.05,
    "failure_rate": 0.0,
    "rejection_rate": 0.0,
})
test("Orchestrate returns 200", code == 200, f"code={code}")
test("Final status is done", orch.get("final_status") == "done",
     f"got: {orch.get('final_status')}")
test("5 steps executed", len(orch.get("steps", [])) == 5,
     f"got: {len(orch.get('steps', []))}")

# Verify step roles in order
step_roles = [s["role"] for s in orch.get("steps", [])]
test("Steps in order: planner,builder,qa,security_reviewer,reviewer",
     step_roles == ["planner", "builder", "qa", "security_reviewer", "reviewer"],
     f"got: {step_roles}")

# Verify all steps succeeded
all_success = all(s["success"] for s in orch.get("steps", []))
test("All steps succeeded", all_success)

# Verify Reviewer decision
reviewer_step = orch.get("steps", [])[-1] if orch.get("steps") else {}
test("Reviewer decision is APPROVE",
     reviewer_step.get("decision") == "APPROVE",
     f"got: {reviewer_step.get('decision')}")

# Verify no error
test("No error in result", orch.get("error") is None,
     f"got: {orch.get('error')}")

# Verify task status via GET
code, task_after = req("GET", f"/tasks/{TID1}")
test("Task status is done", task_after.get("status") == "done",
     f"got: {task_after.get('status')}")

# Verify AgentRun records
code, runs = req("GET", f"/tasks/{TID1}/runs")
test("5 AgentRun records created", len(runs) == 5, f"got: {len(runs)}")

run_statuses = [r["status"] for r in runs]
test("All runs completed", all(s == "completed" for s in run_statuses),
     f"got: {run_statuses}")

# /api/tasks/{id}/runs returns runs in chronological (created_at ASC) order
run_roles = [r["role"] for r in runs]
test("Run roles match pipeline", run_roles == ["planner", "builder", "qa", "security_reviewer", "reviewer"],
     f"got: {run_roles}")

# Verify started_at and ended_at are set
for r in runs:
    if r["started_at"] is None or r["ended_at"] is None:
        test(f"Run {r['role']} has timestamps", False,
             f"started={r['started_at']}, ended={r['ended_at']}")
        break
else:
    test("All runs have start/end timestamps", True)

# Verify output_summary is non-empty JSON
for r in runs:
    try:
        out = json.loads(r["output_summary"])
        if not out:
            test(f"Run {r['role']} has output", False, "output is empty")
            break
    except Exception as e:
        test(f"Run {r['role']} output is JSON", False, str(e))
        break
else:
    test("All runs have valid JSON output", True)

# Verify log events were created
code, logs = req("GET", f"/tasks/{TID1}/logs")
test("Logs created for task", len(logs) >= 8,
     f"got {len(logs)} logs (expect >= 8: start/end per role + orchestrator)")

# Verify orchestration-status endpoint
code, status = req("GET", f"/tasks/{TID1}/orchestration-status")
test("Orchestration status returns 200", code == 200)
test("Status shows done", status.get("task_status") == "done")
test("Status is_complete true", status.get("is_complete") is True)
test("Status current_role is null", status.get("current_role") is None)
test("Status has runs", len(status.get("runs", [])) == 5)

# ================================================================
# TEST 2: Failure path — agent fails
# ================================================================
print("\n-- 2. Failure Path (failure_rate=1.0) --")

code, task2 = req("POST", f"/projects/{PID}/tasks", {
    "title": "This will fail",
    "priority": "medium",
})
TID2 = task2["id"]

code, orch2 = req("POST", f"/tasks/{TID2}/orchestrate", {
    "delay_seconds": 0.01,
    "failure_rate": 1.0,
    "rejection_rate": 0.0,
})
test("Failure orchestration returns 200", code == 200)
test("Final status is failed", orch2.get("final_status") == "failed",
     f"got: {orch2.get('final_status')}")
test("Only 1 step (Planner fails immediately)", len(orch2.get("steps", [])) == 1,
     f"got: {len(orch2.get('steps', []))}")
test("Error message present", orch2.get("error") is not None)

# Verify task status
code, t2 = req("GET", f"/tasks/{TID2}")
test("Task is failed", t2.get("status") == "failed")

# Verify 1 failed run
code, runs2 = req("GET", f"/tasks/{TID2}/runs")
test("1 run created", len(runs2) == 1)
test("Run status is failed", runs2[0]["status"] == "failed" if runs2 else False)

# ================================================================
# TEST 3: Rejection path — Reviewer always rejects
# ================================================================
print("\n-- 3. Rejection Path (rejection_rate=1.0, max 3 rejections) --")

code, task3 = req("POST", f"/projects/{PID}/tasks", {
    "title": "Will be rejected repeatedly",
    "priority": "medium",
})
TID3 = task3["id"]

code, orch3 = req("POST", f"/tasks/{TID3}/orchestrate", {
    "delay_seconds": 0.01,
    "failure_rate": 0.0,
    "rejection_rate": 1.0,
})
test("Rejection orchestration returns 200", code == 200)
test("Final status is failed (max rejections)",
     orch3.get("final_status") == "failed",
     f"got: {orch3.get('final_status')}")

# Should have: Planner(1) + 3x(Builder+QA+Reviewer) = 1+9 = 10 steps
steps3 = orch3.get("steps", [])
test("Multiple steps from retry loops",
     len(steps3) > 4,
     f"got: {len(steps3)} steps")

# Count how many reviewer steps
reviewer_steps = [s for s in steps3 if s["role"] == "reviewer"]
test("3 reviewer attempts (max rejections)",
     len(reviewer_steps) == 3,
     f"got: {len(reviewer_steps)}")

# All reviewer decisions should be REQUEST_CHANGES
reviewer_decisions = [s.get("decision") for s in reviewer_steps]
test("All reviewer decisions are REQUEST_CHANGES",
     all(d == "REQUEST_CHANGES" for d in reviewer_decisions),
     f"got: {reviewer_decisions}")

# Error message mentions max rejections
test("Error mentions max rejections",
     "rejection" in (orch3.get("error") or "").lower(),
     f"got: {orch3.get('error')}")

# Verify task is failed
code, t3 = req("GET", f"/tasks/{TID3}")
test("Task is failed", t3.get("status") == "failed")

# Verify multiple runs
code, runs3 = req("GET", f"/tasks/{TID3}/runs")
test("Multiple runs created from retries",
     len(runs3) > 4,
     f"got: {len(runs3)} runs")

# ================================================================
# TEST 4: Pre-condition validation
# ================================================================
print("\n-- 4. Pre-condition Validation --")

# Orchestrate a non-pending task (TID1 is done)
code, err = req("POST", f"/tasks/{TID1}/orchestrate", {"delay_seconds": 0.01})
test("Cannot orchestrate done task -> 400", code == 400,
     f"code={code}")

# Orchestrate non-existent task
code, err = req("POST", "/tasks/nonexistent-id/orchestrate", {"delay_seconds": 0.01})
test("Cannot orchestrate missing task -> 404", code == 404,
     f"code={code}")

# Orchestration status for missing task
code, err = req("GET", "/tasks/nonexistent-id/orchestration-status")
test("Status for missing task -> 404", code == 404)

# Orchestrate a failed task (TID2 is failed, not pending)
code, err = req("POST", f"/tasks/{TID2}/orchestrate", {"delay_seconds": 0.01})
test("Cannot orchestrate failed task -> 400", code == 400)

# ================================================================
# TEST 5: Orchestrate with default body (no params)
# ================================================================
print("\n-- 5. Default Params (no body) --")

code, task5 = req("POST", f"/projects/{PID}/tasks", {
    "title": "Default params test",
})
TID5 = task5["id"]

# Send POST with no body at all
code, orch5 = req("POST", f"/tasks/{TID5}/orchestrate")
test("Orchestrate with no body -> 200", code == 200, f"code={code}")
test("Completes successfully", orch5.get("final_status") == "done",
     f"got: {orch5.get('final_status')}")

# ================================================================
# TEST 6: Step output structure validation
# ================================================================
print("\n-- 6. Output Structure Validation --")

# Use TID1's orchestration result (already stored in orch)
planner_out = orch["steps"][0].get("output", {})
test("Planner has goal_summary", "goal_summary" in planner_out,
     f"keys: {list(planner_out.keys())}")
test("Planner has task_breakdown", "task_breakdown" in planner_out)
test("Planner has acceptance_criteria", "acceptance_criteria" in planner_out)

builder_out = orch["steps"][1].get("output", {})
test("Builder has proposed_files", "proposed_files" in builder_out)
test("Builder has validation_plan", "validation_plan" in builder_out)

qa_out = orch["steps"][2].get("output", {})
test("QA has result field", "result" in qa_out)
test("QA result is PASS", qa_out.get("result") == "PASS")

# steps[3] is now Security Reviewer; Reviewer moved to steps[4]
reviewer_out = orch["steps"][4].get("output", {})
test("Reviewer has decision field", "decision" in reviewer_out)
test("Reviewer decision is APPROVE", reviewer_out.get("decision") == "APPROVE")

# ================================================================
# TEST 7: Log levels and sources
# ================================================================
print("\n-- 7. Log Levels and Sources --")

code, all_logs = req("GET", f"/tasks/{TID1}/logs")
log_sources = set(l["source"] for l in all_logs)
test("Logs from orchestrator source", "orchestrator" in log_sources,
     f"sources: {log_sources}")
test("Logs from planner source", "planner" in log_sources)
test("Logs from builder source", "builder" in log_sources)
test("Logs from qa source", "qa" in log_sources)
test("Logs from reviewer source", "reviewer" in log_sources)

# Verify error logs exist for failure task
code, fail_logs = req("GET", f"/tasks/{TID2}/logs?level=error")
test("Error logs for failed task", len(fail_logs) >= 1,
     f"got: {len(fail_logs)}")

# Verify warn logs for rejection task
code, warn_logs = req("GET", f"/tasks/{TID3}/logs?level=warn")
test("Warn logs for rejected task", len(warn_logs) >= 1,
     f"got: {len(warn_logs)}")

# ================================================================
# SUMMARY
# ================================================================
print("\n" + "=" * 70)
passes = sum(1 for _, s, _ in results if s == "PASS")
fails = sum(1 for _, s, _ in results if s == "FAIL")
total = len(results)
print(f"TOTAL: {total}  |  PASS: {passes}  |  FAIL: {fails}")
print("=" * 70)

if fails > 0:
    print("\nFAILED TESTS:")
    for name, status, detail in results:
        if status == "FAIL":
            print(f"  X {name}" + (f" -- {detail}" if detail else ""))

sys.exit(0 if fails == 0 else 1)
