"""Phase 19-4: Readiness summary endpoint validation."""
import os, sys, uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "runtime"))
os.environ.setdefault("ATS_DB_PATH", ":memory:")

from database import get_connection, init_db


def _setup_db():
    """Initialize DB and seed test data."""
    init_db()
    conn = get_connection()
    # Seed a provider with API key
    conn.execute(
        "INSERT OR REPLACE INTO provider_settings (provider_name, api_key, base_url, enabled) "
        "VALUES (?, ?, ?, ?)",
        ("anthropic", "sk-test-key", "", 1),
    )
    conn.commit()
    conn.close()


def _readiness():
    """Simulate the readiness query logic."""
    _ALL_PROVIDERS = ["anthropic", "openai", "gemini", "deepseek", "kimi", "minimax"]
    _ALL_ROLES = ["planner", "architect", "builder", "qa", "security_reviewer", "reviewer"]
    conn = get_connection()
    try:
        provider_rows = conn.execute(
            "SELECT provider_name, api_key FROM provider_settings"
        ).fetchall()
        provider_keys = {r["provider_name"]: bool(r["api_key"]) for r in provider_rows}
        providers_status = []
        for name in _ALL_PROVIDERS:
            providers_status.append({"name": name, "configured": provider_keys.get(name, False)})
        configured_count = sum(1 for p in providers_status if p["configured"])

        role_rows = conn.execute(
            "SELECT role, provider, model, enabled FROM role_model_settings"
        ).fetchall()
        role_map = {r["role"]: dict(r) for r in role_rows}
        roles_status = []
        real_model_count = 0
        for role in _ALL_ROLES:
            rm = role_map.get(role, {})
            provider = rm.get("provider", "mock")
            model = rm.get("model", "")
            enabled = bool(rm.get("enabled", 0))
            is_real = enabled and provider != "mock" and bool(model)
            if is_real:
                real_model_count += 1
            roles_status.append({
                "role": role, "provider": provider, "model": model,
                "enabled": enabled, "is_real": is_real,
            })

        missing_steps = []
        if configured_count == 0:
            missing_steps.append("configure_at_least_one_provider")
        if real_model_count == 0:
            missing_steps.append("enable_at_least_one_real_model")
        for rs in roles_status:
            if rs["is_real"] and not provider_keys.get(rs["provider"], False):
                missing_steps.append(f"configure_provider_{rs['provider']}")

        overall_ready = configured_count > 0 and real_model_count > 0 and len(missing_steps) == 0

        return {
            "overall_ready": overall_ready,
            "providers": {"total": len(_ALL_PROVIDERS), "configured": configured_count, "details": providers_status},
            "roles": {"total": len(_ALL_ROLES), "real_model_count": real_model_count, "details": roles_status},
            "missing_steps": missing_steps,
        }
    finally:
        conn.close()


# ── Tests ──

def test_provider_configured():
    result = _readiness()
    assert result["providers"]["configured"] >= 1, "At least anthropic should be configured"
    anthropic = next(p for p in result["providers"]["details"] if p["name"] == "anthropic")
    assert anthropic["configured"] is True
    print("  [PASS] provider configured detection")


def test_unconfigured_providers():
    result = _readiness()
    openai = next(p for p in result["providers"]["details"] if p["name"] == "openai")
    assert openai["configured"] is False
    print("  [PASS] unconfigured provider detection")


def test_role_defaults():
    result = _readiness()
    roles = {r["role"]: r for r in result["roles"]["details"]}
    # Planner should be enabled by default (seeded in V5 migration)
    assert roles["planner"]["enabled"] is True
    assert roles["planner"]["provider"] == "anthropic"
    assert roles["planner"]["is_real"] is True
    print("  [PASS] role defaults")


def test_mock_roles():
    result = _readiness()
    roles = {r["role"]: r for r in result["roles"]["details"]}
    # Builder and QA default to mock
    assert roles["builder"]["is_real"] is False
    assert roles["qa"]["is_real"] is False
    print("  [PASS] mock role detection")


def test_overall_ready():
    result = _readiness()
    # With anthropic configured and planner+reviewer enabled, should be ready
    assert result["overall_ready"] is True
    print("  [PASS] overall ready when conditions met")


def test_missing_steps_empty_when_ready():
    result = _readiness()
    assert len(result["missing_steps"]) == 0
    print("  [PASS] no missing steps when ready")


def test_structure():
    result = _readiness()
    assert "overall_ready" in result
    assert "providers" in result
    assert "roles" in result
    assert "missing_steps" in result
    assert result["providers"]["total"] == 6
    assert result["roles"]["total"] == 6
    print("  [PASS] response structure")


if __name__ == "__main__":
    print("Phase 19-4: Readiness Summary Validation")
    print("=" * 50)
    _setup_db()

    passed = 0
    failed = 0
    for fn in [
        test_provider_configured,
        test_unconfigured_providers,
        test_role_defaults,
        test_mock_roles,
        test_overall_ready,
        test_missing_steps_empty_when_ready,
        test_structure,
    ]:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            failed += 1

    print(f"\n  {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
    print("  ALL PASS")
