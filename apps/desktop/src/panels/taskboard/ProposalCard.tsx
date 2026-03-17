import type { ApprovalRequest as ApprovalRequestType } from "../../types/api";
import type {
  ExecutionProposal,
  ExecutionSnapshotResponse,
  ExecutionRequestResponse,
  DryRunResult,
  ActionPlanResponse,
  RealRunResult,
  RollbackResult,
} from "./types";
import {
  RISK_COLORS,
  ACTION_COLORS,
  formatAuditTimestamp,
} from "./types";
import { ExecutionPipelineViewer } from "./ExecutionPipelineViewer";

interface ProposalCardProps {
  proposal: ExecutionProposal;
  // Inline approval
  taskApprovals: ApprovalRequestType[];
  approvalLoading: string | null;
  approvalError: string | null;
  onResolveApproval: (approvalId: string, status: "approved" | "rejected", proposalId?: string) => void;
  // Snapshot
  snapshot?: ExecutionSnapshotResponse;
  snapshotLoading: boolean;
  snapshotError: string | null;
  onFreezeAndView: (proposalId: string) => void;
  onHideSnapshot: (proposalId: string) => void;
  // Execution pipeline (passed through)
  execRequest?: ExecutionRequestResponse;
  execReqLoading: boolean;
  execReqError: string | null;
  onRequestExecution: (snapshotId: string) => void;
  onUpdateExecRequestStatus: (snapshotId: string, requestId: string, newStatus: "confirmed" | "rejected") => void;
  onConfirmAndDryRun: (snapshotId: string, requestId: string) => void;
  dryRunResult?: DryRunResult;
  dryRunLoading: boolean;
  dryRunError: string | null;
  onTriggerDryRun: (requestId: string) => void;
  onLoadDryRunResult: (requestId: string) => void;
  actionPlan?: ActionPlanResponse;
  actionPlanLoading: boolean;
  actionPlanError: string | null;
  onLoadActionPlan: (requestId: string) => void;
  realRunResult?: RealRunResult | "empty";
  realRunLoading: boolean;
  realRunError: string | null;
  executeLoading: boolean;
  executeError: string | null;
  onTriggerExecution: (requestId: string) => void;
  onLoadRealRunResult: (requestId: string) => void;
  rollbackResult?: RollbackResult | "empty";
  rollbackLoading: boolean;
  rollbackError: string | null;
  rollbackTriggerLoading: boolean;
  rollbackTriggerError: string | null;
  onTriggerRollback: (requestId: string, resultId: string) => void;
  onLoadRollbackResult: (requestId: string) => void;
  formatDate: (iso: string) => string;
}

export function ProposalCard({
  proposal: p,
  taskApprovals,
  approvalLoading,
  approvalError,
  onResolveApproval,
  snapshot,
  snapshotLoading,
  snapshotError,
  onFreezeAndView,
  onHideSnapshot,
  execRequest,
  execReqLoading,
  execReqError,
  onRequestExecution,
  onUpdateExecRequestStatus,
  onConfirmAndDryRun,
  dryRunResult,
  dryRunLoading,
  dryRunError,
  onTriggerDryRun,
  onLoadDryRunResult,
  actionPlan,
  actionPlanLoading,
  actionPlanError,
  onLoadActionPlan,
  realRunResult,
  realRunLoading,
  realRunError,
  executeLoading,
  executeError,
  onTriggerExecution,
  onLoadRealRunResult,
  rollbackResult,
  rollbackLoading,
  rollbackError,
  rollbackTriggerLoading,
  rollbackTriggerError,
  onTriggerRollback,
  onLoadRollbackResult,
  formatDate,
}: ProposalCardProps) {
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

      {/* Inline approval actions (Phase 9-4) */}
      {p.requires_approval && p.status === "pending" && (() => {
        const linked = taskApprovals.find(
          (a) => a.proposal_id === p.id && a.status === "pending"
        );
        if (!linked) return null;
        return (
          <div className="inline-approval-actions" style={{
            margin: "8px 0",
            padding: "8px 10px",
            background: "rgba(255, 200, 50, 0.08)",
            borderRadius: 6,
            border: "1px solid rgba(255, 200, 50, 0.2)",
          }}>
            <div style={{ fontSize: 11, color: "var(--accent-yellow)", marginBottom: 6, fontWeight: 600 }}>
              Awaiting approval
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                className="btn btn-sm"
                style={{
                  background: "var(--accent-green)",
                  color: "#000",
                  fontWeight: 600,
                  fontSize: 11,
                  padding: "3px 12px",
                  border: "none",
                  borderRadius: 4,
                  cursor: "pointer",
                }}
                disabled={approvalLoading === linked.id}
                onClick={() => onResolveApproval(linked.id, "approved", p.id)}
              >
                {approvalLoading === linked.id ? "..." : "Approve"}
              </button>
              <button
                className="btn btn-sm"
                style={{
                  background: "var(--accent-red)",
                  color: "#fff",
                  fontWeight: 600,
                  fontSize: 11,
                  padding: "3px 12px",
                  border: "none",
                  borderRadius: 4,
                  cursor: "pointer",
                }}
                disabled={approvalLoading === linked.id}
                onClick={() => onResolveApproval(linked.id, "rejected", p.id)}
              >
                {approvalLoading === linked.id ? "..." : "Reject"}
              </button>
            </div>
            {approvalError && approvalLoading === null && (
              <div style={{ color: "var(--accent-red)", fontSize: 11, marginTop: 4 }}>
                {approvalError}
              </div>
            )}
          </div>
        );
      })()}

      {/* Show resolved approval status (Phase 9-4) */}
      {p.requires_approval && p.status !== "pending" && (() => {
        const linked = taskApprovals.find(
          (a) => a.proposal_id === p.id && a.status !== "pending"
        );
        if (!linked) return null;
        const isApproved = linked.status === "approved" || linked.status === "consumed";
        return (
          <div style={{
            margin: "6px 0",
            fontSize: 11,
            color: isApproved ? "var(--accent-green)" : "var(--accent-red)",
          }}>
            {isApproved ? "Approved" : "Rejected"}
            {linked.reviewer_comment && (
              <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>
                — {linked.reviewer_comment}
              </span>
            )}
            {linked.resolved_at && (
              <span style={{ color: "var(--text-muted)", marginLeft: 6, fontSize: 10 }}>
                {formatAuditTimestamp(linked.resolved_at)}
              </span>
            )}
          </div>
        );
      })()}

      {/* Snapshot actions (Phase 6E-B) */}
      {p.status === "approved" && (
        <div className="snapshot-actions">
          {snapshot ? (
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => onHideSnapshot(p.id)}
            >
              Hide Snapshot
            </button>
          ) : (
            <button
              className="btn btn-secondary btn-sm"
              disabled={snapshotLoading}
              onClick={() => onFreezeAndView(p.id)}
            >
              {snapshotLoading
                ? "Loading..."
                : "Freeze & View Snapshot"}
            </button>
          )}
        </div>
      )}

      {/* Snapshot viewer + execution pipeline (Phase 6E-B onwards) */}
      {snapshot && (
        <ExecutionPipelineViewer
          snapshot={snapshot}
          execRequest={execRequest}
          execReqLoading={execReqLoading}
          execReqError={execReqError}
          onRequestExecution={onRequestExecution}
          onUpdateExecRequestStatus={onUpdateExecRequestStatus}
          onConfirmAndDryRun={onConfirmAndDryRun}
          dryRunResult={dryRunResult}
          dryRunLoading={dryRunLoading}
          dryRunError={dryRunError}
          onTriggerDryRun={onTriggerDryRun}
          onLoadDryRunResult={onLoadDryRunResult}
          actionPlan={actionPlan}
          actionPlanLoading={actionPlanLoading}
          actionPlanError={actionPlanError}
          onLoadActionPlan={onLoadActionPlan}
          executeLoading={executeLoading}
          executeError={executeError}
          onTriggerExecution={onTriggerExecution}
          realRunResult={realRunResult}
          realRunLoading={realRunLoading}
          realRunError={realRunError}
          onLoadRealRunResult={onLoadRealRunResult}
          rollbackResult={rollbackResult}
          rollbackLoading={rollbackLoading}
          rollbackError={rollbackError}
          rollbackTriggerLoading={rollbackTriggerLoading}
          rollbackTriggerError={rollbackTriggerError}
          onTriggerRollback={onTriggerRollback}
          onLoadRollbackResult={onLoadRollbackResult}
          formatDate={formatDate}
        />
      )}

      {snapshotError && !snapshotLoading && (
        <div className="snapshot-error">{snapshotError}</div>
      )}
    </div>
  );
}
