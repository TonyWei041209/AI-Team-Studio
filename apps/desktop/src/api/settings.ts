/**
 * Settings API client functions (Phase 6A + 6C).
 */

import { api } from "./client";
import type {
  ProviderSettingsResponse,
  ProviderSettingUpdate,
  RoleModelSettingsResponse,
  RoleModelSettingUpdate,
} from "../types/api";

export const settingsApi = {
  getProviders: () =>
    api.get<ProviderSettingsResponse>("/api/settings/providers"),

  updateProviders: (providers: ProviderSettingUpdate[]) =>
    api.patch<ProviderSettingsResponse>("/api/settings/providers", { providers }),

  getRoleModels: () =>
    api.get<RoleModelSettingsResponse>("/api/settings/role-models"),

  updateRoleModels: (role_models: RoleModelSettingUpdate[]) =>
    api.patch<RoleModelSettingsResponse>("/api/settings/role-models", { role_models }),

  getReadiness: () =>
    api.get<ReadinessSummary>("/api/settings/readiness"),
};

// Readiness types (Phase 19-4)
export interface ReadinessProviderStatus {
  name: string;
  configured: boolean;
}

export interface ReadinessRoleStatus {
  role: string;
  provider: string;
  model: string;
  enabled: boolean;
  is_real: boolean;
}

export interface ReadinessSummary {
  overall_ready: boolean;
  providers: {
    total: number;
    configured: number;
    details: ReadinessProviderStatus[];
  };
  roles: {
    total: number;
    real_model_count: number;
    details: ReadinessRoleStatus[];
  };
  missing_steps: string[];
}
