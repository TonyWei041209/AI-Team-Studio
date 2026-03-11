/**
 * Provider API client functions (Phase 6A).
 */

import { api } from "./client";
import type {
  ProviderInfo,
  ProviderModelInfo,
  ProviderTestResult,
} from "../types/api";

export const providersApi = {
  list: () => api.get<ProviderInfo[]>("/api/providers"),

  listModels: (provider?: string) => {
    const qs = provider ? `?provider=${encodeURIComponent(provider)}` : "";
    return api.get<ProviderModelInfo[]>(`/api/models${qs}`);
  },

  test: (provider: string) =>
    api.post<ProviderTestResult>("/api/provider-test", { provider }),
};
