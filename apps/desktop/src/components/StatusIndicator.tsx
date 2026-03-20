import { useTranslation } from "react-i18next";
import type { ConnectionState } from "../types/api";

interface StatusIndicatorProps {
  state: ConnectionState;
}

export function StatusIndicator({ state }: StatusIndicatorProps) {
  const { t } = useTranslation();

  const color =
    state === "connected"
      ? "var(--accent-green)"
      : state === "checking"
        ? "var(--accent-yellow)"
        : "var(--accent-red)";

  const labelKey =
    state === "connected"
      ? "status.connected"
      : state === "checking"
        ? "status.connecting"
        : "status.disconnected";

  return (
    <div className="status-indicator">
      <span className="status-dot" style={{ backgroundColor: color }} />
      <span className="status-text">{t(labelKey)}</span>
    </div>
  );
}
