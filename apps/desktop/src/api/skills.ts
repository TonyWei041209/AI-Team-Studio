import type { Skill, SkillCreate, SkillUpdate } from "../types/api";
import { api } from "./client";

export const skillsApi = {
  list: () => api.get<Skill[]>("/api/skills"),
  get: (id: string) => api.get<Skill>(`/api/skills/${id}`),
  create: (data: SkillCreate) => api.post<Skill>("/api/skills", data),
  update: (id: string, data: SkillUpdate) => api.patch<Skill>(`/api/skills/${id}`, data),
  delete: (id: string) => api.delete(`/api/skills/${id}`),
};
