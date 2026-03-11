import { useState, useEffect, useCallback, useRef } from "react";
import type { ApprovalRequest, ApprovalResolve } from "../types/api";
import { approvalsApi } from "../api/approvals";

const POLL_INTERVAL = 5000;
const MAX_FAILURES = 3;

export function useApprovals() {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const failCountRef = useRef(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const inFlightRef = useRef(false);
  const mountedRef = useRef(true);

  // Stop any running interval
  const stopPolling = useCallback(() => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  // Core fetch — silent=true for polling ticks, false for first load / manual refresh
  const fetchData = useCallback(async (silent: boolean) => {
    if (inFlightRef.current) return; // prevent concurrent requests
    inFlightRef.current = true;

    if (!silent) {
      setError(null);
    }

    try {
      const data = await approvalsApi.listPending();
      if (!mountedRef.current) return;
      setApprovals(data);
      failCountRef.current = 0;
      setError(null);
    } catch (err) {
      if (!mountedRef.current) return;
      if (silent) {
        // Polling failure — keep old data, increment failure count
        failCountRef.current += 1;
        if (failCountRef.current >= MAX_FAILURES) {
          stopPolling();
          setError("Connection lost. Click Refresh to reconnect.");
        }
      } else {
        // First load / manual refresh failure — show error immediately
        setError(
          err instanceof Error ? err.message : "Failed to load approvals",
        );
      }
    } finally {
      inFlightRef.current = false;
      if (mountedRef.current) {
        setInitialLoading(false);
      }
    }
  }, [stopPolling]);

  // Start polling interval
  const startPolling = useCallback(() => {
    stopPolling();
    intervalRef.current = setInterval(() => fetchData(true), POLL_INTERVAL);
  }, [stopPolling, fetchData]);

  // Mount: initial fetch + start polling
  useEffect(() => {
    mountedRef.current = true;
    fetchData(false);
    startPolling();
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, [fetchData, startPolling, stopPolling]);

  // Manual refresh — resets failure count, re-fetches, restarts polling
  const refresh = useCallback(async () => {
    failCountRef.current = 0;
    stopPolling();
    await fetchData(false);
    if (mountedRef.current) {
      startPolling();
    }
  }, [fetchData, startPolling, stopPolling]);

  // Resolve an approval — optimistic local removal
  const resolve = useCallback(
    async (id: string, data: ApprovalResolve) => {
      await approvalsApi.resolve(id, data);
      if (mountedRef.current) {
        setApprovals((prev) => prev.filter((a) => a.id !== id));
      }
    },
    [],
  );

  return {
    approvals,
    loading: initialLoading,
    error,
    refresh,
    resolve,
  };
}
