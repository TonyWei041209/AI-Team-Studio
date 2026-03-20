import type { Role, RoleCreate, RoleUpdate } from "../types/api";
import { api } from "./client";

export const rolesApi = {
  list: () => api.get<Role[]>("/api/roles"),
  get: (id: string) => api.get<Role>(`/api/roles/${id}`),
  create: (data: RoleCreate) => api.post<Role>("/api/roles", data),
  update: (id: string, data: RoleUpdate) => api.patch<Role>(`/api/roles/${id}`, data),
  delete: (id: string) => api.delete(`/api/roles/${id}`),
};
