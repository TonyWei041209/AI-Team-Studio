import { useState, useEffect, useCallback } from "react";
import type { Skill, SkillCreate, SkillUpdate } from "../types/api";
import { skillsApi } from "../api/skills";

export function useSkills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await skillsApi.list();
      setSkills(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load skills");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createSkill = useCallback(async (body: SkillCreate) => {
    const created = await skillsApi.create(body);
    setSkills((prev) => [created, ...prev]);
    return created;
  }, []);

  const updateSkill = useCallback(async (id: string, body: SkillUpdate) => {
    const updated = await skillsApi.update(id, body);
    setSkills((prev) => prev.map((s) => (s.id === id ? updated : s)));
    return updated;
  }, []);

  const deleteSkill = useCallback(async (id: string) => {
    await skillsApi.delete(id);
    setSkills((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const toggleSkill = useCallback(async (id: string, enabled: boolean) => {
    const updated = await skillsApi.update(id, { is_enabled: enabled });
    setSkills((prev) => prev.map((s) => (s.id === id ? updated : s)));
  }, []);

  return { skills, loading, error, refresh, createSkill, updateSkill, deleteSkill, toggleSkill };
}
