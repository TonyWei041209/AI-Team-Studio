import type { AuditEvent } from "./types";
import { OBJECT_TYPE_COLORS, AUDIT_STATUS_COLORS, formatAuditTimestamp } from "./types";

interface AuditTrailSectionProps {
  events: AuditEvent[];
  count: number | null;
  loading: boolean;
  error: string | null;
}

export function AuditTrailSection({
  events,
  count,
  loading,
  error,
}: AuditTrailSectionProps) {
  return (
    <div className="audit-trail-section">
      <div className="audit-trail-header">
        <span className="proposal-label" style={{ margin: 0 }}>
          {count !== null
            ? `Audit Trail (${count} events)`
            : "Audit Trail"}
        </span>
      </div>
      {loading && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Loading audit trail...
        </div>
      )}
      {error && (
        <div className="snapshot-error">{error}</div>
      )}
      {!loading && !error && events.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No audit events yet
        </div>
      )}
      {!loading && events.length > 0 && (
        <div className="audit-trail-list">
          {events.map((ev, idx) => (
            <div key={`${ev.object_id}-${ev.event_type}-${idx}`} className="audit-event-row">
              <span className="audit-event-ts">
                {formatAuditTimestamp(ev.timestamp)}
              </span>
              <span
                className="badge audit-event-type-badge"
                style={{
                  color: OBJECT_TYPE_COLORS[ev.object_type] || "var(--text-muted)",
                  borderColor: OBJECT_TYPE_COLORS[ev.object_type] || "var(--text-muted)",
                }}
              >
                {ev.object_type.replace("_", " ")}
              </span>
              <span
                className="audit-event-status"
                style={{
                  color: AUDIT_STATUS_COLORS[ev.status] || "var(--text-muted)",
                }}
              >
                {ev.status}
              </span>
              <span className="audit-event-summary">{ev.summary}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
