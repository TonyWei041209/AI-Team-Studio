import { useState, useEffect, useCallback } from "react";
import type { Project, ProjectCreate } from "../types/api";
import { projectsApi } from "../api/projects";

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await projectsApi.list();
      setProjects(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load projects");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createProject = useCallback(async (body: ProjectCreate) => {
    const created = await projectsApi.create(body);
    setProjects((prev) => [created, ...prev]);
    return created;
  }, []);

  return { projects, loading, error, refresh, createProject };
}
