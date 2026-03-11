import type { LogEvent } from "../types/api";
import { api } from "./client";

export const logsApi = {
  recent: (level?: string, limit?: number) => {
    const params = new URLSearchParams();
    if (level) params.set("level", level);
    if (limit) params.set("limit", String(limit));
    const qs = params.toString();
    return api.get<LogEvent[]>(`/api/logs/recent${qs ? `?${qs}` : ""}`);
  },
};
