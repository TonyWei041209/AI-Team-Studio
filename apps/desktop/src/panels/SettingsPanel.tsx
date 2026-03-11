/**
 * Settings panel — Provider configuration, model selection, API key management.
 * Phase 6A: minimal but functional.
 */

import { useState } from "react";
import { useProviders } from "../hooks/useProviders";
import type { ProviderSettingUpdate } from "../types/api";

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

  // Local edit state keyed by provider name
  const [edits, setEdits] = useState<Record<string, { apiKey: string; baseUrl: string }>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);

  if (loading) {
    return <div className="settings-panel"><p className="settings-loading">Loading provider settings...</p></div>;
  }

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
      // Clear local edit after save
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

  return (
    <div className="settings-panel">
      <h2>Provider Settings</h2>
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

              {/* Show base_url only for openai-compatible providers */}
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
