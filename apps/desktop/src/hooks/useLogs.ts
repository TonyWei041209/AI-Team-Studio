import { useState, useEffect, useCallback, useRef } from "react";
import type { LogEvent } from "../types/api";
import { logsApi } from "../api/logs";

const POLL_INTERVAL = Number(import.meta.env.VITE_LOGS_POLL_MS) || 8000;
const MAX_FAILURES = 3;

export function useLogs(level?: string) {
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

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

  // Core fetch — silent=true for polling ticks
  const fetchData = useCallback(
    async (silent: boolean) => {
      if (inFlightRef.current) return; // prevent concurrent requests
      inFlightRef.current = true;

      if (!silent) {
        setError(null);
      }

      try {
        const data = await logsApi.recent(level, 100);
        if (!mountedRef.current) return;
        setLogs(data);
        setLastUpdated(new Date());
        failCountRef.current = 0;
        setError(null);
      } catch (err) {
        if (!mountedRef.current) return;
        if (silent) {
          failCountRef.current += 1;
          if (failCountRef.current >= MAX_FAILURES) {
            stopPolling();
            setError("Connection lost. Click Refresh to reconnect.");
          }
        } else {
          setError(
            err instanceof Error ? err.message : "Failed to load logs",
          );
        }
      } finally {
        inFlightRef.current = false;
        if (mountedRef.current) {
          setInitialLoading(false);
        }
      }
    },
    [level, stopPolling],
  );

  // Start polling interval with current fetchData
  const startPolling = useCallback(() => {
    stopPolling();
    intervalRef.current = setInterval(() => fetchData(true), POLL_INTERVAL);
  }, [stopPolling, fetchData]);

  // When level changes (or on mount): reset state, immediate fetch, rebuild interval
  useEffect(() => {
    mountedRef.current = true;
    failCountRef.current = 0;
    setInitialLoading(true);
    setError(null);

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

  return { logs, loading: initialLoading, error, refresh, lastUpdated };
}
