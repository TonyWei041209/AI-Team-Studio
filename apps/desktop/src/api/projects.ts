import type { Project, ProjectCreate, ProjectParticipant, ProjectParticipantsUpdate } from "../types/api";
import { api } from "./client";

export const projectsApi = {
  list: () => api.get<Project[]>("/api/projects"),
  get: (id: string) => api.get<Project>(`/api/projects/${id}`),
  create: (data: ProjectCreate) => api.post<Project>("/api/projects", data),
  delete: (id: string) => api.delete(`/api/projects/${id}`),
  getParticipants: (projectId: string) =>
    api.get<ProjectParticipant[]>(`/api/projects/${projectId}/participants`),
  updateParticipants: (projectId: string, data: ProjectParticipantsUpdate) =>
    api.put<ProjectParticipant[]>(`/api/projects/${projectId}/participants`, data),
  getGodotInfo: (projectId: string) =>
    api.get<GodotInfo>(`/api/projects/${projectId}/godot-info`),
};

// Godot detection types (Phase 5.8)
export interface GodotInfo {
  is_godot: boolean;
  project_godot_path?: string;
  project_name?: string | null;
  config_version?: string | null;
  godot_version?: string | null;
  renderer?: string | null;
  reason?: string;
}
