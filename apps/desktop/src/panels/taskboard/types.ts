import type { TaskStatus, TaskPriority } from "../../types/api";

export type { TaskStatus, TaskPriority };

// ── Agent Run Summary (Phase 16-1) ────────────────────────
export interface AgentRunSummary {
  id: string;
  role: string;
  status: string; // "pending" | "running" | "completed" | "failed"
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

// ── Proposal types (Phase 6E-A) ────────────────────────────

export interface ProposedFile {
  path: string;
  action: "create" | "modify" | "delete";
  reason: string;
}

export interface ProposedCommand {
  command: string;
  working_dir?: string;
  risk_level?: string;
  reason: string;
}

export interface ProposalData {
  change_summary?: string;
  proposed_files?: ProposedFile[];
  /** Legacy/alternate Builder output field; some outputs use this instead of proposed_files. */
  changed_files?: ProposedFile[];
  proposed_commands?: ProposedCommand[];
  risk_level?: string;
  requires_approval?: boolean;
  approval_reasons?: string[];
}

export interface ExecutionProposal {
  id: string;
  task_id: string;
  run_id: string;
  role: string;
  risk_level: string;
  requires_approval: boolean;
  status: string;
  /** Raw JSON string of the Builder output (the API returns both this and the parsed form). */
  proposal_data: string;
  proposal_data_parsed: ProposalData;
  approval_reasons_parsed: string[];
  created_at: string;
}

// ── Snapshot types (Phase 6E-B) ─────────────────────────
export interface ExecutionSnapshotResponse {
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
export interface ExecutionRequestResponse {
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
export interface DryRunFileAction {
  path: string;
  action: string;
  status: string;
}

export interface DryRunCommandAction {
  command: string;
  working_dir: string;
  status: string;
}

export interface DryRunResultData {
  mode: string;
  summary: string;
  planned_file_actions: DryRunFileAction[];
  planned_command_actions: DryRunCommandAction[];
  warnings: string[];
  snapshot_content_hash?: string;
}

export interface DryRunResult {
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

// ── Action Plan types (Phase 6G-A) ──────────────────────
export interface ActionPlanAction {
  type: string;
  target: string;
  params: Record<string, unknown>;
  risk_level: string;
  policy_decision: string;
  reason: string;
}

export interface ActionPlanResponse {
  execution_request_id: string;
  snapshot_id: string;
  snapshot_content_hash: string;
  actions: ActionPlanAction[];
  overall_risk: string;
  has_denied: boolean;
  needs_confirmation_count: number;
  summary: string;
}

// ── Audit Trail types (Phase 6E-E) ──────────────────────
export interface AuditEvent {
  event_type: string;
  object_type: string;
  object_id: string;
  status: string;
  timestamp: string;
  summary: string;
  related_ids: Record<string, string>;
  detail: Record<string, unknown>;
}

export interface AuditTrailResponse {
  task_id: string;
  events: AuditEvent[];
  count: number;
}

// ── Real-Run Result types (Phase 7B-1) ───────────────────
export interface RealRunFileResult {
  path: string;
  operation: string;
  status: string;
  error?: string;
  reason?: string;
  before_hash: string | null;
  after_hash: string | null;
}

export interface RealRunCommandResult {
  command: string;
  working_dir?: string;
  status: string;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  duration_ms: number;
  truncated?: boolean;
  error?: string;
}

export interface RealRunResultData {
  mode: string;
  summary: string;
  file_results: RealRunFileResult[];
  command_results?: RealRunCommandResult[];
  stopped_at: number | null;
  stop_reason: string | null;
}

export interface RealRunResult {
  id: string;
  execution_request_id: string;
  task_id: string;
  snapshot_id: string;
  snapshot_content_hash: string;
  mode: string;
  status: string;
  result_data: RealRunResultData;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

// ── Rollback Result types (Phase 7D-1) ─────────────────
export interface RollbackFileResult {
  path: string;
  operation: string;
  status: string;
  error?: string;
}

export interface RollbackResultData {
  mode: string;
  summary: string;
  file_results: RollbackFileResult[];
}

export interface RollbackResult {
  id: string;
  execution_request_id: string;
  task_id: string;
  snapshot_id: string;
  snapshot_content_hash: string;
  mode: string;
  status: string;
  result_data: RollbackResultData;
  created_at: string;
}

// ── Color constant maps ──────────────────────────────────

export const OBJECT_TYPE_COLORS: Record<string, string> = {
  proposal: "var(--accent-blue)",
  approval: "var(--accent-yellow)",
  snapshot: "var(--accent-green)",
  execution_request: "#ff8c00",
  execution_result: "var(--accent-blue)",
};

export const AUDIT_STATUS_COLORS: Record<string, string> = {
  approved: "var(--accent-green)",
  rejected: "var(--accent-red)",
  frozen: "var(--accent-green)",
  requested: "var(--accent-yellow)",
  confirmed: "var(--accent-green)",
  completed: "var(--accent-green)",
  failed: "var(--accent-red)",
  pending: "var(--text-muted)",
};

export const REAL_RUN_FILE_STATUS_COLORS: Record<string, string> = {
  success: "var(--accent-green)",
  failed: "var(--accent-red)",
  skipped: "var(--text-muted)",
};

export const ROLLBACK_FILE_STATUS_COLORS: Record<string, string> = {
  restored: "var(--accent-green)",
  deleted: "var(--accent-green)",
  already_absent: "var(--text-muted)",
  rollback_failed: "var(--accent-red)",
  skipped: "var(--text-muted)",
};

export const POLICY_DECISION_COLORS: Record<string, string> = {
  allow: "var(--accent-green)",
  deny: "var(--accent-red)",
  needs_confirmation: "var(--accent-yellow)",
};

export const RISK_COLORS: Record<string, string> = {
  low: "var(--accent-green)",
  medium: "var(--accent-yellow)",
  high: "#ff8c00",
  critical: "var(--accent-red)",
};

export const ACTION_COLORS: Record<string, string> = {
  create: "var(--accent-green)",
  modify: "var(--accent-yellow)",
  delete: "var(--accent-red)",
};

export const EXEC_REQUEST_STATUS_COLORS: Record<string, string> = {
  requested: "var(--accent-yellow)",
  confirmed: "var(--accent-green)",
  rejected: "var(--accent-red)",
};

export const STATUS_COLORS: Record<TaskStatus, string> = {
  pending: "var(--text-muted)",
  planning: "var(--accent-blue)",
  in_progress: "var(--accent-yellow)",
  reviewing: "var(--accent-blue)",
  done: "var(--accent-green)",
  failed: "var(--accent-red)",
};

export const PRIORITY_COLORS: Record<TaskPriority, string> = {
  low: "var(--text-muted)",
  medium: "var(--accent-blue)",
  high: "var(--accent-yellow)",
  critical: "var(--accent-red)",
};

// ── Orchestration progress labels (Phase 9-2) ────────────────
export const ORCHESTRATION_PHASE_LABELS: Record<string, string> = {
  pending: "Starting\u2026",
  planning: "Planning\u2026",
  in_progress: "Building\u2026",
  reviewing: "Reviewing\u2026",
  done: "Done",
  failed: "Failed",
};

/** Format timestamp: today → HH:mm:ss, otherwise → YYYY-MM-DD HH:mm */
export function formatAuditTimestamp(iso: string): string {
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
