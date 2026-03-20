/**
 * Settings panel — Language, Provider configuration, model selection,
 * API key management, and per-role model routing (Phase 6A + 6C + 13-2).
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useProviders } from "../hooks/useProviders";
import { settingsApi } from "../api/settings";
import { SUPPORTED_LANGUAGES, setLanguage } from "../i18n/index.ts";
import type { LanguageCode } from "../i18n/index.ts";
import type { ProviderSettingUpdate, RoleModelSetting, RoleModelSettingUpdate } from "../types/api";

// Roles that support real model configuration (Phase 6C+6D)
const CONFIGURABLE_ROLES = ["planner", "builder", "reviewer"] as const;

export function SettingsPanel() {
  const { t, i18n } = useTranslation();
  const {
    providers,
    models,
    settings,
    loading,
    testResults,
    updateSettings,
    testProvider,
  } = useProviders();

  // Provider edit state
  const [edits, setEdits] = useState<Record<string, { apiKey: string; baseUrl: string }>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [testTimestamps, setTestTimestamps] = useState<Record<string, number>>({});

  // Role-model state
  const [roleModels, setRoleModels] = useState<Record<string, RoleModelSetting>>({});
  const [roleEdits, setRoleEdits] = useState<Record<string, Partial<RoleModelSettingUpdate>>>({});
  const [roleLoading, setRoleLoading] = useState(true);
  const [roleSaving, setRoleSaving] = useState<string | null>(null);
  const [roleError, setRoleError] = useState<string | null>(null);

  // Load role-model settings
  const refreshRoleModels = useCallback(async () => {
    try {
      const resp = await settingsApi.getRoleModels();
      setRoleModels(resp.role_models);
    } catch {
      // silent
    } finally {
      setRoleLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshRoleModels();
  }, [refreshRoleModels]);

  if (loading || roleLoading) {
    return <div className="settings-panel"><p className="settings-loading">{t("settings.loadingSettings")}</p></div>;
  }

  // ── Language handler ──

  const handleLanguageChange = (code: string) => {
    setLanguage(code as LanguageCode);
  };

  // ── Provider helpers ──

  const getEdit = (name: string) => {
    if (edits[name]) return edits[name];
    const s = settings.find((s) => s.provider_name === name);
    return {
      apiKey: "",
      baseUrl: s?.base_url ?? "",
    };
  };

  const setEdit = (name: string, field: "apiKey" | "baseUrl", value: string) => {
    setEdits((prev) => ({
      ...prev,
      [name]: { ...getEdit(name), [field]: value },
    }));
  };

  const handleSave = async (providerName: string) => {
    const edit = getEdit(providerName);
    const update: ProviderSettingUpdate = {
      provider_name: providerName,
      enabled: true,
    };
    if (edit.apiKey) update.api_key = edit.apiKey;
    if (edit.baseUrl !== undefined) update.base_url = edit.baseUrl;

    setSaving(providerName);
    try {
      await updateSettings([update]);
      setEdits((prev) => {
        const next = { ...prev };
        delete next[providerName];
        return next;
      });
      setSaveSuccess(providerName);
      setTimeout(() => setSaveSuccess(null), 2000);
    } finally {
      setSaving(null);
    }
  };

  const handleTest = async (providerName: string) => {
    setTesting(providerName);
    try {
      await testProvider(providerName);
      setTestTimestamps((prev) => ({ ...prev, [providerName]: Date.now() }));
    } finally {
      setTesting(null);
    }
  };

  const providerModels = (name: string) =>
    models.filter((m) => m.provider === name);

  // ── Role-model helpers ──

  const getRoleEdit = (role: string): Partial<RoleModelSettingUpdate> => {
    return roleEdits[role] || {};
  };

  const setRoleEdit = (role: string, field: "provider" | "model" | "enabled", value: string | boolean) => {
    setRoleEdits((prev) => ({
      ...prev,
      [role]: { ...getRoleEdit(role), role, [field]: value },
    }));
  };

  const handleRoleSave = async (role: string) => {
    const edit = getRoleEdit(role);
    const current = roleModels[role];
    const update: RoleModelSettingUpdate = {
      role,
      provider: edit.provider ?? current?.provider,
      model: edit.model ?? current?.model,
      enabled: edit.enabled ?? current?.enabled,
    };

    setRoleSaving(role);
    setRoleError(null);
    try {
      const resp = await settingsApi.updateRoleModels([update]);
      setRoleModels(resp.role_models);
      setRoleEdits((prev) => {
        const next = { ...prev };
        delete next[role];
        return next;
      });
    } catch (err: any) {
      const msg = err?.message || err?.detail || "Failed to save";
      setRoleError(`${role}: ${msg}`);
    } finally {
      setRoleSaving(null);
    }
  };

  // ── Status overview ──
  const configuredProviderCount = settings.filter(
    (s) => s.api_key_masked !== "(not set)"
  ).length;

  const realModelCount = CONFIGURABLE_ROLES.filter((role) => {
    const current = roleModels[role];
    return current?.enabled && current?.provider !== "mock";
  }).length;

  // System is "ready" when at least 1 provider configured AND at least 1 role using real model
  const overallReady = configuredProviderCount > 0 && realModelCount > 0;

  // ── Provider status helpers ──
  const getProviderStatusClass = (name: string): string => {
    const s = settings.find((s) => s.provider_name === name);
    const isConfigured = s?.api_key_masked !== "(not set)";
    const result = testResults[name];
    if (result?.ok) return "connected";
    if (result && !result.ok) return "failed";
    if (isConfigured) return "configured";
    return "not-configured";
  };

  const getProviderStatusLabel = (name: string): string => {
    const s = settings.find((s) => s.provider_name === name);
    const isConfigured = s?.api_key_masked !== "(not set)";
    const result = testResults[name];
    if (result?.ok) return t("settings.connected");
    if (result && !result.ok) return t("settings.connectionFailed");
    if (isConfigured) return t("settings.configured");
    return t("settings.notConfigured");
  };

  return (
    <div className="settings-panel">
      {/* ── Status Overview (Phase 19-1) ── */}
      <div className="settings-overview">
        <div className="settings-overview-card">
          <div className="settings-overview-value">{configuredProviderCount}/{providers.length}</div>
          <div className="settings-overview-label">{t("settings.providersConfigured")}</div>
          <div className="settings-overview-dots">
            {providers.map((p) => {
              const s = settings.find((s) => s.provider_name === p.name);
              const isConfigured = s?.api_key_masked !== "(not set)";
              return (
                <span
                  key={p.name}
                  className={`settings-overview-dot ${isConfigured ? "configured" : "not-configured"}`}
                  title={`${p.display_name}: ${isConfigured ? t("settings.configured") : t("settings.notConfigured")}`}
                />
              );
            })}
          </div>
        </div>

        <div className="settings-overview-card">
          <div className="settings-overview-value">{realModelCount}/{CONFIGURABLE_ROLES.length}</div>
          <div className="settings-overview-label">{t("settings.rolesUsingRealModel")}</div>
          <div className="settings-overview-dots">
            {CONFIGURABLE_ROLES.map((role) => {
              const current = roleModels[role];
              const isReal = current?.enabled && current?.provider !== "mock";
              return (
                <span
                  key={role}
                  className={`settings-overview-dot ${isReal ? "real" : "mock"}`}
                  title={`${role}: ${isReal ? t("settings.realModel") : t("settings.mockModel")}`}
                />
              );
            })}
          </div>
        </div>

        <div className="settings-overview-card">
          <div className={`settings-overview-value ${overallReady ? "ready" : "not-ready"}`}>
            {overallReady ? t("settings.readyStatus") : t("settings.setupNeeded")}
          </div>
          <div className="settings-overview-label">{t("settings.systemStatus")}</div>
        </div>
      </div>

      {/* ── Language (Phase 13-2) ── */}
      <h2>{t("settings.language")}</h2>
      <p className="settings-subtitle">{t("settings.languageDescription")}</p>

      <div className="provider-card" style={{ maxWidth: 400 }}>
        <div className="provider-field">
          <label>{t("settings.languageLabel")}</label>
          <select
            className="provider-input"
            value={i18n.language}
            onChange={(e) => handleLanguageChange(e.target.value)}
          >
            {SUPPORTED_LANGUAGES.map((lang) => (
              <option key={lang.code} value={lang.code}>
                {lang.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* ── Role Model Configuration (Phase 6C+6D) ── */}
      <h2 style={{ marginTop: 32 }}>{t("settings.roleModelConfig")}</h2>
      <p className="settings-subtitle">{t("settings.roleModelDescription")}</p>

      {roleError && (
        <div className="provider-test-result error" style={{ marginBottom: 12 }}>
          {roleError}
        </div>
      )}

      <div className="provider-cards">
        {CONFIGURABLE_ROLES.map((role) => {
          const current = roleModels[role];
          const edit = getRoleEdit(role);
          const effectiveProvider = edit.provider ?? current?.provider ?? "mock";
          const effectiveModel = edit.model ?? current?.model ?? "";
          const effectiveEnabled = edit.enabled ?? current?.enabled ?? false;

          // Derive role status for badge
          const isMock = effectiveProvider === "mock";
          const isReal = effectiveEnabled && !isMock;
          const providerSetting = settings.find((s) => s.provider_name === effectiveProvider);
          const providerNotConfigured = !isMock && providerSetting?.api_key_masked === "(not set)";

          return (
            <div key={role} className={`provider-card${isReal ? " role-card-real" : ""}`}>
              <div className="provider-card-header">
                <h3>{role.charAt(0).toUpperCase() + role.slice(1)}</h3>
                <div className="role-badges">
                  <span className={`role-mode-badge ${isReal ? "real" : "mock"}`}>
                    {isReal ? t("settings.realModel") : t("settings.mockModel")}
                  </span>
                  <span className={`provider-status ${effectiveEnabled ? "configured" : "not-configured"}`}>
                    {effectiveEnabled ? t("settings.enabled") : t("settings.disabled")}
                  </span>
                </div>
              </div>

              {role === "builder" && (
                <div className="role-info-banner role-info-warning">
                  {t("settings.builderPlanOnly")}
                </div>
              )}

              {providerNotConfigured && !isMock && (
                <div className="role-info-banner role-info-error">
                  {t("settings.providerKeyMissing", { provider: effectiveProvider })}
                </div>
              )}

              <div className="provider-field">
                <label>{t("settings.provider")}</label>
                <select
                  className="provider-input"
                  value={effectiveProvider}
                  onChange={(e) => setRoleEdit(role, "provider", e.target.value)}
                >
                  <option value="mock">{t("settings.mockOption")}</option>
                  {providers.map((p) => (
                    <option key={p.name} value={p.name}>{p.display_name}</option>
                  ))}
                </select>
              </div>

              <div className="provider-field">
                <label>{t("settings.model")}</label>
                <select
                  className="provider-input"
                  value={effectiveModel}
                  onChange={(e) => setRoleEdit(role, "model", e.target.value)}
                >
                  <option value="">{t("settings.modelPlaceholder")}</option>
                  {models
                    .filter((m) => m.provider === effectiveProvider)
                    .map((m) => (
                      <option key={m.id} value={m.id}>{m.display_name}</option>
                    ))}
                </select>
                {/* Allow free-text if model not in dropdown */}
                <input
                  type="text"
                  className="provider-input"
                  placeholder={t("settings.modelManualPlaceholder")}
                  value={effectiveModel}
                  onChange={(e) => setRoleEdit(role, "model", e.target.value)}
                  style={{ marginTop: 4 }}
                />
              </div>

              <div className="provider-field">
                <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input
                    type="checkbox"
                    checked={effectiveEnabled}
                    onChange={(e) => setRoleEdit(role, "enabled", e.target.checked)}
                  />
                  {t("settings.enableRealModel")}
                </label>
              </div>

              <div className="provider-actions">
                <button
                  className="btn btn-primary"
                  disabled={roleSaving === role}
                  onClick={() => handleRoleSave(role)}
                >
                  {roleSaving === role ? t("settings.saving") : t("settings.save")}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* QA info */}
      <div style={{ margin: "12px 0", padding: "8px 12px", background: "rgba(255,255,255,0.05)", borderRadius: 6, fontSize: 13, color: "#999" }}>
        <strong>QA</strong> — {t("settings.qaNote")}
      </div>

      {/* ── Provider Settings (Phase 6A) ── */}
      <h2 style={{ marginTop: 32 }}>{t("settings.providerSettings")}</h2>
      <p className="settings-subtitle">{t("settings.providerDescription")}</p>

      <div className="provider-cards">
        {providers.map((p) => {
          const s = settings.find((s) => s.provider_name === p.name);
          const edit = getEdit(p.name);
          const result = testResults[p.name];
          const pModels = providerModels(p.name);

          return (
            <div key={p.name} className="provider-card">
              <div className="provider-card-header">
                <h3>{p.display_name}</h3>
                <span className={`provider-status ${getProviderStatusClass(p.name)}`}>
                  {getProviderStatusLabel(p.name)}
                </span>
              </div>

              <div className="provider-field">
                <label>{t("settings.apiKey")}</label>
                <div className="provider-field-row">
                  <input
                    type="password"
                    placeholder={s?.api_key_masked ?? t("settings.apiKeyPlaceholder")}
                    value={edit.apiKey}
                    onChange={(e) => setEdit(p.name, "apiKey", e.target.value)}
                    className="provider-input"
                  />
                </div>
                {s?.api_key_masked && s.api_key_masked !== "(not set)" && (
                  <span className="provider-hint">{t("settings.currentKey", { masked: s.api_key_masked })}</span>
                )}
              </div>

              {["openai", "deepseek", "kimi", "minimax"].includes(p.name) && (
                <div className="provider-field">
                  <label>{t("settings.baseUrl")}</label>
                  <input
                    type="text"
                    placeholder={s?.base_url || t("settings.baseUrlPlaceholder")}
                    value={edit.baseUrl}
                    onChange={(e) => setEdit(p.name, "baseUrl", e.target.value)}
                    className="provider-input"
                  />
                </div>
              )}

              {pModels.length > 0 && (
                <div className="provider-field">
                  <label>{t("settings.availableModels")}</label>
                  <div className="provider-models">
                    {pModels.map((m) => (
                      <span key={m.id} className="model-tag">{m.display_name}</span>
                    ))}
                  </div>
                </div>
              )}

              <div className="provider-actions">
                <button
                  className="btn btn-primary"
                  disabled={saving === p.name || !edit.apiKey}
                  onClick={() => handleSave(p.name)}
                >
                  {saving === p.name ? t("settings.saving") : t("settings.save")}
                </button>
                <button
                  className="btn btn-secondary"
                  disabled={testing === p.name || s?.api_key_masked === "(not set)"}
                  onClick={() => handleTest(p.name)}
                >
                  {testing === p.name ? t("settings.testing") : t("settings.testConnection")}
                </button>
                {saveSuccess === p.name && (
                  <span className="provider-save-success">{t("settings.saved")}</span>
                )}
              </div>

              {result && (
                <div className={`provider-test-result ${result.ok ? "success" : "error"}`}>
                  {result.ok
                    ? t("settings.testSuccess", { ms: Math.round(result.latency_ms) })
                    : result.message}
                  {testTimestamps[p.name] && (
                    <span className="provider-test-time">
                      {new Date(testTimestamps[p.name]).toLocaleTimeString()}
                    </span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
