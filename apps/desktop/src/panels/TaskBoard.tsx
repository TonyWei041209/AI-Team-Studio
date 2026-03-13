import { useState, useEffect, useCallback } from "react";
import type { TaskCreate, TaskStatus, TaskPriority } from "../types/api";
import { useTasks } from "../hooks/useTasks";
import { api } from "../api/client";
import "./TaskBoard.css";

interface TaskBoardProps {
  projectId: string | null;
}

// ── Proposal types (Phase 6E-A) ────────────────────────────

interface ProposedFile {
  path: string;
  action: "create" | "modify" | "delete";
  reason: string;
}

interface ProposedCommand {
  command: string;
  working_dir?: string;
  risk_level?: string;
  reason: string;
}

interface ProposalData {
  change_summary?: string;
  proposed_files?: ProposedFile[];
  proposed_commands?: ProposedCommand[];
  risk_level?: string;
  requires_approval?: boolean;
  approval_reasons?: string[];
}

interface ExecutionProposal {
  id: string;
  task_id: string;
  run_id: string;
  role: string;
  risk_level: string;
  requires_approval: boolean;
  status: string;
  proposal_data_parsed: ProposalData;
  approval_reasons_parsed: string[];
  created_at: string;
}

// ── Snapshot types (Phase 6E-B) ─────────────────────────
interface ExecutionSnapshotResponse {
  id: string;
  proposal_id: string;
  approval_id: string;
  task_id: string;
  snapshot_data: string;
  snapshot_data_parsed?: ProposalData;
  content_hash: string;
  risk_level: string;
  status: string;
  created_at: string;
}

// ── Execution Request types (Phase 6E-C) ────────────────
interface ExecutionRequestResponse {
  id: string;
  task_id: string;
  proposal_id: string;
  approval_id: string;
  snapshot_id: string;
  snapshot_content_hash: string;
  risk_level: string;
  status: string;
  created_at: string;
  updated_at: string;
}

// ── Dry-Run Result types (Phase 6F-B) ───────────────────
interface DryRunFileAction {
  path: string;
  action: string;
  status: string;
}

interface DryRunCommandAction {
  command: string;
  working_dir: string;
  status: string;
}

interface DryRunResultData {
  mode: string;
  summary: string;
  planned_file_actions: DryRunFileAction[];
  planned_command_actions: DryRunCommandAction[];
  warnings: string[];
  snapshot_content_hash?: string;
}

interface DryRunResult {
  id: string;
  execution_request_id: string;
  task_id: string;
  snapshot_id: string;
  snapshot_content_hash: string;
  status: string;
  result_data: DryRunResultData;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

// ── Audit Trail types (Phase 6E-E) ──────────────────────
interface AuditEvent {
  event_type: string;
  object_type: string;
  object_id: string;
  status: string;
  timestamp: string;
  summary: string;
  related_ids: Record<string, string>;
  detail: Record<string, unknown>;
}

interface AuditTrailResponse {
  task_id: string;
  events: AuditEvent[];
  count: number;
}

const OBJECT_TYPE_COLORS: Record<string, string> = {
  proposal: "var(--accent-blue)",
  approval: "var(--accent-yellow)",
  snapshot: "var(--accent-green)",
  execution_request: "#ff8c00",
};

const AUDIT_STATUS_COLORS: Record<string, string> = {
  approved: "var(--accent-green)",
  rejected: "var(--accent-red)",
  frozen: "var(--accent-green)",
  requested: "var(--accent-yellow)",
  confirmed: "var(--accent-green)",
  pending: "var(--text-muted)",
};

/** Format timestamp: today → HH:mm:ss, otherwise → YYYY-MM-DD HH:mm */
function formatAuditTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const isToday =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    if (isToday) {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  } catch {
    return iso;
  }
}

const RISK_COLORS: Record<string, string> = {
  low: "var(--accent-green)",
  medium: "var(--accent-yellow)",
  high: "#ff8c00",
  critical: "var(--accent-red)",
};

const ACTION_COLORS: Record<string, string> = {
  create: "var(--accent-green)",
  modify: "var(--accent-yellow)",
  delete: "var(--accent-red)",
};

const EXEC_REQUEST_STATUS_COLORS: Record<string, string> = {
  requested: "var(--accent-yellow)",
  confirmed: "var(--accent-green)",
  rejected: "var(--accent-red)",
};

const STATUS_COLORS: Record<TaskStatus, string> = {
  pending: "var(--text-muted)",
  planning: "var(--accent-blue)",
  in_progress: "var(--accent-yellow)",
  reviewing: "var(--accent-blue)",
  done: "var(--accent-green)",
  failed: "var(--accent-red)",
};

const PRIORITY_COLORS: Record<TaskPriority, string> = {
  low: "var(--text-muted)",
  medium: "var(--accent-blue)",
  high: "var(--accent-yellow)",
  critical: "var(--accent-red)",
};

export function TaskBoard({ projectId }: TaskBoardProps) {
  const { tasks, loading, error, refresh, createTask } = useTasks(projectId);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Form fields
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("medium");

  // Proposal expansion (Phase 6E-A)
  const [expandedTask, setExpandedTask] = useState<string | null>(null);
  const [proposals, setProposals] = useState<ExecutionProposal[]>([]);
  const [proposalLoading, setProposalLoading] = useState(false);

  // Snapshot viewer (Phase 6E-B)
  const [snapshots, setSnapshots] = useState<Record<string, ExecutionSnapshotResponse>>({});
  const [snapshotLoading, setSnapshotLoading] = useState<string | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  // Execution request (Phase 6E-C) — keyed by snapshot_id
  const [execRequests, setExecRequests] = useState<Record<string, ExecutionRequestResponse>>({});
  const [execReqLoading, setExecReqLoading] = useState<string | null>(null);
  const [execReqError, setExecReqError] = useState<string | null>(null);

  // Audit trail (Phase 6E-E)
  const [auditTrailTaskId, setAuditTrailTaskId] = useState<string | null>(null);
  const [auditTrailEvents, setAuditTrailEvents] = useState<AuditEvent[]>([]);
  const [auditTrailCount, setAuditTrailCount] = useState<number | null>(null);
  const [auditTrailLoading, setAuditTrailLoading] = useState(false);
  const [auditTrailError, setAuditTrailError] = useState<string | null>(null);

  // Dry-run results (Phase 6F-B) — keyed by execution_request_id
  const [dryRunResults, setDryRunResults] = useState<Record<string, DryRunResult>>({});
  const [dryRunLoading, setDryRunLoading] = useState<string | null>(null);
  const [dryRunError, setDryRunError] = useState<string | null>(null);

  const loadProposals = useCallback(async (taskId: string) => {
    setProposalLoading(true);
    try {
      const data = await api.get<{ proposals: ExecutionProposal[] }>(
        `/api/tasks/${taskId}/proposals`,
      );
      setProposals(data.proposals);
    } catch {
      setProposals([]);
    } finally {
      setProposalLoading(false);
    }
  }, []);

  const toggleProposals = useCallback(
    (taskId: string) => {
      if (expandedTask === taskId) {
        setExpandedTask(null);
        setProposals([]);
      } else {
        setExpandedTask(taskId);
        loadProposals(taskId);
      }
    },
    [expandedTask, loadProposals],
  );

  // Execution request: load existing for a snapshot (Phase 6E-C)
  const loadExecRequest = useCallback(async (snapshotId: string) => {
    const existing = await api.getOrNull<ExecutionRequestResponse>(
      `/api/snapshots/${snapshotId}/execution-request`,
    );
    if (existing) {
      setExecRequests((prev) => ({ ...prev, [snapshotId]: existing }));
      // If confirmed, also try loading dry-run result (Phase 6F-B)
      if (existing.status === "confirmed") {
        const drResult = await api.getOrNull<DryRunResult>(
          `/api/execution-requests/${existing.id}/dry-run`,
        );
        if (drResult) {
          setDryRunResults((prev) => ({ ...prev, [existing.id]: drResult }));
        }
      }
    }
  }, []);

  // Execution request: create (Phase 6E-C)
  const requestExecution = useCallback(async (snapshotId: string) => {
    setExecReqLoading(snapshotId);
    setExecReqError(null);
    try {
      const req = await api.post<ExecutionRequestResponse>(
        `/api/snapshots/${snapshotId}/request-execution`,
      );
      setExecRequests((prev) => ({ ...prev, [snapshotId]: req }));
    } catch (err) {
      setExecReqError(
        err instanceof Error ? err.message : "Failed to create execution request",
      );
    } finally {
      setExecReqLoading(null);
    }
  }, []);

  // Execution request: confirm or reject (Phase 6E-D)
  const updateExecRequestStatus = useCallback(
    async (snapshotId: string, requestId: string, newStatus: "confirmed" | "rejected") => {
      setExecReqLoading(snapshotId);
      setExecReqError(null);
      try {
        const updated = await api.patch<ExecutionRequestResponse>(
          `/api/execution-requests/${requestId}`,
          { status: newStatus },
        );
        setExecRequests((prev) => ({ ...prev, [snapshotId]: updated }));
      } catch (err) {
        setExecReqError(
          err instanceof Error ? err.message : "Failed to update execution request",
        );
      } finally {
        setExecReqLoading(null);
      }
    },
    [],
  );

  // Dry-run: load existing result (Phase 6F-B)
  const loadDryRunResult = useCallback(async (requestId: string) => {
    setDryRunLoading(requestId);
    setDryRunError(null);
    try {
      const result = await api.getOrNull<DryRunResult>(
        `/api/execution-requests/${requestId}/dry-run`,
      );
      if (result) {
        setDryRunResults((prev) => ({ ...prev, [requestId]: result }));
      }
    } catch {
      setDryRunError("Failed to load dry-run result");
    } finally {
      setDryRunLoading(null);
    }
  }, []);

  // Dry-run: trigger execution (Phase 6F-B)
  const triggerDryRun = useCallback(async (requestId: string) => {
    setDryRunLoading(requestId);
    setDryRunError(null);
    try {
      const result = await api.post<DryRunResult>(
        `/api/execution-requests/${requestId}/dry-run`,
      );
      setDryRunResults((prev) => ({ ...prev, [requestId]: result }));
    } catch (err) {
      setDryRunError(
        err instanceof Error ? err.message : "Failed to run dry-run",
      );
    } finally {
      setDryRunLoading(null);
    }
  }, []);

  // Audit trail: toggle open/close (Phase 6E-E)
  const toggleAuditTrail = useCallback(
    async (taskId: string) => {
      if (auditTrailTaskId === taskId) {
        // Close
        setAuditTrailTaskId(null);
        setAuditTrailEvents([]);
        setAuditTrailCount(null);
        setAuditTrailError(null);
        return;
      }
      // Open and load
      setAuditTrailTaskId(taskId);
      setAuditTrailLoading(true);
      setAuditTrailError(null);
      setAuditTrailEvents([]);
      setAuditTrailCount(null);
      try {
        const data = await api.get<AuditTrailResponse>(
          `/api/tasks/${taskId}/audit-trail`,
        );
        setAuditTrailEvents(data.events);
        setAuditTrailCount(data.count);
      } catch {
        setAuditTrailError("Failed to load audit trail");
        setAuditTrailEvents([]);
        setAuditTrailCount(null);
      } finally {
        setAuditTrailLoading(false);
      }
    },
    [auditTrailTaskId],
  );

  // When a snapshot is loaded, also check for existing execution request
  const freezeAndViewWithReqCheck = useCallback(async (proposalId: string) => {
    setSnapshotLoading(proposalId);
    setSnapshotError(null);
    try {
      const snap = await api.post<ExecutionSnapshotResponse>(
        `/api/proposals/${proposalId}/freeze`,
      );
      setSnapshots((prev) => ({ ...prev, [proposalId]: snap }));
      // Also load execution request if one exists
      await loadExecRequest(snap.id);
    } catch (err) {
      setSnapshotError(
        err instanceof Error ? err.message : "Failed to freeze snapshot",
      );
    } finally {
      setSnapshotLoading(null);
    }
  }, [loadExecRequest]);

  // Reset form state when project changes
  useEffect(() => {
    setShowForm(false);
    setTitle("");
    setDescription("");
    setPriority("medium");
    setFormError(null);
    setExpandedTask(null);
    setProposals([]);
    setSnapshots({});
    setSnapshotError(null);
    setExecRequests({});
    setExecReqError(null);
    setAuditTrailTaskId(null);
    setAuditTrailEvents([]);
    setAuditTrailCount(null);
    setAuditTrailError(null);
    setDryRunResults({});
    setDryRunError(null);
  }, [projectId]);

  if (!projectId) {
    return (
      <div className="task-board">
        <div className="panel-empty">
          Select a project first to view tasks.
        </div>
      </div>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;

    setSubmitting(true);
    setFormError(null);
    try {
      const body: TaskCreate = {
        title: title.trim(),
        description: description.trim() || undefined,
        priority,
      };
      await createTask(body);
      setTitle("");
      setDescription("");
      setPriority("medium");
      setShowForm(false);
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Failed to create task",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  return (
    <div className="task-board">
      <div className="panel-header">
        <h2 className="panel-title">Tasks</h2>
        <div className="panel-actions">
          <button
            className="btn btn-secondary"
            onClick={refresh}
            title="Refresh"
          >
            &#8635;
          </button>
          <button
            className="btn btn-primary"
            data-testid="task-create-btn"
            onClick={() => setShowForm(!showForm)}
          >
            {showForm ? "Cancel" : "+ New Task"}
          </button>
        </div>
      </div>

      {/* Create form */}
      {showForm && (
        <form className="task-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label className="form-label">Title *</label>
            <input
              className="form-input"
              data-testid="task-title-input"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Task title"
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label">Description</label>
            <textarea
              className="form-input form-textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
              rows={3}
            />
          </div>
          <div className="form-field">
            <label className="form-label">Priority</label>
            <select
              className="form-input"
              value={priority}
              onChange={(e) => setPriority(e.target.value as TaskPriority)}
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          </div>
          {formError && <div className="form-error">{formError}</div>}
          <button
            className="btn btn-primary"
            data-testid="task-submit-btn"
            type="submit"
            disabled={submitting || !title.trim()}
          >
            {submitting ? "Creating..." : "Create Task"}
          </button>
        </form>
      )}

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
      {loading && <div className="panel-loading">Loading tasks...</div>}

      {/* Empty */}
      {!loading && !error && tasks.length === 0 && (
        <div className="panel-empty">
          No tasks for this project. Create one to begin.
        </div>
      )}

      {/* Task list */}
      {!loading && tasks.length > 0 && (
        <div className="task-list" data-testid="task-list">
          {tasks.map((t) => (
            <div key={t.id} className="task-item" data-testid="task-item">
              <div className="task-item-header">
                <span className="task-title">{t.title}</span>
                <span className="task-date">{formatDate(t.updated_at)}</span>
              </div>
              {t.description && (
                <div className="task-desc">{t.description}</div>
              )}
              <div className="task-badges">
                <span
                  className="badge badge-status"
                  style={{
                    color: STATUS_COLORS[t.status],
                    borderColor: STATUS_COLORS[t.status],
                  }}
                >
                  {t.status.replace("_", " ")}
                </span>
                <span
                  className="badge badge-priority"
                  style={{
                    color: PRIORITY_COLORS[t.priority],
                    borderColor: PRIORITY_COLORS[t.priority],
                  }}
                >
                  {t.priority}
                </span>
                {t.assigned_agent_role && (
                  <span className="badge badge-role">
                    {t.assigned_agent_role}
                  </span>
                )}
                {t.status !== "pending" && (
                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ marginLeft: "auto", fontSize: 11 }}
                    onClick={() => toggleProposals(t.id)}
                  >
                    {expandedTask === t.id ? "Hide Proposals" : "Proposals"}
                  </button>
                )}
                <button
                  className="btn btn-secondary btn-sm"
                  style={{ fontSize: 11 }}
                  onClick={() => toggleAuditTrail(t.id)}
                >
                  {auditTrailTaskId === t.id
                    ? "Hide Audit Trail"
                    : auditTrailCount !== null && auditTrailTaskId === t.id
                      ? `Audit Trail (${auditTrailCount} events)`
                      : "Audit Trail"}
                </button>
              </div>

              {/* Audit Trail (Phase 6E-E) */}
              {auditTrailTaskId === t.id && (
                <div className="audit-trail-section">
                  <div className="audit-trail-header">
                    <span className="proposal-label" style={{ margin: 0 }}>
                      {auditTrailCount !== null
                        ? `Audit Trail (${auditTrailCount} events)`
                        : "Audit Trail"}
                    </span>
                  </div>
                  {auditTrailLoading && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      Loading audit trail...
                    </div>
                  )}
                  {auditTrailError && (
                    <div className="snapshot-error">{auditTrailError}</div>
                  )}
                  {!auditTrailLoading && !auditTrailError && auditTrailEvents.length === 0 && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      No audit events yet
                    </div>
                  )}
                  {!auditTrailLoading && auditTrailEvents.length > 0 && (
                    <div className="audit-trail-list">
                      {auditTrailEvents.map((ev, idx) => (
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
              )}

              {/* Execution Proposals (Phase 6E-A) */}
              {expandedTask === t.id && (
                <div className="proposal-section">
                  {proposalLoading && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      Loading proposals...
                    </div>
                  )}
                  {!proposalLoading && proposals.length === 0 && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      No proposals for this task.
                    </div>
                  )}
                  {!proposalLoading &&
                    proposals.map((p) => {
                      const pd = p.proposal_data_parsed;
                      return (
                        <div key={p.id} className="proposal-card">
                          <div className="proposal-header">
                            <span className="proposal-summary">
                              {pd.change_summary || "Execution Proposal"}
                            </span>
                            <span
                              className="badge"
                              style={{
                                color: RISK_COLORS[p.risk_level] || "#888",
                                borderColor: RISK_COLORS[p.risk_level] || "#888",
                                fontSize: 10,
                              }}
                            >
                              {p.risk_level}
                            </span>
                            {p.requires_approval && (
                              <span
                                className="badge"
                                style={{
                                  color: "var(--accent-yellow)",
                                  borderColor: "var(--accent-yellow)",
                                  fontSize: 10,
                                }}
                              >
                                approval required
                              </span>
                            )}
                            <span
                              className="badge"
                              style={{
                                color: "var(--text-muted)",
                                borderColor: "var(--text-muted)",
                                fontSize: 10,
                              }}
                            >
                              {p.status}
                            </span>
                          </div>

                          {/* Proposed files */}
                          {pd.proposed_files && pd.proposed_files.length > 0 && (
                            <div className="proposal-files">
                              <div className="proposal-label">Proposed Files</div>
                              {pd.proposed_files.map((f, i) => (
                                <div key={i} className="proposal-file-item">
                                  <span
                                    className="badge"
                                    style={{
                                      color: ACTION_COLORS[f.action] || "#888",
                                      borderColor: ACTION_COLORS[f.action] || "#888",
                                      fontSize: 10,
                                      marginRight: 6,
                                    }}
                                  >
                                    {f.action}
                                  </span>
                                  <span style={{ fontFamily: "monospace", fontSize: 12 }}>
                                    {f.path}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Proposed commands */}
                          {pd.proposed_commands && pd.proposed_commands.length > 0 && (
                            <div className="proposal-commands">
                              <div className="proposal-label">Proposed Commands</div>
                              {pd.proposed_commands.map((c, i) => (
                                <div key={i} className="proposal-cmd-item">
                                  <code style={{ fontSize: 11 }}>{c.command}</code>
                                  {c.risk_level && (
                                    <span
                                      className="badge"
                                      style={{
                                        color: RISK_COLORS[c.risk_level] || "#888",
                                        borderColor: RISK_COLORS[c.risk_level] || "#888",
                                        fontSize: 9,
                                        marginLeft: 6,
                                      }}
                                    >
                                      {c.risk_level}
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Approval reasons */}
                          {p.approval_reasons_parsed.length > 0 && (
                            <div className="proposal-reasons">
                              <div className="proposal-label">Approval Reasons</div>
                              <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11 }}>
                                {p.approval_reasons_parsed.map((r, i) => (
                                  <li key={i}>{r}</li>
                                ))}
                              </ul>
                            </div>
                          )}

                          {/* Snapshot actions (Phase 6E-B) */}
                          {p.status === "approved" && (
                            <div className="snapshot-actions">
                              {snapshots[p.id] ? (
                                <button
                                  className="btn btn-secondary btn-sm"
                                  onClick={() => setSnapshots((prev) => {
                                    const next = { ...prev };
                                    delete next[p.id];
                                    return next;
                                  })}
                                >
                                  Hide Snapshot
                                </button>
                              ) : (
                                <button
                                  className="btn btn-secondary btn-sm"
                                  disabled={snapshotLoading === p.id}
                                  onClick={() => freezeAndViewWithReqCheck(p.id)}
                                >
                                  {snapshotLoading === p.id
                                    ? "Loading..."
                                    : "Freeze & View Snapshot"}
                                </button>
                              )}
                            </div>
                          )}

                          {/* Snapshot viewer (Phase 6E-B) */}
                          {snapshots[p.id] && (() => {
                            const s = snapshots[p.id];
                            const sd = s.snapshot_data_parsed;
                            return (
                              <div className="snapshot-viewer">
                                <div className="proposal-label">Execution Snapshot</div>
                                <div className="snapshot-meta">
                                  <span className="snapshot-meta-item">
                                    <strong>Status:</strong>{" "}
                                    <span className="snapshot-frozen-badge">
                                      {s.status}
                                    </span>
                                  </span>
                                  <span className="snapshot-meta-item">
                                    <strong>Risk:</strong>{" "}
                                    <span style={{ color: RISK_COLORS[s.risk_level] || "#888" }}>
                                      {s.risk_level}
                                    </span>
                                  </span>
                                  <span className="snapshot-meta-item">
                                    <strong>Hash:</strong>{" "}
                                    <code className="snapshot-hash">
                                      {s.content_hash.slice(0, 12)}...
                                    </code>
                                  </span>
                                  <span className="snapshot-meta-item">
                                    <strong>Frozen:</strong>{" "}
                                    {formatDate(s.created_at)}
                                  </span>
                                </div>
                                {sd?.change_summary && (
                                  <div className="snapshot-summary">
                                    {sd.change_summary}
                                  </div>
                                )}
                                {sd?.proposed_files && sd.proposed_files.length > 0 && (
                                  <div className="proposal-files">
                                    <div className="proposal-label">Frozen Files</div>
                                    {sd.proposed_files.map((f, i) => (
                                      <div key={i} className="proposal-file-item">
                                        <span
                                          className="badge"
                                          style={{
                                            color: ACTION_COLORS[f.action] || "#888",
                                            borderColor: ACTION_COLORS[f.action] || "#888",
                                            fontSize: 10,
                                            marginRight: 6,
                                          }}
                                        >
                                          {f.action}
                                        </span>
                                        <span style={{ fontFamily: "monospace", fontSize: 12 }}>
                                          {f.path}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                )}

                                {/* Execution Request (Phase 6E-C) */}
                                <div className="exec-request-section">
                                  {execRequests[s.id] ? (() => {
                                    const er = execRequests[s.id];
                                    const statusColor = EXEC_REQUEST_STATUS_COLORS[er.status] || "var(--text-muted)";
                                    const isTerminal = er.status === "confirmed" || er.status === "rejected";
                                    const cardBorderColor = isTerminal ? statusColor : "var(--accent-yellow)";
                                    return (<>
                                    <div className="exec-request-card" style={{ borderColor: cardBorderColor }}>
                                      <div className="proposal-label">Execution Request</div>
                                      <div className="exec-request-meta">
                                        <span className="exec-request-meta-item">
                                          <strong>ID:</strong>{" "}
                                          <code className="snapshot-hash">
                                            {er.id.slice(0, 8)}...
                                          </code>
                                        </span>
                                        <span className="exec-request-meta-item">
                                          <strong>Status:</strong>{" "}
                                          <span className="exec-request-status" style={{ color: statusColor }}>
                                            {er.status}
                                          </span>
                                        </span>
                                        <span className="exec-request-meta-item">
                                          <strong>Risk:</strong>{" "}
                                          <span style={{ color: RISK_COLORS[er.risk_level] || "#888" }}>
                                            {er.risk_level}
                                          </span>
                                        </span>
                                        <span className="exec-request-meta-item">
                                          <strong>Created:</strong>{" "}
                                          {formatDate(er.created_at)}
                                        </span>
                                        <span className="exec-request-meta-item">
                                          <strong>Hash:</strong>{" "}
                                          <code className="snapshot-hash">
                                            {er.snapshot_content_hash.slice(0, 12)}...
                                          </code>
                                        </span>
                                      </div>
                                      <div className="exec-request-notice">
                                        This records an execution intent only. No file, shell, or git operations are performed.
                                      </div>
                                      {!isTerminal && (
                                        <div className="exec-request-buttons">
                                          <button
                                            className="btn btn-sm exec-request-btn-confirm"
                                            disabled={execReqLoading === s.id}
                                            onClick={() => updateExecRequestStatus(s.id, er.id, "confirmed")}
                                          >
                                            {execReqLoading === s.id ? "Updating..." : "Confirm Request"}
                                          </button>
                                          <button
                                            className="btn btn-sm exec-request-btn-reject"
                                            disabled={execReqLoading === s.id}
                                            onClick={() => updateExecRequestStatus(s.id, er.id, "rejected")}
                                          >
                                            Reject Request
                                          </button>
                                        </div>
                                      )}
                                    </div>

                                    {/* Dry-Run Result (Phase 6F-B) */}
                                    {er.status === "confirmed" && (() => {
                                      const dr = dryRunResults[er.id];
                                      const drLoading = dryRunLoading === er.id;
                                      return (
                                        <div className="dry-run-section">
                                          {dr ? (
                                            <div className="dry-run-result-card">
                                              <div className="proposal-label">Dry-Run Result</div>
                                              <div className="dry-run-meta">
                                                <span className="dry-run-mode-badge">
                                                  {dr.result_data.mode?.toUpperCase() || "DRY RUN"}
                                                </span>
                                                <span
                                                  className="dry-run-status-badge"
                                                  style={{
                                                    color: dr.status === "completed"
                                                      ? "var(--accent-green)"
                                                      : "var(--accent-red)",
                                                  }}
                                                >
                                                  {dr.status}
                                                </span>
                                                <span className="dry-run-ts">
                                                  {formatAuditTimestamp(dr.created_at)}
                                                </span>
                                              </div>
                                              {dr.result_data.summary && (
                                                <div className="dry-run-summary">
                                                  {dr.result_data.summary}
                                                </div>
                                              )}

                                              {/* Planned file actions */}
                                              <div className="dry-run-list-section">
                                                <div className="proposal-label">Planned File Actions</div>
                                                {dr.result_data.planned_file_actions.length > 0 ? (
                                                  dr.result_data.planned_file_actions.map((fa, i) => (
                                                    <div key={i} className="dry-run-action-item">
                                                      <span
                                                        className="badge"
                                                        style={{
                                                          color: ACTION_COLORS[fa.action] || "#888",
                                                          borderColor: ACTION_COLORS[fa.action] || "#888",
                                                          fontSize: 10,
                                                          marginRight: 6,
                                                        }}
                                                      >
                                                        {fa.action}
                                                      </span>
                                                      <span style={{ fontFamily: "monospace", fontSize: 11 }}>
                                                        {fa.path}
                                                      </span>
                                                      <span className="dry-run-action-status">
                                                        {fa.status}
                                                      </span>
                                                    </div>
                                                  ))
                                                ) : (
                                                  <div className="dry-run-empty">
                                                    No file actions planned
                                                  </div>
                                                )}
                                              </div>

                                              {/* Planned command actions */}
                                              <div className="dry-run-list-section">
                                                <div className="proposal-label">Planned Command Actions</div>
                                                {dr.result_data.planned_command_actions.length > 0 ? (
                                                  dr.result_data.planned_command_actions.map((ca, i) => (
                                                    <div key={i} className="dry-run-action-item">
                                                      <code style={{ fontSize: 11 }}>{ca.command}</code>
                                                      {ca.working_dir && (
                                                        <span className="dry-run-workdir">
                                                          in {ca.working_dir}
                                                        </span>
                                                      )}
                                                      <span className="dry-run-action-status">
                                                        {ca.status}
                                                      </span>
                                                    </div>
                                                  ))
                                                ) : (
                                                  <div className="dry-run-empty">
                                                    No command actions planned
                                                  </div>
                                                )}
                                              </div>

                                              {/* Warnings */}
                                              {dr.result_data.warnings.length > 0 && (
                                                <div className="dry-run-warnings">
                                                  <div className="proposal-label" style={{ color: "var(--accent-yellow)" }}>
                                                    Warnings
                                                  </div>
                                                  {dr.result_data.warnings.map((w, i) => (
                                                    <div key={i} className="dry-run-warning-item">
                                                      {w}
                                                    </div>
                                                  ))}
                                                </div>
                                              )}
                                            </div>
                                          ) : drLoading ? (
                                            <div className="dry-run-loading">
                                              Loading dry-run result...
                                            </div>
                                          ) : dryRunError && dryRunLoading === null ? (
                                            <div className="dry-run-error-section">
                                              <span className="snapshot-error" style={{ marginTop: 0 }}>
                                                Failed to load dry-run result
                                              </span>
                                              <button
                                                className="btn btn-secondary btn-sm"
                                                style={{ marginLeft: 8, fontSize: 10 }}
                                                onClick={() => loadDryRunResult(er.id)}
                                              >
                                                Retry
                                              </button>
                                            </div>
                                          ) : (
                                            <div className="dry-run-trigger">
                                              <button
                                                className="btn btn-secondary btn-sm dry-run-btn"
                                                onClick={() => triggerDryRun(er.id)}
                                              >
                                                Run Dry-Run
                                              </button>
                                              <span className="exec-request-hint">
                                                Simulates execution without performing any real operations.
                                              </span>
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })()}
                                  </>);
                                    })() : (
                                    <div className="exec-request-action">
                                      <button
                                        className="btn btn-secondary btn-sm"
                                        disabled={execReqLoading === s.id}
                                        onClick={() => requestExecution(s.id)}
                                      >
                                        {execReqLoading === s.id
                                          ? "Requesting..."
                                          : "Request Execution"}
                                      </button>
                                      <span className="exec-request-hint">
                                        Records execution intent only — does not execute files, shell, or git operations.
                                      </span>
                                    </div>
                                  )}
                                  {execReqError && execReqLoading === null && (
                                    <div className="snapshot-error">{execReqError}</div>
                                  )}
                                </div>
                              </div>
                            );
                          })()}

                          {snapshotError && snapshotLoading === null && (
                            <div className="snapshot-error">{snapshotError}</div>
                          )}
                        </div>
                      );
                    })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
