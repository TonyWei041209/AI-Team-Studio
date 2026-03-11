import { useState, useEffect, useCallback } from "react";
import type { ApprovalRequest, ApprovalResolve } from "../types/api";
import { approvalsApi } from "../api/approvals";

export function useApprovals() {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await approvalsApi.listPending();
      setApprovals(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load approvals",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const resolve = useCallback(
    async (id: string, data: ApprovalResolve) => {
      await approvalsApi.resolve(id, data);
      setApprovals((prev) => prev.filter((a) => a.id !== id));
    },
    [],
  );

  return { approvals, loading, error, refresh, resolve };
}
