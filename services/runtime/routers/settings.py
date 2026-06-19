"""Settings API endpoints (Phase 6A + 6C).

Manages provider configuration (API keys, base URLs, enabled state)
and per-role model mapping (provider, model, enabled per agent role).
API keys are stored in the local SQLite DB only — never in git-tracked files.
Settings responses mask API keys for display safety.
"""

import json
import logging
import os

from fastapi import APIRouter, HTTPException

from database import get_connection
from models import (
    AgentRole,
    ProviderSettingRead,
    ProviderSettingsResponse,
    ProviderSettingsPatch,
    RoleModelSetting,
    RoleModelSettingsResponse,
    RoleModelSettingsPatch,
)
from providers.base import mask_api_key
from providers.registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _load_all_settings() -> dict[str, dict]:
    """Read all rows from provider_settings into a dict keyed by provider_name."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT provider_name, api_key, base_url, enabled, config_json "
            "FROM provider_settings"
        ).fetchall()
        return {
            row["provider_name"]: {
                "api_key": row["api_key"],
                "base_url": row["base_url"],
                "enabled": bool(row["enabled"]),
                "config_json": row["config_json"],
            }
            for row in rows
        }
    finally:
        conn.close()


def _apply_settings_to_registry(settings: dict[str, dict]) -> None:
    """Push saved settings into the live provider registry."""
    registry = get_registry()
    for name, cfg in settings.items():
        extra = {}
        if cfg.get("base_url"):
            extra["base_url"] = cfg["base_url"]
        # Merge any extra config from config_json
        try:
            parsed = json.loads(cfg.get("config_json", "{}"))
            if isinstance(parsed, dict):
                extra.update(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        registry.configure(name, api_key=cfg.get("api_key", ""), **extra)


_ENV_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "KIMI_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


def _seed_from_env() -> None:
    """On first run, seed provider_settings from environment variables.

    Only inserts rows that don't already exist in the DB.
    This provides a zero-config path: set env vars, start app, done.
    """
    conn = get_connection()
    try:
        seeded = []
        for provider_name, env_var in _ENV_KEY_MAP.items():
            key = os.environ.get(env_var, "").strip()
            if not key:
                continue
            existing = conn.execute(
                "SELECT 1 FROM provider_settings WHERE provider_name = ?",
                (provider_name,),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO provider_settings (provider_name, api_key, base_url, enabled) "
                "VALUES (?, ?, '', 1)",
                (provider_name, key),
            )
            seeded.append(provider_name)
        if seeded:
            conn.commit()
            logger.info("[settings] Seeded provider keys from env: %s", ", ".join(seeded))
    finally:
        conn.close()


def load_and_apply_settings() -> None:
    """Load settings from DB and apply to the registry.

    Called during app startup (lifespan).
    Seeds from environment variables on first run if DB is empty.
    """
    _seed_from_env()
    settings = _load_all_settings()
    _apply_settings_to_registry(settings)
    configured = [n for n, c in settings.items() if c.get("api_key")]
    if configured:
        logger.info("[settings] Loaded provider settings for: %s", ", ".join(configured))


@router.get("/providers", response_model=ProviderSettingsResponse)
async def get_provider_settings():
    """Return all provider settings with masked API keys."""
    registry = get_registry()
    saved = _load_all_settings()

    items = []
    for name in registry.names():
        cfg = saved.get(name, {})
        items.append(
            ProviderSettingRead(
                provider_name=name,
                api_key_masked=mask_api_key(cfg.get("api_key", "")),
                base_url=cfg.get("base_url", ""),
                enabled=cfg.get("enabled", False),
            )
        )
    return ProviderSettingsResponse(providers=items)


@router.patch("/providers", response_model=ProviderSettingsResponse)
async def update_provider_settings(body: ProviderSettingsPatch):
    """Update provider settings (API keys, base URLs, enabled state).

    Only provided fields are updated — omitted fields keep their current value.
    After saving, the live registry is re-configured.
    """
    conn = get_connection()
    try:
        for item in body.providers:
            # Ensure row exists
            existing = conn.execute(
                "SELECT api_key, base_url, enabled FROM provider_settings WHERE provider_name = ?",
                (item.provider_name,),
            ).fetchone()

            if existing is None:
                # Insert new row
                conn.execute(
                    "INSERT INTO provider_settings (provider_name, api_key, base_url, enabled) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        item.provider_name,
                        item.api_key or "",
                        item.base_url or "",
                        1 if item.enabled else 0,
                    ),
                )
            else:
                # Merge: only update fields that were provided
                new_key = item.api_key if item.api_key is not None else existing["api_key"]
                new_url = item.base_url if item.base_url is not None else existing["base_url"]
                new_enabled = (
                    (1 if item.enabled else 0)
                    if item.enabled is not None
                    else existing["enabled"]
                )
                conn.execute(
                    "UPDATE provider_settings "
                    "SET api_key = ?, base_url = ?, enabled = ?, updated_at = datetime('now') "
                    "WHERE provider_name = ?",
                    (new_key, new_url, new_enabled, item.provider_name),
                )

            # Log without exposing key
            logger.info(
                "[settings] Updated provider %s (key=%s, enabled=%s)",
                item.provider_name,
                "set" if (item.api_key and item.api_key.strip()) else "unchanged",
                item.enabled if item.enabled is not None else "unchanged",
            )

        conn.commit()
    finally:
        conn.close()

    # Re-apply to live registry
    settings = _load_all_settings()
    _apply_settings_to_registry(settings)

    # Return updated settings (masked)
    return await get_provider_settings()


# ── Role-model mapping endpoints (Phase 6C) ────────────────────


_VALID_ROLES = {r.value for r in AgentRole}


def load_role_model_settings() -> dict[str, dict]:
    """Read all rows from role_model_settings into a dict keyed by role."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT role, provider, model, enabled FROM role_model_settings"
        ).fetchall()
        return {
            row["role"]: {
                "provider": row["provider"],
                "model": row["model"],
                "enabled": bool(row["enabled"]),
            }
            for row in rows
        }
    finally:
        conn.close()


@router.get("/role-models", response_model=RoleModelSettingsResponse)
async def get_role_model_settings():
    """Return per-role model configuration.

    No sensitive information is exposed — API keys live in provider_settings.
    """
    saved = load_role_model_settings()
    role_models = {}
    for role_val in _VALID_ROLES:
        cfg = saved.get(role_val, {"provider": "mock", "model": "", "enabled": False})
        role_models[role_val] = RoleModelSetting(
            role=role_val,
            provider=cfg["provider"],
            model=cfg["model"],
            enabled=cfg["enabled"],
        )
    return RoleModelSettingsResponse(role_models=role_models)


# Roles that are allowed to enable real model calls.
# Phase 6C: planner, reviewer.  Phase 6D: builder added (plan-only mode).
# QA-Real: qa added as a real static-review role (informs the Reviewer, never vetoes).
_REAL_MODEL_ALLOWED_ROLES = {"planner", "architect", "builder", "qa", "security_reviewer", "reviewer"}


@router.patch("/role-models", response_model=RoleModelSettingsResponse)
async def update_role_model_settings(body: RoleModelSettingsPatch):
    """Update per-role model configuration with strong validation.

    Validation rules:
    - role must be a valid AgentRole
    - When enabled=true: provider and model must be non-empty, provider must exist
    - Only roles in _REAL_MODEL_ALLOWED_ROLES may be enabled for real model calls
    - Only provided fields are updated — omitted fields keep their current value
    """
    registry = get_registry()
    registered_providers = set(registry.names()) | {"mock"}

    conn = get_connection()
    try:
        for item in body.role_models:
            # Validate role
            if item.role not in _VALID_ROLES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid role: {item.role}. Must be one of {sorted(_VALID_ROLES)}",
                )

            existing = conn.execute(
                "SELECT provider, model, enabled FROM role_model_settings WHERE role = ?",
                (item.role,),
            ).fetchone()

            new_provider = item.provider if item.provider is not None else (existing["provider"] if existing else "mock")
            new_model = item.model if item.model is not None else (existing["model"] if existing else "")
            new_enabled = (1 if item.enabled else 0) if item.enabled is not None else (existing["enabled"] if existing else 0)

            # Phase 6C constraint: Builder/QA cannot enable real models
            if bool(new_enabled) and item.role not in _REAL_MODEL_ALLOWED_ROLES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Role '{item.role}' cannot be enabled for real model calls in the current phase. "
                        f"Only {sorted(_REAL_MODEL_ALLOWED_ROLES)} support real model integration."
                    ),
                )

            # When enabling: provider must be valid and model must be non-empty
            if bool(new_enabled) and new_provider != "mock":
                if not new_provider:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Role '{item.role}': provider cannot be empty when enabled.",
                    )
                if new_provider not in registered_providers:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Role '{item.role}': provider '{new_provider}' is not registered. "
                               f"Available: {sorted(registered_providers)}",
                    )
                if not new_model:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Role '{item.role}': model cannot be empty when enabled with provider '{new_provider}'.",
                    )

            if existing is None:
                conn.execute(
                    "INSERT INTO role_model_settings (role, provider, model, enabled) "
                    "VALUES (?, ?, ?, ?)",
                    (item.role, new_provider, new_model, new_enabled),
                )
            else:
                conn.execute(
                    "UPDATE role_model_settings "
                    "SET provider = ?, model = ?, enabled = ?, updated_at = datetime('now') "
                    "WHERE role = ?",
                    (new_provider, new_model, new_enabled, item.role),
                )

            logger.info(
                "[settings] Updated role-model %s → provider=%s, model=%s, enabled=%s",
                item.role, new_provider, new_model, bool(new_enabled),
            )

        conn.commit()
    finally:
        conn.close()

    return await get_role_model_settings()


# ──────────────────────────────────────────────────────────────
#  Readiness summary  (Phase 19-4)
# ──────────────────────────────────────────────────────────────


_ALL_PROVIDERS = ["anthropic", "openai", "gemini", "deepseek", "kimi", "minimax"]
_ALL_ROLES = ["planner", "architect", "builder", "qa", "security_reviewer", "reviewer"]


@router.get("/readiness")
async def get_readiness_summary():
    """Read-only readiness summary.

    Returns which providers have API keys, which roles use real models,
    and a list of actionable missing steps.
    """
    conn = get_connection()
    try:
        # Provider status
        provider_rows = conn.execute(
            "SELECT provider_name, api_key FROM provider_settings"
        ).fetchall()
        provider_keys = {
            r["provider_name"]: bool(r["api_key"]) for r in provider_rows
        }
        providers_status = []
        for name in _ALL_PROVIDERS:
            providers_status.append({
                "name": name,
                "configured": provider_keys.get(name, False),
            })

        configured_count = sum(1 for p in providers_status if p["configured"])

        # Role-model status
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
                "role": role,
                "provider": provider,
                "model": model,
                "enabled": enabled,
                "is_real": is_real,
            })

        # Missing steps
        missing_steps = []
        if configured_count == 0:
            missing_steps.append("configure_at_least_one_provider")
        if real_model_count == 0:
            missing_steps.append("enable_at_least_one_real_model")
        # Check if any enabled role points to unconfigured provider
        for rs in roles_status:
            if rs["is_real"] and not provider_keys.get(rs["provider"], False):
                missing_steps.append(f"configure_provider_{rs['provider']}")

        overall_ready = configured_count > 0 and real_model_count > 0 and len(missing_steps) == 0

        return {
            "overall_ready": overall_ready,
            "providers": {
                "total": len(_ALL_PROVIDERS),
                "configured": configured_count,
                "details": providers_status,
            },
            "roles": {
                "total": len(_ALL_ROLES),
                "real_model_count": real_model_count,
                "details": roles_status,
            },
            "missing_steps": missing_steps,
        }
    finally:
        conn.close()
