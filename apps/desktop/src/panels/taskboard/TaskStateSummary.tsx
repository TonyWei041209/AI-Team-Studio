/**
 * Task state summary badge — Phase 20-1/20-2.
 *
 * Shows a compact, human-readable summary of where a task currently
 * stands in the orchestration + execution pipeline. Purely frontend
 * display logic — does not change any backend state.
 *
 * When the task is expanded and pipeline data is available, the phase
 * derivation becomes more precise (e.g. distinguishing "needs approval"
 * from "ready for dry-run" from "ready to execute").
 */

import { useTranslation } from "react-i18next";
import type { TaskStatus } from "../../types/api";

export type TaskPhase =
  | "pending_start"
  | "orchestrating"
  | "has_proposal"
  | "needs_approval"
  | "approved"
  | "has_snapshot"
  | "ready_for_dryrun"
  | "ready_to_execute"
  | "execution_failed"
  | "rollback_available"
  | "completed"
  | "failed";

export interface TaskStateSummaryProps {
  taskStatus: TaskStatus;
  hasProposals: boolean;
  hasPendingApproval: boolean;
  hasApprovedProposal: boolean;
  hasSnapshot: boolean;
  hasConfirmedRequest: boolean;
  hasDryRunResult: boolean;
  hasRealRunResult: boolean;
  hasExecutionError: boolean;
  hasRollbackResult: boolean;
  isOrchestrating: boolean;
}

export function derivePhase(props: TaskStateSummaryProps): TaskPhase {
  const {
    taskStatus,
    hasProposals,
    hasPendingApproval,
    hasApprovedProposal,
    hasSnapshot,
    hasConfirmedRequest,
    hasDryRunResult,
    hasRealRunResult,
    hasExecutionError,
    hasRollbackResult,
    isOrchestrating,
  } = props;

  if (taskStatus === "failed") return "failed";
  if (taskStatus === "done") return "completed";

  if (isOrchestrating) return "orchestrating";
  if (taskStatus === "pending") return "pending_start";

  // Post-orchestration: walk the pipeline in reverse priority
  if (hasRollbackResult) return "rollback_available";
  if (hasExecutionError) return "execution_failed";
  if (hasRealRunResult) return "completed";
  if (hasConfirmedRequest && hasDryRunResult) return "ready_to_execute";
  if (hasConfirmedRequest) return "ready_for_dryrun";
  if (hasSnapshot) return "has_snapshot";
  if (hasApprovedProposal) return "approved";
  if (hasPendingApproval) return "needs_approval";
  if (hasProposals) return "has_proposal";

  // Fallback for in_progress / planning / reviewing without proposals yet
  return "orchestrating";
}

const PHASE_COLORS: Record<TaskPhase, string> = {
  pending_start: "var(--text-muted)",
  orchestrating: "var(--accent-blue)",
  has_proposal: "var(--accent-yellow)",
  needs_approval: "var(--accent-yellow)",
  approved: "var(--accent-green)",
  has_snapshot: "var(--accent-blue)",
  ready_for_dryrun: "var(--accent-blue)",
  ready_to_execute: "#ff8c00",
  execution_failed: "var(--accent-red)",
  rollback_available: "#ff8c00",
  completed: "var(--accent-green)",
  failed: "var(--accent-red)",
};

export function TaskStateSummary(props: TaskStateSummaryProps) {
  const { t } = useTranslation();
  const phase = derivePhase(props);
  const color = PHASE_COLORS[phase];

  return (
    <span
      className="task-state-summary"
      style={{ color, borderColor: color }}
    >
      {t(`taskState.${phase}`)}
    </span>
  );
}
