import { useState, useRef, useEffect } from "react";
import type { LogLevel } from "../types/api";
import { useLogs } from "../hooks/useLogs";
import "./LogsPanel.css";

const LEVEL_COLORS: Record<LogLevel, string> = {
  debug: "var(--text-muted)",
  info: "var(--accent-green)",
  warn: "var(--accent-yellow)",
  error: "var(--accent-red)",
};

const LEVEL_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "All Levels" },
  { value: "debug", label: "Debug" },
  { value: "info", label: "Info" },
  { value: "warn", label: "Warn" },
  { value: "error", label: "Error" },
];

export function LogsPanel() {
  const [selectedLevel, setSelectedLevel] = useState("");
  const { logs, loading, error, refresh } = useLogs(
    selectedLevel || undefined,
  );
  const logEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to newest entry (bottom of chronological list)
  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "auto" });
    }
  }, [logs]);

  const formatTimestamp = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString("en-US", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return iso;
    }
  };

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleDateString();
    } catch {
      return "";
    }
  };

  return (
    <div className="logs-panel">
      <div className="panel-header">
        <h2 className="panel-title">Logs</h2>
        <div className="panel-actions">
          <select
            className="form-input logs-level-filter"
            value={selectedLevel}
            onChange={(e) => setSelectedLevel(e.target.value)}
          >
            {LEVEL_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <button
            className="btn btn-secondary"
            onClick={refresh}
            title="Refresh"
          >
            &#8635;
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="panel-error">
          <span>&#9888; {error}</span>
          <button className="btn btn-secondary btn-sm" onClick={refresh}>
            Retry
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && <div className="panel-loading">Loading logs...</div>}

      {/* Empty */}
      {!loading && !error && logs.length === 0 && (
        <div className="panel-empty">No log entries found.</div>
      )}

      {/* Log entries — terminal style */}
      {!loading && logs.length > 0 && (
        <div className="log-terminal">
          <div className="log-terminal-header">
            <span className="log-count">{logs.length} entries</span>
          </div>
          <div className="log-entries">
            {[...logs].reverse().map((log) => (
              <div key={log.id} className="log-entry">
                <span className="log-date">{formatDate(log.created_at)}</span>
                <span className="log-time">
                  {formatTimestamp(log.created_at)}
                </span>
                <span
                  className="log-level"
                  style={{
                    color: LEVEL_COLORS[log.level],
                    borderColor: LEVEL_COLORS[log.level],
                  }}
                >
                  {log.level.toUpperCase().padEnd(5)}
                </span>
                <span className="log-source">[{log.source}]</span>
                <span className="log-message">{log.message}</span>
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}
    </div>
  );
}
