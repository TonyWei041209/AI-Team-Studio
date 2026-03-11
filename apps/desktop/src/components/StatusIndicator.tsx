import type { ConnectionState } from "../types/api";

interface StatusIndicatorProps {
  state: ConnectionState;
}

export function StatusIndicator({ state }: StatusIndicatorProps) {
  const color =
    state === "connected"
      ? "var(--accent-green)"
      : state === "checking"
        ? "var(--accent-yellow)"
        : "var(--accent-red)";
  const label =
    state === "connected"
      ? "Connected"
      : state === "checking"
        ? "Connecting..."
        : "Disconnected";

  return (
    <div className="status-indicator">
      <span className="status-dot" style={{ backgroundColor: color }} />
      <span className="status-text">{label}</span>
    </div>
  );
}
