/**
 * Settings API client functions (Phase 6A).
 */

import { api } from "./client";
import type {
  ProviderSettingsResponse,
  ProviderSettingUpdate,
} from "../types/api";

export const settingsApi = {
  getProviders: () =>
    api.get<ProviderSettingsResponse>("/api/settings/providers"),

  updateProviders: (providers: ProviderSettingUpdate[]) =>
    api.patch<ProviderSettingsResponse>("/api/settings/providers", { providers }),
};
