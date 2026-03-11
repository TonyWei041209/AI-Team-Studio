import { useState, useEffect, useCallback } from "react";
import type { Task, TaskCreate } from "../types/api";
import { tasksApi } from "../api/tasks";

export function useTasks(projectId: string | null) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) {
      setTasks([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await tasksApi.list(projectId);
      setTasks(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tasks");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createTask = useCallback(
    async (body: TaskCreate) => {
      if (!projectId) throw new Error("No project selected");
      const created = await tasksApi.create(projectId, body);
      setTasks((prev) => [created, ...prev]);
      return created;
    },
    [projectId],
  );

  return { tasks, loading, error, refresh, createTask };
}
