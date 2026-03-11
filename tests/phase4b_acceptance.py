"""Phase 4B acceptance test suite — Audit Logging + Approval Single-Use Consume."""
import json
import sys
import time
import urllib.request
import urllib.error
import concurrent.futures

BASE = "http://127.0.0.1:9800/api"
results = []

REPO_PATH = "D:/AI_Team_Studio"


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


def get_audit_logs(limit=100):
    """Get recent tool_audit logs via GET /api/logs/recent."""
    code, logs = req("GET", f"/logs/recent?limit={limit}")
    if code != 200:
        return []
    return [l for l in logs if l.get("source") == "tool_audit"]


def find_audit_events(logs, event_name, tool_name=None):
    """Filter audit logs by event name and optional tool_name."""
    found = []
    for l in logs:
        payload = json.loads(l.get("payload", "{}"))
        if payload.get("event") == event_name:
            if tool_name is None or payload.get("tool_name") == tool_name:
                found.append((l, payload))
    return found


print("=" * 70)
print("PHASE 4B ACCEPTANCE TEST SUITE")
print("=" * 70)

# ── Setup: create a project ──────────────────────────────────────────
code, proj = req("POST", "/projects", {
    "name": "Phase4B Test Project",
    "local_repo_path": REPO_PATH,
    "description": "Project for Phase 4B audit + consume tests",
})
PID = proj["id"]

# ================================================================
# GROUP 1: Audit Logging — /tools/execute
# ================================================================
print("\n-- 1. Audit Logging — /tools/execute --")

# 1.1 Safe tool execution → produces request + risk_classified + success
code, result = req("POST", "/tools/execute", {
    "tool_name": "read_file",
    "params": {"path": "services/runtime/main.py"},
    "project_id": PID,
})
test("read_file safe tool -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")

audit_logs = get_audit_logs()
req_events = find_audit_events(audit_logs, "tool.execute.request", "read_file")
test("read_file produces request audit log",
     len(req_events) >= 1,
     f"found: {len(req_events)}")

risk_events = find_audit_events(audit_logs, "tool.execute.risk_classified", "read_file")
test("read_file produces risk_classified audit log",
     len(risk_events) >= 1,
     f"found: {len(risk_events)}")

success_events = find_audit_events(audit_logs, "tool.execute.success", "read_file")
test("read_file produces success audit log",
     len(success_events) >= 1,
     f"found: {len(success_events)}")

# 1.2 Failed tool execution → produces request + failed
code, result = req("POST", "/tools/execute", {
    "tool_name": "read_file",
    "params": {"path": "nonexistent_xyz_file.txt"},
    "project_id": PID,
})
test("read_file nonexistent -> success=false",
     result.get("success") is False,
     f"got: {result.get('success')}")

audit_logs = get_audit_logs()
fail_events = find_audit_events(audit_logs, "tool.execute.failed", "read_file")
test("read_file nonexistent produces failed audit log",
     len(fail_events) >= 1,
     f"found: {len(fail_events)}")

# 1.3 Blocked tool → produces request + risk_classified + blocked
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm -rf /tmp/test"},
    "project_id": PID,
})
test("shell rm -rf -> blocked=true",
     result.get("blocked") is True,
     f"blocked: {result.get('blocked')}")

audit_logs = get_audit_logs()
blocked_events = find_audit_events(audit_logs, "tool.execute.blocked", "shell")
test("shell rm -rf produces blocked audit log",
     len(blocked_events) >= 1,
     f"found: {len(blocked_events)}")

# Verify blocked audit payload has approval_id
if blocked_events:
    blocked_payload = blocked_events[0][1]
    test("blocked audit payload has approval_id",
         bool(blocked_payload.get("approval_id")),
         f"approval_id: {blocked_payload.get('approval_id')!r}")
else:
    test("blocked audit payload has approval_id", False, "no blocked events found")

# 1.4 Role denied → produces role_denied audit log
code, result = req("POST", "/tools/execute", {
    "tool_name": "write_file",
    "params": {"path": "test.txt", "content": "x"},
    "project_id": PID,
    "role": "planner",
})
test("write_file with role=planner -> 403",
     code == 403,
     f"code={code}")

audit_logs = get_audit_logs()
denied_events = find_audit_events(audit_logs, "tool.execute.role_denied", "write_file")
test("write_file role=planner produces role_denied audit log",
     len(denied_events) >= 1,
     f"found: {len(denied_events)}")

# 1.5 Unknown tool 404 → NO audit log
audit_before = get_audit_logs()
count_before = len(audit_before)
code, result = req("POST", "/tools/execute", {
    "tool_name": "nonexistent_tool",
    "params": {},
})
test("unknown tool -> 404", code == 404, f"code={code}")
audit_after = get_audit_logs()
count_after = len(audit_after)
test("unknown tool 404 produces NO audit log",
     count_after == count_before,
     f"before={count_before}, after={count_after}")

# 1.6 Audit payload contains required fields
audit_logs = get_audit_logs()
success_events = find_audit_events(audit_logs, "tool.execute.success", "read_file")
if success_events:
    p = success_events[0][1]
    test("audit payload has tool_name",
         p.get("tool_name") == "read_file",
         f"got: {p.get('tool_name')!r}")
    test("audit payload has risk_level",
         "risk_level" in p,
         f"keys: {list(p.keys())}")
    test("audit payload has params_summary",
         "params_summary" in p,
         f"keys: {list(p.keys())}")
else:
    test("audit payload has tool_name", False, "no success events")
    test("audit payload has risk_level", False, "no success events")
    test("audit payload has params_summary", False, "no success events")

# 1.7 Sensitive params are sanitized
code, result = req("POST", "/tools/execute", {
    "tool_name": "read_file",
    "params": {"path": "main.py", "api_key": "super-secret-123"},
    "project_id": PID,
})

audit_logs = get_audit_logs()
sanitize_events = find_audit_events(audit_logs, "tool.execute.request", "read_file")
if sanitize_events:
    latest = sanitize_events[0][1]
    summary = latest.get("params_summary", "")
    test("sensitive param api_key is sanitized",
         "super-secret-123" not in summary and "***" in summary,
         f"summary: {summary!r}")
else:
    test("sensitive param api_key is sanitized", False, "no events found")

# 1.8 stdout/stderr preview in shell success audit
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "echo audit_test_marker"},
    "project_id": PID,
})
test("shell echo -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")

audit_logs = get_audit_logs()
shell_success = find_audit_events(audit_logs, "tool.execute.success", "shell")
if shell_success:
    p = shell_success[0][1]
    test("shell success audit has stdout_preview",
         "audit_test_marker" in (p.get("stdout_preview") or ""),
         f"stdout_preview: {p.get('stdout_preview')!r}")
else:
    test("shell success audit has stdout_preview", False, "no shell success events")


# ================================================================
# GROUP 2: Audit Logging — /tools/execute-approved
# ================================================================
print("\n-- 2. Audit Logging — /tools/execute-approved --")

# Block a command, approve it, then execute-approved
code, block_result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm audit_test_file"},
    "project_id": PID,
})
AUDIT_APPROVAL_ID = block_result.get("approval_id", "")
test("block rm audit_test_file -> approval_id obtained",
     bool(AUDIT_APPROVAL_ID),
     f"id: {AUDIT_APPROVAL_ID!r}")

# Approve it
code, _ = req("PATCH", f"/approvals/{AUDIT_APPROVAL_ID}", {
    "status": "approved",
    "reviewer_comment": "ok for audit test",
})
test("approve -> 200", code == 200, f"code={code}")

# Execute-approved (success path — rm not in whitelist, will fail at tool level)
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": AUDIT_APPROVAL_ID,
    "tool_name": "shell",
    "params": {"command": "rm audit_test_file"},
    "project_id": PID,
})
test("execute-approved -> 200", code == 200, f"code={code}")

audit_logs = get_audit_logs()
ea_req = find_audit_events(audit_logs, "tool.execute_approved.request", "shell")
test("execute-approved produces request audit log",
     len(ea_req) >= 1,
     f"found: {len(ea_req)}")

# rm is not in whitelist → tool execution fails → should have .failed event
ea_fail = find_audit_events(audit_logs, "tool.execute_approved.failed", "shell")
test("execute-approved rm (not whitelisted) produces failed audit log",
     len(ea_fail) >= 1,
     f"found: {len(ea_fail)}")

# 2.2 Denied audit — try execute-approved with a pending approval
code, block2 = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm denied_test"},
    "project_id": PID,
})
PENDING_APPROVAL_ID = block2.get("approval_id", "")

# Don't approve — try execute-approved while still pending
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": PENDING_APPROVAL_ID,
    "tool_name": "shell",
    "params": {"command": "rm denied_test"},
    "project_id": PID,
})
test("execute-approved pending -> 403", code == 403, f"code={code}")

audit_logs = get_audit_logs()
denied_events = find_audit_events(audit_logs, "tool.execute_approved.denied", "shell")
test("execute-approved pending produces denied audit log",
     len(denied_events) >= 1,
     f"found: {len(denied_events)}")

# Check deny_reason in payload
if denied_events:
    err_summary = denied_events[0][1].get("error_summary", "")
    test("denied audit error_summary mentions 'pending'",
         "pending" in err_summary,
         f"error_summary: {err_summary!r}")
else:
    test("denied audit error_summary mentions 'pending'", False, "no denied events")

# 2.3 Already-consumed audit
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": AUDIT_APPROVAL_ID,
    "tool_name": "shell",
    "params": {"command": "rm audit_test_file"},
    "project_id": PID,
})
test("execute-approved already consumed -> 409", code == 409, f"code={code}")

audit_logs = get_audit_logs()
consumed_events = find_audit_events(
    audit_logs, "tool.execute_approved.already_consumed", "shell"
)
test("already consumed produces already_consumed audit log",
     len(consumed_events) >= 1,
     f"found: {len(consumed_events)}")


# ================================================================
# GROUP 3: Single-Use Consume
# ================================================================
print("\n-- 3. Single-Use Consume --")

# 3.1 Full flow: block → approve → execute-approved → status=consumed
code, block3 = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm consume_test"},
    "project_id": PID,
})
CONSUME_APPROVAL_ID = block3.get("approval_id", "")
test("block rm consume_test -> approval_id",
     bool(CONSUME_APPROVAL_ID),
     f"id: {CONSUME_APPROVAL_ID!r}")

# Approve
code, _ = req("PATCH", f"/approvals/{CONSUME_APPROVAL_ID}", {
    "status": "approved",
    "reviewer_comment": "consume test",
})
test("approve consume test -> 200", code == 200, f"code={code}")

# Execute-approved → consumes
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": CONSUME_APPROVAL_ID,
    "tool_name": "shell",
    "params": {"command": "rm consume_test"},
    "project_id": PID,
})
test("execute-approved consume -> 200", code == 200, f"code={code}")

# Verify status is consumed
code, approval = req("GET", f"/approvals/pending")
# The consumed one should NOT be in pending list
pending_ids = [a["id"] for a in approval] if code == 200 else []
test("consumed approval not in pending list",
     CONSUME_APPROVAL_ID not in pending_ids,
     f"pending_ids: {pending_ids}")

# 3.2 Same approval_id second time → 409
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": CONSUME_APPROVAL_ID,
    "tool_name": "shell",
    "params": {"command": "rm consume_test"},
    "project_id": PID,
})
test("second execute-approved -> 409",
     code == 409,
     f"code={code}")
err_detail = result.get("detail", "")
test("409 error mentions 'consumed'",
     "consumed" in err_detail.lower(),
     f"detail: {err_detail!r}")

# 3.3 pending approval direct execute-approved → 403
code, block4 = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm pending_test"},
    "project_id": PID,
})
PENDING2 = block4.get("approval_id", "")
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": PENDING2,
    "tool_name": "shell",
    "params": {"command": "rm pending_test"},
    "project_id": PID,
})
test("pending execute-approved -> 403", code == 403, f"code={code}")
test("403 detail mentions 'pending'",
     "pending" in result.get("detail", "").lower(),
     f"detail: {result.get('detail')!r}")

# 3.4 rejected approval execute-approved → 403
code, block5 = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm rejected_test"},
    "project_id": PID,
})
REJECTED_ID = block5.get("approval_id", "")
req("PATCH", f"/approvals/{REJECTED_ID}", {
    "status": "rejected",
    "reviewer_comment": "no",
})
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": REJECTED_ID,
    "tool_name": "shell",
    "params": {"command": "rm rejected_test"},
    "project_id": PID,
})
test("rejected execute-approved -> 403", code == 403, f"code={code}")
test("403 detail mentions 'rejected'",
     "rejected" in result.get("detail", "").lower(),
     f"detail: {result.get('detail')!r}")

# 3.5 nonexistent approval_id → 403
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": "00000000-0000-0000-0000-000000000000",
    "tool_name": "shell",
    "params": {"command": "echo test"},
    "project_id": PID,
})
test("nonexistent approval_id -> 403", code == 403, f"code={code}")
test("403 detail mentions 'not_found'",
     "not_found" in result.get("detail", "").lower(),
     f"detail: {result.get('detail')!r}")

# 3.6 consumed approval cannot be PATCH'd back to approved
code, result = req("PATCH", f"/approvals/{CONSUME_APPROVAL_ID}", {
    "status": "approved",
    "reviewer_comment": "trying to reset",
})
test("PATCH consumed back to approved -> 400",
     code == 400,
     f"code={code}")
test("400 detail mentions 'consumed'",
     "consumed" in result.get("detail", "").lower(),
     f"detail: {result.get('detail')!r}")

# 3.7 Tool execution fails after consume → consumed status preserved
code, block6 = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm fail_after_consume"},
    "project_id": PID,
})
FAIL_CONSUME_ID = block6.get("approval_id", "")
req("PATCH", f"/approvals/{FAIL_CONSUME_ID}", {
    "status": "approved",
    "reviewer_comment": "will fail but stay consumed",
})
# Execute-approved — rm not in whitelist → tool fails
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": FAIL_CONSUME_ID,
    "tool_name": "shell",
    "params": {"command": "rm fail_after_consume"},
    "project_id": PID,
})
test("execute-approved (will fail) -> 200", code == 200, f"code={code}")
test("tool execution failed",
     result.get("success") is False,
     f"success: {result.get('success')}")
# Verify consumed status preserved — try again → 409
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": FAIL_CONSUME_ID,
    "tool_name": "shell",
    "params": {"command": "rm fail_after_consume"},
    "project_id": PID,
})
test("retry after fail -> 409 (still consumed)",
     code == 409,
     f"code={code}")

# 3.8 Concurrent double-consume — only one succeeds
code, block7 = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm concurrent_test"},
    "project_id": PID,
})
CONCURRENT_ID = block7.get("approval_id", "")
req("PATCH", f"/approvals/{CONCURRENT_ID}", {
    "status": "approved",
    "reviewer_comment": "concurrent test",
})


def _try_consume(aid):
    return req("POST", "/tools/execute-approved", {
        "approval_id": aid,
        "tool_name": "shell",
        "params": {"command": "rm concurrent_test"},
        "project_id": PID,
    })


with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
    f1 = executor.submit(_try_consume, CONCURRENT_ID)
    f2 = executor.submit(_try_consume, CONCURRENT_ID)
    r1_code, r1_body = f1.result()
    r2_code, r2_body = f2.result()

codes = sorted([r1_code, r2_code])
test("concurrent consume: exactly one 200 and one 409",
     codes == [200, 409],
     f"codes: {codes}")


# ================================================================
# GROUP 4: Regression Protection
# ================================================================
print("\n-- 4. Regression Protection --")

# 4.1 Standard Phase 4A flow still works (one-time)
code, block_reg = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm regression_test"},
    "project_id": PID,
})
test("regression: block -> blocked=true",
     block_reg.get("blocked") is True,
     f"blocked: {block_reg.get('blocked')}")

REG_ID = block_reg.get("approval_id", "")
req("PATCH", f"/approvals/{REG_ID}", {
    "status": "approved",
    "reviewer_comment": "regression ok",
})
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": REG_ID,
    "tool_name": "shell",
    "params": {"command": "rm regression_test"},
    "project_id": PID,
})
test("regression: execute-approved -> 200",
     code == 200,
     f"code={code}")
test("regression: blocked=false in response",
     result.get("blocked") is False,
     f"blocked: {result.get('blocked')}")

# 4.2 Audit logs don't affect tool execution results
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "echo regression_ok"},
    "project_id": PID,
})
test("regression: echo with audit -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")
stdout = (result.get("output") or {}).get("stdout", "")
test("regression: echo stdout correct",
     "regression_ok" in stdout,
     f"stdout: {stdout!r}")


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
