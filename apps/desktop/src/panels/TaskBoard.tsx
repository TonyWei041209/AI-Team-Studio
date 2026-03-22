import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { TaskCreate } from "../types/api";
import { useTasks } from "../hooks/useTasks";
import { tasksApi } from "../api/tasks";
import { projectsApi } from "../api/projects";
import { api } from "../api/client";
import { approvalsApi } from "../api/approvals";
import type { ApprovalRequest as ApprovalRequestType, ApprovalResolve } from "../types/api";
import { ConfirmModal } from "../components/ConfirmModal";
import "./TaskBoard.css";

import type {
  ExecutionProposal,
  ExecutionSnapshotResponse,
  ExecutionRequestResponse,
  DryRunResult,
  ActionPlanResponse,
  AuditTrailResponse,
  RealRunResult,
  RollbackResult,
} from "./taskboard/types";
import {
  STATUS_COLORS,
  PRIORITY_COLORS,
} from "./taskboard/types";
import { TaskCreateForm } from "./taskboard/TaskCreateForm";
import { AuditTrailSection } from "./taskboard/AuditTrailSection";
import { ProposalCard } from "./taskboard/ProposalCard";
import { PipelineStepper } from "./taskboard/PipelineStepper";
import { OrchestrationErrorGuide } from "./taskboard/OrchestrationErrorGuide";
import { TaskStateSummary, derivePhase } from "./taskboard/TaskStateSummary";
import { NextStepHint } from "./taskboard/NextStepHint";
import { QuickTaskInput } from "./taskboard/QuickTaskInput";
import { RoleStatusCards } from "./taskboard/RoleStatusCards";

interface TaskBoardProps {
  projectId: string | null;
  onNavigateToSettings?: () => void;
}

export function TaskBoard({ projectId, onNavigateToSettings }: TaskBoardProps) {
  const { t } = useTranslation();
  const { tasks, loading, error, refresh, createTask, orchestrateTask } = useTasks(projectId);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [orchestrateLoading, setOrchestrateLoading] = useState<string | null>(null);
  const [orchestratePhase, setOrchestratePhase] = useState<string>("Starting\u2026");
  const [orchestrateError, setOrchestrateError] = useState<Record<string, string>>({});
  const orchestratePollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Form fields
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<import("../types/api").TaskPriority>("medium");

  // Proposal expansion (Phase 6E-A)
  const [expandedTask, setExpandedTask] = useState<string | null>(null);
  const [proposals, setProposals] = useState<ExecutionProposal[]>([]);
  const [proposalLoading, setProposalLoading] = useState(false);

  // Snapshot viewer (Phase 6E-B)
  const [snapshots, setSnapshots] = useState<Record<string, ExecutionSnapshotResponse>>({});
  const [snapshotLoading, setSnapshotLoading] = useState<string | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  // Execution request (Phase 6E-C) — keyed by snapshot_id
  const [execRequests, setExecRequests] = useState<Record<string, ExecutionRequestResponse>>({});
  const [execReqLoading, setExecReqLoading] = useState<string | null>(null);
  const [execReqError, setExecReqError] = useState<string | null>(null);

  // Audit trail (Phase 6E-E)
  const [auditTrailTaskId, setAuditTrailTaskId] = useState<string | null>(null);
  const [auditTrailEvents, setAuditTrailEvents] = useState<import("./taskboard/types").AuditEvent[]>([]);
  const [auditTrailCount, setAuditTrailCount] = useState<number | null>(null);
  const [auditTrailLoading, setAuditTrailLoading] = useState(false);
  const [auditTrailError, setAuditTrailError] = useState<string | null>(null);

  // Dry-run results (Phase 6F-B) — keyed by execution_request_id
  const [dryRunResults, setDryRunResults] = useState<Record<string, DryRunResult>>({});
  const [dryRunLoading, setDryRunLoading] = useState<string | null>(null);
  const [dryRunError, setDryRunError] = useState<string | null>(null);

  // Action plan (Phase 6G-A) — keyed by execution_request_id
  const [actionPlans, setActionPlans] = useState<Record<string, ActionPlanResponse>>({});
  const [actionPlanLoading, setActionPlanLoading] = useState<string | null>(null);
  const [actionPlanError, setActionPlanError] = useState<string | null>(null);

  // Real-run result (Phase 7B-1) — keyed by execution_request_id
  const [realRunResults, setRealRunResults] = useState<Record<string, RealRunResult | "empty">>({});
  const [realRunLoading, setRealRunLoading] = useState<string | null>(null);
  const [realRunError, setRealRunError] = useState<string | null>(null);

  // Execute trigger (Phase 7B-2)
  const [executeLoading, setExecuteLoading] = useState<string | null>(null);
  const [executeError, setExecuteError] = useState<Record<string, string | null>>({});

  // Rollback result (Phase 7D-1) — keyed by execution_request_id
  const [rollbackResults, setRollbackResults] = useState<Record<string, RollbackResult | "empty">>({});
  const [rollbackLoading, setRollbackLoading] = useState<string | null>(null);
  const [rollbackError, setRollbackError] = useState<string | null>(null);

  // Rollback trigger (Phase 7D-2)
  const [rollbackTriggerLoading, setRollbackTriggerLoading] = useState<string | null>(null);
  const [rollbackTriggerError, setRollbackTriggerError] = useState<Record<string, string | null>>({});

  // Inline approval actions (Phase 9-4)
  const [taskApprovals, setTaskApprovals] = useState<ApprovalRequestType[]>([]);
  const [approvalLoading, setApprovalLoading] = useState<string | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);

  // Godot detection (Phase 5.8)
  const [isGodotProject, setIsGodotProject] = useState(false);
  useEffect(() => {
    if (!projectId) { setIsGodotProject(false); return; }
    projectsApi.getGodotInfo(projectId).then((info) => {
      setIsGodotProject(info.is_godot);
    }).catch(() => setIsGodotProject(false));
  }, [projectId]);

  // Confirm modal state (Phase 13-4 — replaces window.confirm)
  const [confirmModal, setConfirmModal] = useState<{
    type: "execute" | "rollback";
    requestId: string;
    resultId?: string;
  } | null>(null);

  // Enum display label helpers (Phase 13-4)
  const STATUS_LABELS: Record<string, string> = {
    pending: t("enum.statusPending"),
    planning: t("enum.statusPlanning"),
    in_progress: t("enum.statusInProgress"),
    reviewing: t("enum.statusReviewing"),
    done: t("enum.statusDone"),
    failed: t("enum.statusFailed"),
  };
  const PRIORITY_LABELS: Record<string, string> = {
    low: t("enum.priorityLow"),
    medium: t("enum.priorityMedium"),
    high: t("enum.priorityHigh"),
    critical: t("enum.priorityCritical"),
  };
  const ORCH_LABELS: Record<string, string> = {
    pending: t("enum.orchStarting"),
    planning: t("enum.orchPlanning"),
    in_progress: t("enum.orchBuilding"),
    reviewing: t("enum.orchReviewing"),
    done: t("enum.orchDone"),
    failed: t("enum.orchFailed"),
  };

  // Cleanup orchestration poll on unmount (Phase 9-2)
  useEffect(() => {
    return () => {
      if (orchestratePollRef.current) {
        clearInterval(orchestratePollRef.current);
        orchestratePollRef.current = null;
      }
    };
  }, []);

  const loadProposals = useCallback(async (taskId: string) => {
    setProposalLoading(true);
    try {
      const data = await api.get<{ proposals: ExecutionProposal[] }>(
        `/api/tasks/${taskId}/proposals`,
      );
      setProposals(data.proposals);
    } catch {
      setProposals([]);
    } finally {
      setProposalLoading(false);
    }
  }, []);

  // Phase 9-4: Load approvals for a task
  const loadTaskApprovals = useCallback(async (taskId: string) => {
    try {
      const data = await approvalsApi.listForTask(taskId);
      setTaskApprovals(data);
    } catch {
      setTaskApprovals([]);
    }
  }, []);

  // Phase 9-4: Resolve an approval inline
  // Phase 10-1: When approved, auto-freeze snapshot + create execution request
  const resolveApproval = useCallback(
    async (approvalId: string, status: "approved" | "rejected", proposalId?: string) => {
      setApprovalLoading(approvalId);
      setApprovalError(null);
      try {
        await approvalsApi.resolve(approvalId, { status } as ApprovalResolve);
        // Refresh both proposals and approvals after resolution
        if (expandedTask) {
          await Promise.all([
            loadProposals(expandedTask),
            loadTaskApprovals(expandedTask),
          ]);
        }
        // Phase 10-1: Auto-freeze + create execution request on approval
        // Phase Chat-First Step 3: Auto-chain confirm + dry-run after approval
        // Inline API calls to avoid declaration-order dependency on later useCallbacks
        if (status === "approved" && proposalId) {
          try {
            setSnapshotLoading(proposalId);
            setSnapshotError(null);
            const snap = await api.post<ExecutionSnapshotResponse>(
              `/api/proposals/${proposalId}/freeze`,
            );
            setSnapshots((prev) => ({ ...prev, [proposalId]: snap }));
            // Auto-create execution request
            let req: ExecutionRequestResponse | null = null;
            try {
              setExecReqLoading(snap.id);
              setExecReqError(null);
              req = await api.post<ExecutionRequestResponse>(
                `/api/snapshots/${snap.id}/request-execution`,
              );
              setExecRequests((prev) => ({ ...prev, [snap.id]: req! }));
            } catch (reqErr) {
              setExecReqError(
                reqErr instanceof Error ? reqErr.message : "Failed to auto-create execution request",
              );
            } finally {
              setExecReqLoading(null);
            }
            // Auto-confirm + dry-run (irreversible confirm, then simulation)
            if (req) {
              try {
                setExecReqLoading(snap.id);
                setExecReqError(null);
                const confirmed = await api.patch<ExecutionRequestResponse>(
                  `/api/execution-requests/${req.id}`,
                  { status: "confirmed" },
                );
                setExecRequests((prev) => ({ ...prev, [snap.id]: confirmed }));
              } catch (confirmErr) {
                setExecReqError(
                  confirmErr instanceof Error ? confirmErr.message : "Failed to auto-confirm execution request",
                );
                setExecReqLoading(null);
                // Don't proceed to dry-run if confirm failed
                req = null;
              }
              setExecReqLoading(null);
              if (req) {
                try {
                  setDryRunLoading(req.id);
                  setDryRunError(null);
                  const dryResult = await api.post<DryRunResult>(
                    `/api/execution-requests/${req.id}/dry-run`,
                  );
                  setDryRunResults((prev) => ({ ...prev, [req!.id]: dryResult }));
                } catch (drErr) {
                  setDryRunError(
                    drErr instanceof Error ? drErr.message : "Failed to auto-run dry-run",
                  );
                } finally {
                  setDryRunLoading(null);
                }
              }
            }
          } catch (freezeErr) {
            setSnapshotError(
              freezeErr instanceof Error ? freezeErr.message : "Failed to auto-freeze snapshot",
            );
          } finally {
            setSnapshotLoading(null);
          }
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Failed to resolve approval";
        setApprovalError(msg);
      } finally {
        setApprovalLoading(null);
      }
    },
    [expandedTask, loadProposals, loadTaskApprovals],
  );

  const toggleProposals = useCallback(
    (taskId: string) => {
      if (expandedTask === taskId) {
        setExpandedTask(null);
        setProposals([]);
        setTaskApprovals([]);
      } else {
        setExpandedTask(taskId);
        loadProposals(taskId);
        loadTaskApprovals(taskId);
      }
    },
    [expandedTask, loadProposals, loadTaskApprovals],
  );

  // Execution request: load existing for a snapshot (Phase 6E-C)
  const loadExecRequest = useCallback(async (snapshotId: string) => {
    const existing = await api.getOrNull<ExecutionRequestResponse>(
      `/api/snapshots/${snapshotId}/execution-request`,
    );
    if (existing) {
      setExecRequests((prev) => ({ ...prev, [snapshotId]: existing }));
      // If confirmed, also try loading dry-run result (Phase 6F-B)
      if (existing.status === "confirmed") {
        const drResult = await api.getOrNull<DryRunResult>(
          `/api/execution-requests/${existing.id}/dry-run`,
        );
        if (drResult) {
          setDryRunResults((prev) => ({ ...prev, [existing.id]: drResult }));
        }
      }
    }
  }, []);

  // Execution request: create (Phase 6E-C)
  const requestExecution = useCallback(async (snapshotId: string) => {
    setExecReqLoading(snapshotId);
    setExecReqError(null);
    try {
      const req = await api.post<ExecutionRequestResponse>(
        `/api/snapshots/${snapshotId}/request-execution`,
      );
      setExecRequests((prev) => ({ ...prev, [snapshotId]: req }));
    } catch (err) {
      setExecReqError(
        err instanceof Error ? err.message : "Failed to create execution request",
      );
    } finally {
      setExecReqLoading(null);
    }
  }, []);

  // Execution request: confirm or reject (Phase 6E-D)
  const updateExecRequestStatus = useCallback(
    async (snapshotId: string, requestId: string, newStatus: "confirmed" | "rejected") => {
      setExecReqLoading(snapshotId);
      setExecReqError(null);
      try {
        const updated = await api.patch<ExecutionRequestResponse>(
          `/api/execution-requests/${requestId}`,
          { status: newStatus },
        );
        setExecRequests((prev) => ({ ...prev, [snapshotId]: updated }));
      } catch (err) {
        setExecReqError(
          err instanceof Error ? err.message : "Failed to update execution request",
        );
      } finally {
        setExecReqLoading(null);
      }
    },
    [],
  );

  // Phase 10-2: Confirm & Dry-Run combo — confirm is irreversible
  const confirmAndDryRun = useCallback(
    async (snapshotId: string, requestId: string) => {
      // Step 1: Confirm (irreversible)
      setExecReqLoading(snapshotId);
      setExecReqError(null);
      try {
        const updated = await api.patch<ExecutionRequestResponse>(
          `/api/execution-requests/${requestId}`,
          { status: "confirmed" },
        );
        setExecRequests((prev) => ({ ...prev, [snapshotId]: updated }));
      } catch (confirmErr) {
        setExecReqError(
          confirmErr instanceof Error ? confirmErr.message : "Failed to confirm request",
        );
        setExecReqLoading(null);
        return; // Don't proceed to dry-run
      }
      setExecReqLoading(null);
      // Step 2: Dry-run (read-only simulation)
      setDryRunLoading(requestId);
      setDryRunError(null);
      try {
        const result = await api.post<DryRunResult>(
          `/api/execution-requests/${requestId}/dry-run`,
        );
        setDryRunResults((prev) => ({ ...prev, [requestId]: result }));
      } catch (drErr) {
        setDryRunError(
          drErr instanceof Error ? drErr.message : "Failed to run dry-run",
        );
      } finally {
        setDryRunLoading(null);
      }
    },
    [],
  );

  // Dry-run: load existing result (Phase 6F-B)
  const loadDryRunResult = useCallback(async (requestId: string) => {
    setDryRunLoading(requestId);
    setDryRunError(null);
    try {
      const result = await api.getOrNull<DryRunResult>(
        `/api/execution-requests/${requestId}/dry-run`,
      );
      if (result) {
        setDryRunResults((prev) => ({ ...prev, [requestId]: result }));
      }
    } catch {
      setDryRunError("Failed to load dry-run result");
    } finally {
      setDryRunLoading(null);
    }
  }, []);

  // Dry-run: trigger execution (Phase 6F-B)
  const triggerDryRun = useCallback(async (requestId: string) => {
    setDryRunLoading(requestId);
    setDryRunError(null);
    try {
      const result = await api.post<DryRunResult>(
        `/api/execution-requests/${requestId}/dry-run`,
      );
      setDryRunResults((prev) => ({ ...prev, [requestId]: result }));
    } catch (err) {
      setDryRunError(
        err instanceof Error ? err.message : "Failed to run dry-run",
      );
    } finally {
      setDryRunLoading(null);
    }
  }, []);

  // Action plan: load on demand (Phase 6G-A)
  const loadActionPlan = useCallback(async (requestId: string) => {
    setActionPlanLoading(requestId);
    setActionPlanError(null);
    try {
      const plan = await api.get<ActionPlanResponse>(
        `/api/execution-requests/${requestId}/action-plan`,
      );
      setActionPlans((prev) => ({ ...prev, [requestId]: plan }));
    } catch {
      setActionPlanError("Failed to load action plan");
    } finally {
      setActionPlanLoading(null);
    }
  }, []);

  // Load real-run result (Phase 7B-1) — on-demand, 404 = empty
  const loadRealRunResult = useCallback(async (requestId: string) => {
    setRealRunLoading(requestId);
    setRealRunError(null);
    try {
      const result = await api.getOrNull<RealRunResult>(
        `/api/execution-requests/${requestId}/real-run`,
      );
      if (result) {
        setRealRunResults((prev) => ({ ...prev, [requestId]: result }));
      } else {
        // 404 → empty state
        setRealRunResults((prev) => ({ ...prev, [requestId]: "empty" }));
      }
    } catch {
      setRealRunError("Failed to load execution result");
    } finally {
      setRealRunLoading(null);
    }
  }, []);

  // Trigger real execution (Phase 7B-2) — with confirm modal
  const requestExecConfirm = useCallback((requestId: string) => {
    setConfirmModal({ type: "execute", requestId });
  }, []);

  const doExecute = useCallback(async (requestId: string) => {

    setExecuteLoading(requestId);
    setExecuteError((prev) => ({ ...prev, [requestId]: null }));
    try {
      const result = await api.post<RealRunResult>(
        `/api/execution-requests/${requestId}/execute`,
      );
      // Auto-populate real-run result → viewer shows immediately, Execute button hides
      setRealRunResults((prev) => ({ ...prev, [requestId]: result }));
    } catch (err: unknown) {
      // Extract blocked_reasons from ApiError detail (409 case)
      let msg = "Execution failed";
      if (err && typeof err === "object" && "detail" in err) {
        const detail = (err as { detail: string }).detail;
        // ApiError.detail may be JSON string of { message, blocked_reasons }
        try {
          const parsed = JSON.parse(detail);
          if (parsed.blocked_reasons && Array.isArray(parsed.blocked_reasons)) {
            msg = `Not eligible: ${parsed.blocked_reasons.join(", ")}`;
          } else if (parsed.message) {
            msg = parsed.message;
          } else {
            msg = detail;
          }
        } catch {
          msg = detail;
        }
      } else if (err instanceof Error) {
        msg = err.message;
      }
      setExecuteError((prev) => ({ ...prev, [requestId]: msg }));
    } finally {
      setExecuteLoading(null);
    }
  }, []);

  // Load rollback result (Phase 7D-1) — on-demand, 404 = empty
  const loadRollbackResult = useCallback(async (requestId: string) => {
    setRollbackLoading(requestId);
    setRollbackError(null);
    try {
      const result = await api.getOrNull<RollbackResult>(
        `/api/execution-requests/${requestId}/rollback`,
      );
      if (result) {
        setRollbackResults((prev) => ({ ...prev, [requestId]: result }));
      } else {
        // 404 → empty state
        setRollbackResults((prev) => ({ ...prev, [requestId]: "empty" }));
      }
    } catch {
      setRollbackError("Failed to load rollback result");
    } finally {
      setRollbackLoading(null);
    }
  }, []);

  // Trigger rollback (Phase 7D-2) — with confirm modal
  const requestRollbackConfirm = useCallback((requestId: string, resultId: string) => {
    setConfirmModal({ type: "rollback", requestId, resultId });
  }, []);

  const doRollback = useCallback(async (requestId: string, resultId: string) => {
    setRollbackTriggerLoading(requestId);
    setRollbackTriggerError((prev) => ({ ...prev, [requestId]: null }));
    try {
      const result = await api.post<RollbackResult>(
        `/api/execution-results/${resultId}/rollback`,
      );
      // Auto-populate rollback result → viewer shows immediately, button hides
      setRollbackResults((prev) => ({ ...prev, [requestId]: result }));
    } catch (err: unknown) {
      let msg = "Rollback failed";
      if (err && typeof err === "object" && "detail" in err) {
        const detail = (err as { detail: string }).detail;
        try {
          const parsed = JSON.parse(detail);
          if (parsed.message && parsed.reason) {
            msg = `${parsed.message} (${parsed.reason})`;
          } else if (parsed.message) {
            msg = parsed.message;
          } else {
            msg = detail;
          }
        } catch {
          msg = detail;
        }
      } else if (err instanceof Error) {
        msg = err.message;
      }
      setRollbackTriggerError((prev) => ({ ...prev, [requestId]: msg }));
    } finally {
      setRollbackTriggerLoading(null);
    }
  }, []);

  // Audit trail: toggle open/close (Phase 6E-E)
  const toggleAuditTrail = useCallback(
    async (taskId: string) => {
      if (auditTrailTaskId === taskId) {
        // Close
        setAuditTrailTaskId(null);
        setAuditTrailEvents([]);
        setAuditTrailCount(null);
        setAuditTrailError(null);
        return;
      }
      // Open and load
      setAuditTrailTaskId(taskId);
      setAuditTrailLoading(true);
      setAuditTrailError(null);
      setAuditTrailEvents([]);
      setAuditTrailCount(null);
      try {
        const data = await api.get<AuditTrailResponse>(
          `/api/tasks/${taskId}/audit-trail`,
        );
        setAuditTrailEvents(data.events);
        setAuditTrailCount(data.count);
      } catch {
        setAuditTrailError("Failed to load audit trail");
        setAuditTrailEvents([]);
        setAuditTrailCount(null);
      } finally {
        setAuditTrailLoading(false);
      }
    },
    [auditTrailTaskId],
  );

  // When a snapshot is loaded, also check for existing execution request
  // Returns the snapshot on success, or null on failure (Phase 10-1: enables chaining)
  const freezeAndViewWithReqCheck = useCallback(async (proposalId: string): Promise<ExecutionSnapshotResponse | null> => {
    setSnapshotLoading(proposalId);
    setSnapshotError(null);
    try {
      const snap = await api.post<ExecutionSnapshotResponse>(
        `/api/proposals/${proposalId}/freeze`,
      );
      setSnapshots((prev) => ({ ...prev, [proposalId]: snap }));
      // Also load execution request if one exists
      await loadExecRequest(snap.id);
      return snap;
    } catch (err) {
      setSnapshotError(
        err instanceof Error ? err.message : "Failed to freeze snapshot",
      );
      return null;
    } finally {
      setSnapshotLoading(null);
    }
  }, [loadExecRequest]);

  // Reset form state when project changes
  useEffect(() => {
    setShowForm(false);
    setTitle("");
    setDescription("");
    setPriority("medium");
    setFormError(null);
    setExpandedTask(null);
    setProposals([]);
    setSnapshots({});
    setSnapshotError(null);
    setExecRequests({});
    setExecReqError(null);
    setAuditTrailTaskId(null);
    setAuditTrailEvents([]);
    setAuditTrailCount(null);
    setAuditTrailError(null);
    setDryRunResults({});
    setDryRunError(null);
    setActionPlans({});
    setActionPlanError(null);
    setRealRunResults({});
    setRealRunError(null);
    setExecuteError({});
  }, [projectId]);

  if (!projectId) {
    return (
      <div className="task-board">
        <div className="panel-header">
          <h2 className="panel-title">{t("tasks.title")}</h2>
        </div>
        <div className="panel-empty">
          {t("tasks.selectProject")}
        </div>
      </div>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;

    setSubmitting(true);
    setFormError(null);
    try {
      const body: TaskCreate = {
        title: title.trim(),
        description: description.trim() || undefined,
        priority,
      };
      await createTask(body);
      setTitle("");
      setDescription("");
      setPriority("medium");
      setShowForm(false);
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Failed to create task",
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

  const deriveRoleStatuses = useCallback((taskId: string): Array<{role: string, status: "idle" | "running" | "completed" | "failed"}> => {
    const isOrchestrating = orchestrateLoading === taskId;
    const task = tasks.find(t => t.id === taskId);

    const roles = ["planner", "builder", "qa", "reviewer"];
    const phaseMap: Record<string, number> = {
      planning: 0,
      in_progress: 1,
      reviewing: 2,
    };

    if (!isOrchestrating && (!task || task.status === "pending")) {
      return roles.map(r => ({ role: r, status: "idle" as const }));
    }

    if (task?.status === "done") {
      return roles.map(r => ({ role: r, status: "completed" as const }));
    }

    if (task?.status === "failed") {
      return roles.map(r => ({ role: r, status: "idle" as const }));
    }

    const taskStatus = task?.status ?? "";
    const currentPhaseIdx = phaseMap[taskStatus] ?? -1;

    return roles.map((r, i) => {
      if (taskStatus === "reviewing") {
        if (i < 2) return { role: r, status: "completed" as const };
        if (i === 2) return { role: r, status: "completed" as const };
        if (i === 3) return { role: r, status: "running" as const };
      }
      if (i < currentPhaseIdx) return { role: r, status: "completed" as const };
      if (i === currentPhaseIdx) return { role: r, status: "running" as const };
      return { role: r, status: "idle" as const };
    });
  }, [orchestrateLoading, tasks]);

  const handleOrchestrate = useCallback(async (taskId: string) => {
    setOrchestrateLoading(taskId);
    setOrchestratePhase("Starting\u2026");
    setOrchestrateError((prev) => {
      const next = { ...prev };
      delete next[taskId];
      return next;
    });
    if (orchestratePollRef.current) {
      clearInterval(orchestratePollRef.current);
    }
    orchestratePollRef.current = setInterval(async () => {
      try {
        const polledTask = await tasksApi.get(taskId);
        const label = ORCH_LABELS[polledTask.status] ?? polledTask.status;
        setOrchestratePhase(label);
      } catch {
        // Polling failure is non-fatal; keep showing last known phase
      }
    }, 1500);
    try {
      await orchestrateTask(taskId);
    } catch (err) {
      setOrchestrateError((prev) => ({
        ...prev,
        [taskId]: err instanceof Error ? err.message : "Orchestration failed",
      }));
    } finally {
      if (orchestratePollRef.current) {
        clearInterval(orchestratePollRef.current);
        orchestratePollRef.current = null;
      }
      setOrchestrateLoading(null);
      try {
        const finalTask = await tasksApi.get(taskId);
        setExpandedTask(taskId);
        if (finalTask.status === "done") {
          loadProposals(taskId);
          loadTaskApprovals(taskId);
        }
      } catch {
        // Best-effort; don't block UI on fetch failure
      }
    }
  }, [orchestrateTask, loadProposals, loadTaskApprovals, ORCH_LABELS]);

  const handleQuickTask = useCallback(async (text: string) => {
    if (!projectId) return;
    const created = await createTask({ title: text, description: text, priority: "medium" });
    setExpandedTask(created.id);
    void handleOrchestrate(created.id);
  }, [projectId, createTask, handleOrchestrate]);

  return (
    <div className="task-board">
      <div className="panel-header">
        <h2 className="panel-title">{t("tasks.title")}</h2>
        <div className="panel-actions">
          <button
            className="btn btn-secondary"
            onClick={refresh}
            title={t("tasks.refresh")}
          >
            &#8635;
          </button>
          <button
            className="btn btn-primary"
            data-testid="task-create-btn"
            onClick={() => setShowForm(!showForm)}
          >
            {showForm ? t("tasks.cancel") : t("tasks.newTask")}
          </button>
        </div>
      </div>

      <QuickTaskInput
        projectId={projectId}
        onSubmit={handleQuickTask}
        disabled={!!orchestrateLoading}
      />

      {/* Create form */}
      {showForm && (
        <TaskCreateForm
          title={title}
          description={description}
          priority={priority}
          formError={formError}
          submitting={submitting}
          isGodotProject={isGodotProject}
          onTitleChange={setTitle}
          onDescriptionChange={setDescription}
          onPriorityChange={setPriority}
          onSubmit={handleSubmit}
        />
      )}

      {/* Error state */}
      {error && (
        <div className="panel-error">
          <span>&#9888; {error}</span>
          <button className="btn btn-secondary btn-sm" onClick={refresh}>
            {t("tasks.retry")}
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && <div className="panel-loading">{t("tasks.loading")}</div>}

      {/* Empty state with guidance (Phase 20-4) */}
      {!loading && !error && tasks.length === 0 && (
        <div className="task-empty-guidance">
          <div className="task-empty-icon">▶</div>
          <div className="task-empty-text">{t("tasks.empty")}</div>
          <div className="task-empty-hint">{t("tasks.emptyHint")}</div>
          {!showForm && (
            <button className="btn btn-primary btn-sm" onClick={() => setShowForm(true)}>
              {t("tasks.newTask")}
            </button>
          )}
        </div>
      )}

      {/* Task list */}
      {!loading && tasks.length > 0 && (
        <div className="task-list" data-testid="task-list">
          {tasks.map((task) => (
            <div key={task.id} className="task-item" data-testid="task-item">
              <div className="task-item-header">
                <span className="task-title">{task.title}</span>
                <span className="task-date">{formatDate(task.updated_at)}</span>
              </div>
              {task.description && (
                <div className="task-desc">{task.description}</div>
              )}
              <div className="task-badges">
                {(() => {
                  // Derive pipeline state for this task (richer when expanded)
                  const isExpanded = expandedTask === task.id;
                  const firstProposal = isExpanded && proposals.length > 0 ? proposals[0] : undefined;
                  const snap = firstProposal ? snapshots[firstProposal.id] : undefined;
                  const execReq = snap ? execRequests[snap.id] : undefined;
                  const dryRun = execReq ? dryRunResults[execReq.id] : undefined;
                  const realRun = execReq ? realRunResults[execReq.id] : undefined;
                  const rollback = execReq ? rollbackResults[execReq.id] : undefined;
                  const pendingApproval = isExpanded && taskApprovals.some((a) => a.status === "pending");
                  const approvedProposal = isExpanded && taskApprovals.some((a) => a.status === "approved");
                  const hasExecError = isExpanded && Object.values(executeError).some((e) => e != null);

                  return (
                    <TaskStateSummary
                      taskStatus={task.status}
                      hasProposals={isExpanded && proposals.length > 0}
                      hasPendingApproval={pendingApproval}
                      hasApprovedProposal={approvedProposal}
                      hasSnapshot={!!snap}
                      hasConfirmedRequest={!!execReq && execReq.status === "confirmed"}
                      hasDryRunResult={!!dryRun}
                      hasRealRunResult={!!realRun && realRun !== "empty"}
                      hasExecutionError={hasExecError}
                      hasRollbackResult={!!rollback && rollback !== "empty"}
                      isOrchestrating={orchestrateLoading === task.id}
                    />
                  );
                })()}
                <span
                  className="badge badge-status"
                  style={{
                    color: STATUS_COLORS[task.status],
                    borderColor: STATUS_COLORS[task.status],
                  }}
                >
                  {STATUS_LABELS[task.status] ?? task.status.replace("_", " ")}
                </span>
                <span
                  className="badge badge-priority"
                  style={{
                    color: PRIORITY_COLORS[task.priority],
                    borderColor: PRIORITY_COLORS[task.priority],
                  }}
                >
                  {PRIORITY_LABELS[task.priority] ?? task.priority}
                </span>
                {task.assigned_agent_role && (
                  <span className="badge badge-role">
                    {task.assigned_agent_role}
                  </span>
                )}
                {task.status === "pending" && (
                  <button
                    className="btn btn-sm"
                    style={{
                      marginLeft: "auto",
                      fontSize: 11,
                      color: "#ff8c00",
                      borderColor: "#ff8c00",
                    }}
                    disabled={orchestrateLoading === task.id}
                    onClick={() => void handleOrchestrate(task.id)}
                  >
                    {orchestrateLoading === task.id
                      ? orchestratePhase
                      : "\u25b6 " + t("tasks.start")}
                  </button>
                )}
                {task.status !== "pending" && (
                  <button
                    className="btn btn-sm task-action-primary"
                    style={{ marginLeft: "auto" }}
                    onClick={() => toggleProposals(task.id)}
                  >
                    {expandedTask === task.id ? t("tasks.hideProposals") : t("tasks.proposals")}
                  </button>
                )}
                <button
                  className="btn btn-sm task-action-secondary"
                  onClick={() => toggleAuditTrail(task.id)}
                >
                  {auditTrailTaskId === task.id
                    ? t("tasks.hideAuditTrail")
                    : t("tasks.auditTrail")}
                </button>
              </div>

              {/* Current Status Strip — expanded task gets a prominent status bar (readability enhancement) */}
              {expandedTask === task.id && (() => {
                const firstP = proposals.length > 0 ? proposals[0] : undefined;
                const snap = firstP ? snapshots[firstP.id] : undefined;
                const eReq = snap ? execRequests[snap.id] : undefined;
                const dRun = eReq ? dryRunResults[eReq.id] : undefined;
                const rRun = eReq ? realRunResults[eReq.id] : undefined;
                const rbk = eReq ? rollbackResults[eReq.id] : undefined;
                const phase = derivePhase({
                  taskStatus: task.status,
                  hasProposals: proposals.length > 0,
                  hasPendingApproval: taskApprovals.some((a) => a.status === "pending"),
                  hasApprovedProposal: taskApprovals.some((a) => a.status === "approved"),
                  hasSnapshot: !!snap,
                  hasConfirmedRequest: !!eReq && eReq.status === "confirmed",
                  hasDryRunResult: !!dRun,
                  hasRealRunResult: !!rRun && rRun !== "empty",
                  hasExecutionError: Object.values(executeError).some((e) => e != null),
                  hasRollbackResult: !!rbk && rbk !== "empty",
                  isOrchestrating: orchestrateLoading === task.id,
                });
                return (
                  <div className="task-current-status-strip">
                    <TaskStateSummary
                      taskStatus={task.status}
                      hasProposals={proposals.length > 0}
                      hasPendingApproval={taskApprovals.some((a) => a.status === "pending")}
                      hasApprovedProposal={taskApprovals.some((a) => a.status === "approved")}
                      hasSnapshot={!!snap}
                      hasConfirmedRequest={!!eReq && eReq.status === "confirmed"}
                      hasDryRunResult={!!dRun}
                      hasRealRunResult={!!rRun && rRun !== "empty"}
                      hasExecutionError={Object.values(executeError).some((e) => e != null)}
                      hasRollbackResult={!!rbk && rbk !== "empty"}
                      isOrchestrating={orchestrateLoading === task.id}
                    />
                    <NextStepHint phase={phase} />
                  </div>
                );
              })()}

              {/* Pipeline Stepper (Phase 16-1) */}
              {task.status !== "pending" && projectId && (
                <>
                  <div className="task-section-label">{t("tasks.sectionOrchestration")}</div>
                  <PipelineStepper
                    taskId={task.id}
                    taskStatus={task.status}
                    projectId={projectId}
                  />
                </>
              )}

              {/* Orchestration error with guidance (Phase 20-1) */}
              {orchestrateError[task.id] && (
                <OrchestrationErrorGuide
                  error={orchestrateError[task.id]}
                  onGoToSettings={onNavigateToSettings}
                />
              )}

              {/* Audit Trail (Phase 6E-E) */}
              {auditTrailTaskId === task.id && (
                <AuditTrailSection
                  events={auditTrailEvents}
                  count={auditTrailCount}
                  loading={auditTrailLoading}
                  error={auditTrailError}
                />
              )}

              {/* Role Status Cards (Chat-First UX Step 2) */}
              <RoleStatusCards
                roles={deriveRoleStatuses(task.id)}
                visible={expandedTask === task.id && (!!orchestrateLoading || task.status !== "pending")}
              />

              {/* Execution Proposals (Phase 6E-A) */}
              {expandedTask === task.id && proposals.length > 0 && (
                <div className="task-section-label">{t("tasks.sectionProposals")}</div>
              )}
              {expandedTask === task.id && (
                <div className="proposal-section">
                  {proposalLoading && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      {t("tasks.loadingProposals")}
                    </div>
                  )}
                  {!proposalLoading && proposals.length === 0 && (
                    <div className="task-no-proposals">
                      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                        {t("tasks.noProposals")}
                      </div>
                      {task.status === "pending" && (
                        <div className="task-no-proposals-hint">{t("tasks.noProposalsHintPending")}</div>
                      )}
                      {(task.status === "planning" || task.status === "in_progress" || task.status === "reviewing") && (
                        <div className="task-no-proposals-hint">{t("tasks.noProposalsHintRunning")}</div>
                      )}
                      {task.status === "failed" && (
                        <div className="task-no-proposals-hint">{t("tasks.noProposalsHintFailed")}</div>
                      )}
                    </div>
                  )}
                  {!proposalLoading &&
                    proposals.map((p, proposalIdx) => {
                      const snap = snapshots[p.id];
                      const execReq = snap ? execRequests[snap.id] : undefined;
                      const dryRun = execReq ? dryRunResults[execReq.id] : undefined;
                      const actionPlan = execReq ? actionPlans[execReq.id] : undefined;
                      const realRun = execReq ? realRunResults[execReq.id] : undefined;
                      const rollback = execReq ? rollbackResults[execReq.id] : undefined;
                      const isLatest = proposalIdx === 0 && proposals.length > 1;
                      const isHistory = proposalIdx > 0;
                      return (
                        <div key={p.id} className={isHistory ? "proposal-card-historical" : ""}>
                          {proposals.length > 1 && (
                            <span className={isLatest ? "proposal-latest-marker" : "proposal-history-marker"}>
                              {isLatest
                                ? t("tasks.latestProposal")
                                : t("tasks.historyProposal", { n: proposals.length - proposalIdx })}
                            </span>
                          )}
                          <ProposalCard
                            proposal={p}
                            taskApprovals={taskApprovals}
                            approvalLoading={approvalLoading}
                            approvalError={approvalError}
                            onResolveApproval={resolveApproval}
                            snapshot={snap}
                            snapshotLoading={snapshotLoading === p.id}
                            snapshotError={snapshotError}
                            onFreezeAndView={freezeAndViewWithReqCheck}
                            onHideSnapshot={(proposalId) => setSnapshots((prev) => {
                              const next = { ...prev };
                              delete next[proposalId];
                              return next;
                            })}
                            execRequest={execReq}
                            execReqLoading={snap ? execReqLoading === snap.id : false}
                            execReqError={execReqError}
                            onRequestExecution={requestExecution}
                            onUpdateExecRequestStatus={updateExecRequestStatus}
                            onConfirmAndDryRun={confirmAndDryRun}
                            dryRunResult={dryRun}
                            dryRunLoading={execReq ? dryRunLoading === execReq.id : false}
                            dryRunError={dryRunError}
                            onTriggerDryRun={triggerDryRun}
                            onLoadDryRunResult={loadDryRunResult}
                            actionPlan={actionPlan}
                            actionPlanLoading={execReq ? actionPlanLoading === execReq.id : false}
                            actionPlanError={actionPlanError}
                            onLoadActionPlan={loadActionPlan}
                            realRunResult={realRun}
                            realRunLoading={execReq ? realRunLoading === execReq.id : false}
                            realRunError={realRunError}
                            executeLoading={execReq ? executeLoading === execReq.id : false}
                            executeError={execReq ? (executeError[execReq.id] ?? null) : null}
                            onTriggerExecution={requestExecConfirm}
                            onLoadRealRunResult={loadRealRunResult}
                            rollbackResult={rollback}
                            rollbackLoading={execReq ? rollbackLoading === execReq.id : false}
                            rollbackError={rollbackError}
                            rollbackTriggerLoading={execReq ? rollbackTriggerLoading === execReq.id : false}
                            rollbackTriggerError={execReq ? (rollbackTriggerError[execReq.id] ?? null) : null}
                            onTriggerRollback={requestRollbackConfirm}
                            onLoadRollbackResult={loadRollbackResult}
                            formatDate={formatDate}
                          />
                        </div>
                      );
                    })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Confirm Modal (Phase 13-4) */}
      <ConfirmModal
        open={confirmModal !== null}
        message={
          confirmModal?.type === "execute"
            ? t("confirm.executeTitle")
            : t("confirm.rollbackTitle")
        }
        confirmLabel={
          confirmModal?.type === "execute"
            ? t("confirm.executeConfirm")
            : t("confirm.rollbackConfirm")
        }
        cancelLabel={t("confirm.cancel")}
        danger={confirmModal?.type === "rollback"}
        onCancel={() => setConfirmModal(null)}
        onConfirm={() => {
          if (!confirmModal) return;
          setConfirmModal(null);
          if (confirmModal.type === "execute") {
            doExecute(confirmModal.requestId);
          } else if (confirmModal.resultId) {
            doRollback(confirmModal.requestId, confirmModal.resultId);
          }
        }}
      />
    </div>
  );
}
