import type {
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
  EXEC_REQUEST_STATUS_COLORS,
  POLICY_DECISION_COLORS,
  REAL_RUN_FILE_STATUS_COLORS,
  ROLLBACK_FILE_STATUS_COLORS,
  formatAuditTimestamp,
} from "./types";

interface ExecutionPipelineViewerProps {
  snapshot: ExecutionSnapshotResponse;
  // Execution request
  execRequest?: ExecutionRequestResponse;
  execReqLoading: boolean;
  execReqError: string | null;
  onRequestExecution: (snapshotId: string) => void;
  onUpdateExecRequestStatus: (snapshotId: string, requestId: string, newStatus: "confirmed" | "rejected") => void;
  onConfirmAndDryRun: (snapshotId: string, requestId: string) => void;
  // Dry run
  dryRunResult?: DryRunResult;
  dryRunLoading: boolean;
  dryRunError: string | null;
  onTriggerDryRun: (requestId: string) => void;
  onLoadDryRunResult: (requestId: string) => void;
  // Action plan
  actionPlan?: ActionPlanResponse;
  actionPlanLoading: boolean;
  actionPlanError: string | null;
  onLoadActionPlan: (requestId: string) => void;
  // Execute
  executeLoading: boolean;
  executeError: string | null;
  onTriggerExecution: (requestId: string) => void;
  // Real run
  realRunResult?: RealRunResult | "empty";
  realRunLoading: boolean;
  realRunError: string | null;
  onLoadRealRunResult: (requestId: string) => void;
  // Rollback
  rollbackResult?: RollbackResult | "empty";
  rollbackLoading: boolean;
  rollbackError: string | null;
  rollbackTriggerLoading: boolean;
  rollbackTriggerError: string | null;
  onTriggerRollback: (requestId: string, resultId: string) => void;
  onLoadRollbackResult: (requestId: string) => void;
  // Utilities
  formatDate: (iso: string) => string;
}

export function ExecutionPipelineViewer({
  snapshot,
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
  executeLoading,
  executeError,
  onTriggerExecution,
  realRunResult,
  realRunLoading,
  realRunError,
  onLoadRealRunResult,
  rollbackResult,
  rollbackLoading,
  rollbackError,
  rollbackTriggerLoading,
  rollbackTriggerError,
  onTriggerRollback,
  onLoadRollbackResult,
  formatDate,
}: ExecutionPipelineViewerProps) {
  const s = snapshot;
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
        {execRequest ? (() => {
          const er = execRequest;
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
                  disabled={execReqLoading}
                  onClick={() => onUpdateExecRequestStatus(s.id, er.id, "confirmed")}
                >
                  {execReqLoading ? "Updating..." : "Confirm Request"}
                </button>
                <button
                  className="btn btn-sm exec-request-btn-reject"
                  disabled={execReqLoading}
                  onClick={() => onUpdateExecRequestStatus(s.id, er.id, "rejected")}
                >
                  Reject Request
                </button>
                {/* Phase 10-2: Confirm & Dry-Run combo — confirm is irreversible */}
                <button
                  className="btn btn-sm"
                  style={{
                    background: "var(--accent-blue, #4a9eff)",
                    color: "#fff",
                    fontWeight: 600,
                    fontSize: 11,
                    padding: "3px 10px",
                    border: "none",
                    borderRadius: 4,
                    cursor: "pointer",
                  }}
                  disabled={execReqLoading || dryRunLoading}
                  onClick={() => onConfirmAndDryRun(s.id, er.id)}
                >
                  {execReqLoading
                    ? "Confirming..."
                    : dryRunLoading
                      ? "Running dry-run..."
                      : "Confirm & Dry-Run (irreversible)"}
                </button>
              </div>
            )}
          </div>

          {/* Dry-Run Result (Phase 6F-B) */}
          {er.status === "confirmed" && (() => {
            const dr = dryRunResult;
            const drLoading = dryRunLoading;
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
                ) : dryRunError && !dryRunLoading ? (
                  <div className="dry-run-error-section">
                    <span className="snapshot-error" style={{ marginTop: 0 }}>
                      Failed to load dry-run result
                    </span>
                    <button
                      className="btn btn-secondary btn-sm"
                      style={{ marginLeft: 8, fontSize: 10 }}
                      onClick={() => onLoadDryRunResult(er.id)}
                    >
                      Retry
                    </button>
                  </div>
                ) : (
                  <div className="dry-run-trigger">
                    <button
                      className="btn btn-secondary btn-sm dry-run-btn"
                      onClick={() => onTriggerDryRun(er.id)}
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

          {/* Action Plan Viewer (Phase 6G-A) */}
          {(() => {
            const ap = actionPlan;
            const apLoading = actionPlanLoading;
            return (
              <div className="action-plan-section">
                {ap ? (
                  <div className="action-plan-card">
                    <div className="proposal-label">Action Plan</div>
                    <div className="action-plan-meta">
                      <span className="action-plan-summary">{ap.summary}</span>
                      <span
                        className="badge"
                        style={{
                          color: RISK_COLORS[ap.overall_risk] || "var(--text-muted)",
                          borderColor: RISK_COLORS[ap.overall_risk] || "var(--text-muted)",
                          fontSize: 9,
                        }}
                      >
                        {ap.overall_risk}
                      </span>
                      {ap.has_denied && (
                        <span className="badge" style={{
                          color: "var(--accent-red)", borderColor: "var(--accent-red)", fontSize: 9,
                        }}>
                          HAS DENIED
                        </span>
                      )}
                      {ap.needs_confirmation_count > 0 && (
                        <span className="badge" style={{
                          color: "var(--accent-yellow)", borderColor: "var(--accent-yellow)", fontSize: 9,
                        }}>
                          {ap.needs_confirmation_count} NEED CONFIRM
                        </span>
                      )}
                    </div>
                    <div className="action-plan-list">
                      {ap.actions.map((a, i) => (
                        <div key={i} className="action-plan-item">
                          <span
                            className="badge"
                            style={{
                              color: POLICY_DECISION_COLORS[a.policy_decision] || "var(--text-muted)",
                              borderColor: POLICY_DECISION_COLORS[a.policy_decision] || "var(--text-muted)",
                              fontSize: 9,
                              minWidth: 40,
                              textAlign: "center",
                            }}
                          >
                            {a.policy_decision.replace("_", " ")}
                          </span>
                          <span className="action-plan-type">{a.type.replace("_", " ")}</span>
                          <span className="action-plan-target">{a.target}</span>
                          <span
                            className="action-plan-risk"
                            style={{ color: RISK_COLORS[a.risk_level] || "var(--text-muted)" }}
                          >
                            {a.risk_level}
                          </span>
                        </div>
                      ))}
                      {ap.actions.length === 0 && (
                        <div className="dry-run-empty">No actions in plan</div>
                      )}
                    </div>
                  </div>
                ) : apLoading ? (
                  <div className="dry-run-loading">Loading action plan...</div>
                ) : actionPlanError && !actionPlanLoading ? (
                  <div className="dry-run-error-section">
                    <span className="snapshot-error" style={{ marginTop: 0 }}>
                      Failed to load action plan
                    </span>
                    <button
                      className="btn btn-secondary btn-sm"
                      style={{ marginLeft: 8, fontSize: 10 }}
                      onClick={() => onLoadActionPlan(er.id)}
                    >
                      Retry
                    </button>
                  </div>
                ) : (
                  <div className="dry-run-trigger">
                    <button
                      className="btn btn-secondary btn-sm"
                      style={{ color: "var(--accent-blue)", borderColor: "var(--accent-blue)" }}
                      onClick={() => onLoadActionPlan(er.id)}
                    >
                      View Action Plan
                    </button>
                    <span className="exec-request-hint">
                      Shows normalized actions with policy decisions.
                    </span>
                  </div>
                )}
              </div>
            );
          })()}
          {/* Execute Button (Phase 7B-2) */}
          {er.status === "confirmed" &&
            !(realRunResult && realRunResult !== "empty") && (
            <div className="execute-section">
              <button
                className="btn btn-sm execute-btn"
                disabled={executeLoading}
                onClick={() => onTriggerExecution(er.id)}
              >
                {executeLoading ? "Executing..." : "Execute"}
              </button>
              <span className="exec-request-hint">
                Writes real files to your project workspace (file_create / file_modify only).
              </span>
              {executeError && (
                <div className="execute-error">{executeError}</div>
              )}
            </div>
          )}

          {/* Real-Run Execution Result Viewer (Phase 7B-1) */}
          {(() => {
            const rr = realRunResult;
            const rrLoading = realRunLoading;
          return (
            <div className="real-run-section">
              {rr && rr !== "empty" ? (
                <div className="real-run-result-card">
                  <div className="proposal-label">Execution Result</div>
                  <div className="real-run-meta">
                    <span className="real-run-mode-badge">REAL RUN</span>
                    <span
                      className="real-run-status-badge"
                      style={{
                        color: rr.status === "completed" ? "var(--accent-green)" : "var(--accent-red)",
                        borderColor: rr.status === "completed" ? "var(--accent-green)" : "var(--accent-red)",
                      }}
                    >
                      {rr.status}
                    </span>
                    <span className="real-run-ts">{formatAuditTimestamp(rr.created_at)}</span>
                  </div>
                  <div className="real-run-summary">{rr.result_data?.summary}</div>
                  {rr.status === "failed" && rr.result_data?.stop_reason && (
                    <div className="real-run-stop-reason">
                      <strong>Stopped at file #{rr.result_data.stopped_at}:</strong>{" "}
                      {rr.result_data.stop_reason}
                    </div>
                  )}
                  <div className="real-run-file-list">
                    {rr.result_data?.file_results?.map((fr, i) => (
                      <div key={i} className="real-run-file-item">
                        <span
                          className="badge"
                          style={{
                            color: REAL_RUN_FILE_STATUS_COLORS[fr.status] || "var(--text-muted)",
                            borderColor: REAL_RUN_FILE_STATUS_COLORS[fr.status] || "var(--text-muted)",
                            fontSize: 9,
                            minWidth: 40,
                            textAlign: "center",
                          }}
                        >
                          {fr.status}
                        </span>
                        <span className="real-run-file-op">{fr.operation}</span>
                        <span className="real-run-file-path">{fr.path}</span>
                        {fr.before_hash && (
                          <span className="real-run-hash" title={fr.before_hash}>
                            {fr.before_hash.substring(0, 8)}→
                          </span>
                        )}
                        {fr.after_hash && (
                          <span className="real-run-hash" title={fr.after_hash}>
                            {fr.after_hash.substring(0, 8)}
                          </span>
                        )}
                        {fr.error && (
                          <span className="real-run-file-error">{fr.error}</span>
                        )}
                      </div>
                    ))}
                    {(!rr.result_data?.file_results || rr.result_data.file_results.length === 0) && (
                      <div className="dry-run-empty">No file results</div>
                    )}
                  </div>
                  {/* Command Results (Phase 8B-2) */}
                  {rr.result_data?.command_results && rr.result_data.command_results.length > 0 && (
                    <div className="real-run-cmd-section">
                      <div className="real-run-cmd-header">Commands</div>
                      <div className="real-run-cmd-list">
                        {rr.result_data.command_results.map((cr, i) => (
                          <div key={i} className="real-run-cmd-item">
                            <div className="real-run-cmd-row">
                              <span
                                className="badge"
                                style={{
                                  color: REAL_RUN_FILE_STATUS_COLORS[cr.status] || "var(--text-muted)",
                                  borderColor: REAL_RUN_FILE_STATUS_COLORS[cr.status] || "var(--text-muted)",
                                  fontSize: 9,
                                  minWidth: 40,
                                  textAlign: "center",
                                }}
                              >
                                {cr.status}
                              </span>
                              <span className="real-run-cmd-text">{cr.command}</span>
                              {cr.exit_code !== null && cr.exit_code !== undefined && (
                                <span
                                  className="real-run-cmd-exit"
                                  style={{ color: cr.exit_code === 0 ? "var(--accent-green)" : "var(--accent-red)" }}
                                >
                                  exit {cr.exit_code}
                                </span>
                              )}
                              {cr.duration_ms > 0 && (
                                <span className="real-run-cmd-duration">
                                  {cr.duration_ms < 1000 ? `${cr.duration_ms}ms` : `${(cr.duration_ms / 1000).toFixed(1)}s`}
                                </span>
                              )}
                            </div>
                            {cr.error && (
                              <div className="real-run-cmd-error">{cr.error}</div>
                            )}
                            {cr.stdout && (
                              <pre className="real-run-cmd-output">
                                {cr.stdout.length > 2000 ? cr.stdout.substring(0, 2000) + "\n... (truncated)" : cr.stdout}
                              </pre>
                            )}
                            {cr.stderr && (
                              <pre className="real-run-cmd-output real-run-cmd-stderr">
                                {cr.stderr.length > 2000 ? cr.stderr.substring(0, 2000) + "\n... (truncated)" : cr.stderr}
                              </pre>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {/* Rollback Button (Phase 7D-2) */}
                  {(rr.status === "completed" || rr.status === "failed") &&
                    !(rollbackResult && rollbackResult !== "empty") && (
                    <div className="rollback-trigger-section">
                      <button
                        className="btn btn-sm rollback-btn"
                        disabled={rollbackTriggerLoading}
                        onClick={() => onTriggerRollback(er.id, rr.id)}
                      >
                        {rollbackTriggerLoading ? "Rolling back..." : "Rollback"}
                      </button>
                      <span className="exec-request-hint">
                        Restores modified files and removes created files.
                      </span>
                      {rollbackTriggerError && (
                        <div className="rollback-trigger-error">{rollbackTriggerError}</div>
                      )}
                    </div>
                  )}
                </div>
              ) : rr === "empty" ? (
                <div className="real-run-empty">No real execution result yet</div>
              ) : rrLoading ? (
                <div className="dry-run-loading">Loading execution result...</div>
              ) : realRunError && !realRunLoading ? (
                <div className="dry-run-error-section">
                  <span className="snapshot-error" style={{ marginTop: 0 }}>
                    Failed to load execution result
                  </span>
                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ marginLeft: 8, fontSize: 10 }}
                    onClick={() => onLoadRealRunResult(er.id)}
                  >
                    Retry
                  </button>
                </div>
              ) : (
                <div className="dry-run-trigger">
                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ color: "#ff8c00", borderColor: "#ff8c00" }}
                    onClick={() => onLoadRealRunResult(er.id)}
                  >
                    View Execution Result
                  </button>
                  <span className="exec-request-hint">
                    Shows real file execution result (read-only).
                  </span>
                </div>
              )}
              </div>
            );
          })()}

          {/* Rollback Result Viewer (Phase 7D-1) */}
          {(() => {
            const rb = rollbackResult;
            const rbLoading = rollbackLoading;
          return (
            <div className="rollback-section">
              {rb && rb !== "empty" ? (
                <div className="rollback-result-card">
                  <div className="proposal-label">Rollback Result</div>
                  <div className="rollback-meta">
                    <span className="rollback-mode-badge">ROLLBACK</span>
                    <span
                      className="rollback-status-badge"
                      style={{
                        color: rb.status === "completed" ? "var(--accent-green)" : "var(--accent-red)",
                        borderColor: rb.status === "completed" ? "var(--accent-green)" : "var(--accent-red)",
                      }}
                    >
                      {rb.status}
                    </span>
                    <span className="rollback-ts">{formatAuditTimestamp(rb.created_at)}</span>
                  </div>
                  <div className="rollback-summary">{rb.result_data?.summary}</div>
                  <div className="rollback-file-list">
                    {rb.result_data?.file_results?.map((fr, i) => (
                      <div key={i} className="rollback-file-item">
                        <span
                          className="badge"
                          style={{
                            color: ROLLBACK_FILE_STATUS_COLORS[fr.status] || "var(--text-muted)",
                            borderColor: ROLLBACK_FILE_STATUS_COLORS[fr.status] || "var(--text-muted)",
                            fontSize: 9,
                            minWidth: 58,
                            textAlign: "center",
                          }}
                        >
                          {fr.status}
                        </span>
                        <span className="rollback-file-path">{fr.path}</span>
                        {fr.error && (
                          <span className="rollback-file-error">{fr.error}</span>
                        )}
                      </div>
                    ))}
                    {(!rb.result_data?.file_results || rb.result_data.file_results.length === 0) && (
                      <div className="dry-run-empty">No file results</div>
                    )}
                  </div>
                </div>
              ) : rb === "empty" ? (
                <div className="rollback-empty">No rollback result yet</div>
              ) : rbLoading ? (
                <div className="dry-run-loading">Loading rollback result...</div>
              ) : rollbackError && !rollbackLoading ? (
                <div className="dry-run-error-section">
                  <span className="snapshot-error" style={{ marginTop: 0 }}>
                    Failed to load rollback result
                  </span>
                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ marginLeft: 8, fontSize: 10 }}
                    onClick={() => onLoadRollbackResult(er.id)}
                  >
                    Retry
                  </button>
                </div>
              ) : (
                <div className="dry-run-trigger">
                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ color: "#6ea8d9", borderColor: "#6ea8d9" }}
                    onClick={() => onLoadRollbackResult(er.id)}
                  >
                    View Rollback Result
                  </button>
                  <span className="exec-request-hint">
                    Shows rollback result (read-only).
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
              disabled={execReqLoading}
              onClick={() => onRequestExecution(s.id)}
            >
              {execReqLoading
                ? "Requesting..."
                : "Request Execution"}
            </button>
            <span className="exec-request-hint">
              Records execution intent only — does not execute files, shell, or git operations.
            </span>
          </div>
        )}
        {execReqError && !execReqLoading && (
          <div className="snapshot-error">{execReqError}</div>
        )}
      </div>
    </div>
  );
}
