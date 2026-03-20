import { api } from "./client";

export interface ProjectHealth {
  project_id: string;
  project_name: string;
  total_tasks: number;
  active_tasks: number;
  failed_tasks: number;
  done_tasks: number;
  pending_approvals: number;
  last_activity: string | null;
}

export interface TokenSummary {
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  log_entries: number;
}

export interface DashboardSummary {
  project_count: number;
  total_tasks: number;
  task_by_status: Record<string, number>;
  pending_approvals: number;
  active_orchestrations: number;
  failed_tasks: number;
  project_health: ProjectHealth[];
  token_summary: TokenSummary;
}

export interface AttentionFailedTask {
  id: string;
  title: string;
  status: string;
  project_id: string;
  project_name: string;
  updated_at: string;
}

export interface AttentionPendingApproval {
  id: string;
  task_id: string;
  action_type: string;
  status: string;
  created_at: string;
  task_title: string;
  project_id: string;
  project_name: string;
}

export interface AttentionStalledRequest {
  id: string;
  task_id: string;
  status: string;
  risk_level: string;
  created_at: string;
  task_title: string;
  project_id: string;
  project_name: string;
}

export interface RecentTask {
  id: string;
  title: string;
  status: string;
  project_id: string;
  project_name: string;
  created_at: string;
  updated_at: string;
}

export interface RecentRun {
  id: string;
  task_id: string;
  role: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  task_title: string;
}

export interface DashboardAttention {
  attention: {
    failed_tasks: AttentionFailedTask[];
    pending_approvals: AttentionPendingApproval[];
    stalled_requests: AttentionStalledRequest[];
  };
  recent_activity: {
    recent_tasks: RecentTask[];
    recent_runs: RecentRun[];
  };
}

export const dashboardApi = {
  getSummary: () => api.get<DashboardSummary>("/api/dashboard/summary"),
  getAttention: () => api.get<DashboardAttention>("/api/dashboard/attention"),
};
