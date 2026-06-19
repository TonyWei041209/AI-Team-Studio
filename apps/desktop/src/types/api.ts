/**
 * TypeScript interfaces matching backend Pydantic models.
 * Source of truth: services/runtime/models.py
 */

// ── Enums (string union types) ──────────────────────────

export type TaskStatus =
  | "pending"
  | "planning"
  | "in_progress"
  | "reviewing"
  | "done"
  | "failed";

export type TaskPriority = "low" | "medium" | "high" | "critical";

export type AgentRole = "planner" | "builder" | "qa" | "security_reviewer" | "reviewer";

export type RunStatus = "pending" | "running" | "completed" | "failed";

export type ApprovalStatus = "pending" | "approved" | "rejected" | "consumed";

export type LogLevel = "debug" | "info" | "warn" | "error";

// ── Tab navigation ──────────────────────────────────────

export type TabId = "dashboard" | "projects" | "tasks" | "skills" | "roles" | "approvals" | "logs" | "settings";

// ── Connection state ────────────────────────────────────

export type ConnectionState = "checking" | "connected" | "disconnected";

// ── Project ─────────────────────────────────────────────

export interface Project {
  id: string;
  name: string;
  local_repo_path: string;
  default_branch: string;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  name: string;
  local_repo_path: string;
  default_branch?: string;
  description?: string;
}

// ── Task ────────────────────────────────────────────────

export interface Task {
  id: string;
  project_id: string;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  assigned_agent_role: AgentRole | null;
  created_at: string;
  updated_at: string;
}

export interface TaskCreate {
  title: string;
  description?: string;
  priority?: TaskPriority;
  assigned_agent_role?: AgentRole;
}

// ── AgentRun ────────────────────────────────────────────

export interface AgentRun {
  id: string;
  task_id: string;
  role: AgentRole;
  model_provider: string | null;
  model_name: string | null;
  status: RunStatus;
  input_summary: string;
  output_summary: string;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

// ── ApprovalRequest ─────────────────────────────────────

export interface ApprovalRequest {
  id: string;
  task_id: string | null;
  run_id: string | null;
  action_type: string;
  action_payload: string;
  status: ApprovalStatus;
  reviewer_comment: string;
  created_at: string;
  proposal_id: string | null;
  resolved_at: string | null;
}

export interface ApprovalResolve {
  status: "approved" | "rejected";
  reviewer_comment?: string;
}

// ── LogEvent ────────────────────────────────────────────

export interface LogEvent {
  id: string;
  task_id: string | null;
  run_id: string | null;
  level: LogLevel;
  source: string;
  message: string;
  payload: string;
  created_at: string;
}

// ── Health ──────────────────────────────────────────────

export interface HealthStatus {
  status: string;
  version: string;
  database: string;
}

// ── Provider types (Phase 6A) ──────────────────────────

export interface ProviderInfo {
  name: string;
  display_name: string;
}

export interface ProviderModelInfo {
  id: string;
  display_name: string;
  provider: string;
  max_tokens: number;
  supports_system_prompt: boolean;
}

export interface ProviderTestResult {
  ok: boolean;
  provider: string;
  message: string;
  latency_ms: number;
}

export interface ProviderSettingRead {
  provider_name: string;
  api_key_masked: string;
  base_url: string;
  enabled: boolean;
}

export interface ProviderSettingsResponse {
  providers: ProviderSettingRead[];
}

export interface ProviderSettingUpdate {
  provider_name: string;
  api_key?: string;
  base_url?: string;
  enabled?: boolean;
}

// ── Role-model settings (Phase 6C) ────────────────────

export interface RoleModelSetting {
  role: string;
  provider: string;
  model: string;
  enabled: boolean;
}

export interface RoleModelSettingsResponse {
  role_models: Record<string, RoleModelSetting>;
}

export interface RoleModelSettingUpdate {
  role: string;
  provider?: string;
  model?: string;
  enabled?: boolean;
}

// ── Skill (Phase 14) ────────────────────────────────

export type SkillScopeType = "global" | "agent";

export interface Skill {
  id: string;
  name: string;
  description: string;
  content: string;
  scope_type: SkillScopeType;
  agent_role: AgentRole | null;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface SkillCreate {
  name: string;
  description?: string;
  content?: string;
  scope_type: SkillScopeType;
  agent_role?: AgentRole | null;
  is_enabled?: boolean;
}

export interface SkillUpdate {
  name?: string;
  description?: string;
  content?: string;
  scope_type?: SkillScopeType;
  agent_role?: AgentRole | null;
  is_enabled?: boolean;
}

// ── Role (Phase 15) ────────────────────────────────

export interface Role {
  id: string;
  name: string;
  display_name: string;
  description: string;
  department: string;
  is_system: boolean;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface RoleCreate {
  name: string;
  display_name: string;
  description?: string;
  department?: string;
  is_enabled?: boolean;
}

export interface RoleUpdate {
  display_name?: string;
  description?: string;
  department?: string;
  is_enabled?: boolean;
}

// ── Project Participants (Phase 15-3) ──────────────────

export interface ProjectParticipant {
  role_name: string;
  is_enabled: boolean;
}

export interface ProjectParticipantsUpdate {
  participants: ProjectParticipant[];
}
