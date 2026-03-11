import type { Project, ProjectCreate } from "../types/api";
import { api } from "./client";

export const projectsApi = {
  list: () => api.get<Project[]>("/api/projects"),
  get: (id: string) => api.get<Project>(`/api/projects/${id}`),
  create: (data: ProjectCreate) => api.post<Project>("/api/projects", data),
  delete: (id: string) => api.delete(`/api/projects/${id}`),
};
