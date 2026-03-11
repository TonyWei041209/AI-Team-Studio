import type { Task, TaskCreate } from "../types/api";
import { api } from "./client";

export const tasksApi = {
  list: (projectId: string) =>
    api.get<Task[]>(`/api/projects/${projectId}/tasks`),
  get: (taskId: string) => api.get<Task>(`/api/tasks/${taskId}`),
  create: (projectId: string, data: TaskCreate) =>
    api.post<Task>(`/api/projects/${projectId}/tasks`, data),
  updateStatus: (taskId: string, status: string) =>
    api.patch<Task>(`/api/tasks/${taskId}/status`, { status }),
};
