"""Settings API endpoints (Phase 6A).

Manages provider configuration (API keys, base URLs, enabled state).
API keys are stored in the local SQLite DB only — never in git-tracked files.
Settings responses mask API keys for display safety.
"""

import json
import logging

from fastapi import APIRouter

from database import get_connection
from models import (
    ProviderSettingRead,
    ProviderSettingsResponse,
    ProviderSettingsPatch,
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


def load_and_apply_settings() -> None:
    """Load settings from DB and apply to the registry.

    Called during app startup (lifespan).
    """
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
