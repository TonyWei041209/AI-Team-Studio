import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useApprovals } from "../hooks/useApprovals";
import "./ApprovalsPanel.css";

interface ApprovalsPanelProps {
  onCountChange?: (count: number) => void;
}

export function ApprovalsPanel({ onCountChange }: ApprovalsPanelProps) {
  const { t } = useTranslation();
  const { approvals, loading, error, refresh, resolve, lastUpdated } = useApprovals();
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
          {t("approvals.title")}
          {approvals.length > 0 && (
            <span className="count-inline">{approvals.length}</span>
          )}
        </h2>
        <div className="panel-actions">
          {lastUpdated && (
            <span className="last-updated" data-testid="approvals-last-updated">
              {lastUpdated.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </span>
          )}
          <button
            className="btn btn-secondary"
            onClick={refresh}
            title={t("approvals.refresh")}
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
            {t("approvals.retry")}
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && <div className="panel-loading">{t("approvals.loading")}</div>}

      {/* Empty */}
      {!loading && !error && approvals.length === 0 && (
        <div className="panel-empty" data-testid="approvals-empty">{t("approvals.empty")}</div>
      )}

      {/* Approval list */}
      {!loading && approvals.length > 0 && (
        <div className="approval-list">
          {approvals.map((a) => {
            const payload = parsePayload(a.action_payload);
            const isActive = activeAction?.id === a.id;

            return (
              <div key={a.id} className="approval-card" data-testid="approval-card">
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
                        <span className="payload-key">{t("approvals.tool")}</span>
                        <span className="payload-value">
                          {String(payload.tool_name)}
                        </span>
                      </div>
                    )}
                    {payload.risk_level != null && (
                      <div className="payload-row">
                        <span className="payload-key">{t("approvals.risk")}</span>
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
                        <span className="payload-key">{t("approvals.reason")}</span>
                        <span className="payload-value">
                          {String(payload.reason)}
                        </span>
                      </div>
                    )}
                    {payload.params != null && (
                      <div className="payload-row">
                        <span className="payload-key">{t("approvals.params")}</span>
                        <code className="payload-code">
                          {JSON.stringify(payload.params, null, 0).slice(0, 200)}
                        </code>
                      </div>
                    )}
                  </div>
                )}

                {a.task_id && (
                  <div className="approval-meta">
                    <span className="meta-label">{t("approvals.task")}</span>
                    <span className="meta-value">{a.task_id.slice(0, 8)}</span>
                  </div>
                )}

                {/* Action buttons */}
                <div className="approval-actions">
                  <button
                    className={`btn btn-success btn-sm ${isActive && activeAction.type === "approved" ? "active" : ""}`}
                    data-testid="approval-approve-btn"
                    onClick={() => handleAction(a.id, "approved")}
                  >
                    {"✓ " + t("approvals.approve")}
                  </button>
                  <button
                    className={`btn btn-danger btn-sm ${isActive && activeAction.type === "rejected" ? "active" : ""}`}
                    data-testid="approval-reject-btn"
                    onClick={() => handleAction(a.id, "rejected")}
                  >
                    {"✗ " + t("approvals.reject")}
                  </button>
                </div>

                {/* Comment form */}
                {isActive && (
                  <div className="approval-comment">
                    <textarea
                      className="form-input form-textarea"
                      data-testid="approval-comment-input"
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      placeholder={activeAction.type === "approved" ? t("approvals.commentPlaceholderApproval") : t("approvals.commentPlaceholderRejection")}
                      rows={2}
                    />
                    {resolveError && (
                      <div className="form-error">{resolveError}</div>
                    )}
                    <button
                      className={`btn ${activeAction.type === "approved" ? "btn-success" : "btn-danger"}`}
                      data-testid="approval-confirm-btn"
                      onClick={handleSubmit}
                      disabled={submitting}
                    >
                      {submitting
                        ? t("approvals.submitting")
                        : activeAction.type === "approved" ? t("approvals.confirmApprove") : t("approvals.confirmReject")}
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
