/**
 * Settings panel — Provider configuration, model selection, API key management,
 * and per-role model routing (Phase 6A + 6C).
 */

import { useCallback, useEffect, useState } from "react";
import { useProviders } from "../hooks/useProviders";
import { settingsApi } from "../api/settings";
import type { ProviderSettingUpdate, RoleModelSetting, RoleModelSettingUpdate } from "../types/api";

// Roles that support real model configuration in Phase 6C
const CONFIGURABLE_ROLES = ["planner", "reviewer"] as const;

export function SettingsPanel() {
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
    return <div className="settings-panel"><p className="settings-loading">Loading settings...</p></div>;
  }

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
    } finally {
      setSaving(null);
    }
  };

  const handleTest = async (providerName: string) => {
    setTesting(providerName);
    try {
      await testProvider(providerName);
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

  return (
    <div className="settings-panel">
      {/* ── Role Model Configuration (Phase 6C) ── */}
      <h2>Role Model Configuration</h2>
      <p className="settings-subtitle">
        Configure which provider and model each agent role uses.
        Only Planner and Reviewer support real model integration.
      </p>

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

          return (
            <div key={role} className="provider-card">
              <div className="provider-card-header">
                <h3>{role.charAt(0).toUpperCase() + role.slice(1)}</h3>
                <span className={`provider-status ${effectiveEnabled ? "configured" : "not-configured"}`}>
                  {effectiveEnabled ? "Enabled" : "Disabled"}
                </span>
              </div>

              <div className="provider-field">
                <label>Provider</label>
                <select
                  className="provider-input"
                  value={effectiveProvider}
                  onChange={(e) => setRoleEdit(role, "provider", e.target.value)}
                >
                  <option value="mock">mock (disabled)</option>
                  {providers.map((p) => (
                    <option key={p.name} value={p.name}>{p.display_name}</option>
                  ))}
                </select>
              </div>

              <div className="provider-field">
                <label>Model</label>
                <select
                  className="provider-input"
                  value={effectiveModel}
                  onChange={(e) => setRoleEdit(role, "model", e.target.value)}
                >
                  <option value="">Select model...</option>
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
                  placeholder="Or enter model ID manually..."
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
                  Enable real model calls
                </label>
              </div>

              <div className="provider-actions">
                <button
                  className="btn btn-primary"
                  disabled={roleSaving === role}
                  onClick={() => handleRoleSave(role)}
                >
                  {roleSaving === role ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Builder / QA info */}
      <div style={{ margin: "12px 0", padding: "8px 12px", background: "rgba(255,255,255,0.05)", borderRadius: 6, fontSize: 13, color: "#999" }}>
        <strong>Builder</strong> and <strong>QA</strong> roles use mock executors in the current phase.
        Real model integration for these roles will be available in a future update.
      </div>

      {/* ── Provider Settings (Phase 6A) ── */}
      <h2 style={{ marginTop: 32 }}>Provider Settings</h2>
      <p className="settings-subtitle">
        Configure LLM providers and API keys. Keys are stored locally — never in git.
      </p>

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
                <span className={`provider-status ${s?.api_key_masked !== "(not set)" ? "configured" : "not-configured"}`}>
                  {s?.api_key_masked !== "(not set)" ? "Configured" : "Not configured"}
                </span>
              </div>

              <div className="provider-field">
                <label>API Key</label>
                <div className="provider-field-row">
                  <input
                    type="password"
                    placeholder={s?.api_key_masked ?? "Enter API key..."}
                    value={edit.apiKey}
                    onChange={(e) => setEdit(p.name, "apiKey", e.target.value)}
                    className="provider-input"
                  />
                </div>
                {s?.api_key_masked && s.api_key_masked !== "(not set)" && (
                  <span className="provider-hint">Current: {s.api_key_masked}</span>
                )}
              </div>

              {["openai", "deepseek", "kimi", "minimax"].includes(p.name) && (
                <div className="provider-field">
                  <label>Base URL</label>
                  <input
                    type="text"
                    placeholder={s?.base_url || "Default endpoint"}
                    value={edit.baseUrl}
                    onChange={(e) => setEdit(p.name, "baseUrl", e.target.value)}
                    className="provider-input"
                  />
                </div>
              )}

              {pModels.length > 0 && (
                <div className="provider-field">
                  <label>Available Models</label>
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
                  {saving === p.name ? "Saving..." : "Save"}
                </button>
                <button
                  className="btn btn-secondary"
                  disabled={testing === p.name || s?.api_key_masked === "(not set)"}
                  onClick={() => handleTest(p.name)}
                >
                  {testing === p.name ? "Testing..." : "Test Connection"}
                </button>
              </div>

              {result && (
                <div className={`provider-test-result ${result.ok ? "success" : "error"}`}>
                  {result.ok
                    ? `Connected (${result.latency_ms}ms)`
                    : result.message}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
