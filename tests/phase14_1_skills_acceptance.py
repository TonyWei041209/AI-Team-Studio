"""Phase 14-1 acceptance test suite — Skills system foundation.

Covers:
  - POST /api/skills (create global and agent-scoped skills)
  - GET /api/skills (list with filters: scope_type, agent_role, enabled_only)
  - GET /api/skills/{id} (get single skill)
  - PATCH /api/skills/{id} (update skill fields)
  - DELETE /api/skills/{id} (delete skill)
  - Validation: scope_type/agent_role consistency, invalid role

Usage:
    set TEST_API_BASE=http://127.0.0.1:9800/api
    python tests/phase14_1_skills_acceptance.py
"""
import json
import os
import pathlib
import sqlite3
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:9800/api")
results = []


# ── Test isolation: wipe skills table before running ─────────────
def _wipe_skills():
    db_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "ai_team_studio.db"
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DELETE FROM skills")
        conn.commit()
    except Exception:
        pass  # table may not exist yet — migration will create it
    finally:
        conn.close()


_wipe_skills()


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
    tag = "PASS" if passed else "FAIL"
    extra = ""
    if not passed and detail:
        extra = f" -- {detail}"
    print(f"  [{tag}] {name}{extra}")


print("=" * 70)
print("PHASE 14-1 ACCEPTANCE TEST SUITE — Skills System Foundation")
print("=" * 70)

# ----------------------------------------------------------------
# 1. Create global skill
# ----------------------------------------------------------------
print("\n-- 1. Create global skill --")
code, data = req("POST", "/skills", {
    "name": "Python best practices",
    "description": "General Python coding guidelines",
    "content": "Use type hints. Write docstrings. Prefer composition over inheritance.",
    "scope_type": "global",
})
test("201 status for global skill", code == 201, f"got {code}")
test("name matches", data.get("name") == "Python best practices")
test("scope_type is global", data.get("scope_type") == "global")
test("agent_role is null", data.get("agent_role") is None)
test("is_enabled is true", data.get("is_enabled") is True)
test("has id", bool(data.get("id")))
test("has created_at", bool(data.get("created_at")))
test("has updated_at", bool(data.get("updated_at")))
global_skill_id = data.get("id")

# ----------------------------------------------------------------
# 2. Create agent-scoped skill (builder)
# ----------------------------------------------------------------
print("\n-- 2. Create agent-scoped skill (builder) --")
code, data = req("POST", "/skills", {
    "name": "Builder file output format",
    "description": "How Builder should format file proposals",
    "content": "Always include operation, path, and content fields.",
    "scope_type": "agent",
    "agent_role": "builder",
})
test("201 status for builder skill", code == 201, f"got {code}")
test("scope_type is agent", data.get("scope_type") == "agent")
test("agent_role is builder", data.get("agent_role") == "builder")
builder_skill_id = data.get("id")

# ----------------------------------------------------------------
# 3. Create agent-scoped skill (planner)
# ----------------------------------------------------------------
print("\n-- 3. Create agent-scoped skill (planner) --")
code, data = req("POST", "/skills", {
    "name": "Planner decomposition style",
    "description": "How Planner should break down tasks",
    "content": "Always decompose into subtasks with clear deliverables.",
    "scope_type": "agent",
    "agent_role": "planner",
})
test("201 status for planner skill", code == 201, f"got {code}")
test("scope_type is agent", data.get("scope_type") == "agent")
test("agent_role is planner", data.get("agent_role") == "planner")
planner_skill_id = data.get("id")

# ----------------------------------------------------------------
# 4. List all skills — should return 3
# ----------------------------------------------------------------
print("\n-- 4. List all skills --")
code, data = req("GET", "/skills")
test("200 status", code == 200, f"got {code}")
test("returns list", isinstance(data, list))
test("returns 3 skills", len(data) == 3, f"got {len(data)}")

# ----------------------------------------------------------------
# 5. List with scope_type=global — returns 1
# ----------------------------------------------------------------
print("\n-- 5. List filtered by scope_type=global --")
code, data = req("GET", "/skills?scope_type=global")
test("200 status", code == 200)
test("returns 1 skill", len(data) == 1, f"got {len(data)}")
test("skill is global", data[0].get("scope_type") == "global" if data else False)

# ----------------------------------------------------------------
# 6. List with scope_type=agent — returns 2
# ----------------------------------------------------------------
print("\n-- 6. List filtered by scope_type=agent --")
code, data = req("GET", "/skills?scope_type=agent")
test("200 status", code == 200)
test("returns 2 skills", len(data) == 2, f"got {len(data)}")

# ----------------------------------------------------------------
# 7. List with agent_role=builder — returns 1
# ----------------------------------------------------------------
print("\n-- 7. List filtered by agent_role=builder --")
code, data = req("GET", "/skills?agent_role=builder")
test("200 status", code == 200)
test("returns 1 skill", len(data) == 1, f"got {len(data)}")
test("agent_role is builder", data[0].get("agent_role") == "builder" if data else False)

# ----------------------------------------------------------------
# 8. List with enabled_only=true — returns all (all enabled)
# ----------------------------------------------------------------
print("\n-- 8. List with enabled_only=true (all enabled) --")
code, data = req("GET", "/skills?enabled_only=true")
test("200 status", code == 200)
test("returns 3 skills (all enabled)", len(data) == 3, f"got {len(data)}")

# ----------------------------------------------------------------
# 9. Update a skill (name and content)
# ----------------------------------------------------------------
print("\n-- 9. Update skill name and content --")
code, data = req("PATCH", f"/skills/{global_skill_id}", {
    "name": "Python best practices v2",
    "content": "Use type hints. Write docstrings. Prefer composition. Write tests.",
})
test("200 status", code == 200, f"got {code}")
test("name updated", data.get("name") == "Python best practices v2")
test("content updated", "Write tests" in data.get("content", ""))
test("scope_type unchanged", data.get("scope_type") == "global")

# ----------------------------------------------------------------
# 10. Update scope_type from global to agent (must set agent_role)
# ----------------------------------------------------------------
print("\n-- 10. Update scope_type global->agent with agent_role --")
code, data = req("PATCH", f"/skills/{global_skill_id}", {
    "scope_type": "agent",
    "agent_role": "qa",
})
test("200 status", code == 200, f"got {code}")
test("scope_type is agent", data.get("scope_type") == "agent")
test("agent_role is qa", data.get("agent_role") == "qa")
# Revert back to global for subsequent tests
req("PATCH", f"/skills/{global_skill_id}", {
    "scope_type": "global",
    "agent_role": None,
})

# ----------------------------------------------------------------
# 11. Disable a skill (is_enabled=false)
# ----------------------------------------------------------------
print("\n-- 11. Disable a skill --")
code, data = req("PATCH", f"/skills/{builder_skill_id}", {"is_enabled": False})
test("200 status", code == 200, f"got {code}")
test("is_enabled is false", data.get("is_enabled") is False)

# ----------------------------------------------------------------
# 12. enabled_only filter excludes disabled skill
# ----------------------------------------------------------------
print("\n-- 12. enabled_only excludes disabled skill --")
code, data = req("GET", "/skills?enabled_only=true")
test("200 status", code == 200)
test("returns 2 skills (1 disabled excluded)", len(data) == 2, f"got {len(data)}")
ids_in_result = [s.get("id") for s in data]
test("disabled builder skill excluded", builder_skill_id not in ids_in_result)

# ----------------------------------------------------------------
# 13. Delete a skill
# ----------------------------------------------------------------
print("\n-- 13. Delete skill --")
code, data = req("DELETE", f"/skills/{planner_skill_id}")
test("204 status", code == 204, f"got {code}")

# ----------------------------------------------------------------
# 14. Get deleted skill returns 404
# ----------------------------------------------------------------
print("\n-- 14. Get deleted skill returns 404 --")
code, data = req("GET", f"/skills/{planner_skill_id}")
test("404 status", code == 404, f"got {code}")

# ----------------------------------------------------------------
# 15. Create with scope_type=agent but no agent_role → 422
# ----------------------------------------------------------------
print("\n-- 15. Validation: agent scope without agent_role --")
code, data = req("POST", "/skills", {
    "name": "Bad skill",
    "scope_type": "agent",
})
test("422 status", code == 422, f"got {code}")
detail = data.get("detail", "")
test("error mentions agent_role", "agent_role" in str(detail).lower(), f"detail={detail}")

# ----------------------------------------------------------------
# 16. Create with scope_type=global but agent_role set → 422
# ----------------------------------------------------------------
print("\n-- 16. Validation: global scope with agent_role set --")
code, data = req("POST", "/skills", {
    "name": "Bad skill 2",
    "scope_type": "global",
    "agent_role": "builder",
})
test("422 status", code == 422, f"got {code}")
detail = data.get("detail", "")
test("error mentions agent_role", "agent_role" in str(detail).lower(), f"detail={detail}")

# ----------------------------------------------------------------
# 17. Create with invalid agent_role → 422
# ----------------------------------------------------------------
print("\n-- 17. Validation: invalid agent_role --")
code, data = req("POST", "/skills", {
    "name": "Bad skill 3",
    "scope_type": "agent",
    "agent_role": "wizard",
})
test("422 status", code == 422, f"got {code}")
detail = data.get("detail", "")
test("error mentions agent_role", "agent_role" in str(detail).lower(), f"detail={detail}")

# ----------------------------------------------------------------
# 18. Get nonexistent skill returns 404
# ----------------------------------------------------------------
print("\n-- 18. Get nonexistent skill returns 404 --")
code, data = req("GET", "/skills/nonexistent-id-xyz")
test("404 status", code == 404, f"got {code}")

# ----------------------------------------------------------------
# Summary
# ----------------------------------------------------------------
passed = sum(1 for _, s, _ in results if s == "PASS")
failed = sum(1 for _, s, _ in results if s == "FAIL")
total = len(results)

print()
print("=" * 70)
print(f"  Phase 14-1 Skills  Total: {total}  Pass: {passed}  Fail: {failed}")
print("=" * 70)

if failed:
    print(f"\n  {failed} test(s) FAILED")
    for name, status, detail in results:
        if status == "FAIL":
            print(f"    FAIL: {name}" + (f" -- {detail}" if detail else ""))
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
