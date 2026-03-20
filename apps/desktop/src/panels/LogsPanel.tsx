import { useState, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import type { LogLevel } from "../types/api";
import { useLogs } from "../hooks/useLogs";
import "./LogsPanel.css";

const LEVEL_COLORS: Record<LogLevel, string> = {
  debug: "var(--text-muted)",
  info: "var(--accent-green)",
  warn: "var(--accent-yellow)",
  error: "var(--accent-red)",
};

export function LogsPanel() {
  const { t } = useTranslation();
  const [selectedLevel, setSelectedLevel] = useState("");

  const levelOptions = [
    { value: "", labelKey: "logs.allLevels" },
    { value: "debug", labelKey: "logs.debug" },
    { value: "info", labelKey: "logs.info" },
    { value: "warn", labelKey: "logs.warn" },
    { value: "error", labelKey: "logs.error" },
  ];
  const { logs, loading, error, refresh, lastUpdated } = useLogs(
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
        <h2 className="panel-title">{t("logs.title")}</h2>
        <div className="panel-actions">
          <select
            className="form-input logs-level-filter"
            data-testid="logs-level-filter"
            value={selectedLevel}
            onChange={(e) => setSelectedLevel(e.target.value)}
          >
            {levelOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {t(opt.labelKey)}
              </option>
            ))}
          </select>
          {lastUpdated && (
            <span className="last-updated" data-testid="logs-last-updated">
              {lastUpdated.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </span>
          )}
          <button
            className="btn btn-secondary"
            onClick={refresh}
            title={t("logs.refresh")}
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
            {t("logs.retry")}
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && <div className="panel-loading">{t("logs.loading")}</div>}

      {/* Empty */}
      {!loading && !error && logs.length === 0 && (
        <div className="panel-empty">{t("logs.empty")}</div>
      )}

      {/* Log entries — terminal style */}
      {!loading && logs.length > 0 && (
        <div className="log-terminal">
          <div className="log-terminal-header">
            <span className="log-count" data-testid="log-count">{t("logs.entries", { count: logs.length })}</span>
          </div>
          <div className="log-entries" data-testid="log-entries">
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
