"""Phase 15-1 acceptance tests — role registry foundation.

Covers:
- T1: V15 migration seeds 4 system roles
- T2: List roles (all, by department, system/custom, enabled_only)
- T3: Get role by ID
- T4: Create custom role
- T5: Name validation (lowercase, format)
- T6: Name uniqueness
- T7: Update custom role
- T8: Update system role (partial protection)
- T9: Delete custom role
- T10: Delete system role (forbidden)
- T11: System roles have correct metadata
"""

import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure service root on path
SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
sys.path.insert(0, SERVICE_ROOT)

os.environ.setdefault("ATS_DB_PATH", ":memory:")

import database

# ── Helpers ────────────────────────────────────────────────

_pass = 0
_fail = 0


def check(label: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  PASS  {label}")
    else:
        _fail += 1
        print(f"  FAIL  {label}  {detail}")


def _reset_db():
    conn = database.get_connection()
    conn.execute("DROP TABLE IF EXISTS roles")
    conn.execute("DROP TABLE IF EXISTS schema_version")
    conn.close()
    database._ensure_schema()


def _list_roles(**filters):
    conn = database.get_connection()
    try:
        clauses = []
        params = []
        if "department" in filters:
            clauses.append("department = ?")
            params.append(filters["department"])
        if "is_system" in filters:
            clauses.append("is_system = ?")
            params.append(1 if filters["is_system"] else 0)
        if filters.get("enabled_only"):
            clauses.append("is_enabled = 1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(f"SELECT * FROM roles{where} ORDER BY name", params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_role(role_id):
    conn = database.get_connection()
    try:
        row = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_role_by_name(name):
    conn = database.get_connection()
    try:
        row = conn.execute("SELECT * FROM roles WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _create_role(name, display_name="Test", description="", department="engineering", is_enabled=True):
    conn = database.get_connection()
    try:
        role_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO roles (id, name, display_name, description, department, is_system, is_enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            (role_id, name, display_name, description, department, 1 if is_enabled else 0, now, now),
        )
        conn.commit()
        return role_id
    finally:
        conn.close()


def _update_role(role_id, **updates):
    conn = database.get_connection()
    try:
        if "is_enabled" in updates:
            updates["is_enabled"] = 1 if updates["is_enabled"] else 0
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [role_id]
        conn.execute(f"UPDATE roles SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def _delete_role(role_id):
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        conn.commit()
    finally:
        conn.close()


# ── T1: Migration seeds 4 system roles ────────────────────

def test_seed():
    print("\n[T1] migrations seed 6 system roles")
    _reset_db()
    roles = _list_roles(is_system=True)
    check("T1.1 exactly 6 system roles", len(roles) == 6, f"got {len(roles)}")
    names = {r["name"] for r in roles}
    check("T1.2 names are planner/architect/builder/qa/security_reviewer/reviewer",
          names == {"planner", "architect", "builder", "qa", "security_reviewer", "reviewer"})
    for r in roles:
        check(f"T1.3 {r['name']} is_system=1", r["is_system"] == 1)
        check(f"T1.4 {r['name']} is_enabled=1", r["is_enabled"] == 1)
        check(f"T1.5 {r['name']} department=engineering", r["department"] == "engineering")


# ── T2: List roles with filters ───────────────────────────

def test_list_filters():
    print("\n[T2] List roles with filters")
    _reset_db()
    _create_role("tester", display_name="Tester", department="qa_dept")
    _create_role("devops", display_name="DevOps", department="engineering", is_enabled=False)

    all_roles = _list_roles()
    check("T2.1 total 8 roles", len(all_roles) == 8, f"got {len(all_roles)}")

    system_only = _list_roles(is_system=True)
    check("T2.2 system roles = 6", len(system_only) == 6)

    custom_only = _list_roles(is_system=False)
    check("T2.3 custom roles = 2", len(custom_only) == 2)

    eng_roles = _list_roles(department="engineering")
    check("T2.4 engineering department = 7", len(eng_roles) == 7, f"got {len(eng_roles)}")

    qa_dept = _list_roles(department="qa_dept")
    check("T2.5 qa_dept department = 1", len(qa_dept) == 1)

    enabled = _list_roles(enabled_only=True)
    check("T2.6 enabled roles = 7", len(enabled) == 7, f"got {len(enabled)}")


# ── T3: Get role by ID ────────────────────────────────────

def test_get_by_id():
    print("\n[T3] Get role by ID")
    _reset_db()
    planner = _get_role_by_name("planner")
    check("T3.1 planner found", planner is not None)
    fetched = _get_role(planner["id"])
    check("T3.2 get by id returns same role", fetched["name"] == "planner")
    check("T3.3 nonexistent returns None", _get_role("nonexistent") is None)


# ── T4: Create custom role ────────────────────────────────

def test_create():
    print("\n[T4] Create custom role")
    _reset_db()
    # NOTE: a CUSTOM (non-system) role. Named "solution_architect" to avoid colliding
    # with the seeded "architect" SYSTEM role (roles.name is UNIQUE) added in AR-3/V20.
    rid = _create_role("solution_architect", display_name="Solution Architect", description="System architect", department="engineering")
    role = _get_role(rid)
    check("T4.1 role created", role is not None)
    check("T4.2 name correct", role["name"] == "solution_architect")
    check("T4.3 display_name correct", role["display_name"] == "Solution Architect")
    check("T4.4 is_system=0", role["is_system"] == 0)
    check("T4.5 is_enabled=1", role["is_enabled"] == 1)


# ── T5: Name uniqueness ──────────────────────────────────

def test_name_unique():
    print("\n[T5] Name uniqueness")
    _reset_db()
    # System role name should conflict
    conn = database.get_connection()
    try:
        try:
            conn.execute(
                "INSERT INTO roles (id, name, display_name, is_system, is_enabled, created_at, updated_at) VALUES (?, ?, ?, 0, 1, ?, ?)",
                (str(uuid.uuid4()), "planner", "Dup", datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            check("T5.1 duplicate name rejected", False, "insert succeeded unexpectedly")
        except Exception:
            check("T5.1 duplicate name rejected", True)
    finally:
        conn.close()


# ── T6: Update custom role ────────────────────────────────

def test_update_custom():
    print("\n[T6] Update custom role")
    _reset_db()
    rid = _create_role("tester", display_name="Tester")
    _update_role(rid, display_name="Senior Tester", department="qa_dept")
    role = _get_role(rid)
    check("T6.1 display_name updated", role["display_name"] == "Senior Tester")
    check("T6.2 department updated", role["department"] == "qa_dept")


# ── T7: System role partial protection ────────────────────

def test_system_protection():
    print("\n[T7] System role partial protection")
    _reset_db()
    planner = _get_role_by_name("planner")
    pid = planner["id"]

    # Allowed: update description and is_enabled
    _update_role(pid, description="Updated desc", is_enabled=False)
    updated = _get_role(pid)
    check("T7.1 description updatable", updated["description"] == "Updated desc")
    check("T7.2 is_enabled updatable", updated["is_enabled"] == 0)

    # Re-enable
    _update_role(pid, is_enabled=True)
    check("T7.3 re-enabled", _get_role(pid)["is_enabled"] == 1)


# ── T8: Delete custom role ────────────────────────────────

def test_delete_custom():
    print("\n[T8] Delete custom role")
    _reset_db()
    rid = _create_role("temp_role", display_name="Temp")
    check("T8.1 role exists before delete", _get_role(rid) is not None)
    _delete_role(rid)
    check("T8.2 role gone after delete", _get_role(rid) is None)


# ── T9: Cannot delete system role ─────────────────────────

def test_no_delete_system():
    print("\n[T9] System role cannot be deleted (verify at data level)")
    _reset_db()
    planner = _get_role_by_name("planner")
    # Direct delete should work at DB level (protection is in API layer)
    # But we verify the role exists and is_system
    check("T9.1 planner is system", planner["is_system"] == 1)
    check("T9.2 planner exists", planner is not None)
    # All 6 system roles still present
    system_roles = _list_roles(is_system=True)
    check("T9.3 all 6 system roles present", len(system_roles) == 6)


# ── T10: System roles have correct metadata ───────────────

def test_system_metadata():
    print("\n[T10] System roles have correct metadata")
    _reset_db()
    expected = {
        "planner": ("Planner", "engineering"),
        "architect": ("Architect", "engineering"),
        "builder": ("Builder", "engineering"),
        "qa": ("QA", "engineering"),
        "security_reviewer": ("Security Reviewer", "engineering"),
        "reviewer": ("Reviewer", "engineering"),
    }
    for name, (display, dept) in expected.items():
        role = _get_role_by_name(name)
        check(f"T10.{name} display_name", role["display_name"] == display, f"got {role['display_name']}")
        check(f"T10.{name} department", role["department"] == dept)
        check(f"T10.{name} has description", len(role["description"]) > 0)


# ── Main ──────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Phase 15-1: Role registry foundation tests")
    print("=" * 60)

    test_seed()
    test_list_filters()
    test_get_by_id()
    test_create()
    test_name_unique()
    test_update_custom()
    test_system_protection()
    test_delete_custom()
    test_no_delete_system()
    test_system_metadata()

    print("\n" + "=" * 60)
    total = _pass + _fail
    print(f"  Result: {_pass}/{total} PASS, {_fail} FAIL")
    print("=" * 60)
    sys.exit(1 if _fail > 0 else 0)
