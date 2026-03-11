import { useState, useEffect } from "react";
import { useApprovals } from "../hooks/useApprovals";
import "./ApprovalsPanel.css";

interface ApprovalsPanelProps {
  onCountChange?: (count: number) => void;
}

export function ApprovalsPanel({ onCountChange }: ApprovalsPanelProps) {
  const { approvals, loading, error, refresh, resolve } = useApprovals();
  const [activeAction, setActiveAction] = useState<{
    id: string;
    type: "approved" | "rejected";
  } | null>(null);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);

  // Notify parent of count changes (must be in useEffect, not render body)
  useEffect(() => {
    onCountChange?.(approvals.length);
  }, [approvals.length, onCountChange]);

  const handleAction = (id: string, type: "approved" | "rejected") => {
    if (activeAction?.id === id && activeAction?.type === type) {
      setActiveAction(null);
      setComment("");
      return;
    }
    setActiveAction({ id, type });
    setComment("");
    setResolveError(null);
  };

  const handleSubmit = async () => {
    if (!activeAction) return;
    setSubmitting(true);
    setResolveError(null);
    try {
      await resolve(activeAction.id, {
        status: activeAction.type,
        reviewer_comment: comment.trim() || undefined,
      });
      setActiveAction(null);
      setComment("");
    } catch (err) {
      setResolveError(
        err instanceof Error ? err.message : "Failed to resolve approval",
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

  const parsePayload = (payload: string): Record<string, unknown> | null => {
    try {
      return JSON.parse(payload);
    } catch {
      return null;
    }
  };

  return (
    <div className="approvals-panel">
      <div className="panel-header">
        <h2 className="panel-title">
          Pending Approvals
          {approvals.length > 0 && (
            <span className="count-inline">{approvals.length}</span>
          )}
        </h2>
        <div className="panel-actions">
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
      {loading && <div className="panel-loading">Loading approvals...</div>}

      {/* Empty */}
      {!loading && !error && approvals.length === 0 && (
        <div className="panel-empty">No pending approvals. All clear.</div>
      )}

      {/* Approval list */}
      {!loading && approvals.length > 0 && (
        <div className="approval-list">
          {approvals.map((a) => {
            const payload = parsePayload(a.action_payload);
            const isActive = activeAction?.id === a.id;

            return (
              <div key={a.id} className="approval-card">
                <div className="approval-header">
                  <span className="approval-type">{a.action_type}</span>
                  <span className="approval-date">
                    {formatDate(a.created_at)}
                  </span>
                </div>

                {/* Payload details */}
                {payload && (
                  <div className="approval-payload">
                    {payload.tool_name != null && (
                      <div className="payload-row">
                        <span className="payload-key">Tool:</span>
                        <span className="payload-value">
                          {String(payload.tool_name)}
                        </span>
                      </div>
                    )}
                    {payload.risk_level != null && (
                      <div className="payload-row">
                        <span className="payload-key">Risk:</span>
                        <span
                          className="payload-value"
                          style={{
                            color:
                              String(payload.risk_level) === "critical"
                                ? "var(--accent-red)"
                                : "var(--accent-yellow)",
                          }}
                        >
                          {String(payload.risk_level)}
                        </span>
                      </div>
                    )}
                    {payload.reason != null && (
                      <div className="payload-row">
                        <span className="payload-key">Reason:</span>
                        <span className="payload-value">
                          {String(payload.reason)}
                        </span>
                      </div>
                    )}
                    {payload.params != null && (
                      <div className="payload-row">
                        <span className="payload-key">Params:</span>
                        <code className="payload-code">
                          {JSON.stringify(payload.params, null, 0).slice(0, 200)}
                        </code>
                      </div>
                    )}
                  </div>
                )}

                {a.task_id && (
                  <div className="approval-meta">
                    <span className="meta-label">Task:</span>
                    <span className="meta-value">{a.task_id.slice(0, 8)}</span>
                  </div>
                )}

                {/* Action buttons */}
                <div className="approval-actions">
                  <button
                    className={`btn btn-success btn-sm ${isActive && activeAction.type === "approved" ? "active" : ""}`}
                    onClick={() => handleAction(a.id, "approved")}
                  >
                    &#10003; Approve
                  </button>
                  <button
                    className={`btn btn-danger btn-sm ${isActive && activeAction.type === "rejected" ? "active" : ""}`}
                    onClick={() => handleAction(a.id, "rejected")}
                  >
                    &#10007; Reject
                  </button>
                </div>

                {/* Comment form */}
                {isActive && (
                  <div className="approval-comment">
                    <textarea
                      className="form-input form-textarea"
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      placeholder={`Comment for ${activeAction.type === "approved" ? "approval" : "rejection"} (optional)`}
                      rows={2}
                    />
                    {resolveError && (
                      <div className="form-error">{resolveError}</div>
                    )}
                    <button
                      className={`btn ${activeAction.type === "approved" ? "btn-success" : "btn-danger"}`}
                      onClick={handleSubmit}
                      disabled={submitting}
                    >
                      {submitting
                        ? "Submitting..."
                        : `Confirm ${activeAction.type === "approved" ? "Approve" : "Reject"}`}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
