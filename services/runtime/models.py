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
    BUILDER = "builder"
    QA = "qa"
    REVIEWER = "reviewer"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


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
