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
};
