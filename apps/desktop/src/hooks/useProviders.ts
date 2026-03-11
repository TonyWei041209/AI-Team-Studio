/**
 * Hook for provider and settings state management (Phase 6A).
 */

import { useCallback, useEffect, useState } from "react";
import { providersApi } from "../api/providers";
import { settingsApi } from "../api/settings";
import type {
  ProviderInfo,
  ProviderModelInfo,
  ProviderSettingRead,
  ProviderTestResult,
  ProviderSettingUpdate,
} from "../types/api";

export function useProviders() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<ProviderModelInfo[]>([]);
  const [settings, setSettings] = useState<ProviderSettingRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [testResults, setTestResults] = useState<Record<string, ProviderTestResult>>({});

  const refresh = useCallback(async () => {
    try {
      const [p, m, s] = await Promise.all([
        providersApi.list(),
        providersApi.listModels(),
        settingsApi.getProviders(),
      ]);
      setProviders(p);
      setModels(m);
      setSettings(s.providers);
    } catch {
      // silent — connection may not be available yet
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const updateSettings = useCallback(
    async (updates: ProviderSettingUpdate[]) => {
      const result = await settingsApi.updateProviders(updates);
      setSettings(result.providers);
      return result;
    },
    [],
  );

  const testProvider = useCallback(async (providerName: string) => {
    const result = await providersApi.test(providerName);
    setTestResults((prev) => ({ ...prev, [providerName]: result }));
    return result;
  }, []);

  return {
    providers,
    models,
    settings,
    loading,
    testResults,
    refresh,
    updateSettings,
    testProvider,
  };
}
