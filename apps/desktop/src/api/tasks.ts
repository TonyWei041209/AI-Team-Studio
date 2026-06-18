import type { Task, TaskCreate } from "../types/api";
import { api } from "./client";

/** Shape returned by GET /api/tasks/{taskId}/runs (served by tasks.list_task_runs). */
export interface TaskAgentRun {
  id: string;
  role: string;
  status: string;
  model_provider: string | null;
  model_name: string | null;
  output_summary: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

export const tasksApi = {
  list: (projectId: string) =>
    api.get<Task[]>(`/api/projects/${projectId}/tasks`),
  get: (taskId: string) => api.get<Task>(`/api/tasks/${taskId}`),
  create: (projectId: string, data: TaskCreate) =>
    api.post<Task>(`/api/projects/${projectId}/tasks`, data),
  updateStatus: (taskId: string, status: string) =>
    api.patch<Task>(`/api/tasks/${taskId}/status`, { status }),
  orchestrate: (taskId: string) =>
    api.post<Record<string, unknown>>(`/api/tasks/${taskId}/orchestrate`, {}),
  getRuns: (taskId: string) =>
    api.get<TaskAgentRun[]>(`/api/tasks/${taskId}/runs`),
};
