#!/usr/bin/env python3
"""Phase 6E-A acceptance tests -- Builder Supervised Execution Preparation.

Sections
--------
1.  BuilderOutputSchema extended validation (unit tests, Phase 6E-A optional fields)
2.  Database V6 migration verification (execution_proposals table exists)
3.  Proposal creation on orchestration (mock executor)
4.  Proposal-ApprovalRequest linkage
5.  Risk classification via _normalize_builder_proposal
6.  ProposalValidator unit tests
7.  No-execution guarantee (no tool_audit logs, no tool: approvals)
8.  API endpoint tests (proposals CRUD)
9.  Proposal status integrity
10. Backward compatibility (approval_requests without proposal_id)

Run
---
  cd services/runtime
  set RUNTIME_DB=<temp_db_path>
  set RUNTIME_PORT=<port>
  python -m uvicorn main:app --port %RUNTIME_PORT%

  set TEST_API_BASE=http://127.0.0.1:<port>/api
  python ../../tests/phase6ea_acceptance.py
"""

import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any

# ── Add runtime to sys.path for unit-test imports ─────────────
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")
PASS = 0
FAIL = 0

# Module-level state shared across sections
_project_id: str = ""
_task_id: str = ""
_proposal_id: str = ""


def _req(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
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
# Section 1: Extended BuilderOutputSchema validation (unit tests)
# ==============================================================
print("\n=== Section 1: BuilderOutputSchema extended validation ===")

from agents.model_executor import BuilderOutputSchema

VALID_BUILDER_BASE = {
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

# Test valid output with all Phase 6D required fields passes
ok, err = BuilderOutputSchema.validate(VALID_BUILDER_BASE)
check("Valid Phase 6D complete output passes", ok, err)

# Test valid output WITH all Phase 6E-A optional fields passes
with_6ea = {
    **VALID_BUILDER_BASE,
    "proposed_commands": [
        {"command": "npm install", "reason": "Install dependencies", "risk_level": "low"},
    ],
    "execution_steps": [
        {
            "step_number": 1,
            "action_type": "file",
            "target": "src/auth.py",
            "description": "Create auth module",
            "risk_level": "low",
        }
    ],
    "risk_level": "medium",
    "requires_approval": False,
    "approval_reasons": [],
    "estimated_impact": {
        "files_affected": 2,
        "commands_count": 1,
        "risk_summary": "Low risk changes",
    },
}
ok, err = BuilderOutputSchema.validate(with_6ea)
check("Valid output WITH Phase 6E-A optional fields passes", ok, err)

# Test valid output WITHOUT Phase 6E-A fields passes (backward compat)
ok, err = BuilderOutputSchema.validate(VALID_BUILDER_BASE)
check("Valid output WITHOUT Phase 6E-A fields passes (backward compat)", ok, err)

# Test invalid proposed_commands (missing command field)
bad_cmds = {
    **VALID_BUILDER_BASE,
    "proposed_commands": [{"reason": "no command here"}],
}
ok, err = BuilderOutputSchema.validate(bad_cmds)
check("Invalid proposed_commands (missing command field) fails", not ok)
check("Error mentions 'command' for missing command", not ok and "command" in err)

# Test invalid execution_steps (bad action_type)
bad_steps = {
    **VALID_BUILDER_BASE,
    "execution_steps": [
        {
            "step_number": 1,
            "action_type": "unknown",
            "target": "src/foo.py",
            "description": "do something",
        }
    ],
}
ok, err = BuilderOutputSchema.validate(bad_steps)
check("Invalid execution_steps (bad action_type 'unknown') fails", not ok)
check("Error mentions 'action_type' for bad action", not ok and "action_type" in err)

# Test invalid risk_level value
bad_risk = {**VALID_BUILDER_BASE, "risk_level": "extreme"}
ok, err = BuilderOutputSchema.validate(bad_risk)
check("Invalid risk_level 'extreme' fails", not ok)
check("Error mentions 'risk_level'", not ok and "risk_level" in err)

# Test invalid requires_approval (string instead of bool)
bad_ra = {**VALID_BUILDER_BASE, "requires_approval": "yes"}
ok, err = BuilderOutputSchema.validate(bad_ra)
check("Invalid requires_approval (string) fails", not ok)
check("Error mentions 'requires_approval'", not ok and "requires_approval" in err)

# Test all valid risk levels accepted
for level in ("low", "medium", "high", "critical"):
    test_data = {**VALID_BUILDER_BASE, "risk_level": level}
    ok, err = BuilderOutputSchema.validate(test_data)
    check(f"Risk level '{level}' accepted", ok, err)

# Test all valid action_types accepted
for at in ("file", "shell", "git"):
    test_data = {
        **VALID_BUILDER_BASE,
        "execution_steps": [
            {
                "step_number": 1,
                "action_type": at,
                "target": "src/foo.py",
                "description": f"Test {at} action",
            }
        ],
    }
    ok, err = BuilderOutputSchema.validate(test_data)
    check(f"Execution step action_type '{at}' accepted", ok, err)


# ==============================================================
# Section 2: Database V6 migration verification
# ==============================================================
print("\n=== Section 2: Database V6 migration verification ===")

# Verify server is running
code, _data = GET("/projects")
check("Server is running (GET /projects returns 2xx)", code in (200, 201), f"code={code}")

# Create a temporary project+task and attempt proposals query to confirm table exists
code, proj_tmp = POST("/projects", {"name": "6EA-migration-check", "local_repo_path": "/tmp/6ea-mig"})
if code in (200, 201):
    pid_tmp = proj_tmp["id"]
    code2, task_tmp = POST(f"/projects/{pid_tmp}/tasks", {"title": "migration check task"})
    if code2 in (200, 201):
        tid_tmp = task_tmp["id"]
        code3, proposals_data = GET(f"/tasks/{tid_tmp}/proposals")
        check("execution_proposals table exists (GET proposals returns 200)", code3 == 200,
              f"code={code3}, detail={proposals_data}")
        check("Proposals response has 'proposals' key", isinstance(proposals_data, dict) and "proposals" in proposals_data)
        check("New task has zero proposals", isinstance(proposals_data.get("proposals"), list) and len(proposals_data["proposals"]) == 0)
    else:
        check("Could create migration-check task", False, f"code={code2}")
else:
    check("Could create migration-check project", False, f"code={code}")


# ==============================================================
# Section 3: Proposal creation on orchestration (mock executor)
# ==============================================================
print("\n=== Section 3: Proposal creation on orchestration ===")

# Ensure builder is set to mock (no real provider needed)
PATCH("/settings/role-models", {
    "role_models": [{"role": "builder", "provider": "mock", "model": "", "enabled": False}]
})

code, proj = POST("/projects", {"name": "6EA-test-project", "local_repo_path": "/tmp/6ea"})
check("Create project -> 200/201", code in (200, 201), f"code={code}")

if code in (200, 201):
    _project_id = proj["id"]
    code2, task = POST(f"/projects/{_project_id}/tasks", {"title": "6EA test task"})
    check("Create task -> 200/201", code2 in (200, 201), f"code={code2}")

    if code2 in (200, 201):
        _task_id = task["id"]
        code3, result = POST(f"/tasks/{_task_id}/orchestrate", {"delay_seconds": 0.1})
        check("Orchestrate task -> 200", code3 == 200, f"code={code3}")
        check("Orchestration has final_status", "final_status" in result if code3 == 200 else False)

        # Fetch proposals for this task
        code4, proposals_data = GET(f"/tasks/{_task_id}/proposals")
        check("GET /tasks/{task_id}/proposals -> 200", code4 == 200, f"code={code4}")

        proposals_list = proposals_data.get("proposals", []) if code4 == 200 else []
        check("At least one proposal exists after orchestration",
              len(proposals_list) >= 1, f"found {len(proposals_list)} proposals")

        if proposals_list:
            # Role-based selection (robust to multiple proposals).
            # C2 step 2: proposal creation TRANSFERRED from Builder to the Comparator — the
            # Builder now emits a wrapper {proposals:[...]} (creates 0); the Comparator selects
            # ONE and creates the single proposal (role='comparator'). Documentation adds its
            # own SECOND proposal (created after, sorts first by created_at DESC). Select the
            # Comparator proposal by role, and positively assert the documentation proposal too.
            comparator_proposal = next((p for p in proposals_list if p.get("role") == "comparator"), None)
            doc_proposal = next((p for p in proposals_list if p.get("role") == "documentation"), None)
            check("Comparator proposal exists (role-based, C2 step 2 transfer)", comparator_proposal is not None,
                  f"roles={[p.get('role') for p in proposals_list]}")
            check("No Builder proposal exists (Builder wrapper creates 0)",
                  not any(p.get("role") == "builder" for p in proposals_list),
                  f"roles={[p.get('role') for p in proposals_list]}")
            check("Documentation proposal also exists (DOC-3 second proposal)", doc_proposal is not None,
                  f"roles={[p.get('role') for p in proposals_list]}")
            proposal = comparator_proposal or proposals_list[0]
            _proposal_id = proposal.get("id", "")
            check("Proposal has task_id matching the task", proposal.get("task_id") == _task_id)
            check("Proposal role is 'comparator'", proposal.get("role") == "comparator")
            # Phase 11-5: Low-risk proposals (requires_approval=False) are auto-approved
            expected_status = "pending" if proposal.get("requires_approval") else "approved"
            check(f"Proposal status is '{expected_status}'",
                  proposal.get("status") == expected_status,
                  f"got status={proposal.get('status')}, requires_approval={proposal.get('requires_approval')}")
            check("proposal_data_parsed is a dict",
                  isinstance(proposal.get("proposal_data_parsed"), dict))
            pdata = proposal.get("proposal_data_parsed", {})
            check("proposal_data_parsed contains change_summary",
                  bool(pdata.get("change_summary")),
                  f"keys={list(pdata.keys())}")
    else:
        print("  !! Could not create task — skipping orchestration sub-checks")
else:
    print("  !! Could not create project — skipping orchestration sub-checks")


# ==============================================================
# Section 4: Proposal-ApprovalRequest linkage
# ==============================================================
print("\n=== Section 4: Proposal-ApprovalRequest linkage ===")

if _task_id:
    code, approvals_data = GET(f"/tasks/{_task_id}/approvals")
    check("GET /tasks/{task_id}/approvals -> 200", code == 200, f"code={code}")

    approvals = approvals_data if isinstance(approvals_data, list) else []
    # C2 step 2: the chosen proposal is created by the Comparator → action_type "proposal:comparator".
    proposal_approvals = [a for a in approvals if a.get("action_type") == "proposal:comparator"]

    # There may or may not be a linked approval depending on risk level from mock output
    # The mock builder may or may not set requires_approval=True.
    # We verify: IF one exists, it has the proper structure.
    if proposal_approvals:
        pa = proposal_approvals[0]
        check("proposal:comparator approval has proposal_id set",
              bool(pa.get("proposal_id")), f"proposal_id={pa.get('proposal_id')}")
        # Verify the proposal_id refers to an actual proposal
        if _proposal_id:
            payload_str = pa.get("action_payload", "{}")
            try:
                payload = json.loads(payload_str)
            except Exception:
                payload = {}
            check("action_payload contains proposal_id",
                  "proposal_id" in payload, f"payload keys={list(payload.keys())}")
            check("action_payload proposal_id matches known proposal",
                  payload.get("proposal_id") == pa.get("proposal_id"))
        print("  [INFO] Linked approval found (builder output had high/critical risk)")
    else:
        # No approval needed is also valid (mock builder risk may be low/medium)
        print("  [INFO] No proposal:comparator approval created (chosen is auto-approvable — expected for mock)")
        check("No unexpected non-proposal approvals exist",
              all(a.get("action_type", "").startswith("proposal:") or
                  a.get("action_type", "").startswith("manual:") or
                  not a.get("action_type", "").startswith("tool:")
                  for a in approvals),
              "Found unexpected tool: approval")
else:
    print("  !! No task_id available — skipping Section 4")
    check("Section 4 skipped due to missing task_id", False, "task creation failed in Section 3")


# ==============================================================
# Section 5: Risk classification in proposals (_normalize_builder_proposal)
# ==============================================================
print("\n=== Section 5: Risk classification in proposals ===")

from agents.model_executor import _normalize_builder_proposal

# Builder output with a "delete" action -> should get high risk
data_with_delete = {
    "change_summary": "Remove deprecated file",
    "proposed_files": [
        {"path": "src/old.py", "action": "delete", "reason": "Deprecated"},
        {"path": "src/new.py", "action": "create", "reason": "Replacement"},
    ],
    "change_steps": [{"step": 1, "description": "Delete old.py"}],
    "reasoning_summary": "Cleanup",
    "validation_plan": ["Run tests"],
    "risk_notes": [],
}
_normalize_builder_proposal(data_with_delete)
check("Delete action triggers high risk_level",
      data_with_delete.get("risk_level") == "high",
      f"got risk_level={data_with_delete.get('risk_level')}")
check("Delete action triggers requires_approval=True",
      data_with_delete.get("requires_approval") is True,
      f"got requires_approval={data_with_delete.get('requires_approval')}")

# Builder output with only create/modify actions -> should get medium risk (not high)
data_safe = {
    "change_summary": "Add new feature",
    "proposed_files": [
        {"path": "src/feature.py", "action": "create", "reason": "New feature"},
        {"path": "src/main.py", "action": "modify", "reason": "Wire feature"},
    ],
    "change_steps": [{"step": 1, "description": "Add feature.py"}],
    "reasoning_summary": "Feature addition",
    "validation_plan": ["Run tests"],
    "risk_notes": [],
}
_normalize_builder_proposal(data_safe)
check("Create/modify-only gets medium risk_level",
      data_safe.get("risk_level") == "medium",
      f"got risk_level={data_safe.get('risk_level')}")
check("Create/modify-only has requires_approval=False",
      data_safe.get("requires_approval") is False,
      f"got requires_approval={data_safe.get('requires_approval')}")

# Check normalization fills in all expected keys
for field in ("proposed_commands", "execution_steps", "approval_reasons", "estimated_impact"):
    check(f"_normalize_builder_proposal fills '{field}'",
          field in data_safe, f"missing key '{field}'")

check("estimated_impact is a dict", isinstance(data_safe.get("estimated_impact"), dict))
check("estimated_impact has files_affected",
      "files_affected" in data_safe.get("estimated_impact", {}))
check("estimated_impact has commands_count",
      "commands_count" in data_safe.get("estimated_impact", {}))
check("estimated_impact has risk_summary",
      "risk_summary" in data_safe.get("estimated_impact", {}))


# ==============================================================
# Section 6: ProposalValidator unit tests
# ==============================================================
print("\n=== Section 6: ProposalValidator unit tests ===")

from tools.proposal_validator import ProposalValidator

validator = ProposalValidator()

# validate_file_paths: valid paths pass (valid=True)
results = validator.validate_file_paths([
    {"path": "src/auth.py", "action": "create"},
    {"path": "tests/test_auth.py", "action": "create"},
])
check("Valid file paths pass validation",
      all(r["valid"] for r in results), str(results))

# validate_file_paths: traversal path "../etc/passwd" flagged (valid=False)
results = validator.validate_file_paths([
    {"path": "../etc/passwd", "action": "modify"},
])
check("Traversal path '../etc/passwd' is flagged (valid=False)",
      len(results) == 1 and not results[0]["valid"],
      f"valid={results[0].get('valid')}, reason={results[0].get('reason')}")

# validate_file_paths: system directory flagged (valid=False)
results = validator.validate_file_paths([
    {"path": "C:\\Windows\\system32\\foo.dll", "action": "modify"},
])
check("System dir 'C:\\Windows\\system32\\foo.dll' is flagged (valid=False)",
      len(results) == 1 and not results[0]["valid"],
      f"valid={results[0].get('valid')}, reason={results[0].get('reason')}")

# validate_file_paths: empty path flagged (valid=False)
results = validator.validate_file_paths([
    {"path": "", "action": "create"},
])
check("Empty path is flagged (valid=False)",
      len(results) == 1 and not results[0]["valid"],
      f"valid={results[0].get('valid')}")

# classify_commands: whitelisted command -> low risk
results = validator.classify_commands([
    {"command": "npm install"},
])
check("'npm install' is whitelisted",
      len(results) == 1 and results[0].get("whitelisted") is True,
      f"whitelisted={results[0].get('whitelisted')}, risk={results[0].get('risk_level')}")
check("'npm install' has low risk",
      len(results) == 1 and results[0].get("risk_level") == "low",
      f"risk_level={results[0].get('risk_level')}")

# classify_commands: "rm -rf /" -> critical risk
results = validator.classify_commands([
    {"command": "rm -rf /"},
])
check("'rm -rf /' classified as critical risk",
      len(results) == 1 and results[0].get("risk_level") == "critical",
      f"risk_level={results[0].get('risk_level')}")

# classify_commands: unknown binary -> high risk (not whitelisted)
results = validator.classify_commands([
    {"command": "unknown_binary_xyz foo bar"},
])
check("Unknown binary gets high risk (not whitelisted)",
      len(results) == 1 and results[0].get("risk_level") == "high",
      f"risk_level={results[0].get('risk_level')}, whitelisted={results[0].get('whitelisted')}")
check("Unknown binary not whitelisted",
      len(results) == 1 and results[0].get("whitelisted") is False)

# generate_risk_report: returns dict with expected keys
proposal_for_report = {
    "proposed_files": [
        {"path": "src/feature.py", "action": "create"},
    ],
    "proposed_commands": [
        {"command": "npm test"},
    ],
}
report = validator.generate_risk_report(proposal_for_report)
check("generate_risk_report returns a dict", isinstance(report, dict))
for key in ("overall_risk", "file_risks", "command_risks", "approval_required", "reasons"):
    check(f"generate_risk_report has key '{key}'",
          key in report, f"keys={list(report.keys())}")
check("file_risks is a list", isinstance(report.get("file_risks"), list))
check("command_risks is a list", isinstance(report.get("command_risks"), list))
check("approval_required is a bool", isinstance(report.get("approval_required"), bool))


# ==============================================================
# Section 7: No-execution guarantee
# ==============================================================
print("\n=== Section 7: No-execution guarantee ===")

if _task_id:
    # Check log events for this task — no "tool_audit" source allowed
    code, logs_data = GET(f"/logs/recent?limit=200")
    if code == 200:
        logs = logs_data if isinstance(logs_data, list) else []
        task_logs = [l for l in logs if l.get("task_id") == _task_id]
        tool_audit_logs = [l for l in task_logs if l.get("source") == "tool_audit"]
        check("No tool_audit log events for orchestrated task",
              len(tool_audit_logs) == 0,
              f"found {len(tool_audit_logs)} tool_audit events")
    else:
        check("Could read log events", False, f"code={code}")

    # Check orchestration status
    code2, status_data = GET(f"/tasks/{_task_id}/orchestration-status")
    check("GET /tasks/{task_id}/orchestration-status -> 200", code2 == 200, f"code={code2}")

    # Check approval_requests — only "proposal:builder" type allowed, never "tool:"
    code3, all_approvals = GET(f"/tasks/{_task_id}/approvals")
    if code3 == 200:
        approvals = all_approvals if isinstance(all_approvals, list) else []
        tool_approvals = [a for a in approvals if str(a.get("action_type", "")).startswith("tool:")]
        check("No 'tool:' action_type approvals from orchestration",
              len(tool_approvals) == 0,
              f"found {len(tool_approvals)} tool: approvals")
    else:
        check("Could read task approvals", False, f"code={code3}")
else:
    print("  !! No task_id — skipping Section 7")
    check("Section 7 skipped due to missing task_id", False, "task creation failed earlier")


# ==============================================================
# Section 8: API endpoint tests
# ==============================================================
print("\n=== Section 8: API endpoint tests ===")

if _task_id:
    # GET /tasks/{task_id}/proposals -> 200 with proposals array
    code, data = GET(f"/tasks/{_task_id}/proposals")
    check("GET /tasks/{task_id}/proposals -> 200", code == 200, f"code={code}")
    check("Response has 'proposals' array key",
          isinstance(data, dict) and isinstance(data.get("proposals"), list),
          f"type={type(data)}")

    # GET /tasks/nonexistent-id/proposals -> 404
    code2, data2 = GET("/tasks/nonexistent-00000000/proposals")
    check("GET /tasks/nonexistent/proposals -> 404", code2 == 404, f"code={code2}")

    # GET /proposals/{proposal_id} -> 200 with correct proposal
    if _proposal_id:
        code3, prop_data = GET(f"/proposals/{_proposal_id}")
        check("GET /proposals/{proposal_id} -> 200", code3 == 200, f"code={code3}")
        check("Returned proposal id matches", prop_data.get("id") == _proposal_id,
              f"got id={prop_data.get('id')}")
        check("proposal_data_parsed is a dict in response",
              isinstance(prop_data.get("proposal_data_parsed"), dict),
              f"type={type(prop_data.get('proposal_data_parsed'))}")
        check("approval_reasons_parsed is a list in response",
              isinstance(prop_data.get("approval_reasons_parsed"), list),
              f"type={type(prop_data.get('approval_reasons_parsed'))}")
    else:
        print("  !! No proposal_id — skipping single-proposal endpoint checks")

    # GET /proposals/nonexistent-id -> 404
    code4, data4 = GET("/proposals/nonexistent-00000000")
    check("GET /proposals/nonexistent -> 404", code4 == 404, f"code={code4}")
else:
    print("  !! No task_id — skipping Section 8")
    check("Section 8 skipped due to missing task_id", False, "task creation failed earlier")


# ==============================================================
# Section 9: Proposal status integrity
# ==============================================================
print("\n=== Section 9: Proposal status integrity ===")

if _task_id:
    code, proposals_data = GET(f"/tasks/{_task_id}/proposals")
    if code == 200:
        proposals = proposals_data.get("proposals", [])
        # Phase 11-5: Low-risk proposals are auto-approved; high-risk stay pending
        valid_statuses = all(
            p.get("status") == ("pending" if p.get("requires_approval") else "approved")
            for p in proposals
        )
        check("All proposals have correct initial status (pending if approval required, approved otherwise)",
              valid_statuses or len(proposals) == 0,
              f"statuses={[(p.get('status'), p.get('requires_approval')) for p in proposals]}")

        for p in proposals:
            check(f"Proposal {p.get('id', '?')[:8]} has created_at set",
                  bool(p.get("created_at")),
                  f"created_at={p.get('created_at')}")
            check(f"Proposal {p.get('id', '?')[:8]} has updated_at set",
                  bool(p.get("updated_at")),
                  f"updated_at={p.get('updated_at')}")
    else:
        check("Could read proposals for status check", False, f"code={code}")
else:
    print("  !! No task_id — skipping Section 9")
    check("Section 9 skipped due to missing task_id", False, "task creation failed earlier")


# ==============================================================
# Section 10: Backward compatibility
# ==============================================================
print("\n=== Section 10: Backward compatibility ===")

if _task_id:
    # Existing approval_requests without proposal_id still work
    # POST manual approval (no proposal_id field in request body)
    code, manual_approval = POST(f"/tasks/{_task_id}/approvals", {
        "run_id": None,
        "action_type": "manual:test",
        "action_payload": "{}",
    })
    check("POST manual approval without proposal_id -> 201",
          code == 201, f"code={code}, data={manual_approval}")

    if code == 201:
        manual_id = manual_approval.get("id", "")

        # GET /tasks/{task_id}/approvals — returned approval should have proposal_id null/missing
        code2, approvals_list = GET(f"/tasks/{_task_id}/approvals")
        if code2 == 200:
            manual_entries = [
                a for a in approvals_list
                if a.get("action_type") == "manual:test"
            ]
            check("Manual approval appears in task approvals list",
                  len(manual_entries) >= 1, f"found {len(manual_entries)}")
            if manual_entries:
                ma = manual_entries[0]
                proposal_id_val = ma.get("proposal_id")
                check("Manual approval has proposal_id as null/None",
                      proposal_id_val is None,
                      f"proposal_id={proposal_id_val!r}")
        else:
            check("Could list task approvals", False, f"code={code2}")

        # GET /approvals/pending — should include the manual approval
        code3, pending = GET("/approvals/pending")
        check("GET /approvals/pending -> 200", code3 == 200, f"code={code3}")
        if code3 == 200:
            pending_ids = [a.get("id") for a in (pending if isinstance(pending, list) else [])]
            check("Manual approval appears in pending approvals",
                  manual_id in pending_ids,
                  f"manual_id={manual_id!r}, pending_ids count={len(pending_ids)}")
else:
    print("  !! No task_id — skipping Section 10")
    check("Section 10 skipped due to missing task_id", False, "task creation failed earlier")


# ==============================================================
# Summary
# ==============================================================
print(f"\n{'=' * 60}")
print(f"Phase 6E-A acceptance: {PASS} passed, {FAIL} failed")
print(f"{'=' * 60}")
sys.exit(0 if FAIL == 0 else 1)
