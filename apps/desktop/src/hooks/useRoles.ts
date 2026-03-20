import { useState, useEffect, useCallback } from "react";
import type { Role, RoleCreate, RoleUpdate } from "../types/api";
import { rolesApi } from "../api/roles";

export function useRoles() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await rolesApi.list();
      setRoles(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load roles");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createRole = useCallback(async (body: RoleCreate) => {
    const created = await rolesApi.create(body);
    setRoles((prev) => [created, ...prev]);
    return created;
  }, []);

  const updateRole = useCallback(async (id: string, body: RoleUpdate) => {
    const updated = await rolesApi.update(id, body);
    setRoles((prev) => prev.map((r) => (r.id === id ? updated : r)));
    return updated;
  }, []);

  const deleteRole = useCallback(async (id: string) => {
    await rolesApi.delete(id);
    setRoles((prev) => prev.filter((r) => r.id !== id));
  }, []);

  return { roles, loading, error, refresh, createRole, updateRole, deleteRole };
}
