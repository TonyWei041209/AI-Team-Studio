"""Pydantic models for all core entities."""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


# ── Enums ──────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentRole(str, Enum):
    PLANNER = "planner"
    ARCHITECT = "architect"
    BUILDER = "builder"
    QA = "qa"
    SECURITY_REVIEWER = "security_reviewer"
    REVIEWER = "reviewer"
    DOCUMENTATION = "documentation"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONSUMED = "consumed"    # Phase 4B: one-time use after approved execution


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    BLOCK = "BLOCK"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


# ── Task State Machine ─────────────────────────────────

TASK_TRANSITIONS: dict[TaskStatus, list[TaskStatus]] = {
    TaskStatus.PENDING:     [TaskStatus.PLANNING, TaskStatus.IN_PROGRESS, TaskStatus.FAILED],
    TaskStatus.PLANNING:    [TaskStatus.IN_PROGRESS, TaskStatus.FAILED],
    TaskStatus.IN_PROGRESS: [TaskStatus.REVIEWING, TaskStatus.FAILED],
    TaskStatus.REVIEWING:   [TaskStatus.DONE, TaskStatus.IN_PROGRESS, TaskStatus.FAILED],
    TaskStatus.DONE:        [],
    TaskStatus.FAILED:      [TaskStatus.PENDING],
}


def is_valid_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    """Check if a task status transition is allowed."""
    return to_status in TASK_TRANSITIONS.get(from_status, [])


# ── Project ────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    local_repo_path: str
    default_branch: str = "main"
    description: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    local_repo_path: Optional[str] = None
    default_branch: Optional[str] = None
    description: Optional[str] = None


class Project(BaseModel):
    id: str
    name: str
    local_repo_path: str
    default_branch: str
    description: str
    created_at: str
    updated_at: str


# ── Task ───────────────────────────────────────────────

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    assigned_agent_role: Optional[AgentRole] = None


class TaskStatusUpdate(BaseModel):
    status: TaskStatus


class Task(BaseModel):
    id: str
    project_id: str
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    assigned_agent_role: Optional[str]
    created_at: str
    updated_at: str


# ── AgentRun ───────────────────────────────────────────

class AgentRunCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    role: AgentRole
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    input_summary: str = ""


class AgentRunUpdate(BaseModel):
    status: Optional[RunStatus] = None
    output_summary: Optional[str] = None


class AgentRun(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    task_id: str
    role: AgentRole
    model_provider: Optional[str]
    model_name: Optional[str]
    status: RunStatus
    input_summary: str
    output_summary: str
    started_at: Optional[str]
    ended_at: Optional[str]
    created_at: str


# ── ApprovalRequest ────────────────────────────────────

class ApprovalRequestCreate(BaseModel):
    run_id: Optional[str] = None
    action_type: str
    action_payload: str = "{}"


class ApprovalResolve(BaseModel):
    status: ApprovalStatus  # approved or rejected
    reviewer_comment: str = ""


class ApprovalRequest(BaseModel):
    id: str
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    action_type: str
    action_payload: str
    status: ApprovalStatus
    reviewer_comment: str
    created_at: str
    resolved_at: Optional[str]
    proposal_id: Optional[str] = None  # Phase 6E-A: FK to execution_proposals


# ── LogEvent ───────────────────────────────────────────

class LogEventCreate(BaseModel):
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    level: LogLevel = LogLevel.INFO
    source: str = "system"
    message: str
    payload: str = "{}"


class LogEvent(BaseModel):
    id: str
    task_id: Optional[str]
    run_id: Optional[str]
    level: LogLevel
    source: str
    message: str
    payload: str
    created_at: str


# ── Provider models (Phase 6A) ────────────────────────────────────

class ProviderInfo(BaseModel):
    """Provider metadata returned by GET /api/providers."""
    name: str
    display_name: str


class ProviderModelInfo(BaseModel):
    """Model metadata returned by GET /api/models."""
    id: str
    display_name: str
    provider: str
    max_tokens: int = 4096
    supports_system_prompt: bool = True


class ProviderTestRequest(BaseModel):
    """Request body for POST /api/provider-test."""
    provider: str


class ProviderTestResult(BaseModel):
    """Response from POST /api/provider-test."""
    ok: bool
    provider: str
    message: str = ""
    latency_ms: float = 0.0


class ProviderSettingRead(BaseModel):
    """Single provider's settings — api_key is masked."""
    provider_name: str
    api_key_masked: str
    base_url: str = ""
    enabled: bool = False


class ProviderSettingsResponse(BaseModel):
    """Response for GET /api/settings/providers."""
    providers: list[ProviderSettingRead]


class ProviderSettingUpdate(BaseModel):
    """Patch body for a single provider's settings."""
    provider_name: str
    api_key: Optional[str] = None  # None = don't change
    base_url: Optional[str] = None
    enabled: Optional[bool] = None


class ProviderSettingsPatch(BaseModel):
    """Request body for PATCH /api/settings/providers."""
    providers: list[ProviderSettingUpdate]


# ── Role-model settings (Phase 6C) ──────────────────────────────

class RoleModelSetting(BaseModel):
    """Single role's model configuration (read response)."""
    model_config = ConfigDict(protected_namespaces=())

    role: str
    provider: str = "mock"
    model: str = ""
    enabled: bool = False


class RoleModelSettingsResponse(BaseModel):
    """Response for GET /api/settings/role-models."""
    role_models: dict[str, RoleModelSetting]


class RoleModelSettingUpdate(BaseModel):
    """Patch body for a single role's model settings."""
    model_config = ConfigDict(protected_namespaces=())

    role: str
    provider: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None


class RoleModelSettingsPatch(BaseModel):
    """Request body for PATCH /api/settings/role-models."""
    role_models: list[RoleModelSettingUpdate]


# ── Execution Proposals (Phase 6E-A) ──────────────────────────────


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ExecutionProposal(BaseModel):
    """Persistent execution proposal from Builder agent."""
    id: str
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    role: str = "builder"
    proposal_data: str = "{}"           # JSON string — full Builder output
    risk_level: str = "medium"
    requires_approval: bool = True
    approval_reasons: str = "[]"        # JSON array string
    status: str = "pending"
    created_at: str
    updated_at: str


class ExecutionProposalResponse(BaseModel):
    """Response for GET /api/tasks/{task_id}/proposals."""
    proposals: list[ExecutionProposal]


# ── Execution Snapshots (Phase 6E-B) ─────────────────────────────


class ExecutionSnapshot(BaseModel):
    """Frozen, immutable copy of an approved execution proposal."""
    id: str
    proposal_id: str
    approval_id: str
    task_id: str
    snapshot_data: str          # JSON string — frozen copy of proposal_data
    content_hash: str           # SHA-256 of snapshot_data for tamper detection
    risk_level: str
    status: str = "frozen"      # Only 'frozen' allowed in Phase 6E-B
    created_at: str


class ExecutionSnapshotResponse(BaseModel):
    """Response for snapshot endpoints."""
    snapshot: ExecutionSnapshot


# ── Skill Scope ───────────────────────────────────────

class SkillScopeType(str, Enum):
    GLOBAL = "global"
    AGENT = "agent"


# ── Skill ─────────────────────────────────────────────

class SkillCreate(BaseModel):
    name: str
    description: str = ""
    content: str = ""
    scope_type: SkillScopeType = SkillScopeType.GLOBAL
    agent_role: Optional[str] = None
    is_enabled: bool = True


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    scope_type: Optional[SkillScopeType] = None
    agent_role: Optional[str] = None
    is_enabled: Optional[bool] = None


class Skill(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    content: str
    scope_type: str
    agent_role: Optional[str] = None
    is_enabled: bool
    created_at: str
    updated_at: str


# ── Role Registry (Phase 15-1) ───────────────────────


class RoleCreate(BaseModel):
    name: str
    display_name: str
    description: str = ""
    department: str = "engineering"
    is_enabled: bool = True


class RoleUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    department: Optional[str] = None
    is_enabled: Optional[bool] = None


class Role(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    display_name: str
    description: str
    department: str
    is_system: bool
    is_enabled: bool
    created_at: str
    updated_at: str


# ── Project Participants (Phase 15-3) ──────────────────

class ProjectParticipant(BaseModel):
    role_name: str
    is_enabled: bool

class ProjectParticipantsUpdate(BaseModel):
    participants: list[ProjectParticipant]
