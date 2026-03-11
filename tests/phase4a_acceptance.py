"""Phase 4A acceptance test suite — Tool Layer + Approval Gating."""
import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")
results = []

REPO_PATH = "D:/AI_Team_Studio"
TEMP_FILE = "data/test_phase4a_temp.txt"


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
print("PHASE 4A ACCEPTANCE TEST SUITE")
print("=" * 70)

# ── Setup: create a project pointing at this repo ─────────────────────
code, proj = req("POST", "/projects", {
    "name": "Phase4A Test Project",
    "local_repo_path": REPO_PATH,
    "description": "Project for Phase 4A tool tests",
})
PID = proj["id"]

# ================================================================
# GROUP 1: Tool Registry
# ================================================================
print("\n-- 1. Tool Registry --")

code, tools = req("GET", "/tools")
test("GET /api/tools returns 200", code == 200, f"code={code}")
test("Returns exactly 7 tools", len(tools) == 7,
     f"got: {len(tools)}")

# Check each tool has required fields
all_have_fields = all(
    "name" in t and "description" in t and "category" in t
    for t in tools
)
test("Each tool has name, description, category", all_have_fields,
     f"tools: {tools}")

EXPECTED_TOOL_NAMES = {
    "read_file", "list_directory", "write_file",
    "git_status", "git_diff", "git_log", "shell",
}
actual_names = {t["name"] for t in tools}
test("Tool names match expected set",
     actual_names == EXPECTED_TOOL_NAMES,
     f"got: {sorted(actual_names)}, expected: {sorted(EXPECTED_TOOL_NAMES)}")

# ================================================================
# GROUP 2: File — read_file
# ================================================================
print("\n-- 2. File — read_file --")

# Read an existing file
code, result = req("POST", "/tools/execute", {
    "tool_name": "read_file",
    "params": {"path": "services/runtime/main.py"},
    "project_id": PID,
})
test("read_file existing file -> 200", code == 200, f"code={code}")
test("read_file existing file -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")
content = (result.get("output") or {}).get("content", "")
test("read_file content contains 'FastAPI'",
     "FastAPI" in content,
     f"content_start: {content[:100]!r}")

# Read a nonexistent file
code, result = req("POST", "/tools/execute", {
    "tool_name": "read_file",
    "params": {"path": "nonexistent/does_not_exist.txt"},
    "project_id": PID,
})
test("read_file nonexistent -> 200", code == 200, f"code={code}")
test("read_file nonexistent -> success=false",
     result.get("success") is False,
     f"got: {result.get('success')}")
test("read_file nonexistent -> error present",
     bool(result.get("error")),
     f"error: {result.get('error')!r}")

# Missing path param
code, result = req("POST", "/tools/execute", {
    "tool_name": "read_file",
    "params": {},
    "project_id": PID,
})
test("read_file missing path param -> 422", code == 422,
     f"code={code}")

# ================================================================
# GROUP 3: File — list_directory
# ================================================================
print("\n-- 3. File — list_directory --")

code, result = req("POST", "/tools/execute", {
    "tool_name": "list_directory",
    "params": {"path": "services/runtime"},
    "project_id": PID,
})
test("list_directory services/runtime -> 200", code == 200, f"code={code}")
test("list_directory -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")
entries = (result.get("output") or {}).get("entries", [])
entry_names = [e["name"] for e in entries]
test("list_directory entries contain 'main.py'",
     "main.py" in entry_names,
     f"entries: {entry_names}")

# Nonexistent directory
code, result = req("POST", "/tools/execute", {
    "tool_name": "list_directory",
    "params": {"path": "nonexistent_dir_xyz"},
    "project_id": PID,
})
test("list_directory nonexistent -> 200", code == 200, f"code={code}")
test("list_directory nonexistent -> success=false",
     result.get("success") is False,
     f"got: {result.get('success')}")
test("list_directory nonexistent -> error present",
     bool(result.get("error")),
     f"error: {result.get('error')!r}")

# ================================================================
# GROUP 4: File — write_file
# ================================================================
print("\n-- 4. File — write_file --")

# Write a new temp file
code, result = req("POST", "/tools/execute", {
    "tool_name": "write_file",
    "params": {
        "path": TEMP_FILE,
        "content": "phase4a test content",
    },
    "project_id": PID,
})
test("write_file new file -> 200", code == 200, f"code={code}")
test("write_file new file -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}, result: {result}")
output = result.get("output") or {}
test("write_file new file -> created=true",
     output.get("created") is True,
     f"output: {output}")

# Overwrite the same file
code, result = req("POST", "/tools/execute", {
    "tool_name": "write_file",
    "params": {
        "path": TEMP_FILE,
        "content": "updated content",
    },
    "project_id": PID,
})
test("write_file overwrite -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")
output2 = result.get("output") or {}
test("write_file overwrite -> created=false",
     output2.get("created") is False,
     f"output: {output2}")

# Write to a .env path — should be blocked by risk classifier
code, result = req("POST", "/tools/execute", {
    "tool_name": "write_file",
    "params": {
        "path": "data/.env",
        "content": "SECRET=blocked",
    },
    "project_id": PID,
})
test("write_file .env path -> 200", code == 200, f"code={code}")
test("write_file .env path -> blocked=true",
     result.get("blocked") is True,
     f"blocked: {result.get('blocked')}, result: {result}")
test("write_file .env path -> approval_id present",
     bool(result.get("approval_id")),
     f"approval_id: {result.get('approval_id')!r}")

# Clean up: delete the temp file
# (We'll do a shell echo to verify cleanup isn't possible directly,
#  but we can use python to remove it after tests — done at end)
TEMP_FILE_CLEANUP_NEEDED = True

# ================================================================
# GROUP 5: Git Tools
# ================================================================
print("\n-- 5. Git Tools --")

# git_status with cwd
code, result = req("POST", "/tools/execute", {
    "tool_name": "git_status",
    "params": {"cwd": REPO_PATH},
    "project_id": PID,
})
test("git_status -> 200", code == 200, f"code={code}")
test("git_status -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")
output = result.get("output") or {}
test("git_status output has 'entries' key", "entries" in output,
     f"keys: {list(output.keys())}")
test("git_status output has 'clean' key", "clean" in output,
     f"keys: {list(output.keys())}")
test("git_status output has 'raw' key", "raw" in output,
     f"keys: {list(output.keys())}")

# git_diff
code, result = req("POST", "/tools/execute", {
    "tool_name": "git_diff",
    "params": {"cwd": REPO_PATH},
    "project_id": PID,
})
test("git_diff -> 200", code == 200, f"code={code}")
test("git_diff -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")
diff_output = result.get("output") or {}
test("git_diff output has 'diff' key", "diff" in diff_output,
     f"keys: {list(diff_output.keys())}")
test("git_diff output has 'lines' key", "lines" in diff_output,
     f"keys: {list(diff_output.keys())}")
test("git_diff output has 'truncated' key", "truncated" in diff_output,
     f"keys: {list(diff_output.keys())}")

# git_log
code, result = req("POST", "/tools/execute", {
    "tool_name": "git_log",
    "params": {"cwd": REPO_PATH},
    "project_id": PID,
})
test("git_log -> 200", code == 200, f"code={code}")
test("git_log -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")
log_output = result.get("output") or {}
commits = log_output.get("commits", [])
test("git_log has commits list", isinstance(commits, list),
     f"type: {type(commits)}")
test("git_log commit count > 0", log_output.get("count", 0) > 0,
     f"count: {log_output.get('count')}")

# git_status with bad cwd (nonexistent directory)
code, result = req("POST", "/tools/execute", {
    "tool_name": "git_status",
    "params": {"cwd": "/nonexistent/path/xyz"},
    "project_id": PID,
})
test("git_status bad cwd -> 200", code == 200, f"code={code}")
test("git_status bad cwd -> success=false",
     result.get("success") is False,
     f"got: {result.get('success')}")
test("git_status bad cwd -> error present",
     bool(result.get("error")),
     f"error: {result.get('error')!r}")

# ================================================================
# GROUP 6: Shell Executor
# ================================================================
print("\n-- 6. Shell Executor --")

# echo hello — whitelisted, safe
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "echo hello"},
    "project_id": PID,
})
test("shell 'echo hello' -> 200", code == 200, f"code={code}")
test("shell 'echo hello' -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")
stdout = (result.get("output") or {}).get("stdout", "")
test("shell 'echo hello' stdout contains 'hello'",
     "hello" in stdout,
     f"stdout: {stdout!r}")

# curl — not in whitelist, should be rejected
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "curl http://example.com"},
    "project_id": PID,
})
test("shell 'curl' not whitelisted -> 200", code == 200, f"code={code}")
test("shell 'curl' -> success=false",
     result.get("success") is False,
     f"got: {result.get('success')}")
curl_error = result.get("error") or ""
test("shell 'curl' error mentions whitelist",
     "whitelist" in curl_error.lower(),
     f"error: {curl_error!r}")

# echo with timeout param
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "echo test", "timeout": 1},
    "project_id": PID,
})
test("shell 'echo test' with timeout=1 -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")

# ================================================================
# GROUP 7: Risk Classification (via /tools/execute)
# ================================================================
print("\n-- 7. Risk Classification --")

# rm -rf / — CRITICAL risk, should be blocked
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm -rf /"},
    "project_id": PID,
})
test("shell 'rm -rf /' -> 200", code == 200, f"code={code}")
test("shell 'rm -rf /' -> blocked=true",
     result.get("blocked") is True,
     f"blocked: {result.get('blocked')}, result: {result}")
risk_level = result.get("risk_level", "")
test("shell 'rm -rf /' -> risk_level is critical or high",
     risk_level in ("critical", "high"),
     f"risk_level: {risk_level!r}")
test("shell 'rm -rf /' -> approval_id present",
     bool(result.get("approval_id")),
     f"approval_id: {result.get('approval_id')!r}")

# echo safe — not risky, should succeed
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "echo safe"},
    "project_id": PID,
})
test("shell 'echo safe' -> blocked=false",
     result.get("blocked") is False,
     f"blocked: {result.get('blocked')}")
test("shell 'echo safe' -> success=true",
     result.get("success") is True,
     f"error: {result.get('error')}")

# write_file to .env path — HIGH risk, should be blocked
code, result = req("POST", "/tools/execute", {
    "tool_name": "write_file",
    "params": {
        "path": "secrets/.env",
        "content": "SECRET=value",
    },
    "project_id": PID,
})
test("write_file '.env' path -> blocked=true",
     result.get("blocked") is True,
     f"blocked: {result.get('blocked')}, result: {result}")

# ================================================================
# GROUP 8: Approval Workflow
# ================================================================
print("\n-- 8. Approval Workflow --")

# Trigger a block for "rm somefile" — rm matches \brm\b (HIGH)
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "rm somefile"},
    "project_id": PID,
})
test("shell 'rm somefile' -> blocked=true",
     result.get("blocked") is True,
     f"blocked: {result.get('blocked')}, result: {result}")
BLOCKED_APPROVAL_ID = result.get("approval_id", "")
test("shell 'rm somefile' -> approval_id obtained",
     bool(BLOCKED_APPROVAL_ID),
     f"approval_id: {BLOCKED_APPROVAL_ID!r}")

# Try execute-approved BEFORE approving — should get 403
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": BLOCKED_APPROVAL_ID,
    "tool_name": "shell",
    "params": {"command": "rm somefile"},
    "project_id": PID,
})
test("execute-approved before approval -> 403",
     code == 403,
     f"code={code}, result: {result}")

# Approve via PATCH /api/approvals/{approval_id}
code, approval = req("PATCH", f"/approvals/{BLOCKED_APPROVAL_ID}", {
    "status": "approved",
    "reviewer_comment": "ok",
})
test("PATCH approval -> 200",
     code == 200,
     f"code={code}, result: {approval}")
test("approval status is 'approved'",
     approval.get("status") == "approved",
     f"status: {approval.get('status')!r}")

# Execute-approved after approval — should not be blocked
# rm is not in whitelist, so tool execution will fail, but it should NOT be blocked
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": BLOCKED_APPROVAL_ID,
    "tool_name": "shell",
    "params": {"command": "rm somefile"},
    "project_id": PID,
})
test("execute-approved after approval -> 200",
     code == 200,
     f"code={code}")
test("execute-approved after approval -> blocked=false",
     result.get("blocked") is False,
     f"blocked: {result.get('blocked')}, result: {result}")

# Execute-approved with a completely fake/nonexistent approval_id -> 403
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": "00000000-0000-0000-0000-000000000000",
    "tool_name": "shell",
    "params": {"command": "echo test"},
    "project_id": PID,
})
test("execute-approved with fake approval_id -> 403",
     code == 403,
     f"code={code}")

# ================================================================
# GROUP 9: Role Permissions
# ================================================================
print("\n-- 9. Role Permissions --")

# write_file with role=planner — planner has ["read", "grep", "glob"],
# write_file aliases are ["write", "edit"], none in planner's allowed list
code, result = req("POST", "/tools/execute", {
    "tool_name": "write_file",
    "params": {
        "path": "data/planner_test.txt",
        "content": "should be forbidden",
    },
    "project_id": PID,
    "role": "planner",
})
test("write_file with role=planner -> 403",
     code == 403,
     f"code={code}, result: {result}")

# read_file with role=planner — read is in planner's allowed_tools
code, result = req("POST", "/tools/execute", {
    "tool_name": "read_file",
    "params": {"path": "services/runtime/main.py"},
    "project_id": PID,
    "role": "planner",
})
test("read_file with role=planner -> success=true",
     result.get("success") is True,
     f"code={code}, error: {result.get('error')}")

# shell with role=reviewer — reviewer has ["read", "grep", "glob"],
# shell aliases are ["bash"], not in reviewer's list
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "echo test"},
    "project_id": PID,
    "role": "reviewer",
})
test("shell with role=reviewer -> 403",
     code == 403,
     f"code={code}, result: {result}")

# shell with role=builder — builder has ["read", "edit", "write", "bash", "grep", "glob"]
# bash is an alias for shell, so it should be allowed
# (echo test is safe, so it will execute)
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {"command": "echo test"},
    "project_id": PID,
    "role": "builder",
})
test("shell with role=builder -> NOT 403",
     code != 403,
     f"code={code}, result: {result}")

# ================================================================
# GROUP 10: Error Cases
# ================================================================
print("\n-- 10. Error Cases --")

# Unknown tool name -> 404
code, result = req("POST", "/tools/execute", {
    "tool_name": "nonexistent_tool_xyz",
    "params": {},
    "project_id": PID,
})
test("unknown tool name -> 404",
     code == 404,
     f"code={code}")

# Execute shell with missing required params (no 'command') -> 422
code, result = req("POST", "/tools/execute", {
    "tool_name": "shell",
    "params": {},
    "project_id": PID,
})
test("shell missing 'command' param -> 422",
     code == 422,
     f"code={code}")

# Execute-approved with nonexistent approval_id -> 403
code, result = req("POST", "/tools/execute-approved", {
    "approval_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
    "tool_name": "shell",
    "params": {"command": "echo hello"},
    "project_id": PID,
})
test("execute-approved nonexistent approval_id -> 403",
     code == 403,
     f"code={code}")

# ================================================================
# Cleanup: delete temp file via Python (not a tool call, just OS)
# ================================================================
if TEMP_FILE_CLEANUP_NEEDED:
    import os
    import pathlib
    temp_path = pathlib.Path(REPO_PATH) / TEMP_FILE
    try:
        if temp_path.exists():
            temp_path.unlink()
    except Exception:
        pass  # best effort

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
