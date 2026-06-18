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
import { RoleStatusCards, type RoleStatus } from "./taskboard/RoleStatusCards";
import { TokenSummaryRow } from "./taskboard/TokenSummaryRow";
import { TeamConversation } from "./taskboard/TeamConversation";
import { TaskTeamView } from "./taskboard/TaskTeamView";

interface TaskBoardProps {
  projectId: string | null;
  onNavigateToSettings?: () => void;
  autoExpandTaskId?: string | null;
  onAutoExpandConsumed?: () => void;
}

export function TaskBoard({ projectId, onNavigateToSettings, autoExpandTaskId, onAutoExpandConsumed }: TaskBoardProps) {
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

  // Proposal detail expansion — collapsed by default, shows full ProposalCard when set to proposal id
  const [proposalDetailExpanded, setProposalDetailExpanded] = useState<string | null>(null);

  // Snapshot viewer (Phase 6E-B)
  const [snapshots, setSnapshots] = useState<Record<string, ExecutionSnapshotResponse>>({});
  const [snapshotLoading, setSnapshotLoading] = useState<string | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  // Execution request (Phase 6E-C) — keyed by snapshot_id
  const [execRequests, setExecRequests] = useState<Record<string, ExecutionRequestResponse>>({});
  const [execReqLoading, setExecReqLoading] = useState<string | null>(null);
  const [execReqError, setExecReqError] = useState<string | null>(null);

  // Cached runs per task (for role status cards)
  const [taskRunsCache, setTaskRunsCache] = useState<Record<string, Array<{role: string, status: string, output_summary: string, started_at: string | null, ended_at: string | null}>>>({});

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
  // route-3: per-request missing-parent policy, keyed by execution_request_id.
  // Effective value is `strictParent[requestId] ?? true` (default = fail-fast).
  const [strictParent, setStrictParent] = useState<Record<string, boolean>>({});

  // Rollback result (Phase 7D-1) — keyed by execution_request_id
  const [rollbackResults, setRollbackResults] = useState<Record<string, RollbackResult | "empty">>({});
  const [rollbackLoading, setRollbackLoading] = useState<string | null>(null);
  const [rollbackError, setRollbackError] = useState<string | null>(null);

  // Rollback trigger (Phase 7D-2)
  const [rollbackTriggerLoading, setRollbackTriggerLoading] = useState<string | null>(null);
  const [rollbackTriggerError, setRollbackTriggerError] = useState<Record<string, string | null>>({});

  // Team view mode — shows TaskTeamView instead of task list
  const [teamViewTaskId, setTeamViewTaskId] = useState<string | null>(null);

  // Single-flow workspace: which task is currently focused
  const [focusedTaskId, setFocusedTaskId] = useState<string | null>(null);

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

  // Auto-focus the most recent non-pending task, or first task, when tasks load
  useEffect(() => {
    if (!focusedTaskId && tasks.length > 0) {
      const recent = tasks.find(t => t.status !== "pending") || tasks[0];
      setFocusedTaskId(recent.id);
    }
  }, [tasks, focusedTaskId]);

  // Load runs for non-pending tasks (for role status cards output summaries)
  useEffect(() => {
    const nonPending = tasks.filter(t => t.status !== "pending");
    for (const t of nonPending) {
      if (taskRunsCache[t.id]) continue;
      tasksApi.getRuns(t.id).then((runs: Array<{role: string, status: string, output_summary: string, started_at: string | null, ended_at: string | null, model_provider?: string | null, model_name?: string | null}>) => {
        setTaskRunsCache(prev => ({ ...prev, [t.id]: runs }));
      }).catch(() => { /* ignore */ });
    }
  }, [tasks]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-expand task from sidebar composer — orchestrate only if still pending
  useEffect(() => {
    if (autoExpandTaskId && tasks.some((t) => t.id === autoExpandTaskId)) {
      setExpandedTask(autoExpandTaskId);
      setFocusedTaskId(autoExpandTaskId);
      const targetTask = tasks.find((t) => t.id === autoExpandTaskId);
      if (targetTask && targetTask.status === "pending") {
        void handleOrchestrate(autoExpandTaskId);
      }
      onAutoExpandConsumed?.();
    }
  }, [autoExpandTaskId, tasks]); // eslint-disable-line react-hooks/exhaustive-deps

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
          { strict_parent: strictParent[requestId] ?? true },
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
    [strictParent],
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
        { strict_parent: strictParent[requestId] ?? true },
      );
      setDryRunResults((prev) => ({ ...prev, [requestId]: result }));
    } catch (err) {
      setDryRunError(
        err instanceof Error ? err.message : "Failed to run dry-run",
      );
    } finally {
      setDryRunLoading(null);
    }
  }, [strictParent]);

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
        { strict_parent: strictParent[requestId] ?? true },
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
  }, [strictParent]);

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
    setTeamViewTaskId(null);
    setFocusedTaskId(null);
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

  const deriveRoleStatuses = useCallback((taskId: string): RoleStatus[] => {
    const isOrchestrating = orchestrateLoading === taskId;
    const task = tasks.find(t => t.id === taskId);
    const cachedRuns = taskRunsCache[taskId] || [];

    const roleNames = ["planner", "builder", "qa", "reviewer"];
    const phaseMap: Record<string, number> = { planning: 0, in_progress: 1, reviewing: 2 };

    // Helper: extract short output summary from a run
    // Parse "[N items]" string format from truncated output
    const parseCount = (val: unknown): number => {
      if (Array.isArray(val)) return val.length;
      if (typeof val === "string") { const m = val.match(/^\[(\d+)\s*items?\]$/i); return m ? parseInt(m[1], 10) : 0; }
      return 0;
    };

    const getRunSummary = (role: string): { outputSummary?: string; duration?: string; model?: string; nextStep?: string } => {
      const run = cachedRuns.find(r => r.role === role && (r.status === "completed" || r.status === "failed"));
      if (!run || !run.output_summary) return {};
      let summary = "";
      try {
        const parsed = JSON.parse(run.output_summary);
        const data = parsed?.output && typeof parsed.output === "object" ? parsed.output : parsed;
        if (role === "planner") {
          const goal = data?.goal_summary || data?.goal || "";
          const stepCount = parseCount(data?.task_breakdown);
          const criteriaCount = parseCount(data?.acceptance_criteria);
          summary = goal ? `${String(goal).slice(0, 90)}` : "";
          if (stepCount) summary += ` · ${stepCount} steps`;
          if (criteriaCount) summary += ` · ${criteriaCount} criteria`;
        } else if (role === "builder") {
          const cs = data?.change_summary || "";
          const fc = parseCount(data?.proposed_files || data?.changed_files);
          const cc = parseCount(data?.proposed_commands);
          summary = cs ? `${String(cs).slice(0, 90)}` : "";
          if (fc) summary += ` · ${fc} file(s)`;
          if (cc) summary += ` · ${cc} cmd(s)`;
        } else if (role === "qa") {
          const result = data?.result || "";
          const scope = data?.validation_scope || "";
          summary = result ? `${String(result)}` : "";
          if (scope) summary += ` — ${String(scope).slice(0, 60)}`;
        } else if (role === "reviewer") {
          const decision = data?.decision || "";
          const reason = data?.reason || "";
          summary = decision ? `${String(decision).toUpperCase()}${reason ? ": " + String(reason).slice(0, 80) : ""}` : "";
        }
      } catch { /* fallback */ }
      let duration = "";
      if (run.started_at && run.ended_at) {
        const ms = new Date(run.ended_at).getTime() - new Date(run.started_at).getTime();
        duration = ms < 1000 ? "<1s" : ms < 60000 ? `${Math.round(ms/1000)}s` : `${Math.floor(ms/60000)}m ${Math.round((ms%60000)/1000)}s`;
      }
      const model = run.model_provider && run.model_name ? `${run.model_provider}/${run.model_name}` : run.model_provider || undefined;
      // Infer nextStep based on task state and role completion
      let nextStep: string | undefined;
      const taskStatus = task?.status || "";
      if (run.status === "completed") {
        if (role === "planner" && (taskStatus === "planning" || taskStatus === "in_progress")) nextStep = "Waiting for Builder";
        if (role === "builder" && taskStatus === "reviewing") nextStep = "Waiting for review";
        if (role === "qa" && taskStatus === "reviewing") nextStep = "Waiting for Reviewer";
        if (role === "reviewer" && taskStatus === "done") nextStep = "Idle — task complete";
      } else if (run.status === "failed") {
        nextStep = "Encountered error";
      }
      return { outputSummary: summary || undefined, duration: duration || undefined, model: model || undefined, nextStep };
    };

    if (!isOrchestrating && (!task || task.status === "pending")) {
      return roleNames.map(r => ({ role: r, status: "idle" as const }));
    }

    if (task?.status === "done") {
      return roleNames.map(r => ({ role: r, status: "completed" as const, ...getRunSummary(r) }));
    }

    if (task?.status === "failed") {
      return roleNames.map(r => {
        const run = cachedRuns.find(cr => cr.role === r);
        if (run?.status === "completed") return { role: r, status: "completed" as const, ...getRunSummary(r) };
        if (run?.status === "failed") return { role: r, status: "failed" as const, ...getRunSummary(r) };
        return { role: r, status: "idle" as const };
      });
    }

    const taskStatus = task?.status ?? "";
    const currentPhaseIdx = phaseMap[taskStatus] ?? -1;

    return roleNames.map((r, i) => {
      if (taskStatus === "reviewing") {
        if (i < 3) return { role: r, status: "completed" as const, ...getRunSummary(r) };
        if (i === 3) return { role: r, status: "running" as const };
      }
      if (i < currentPhaseIdx) return { role: r, status: "completed" as const, ...getRunSummary(r) };
      if (i === currentPhaseIdx) return { role: r, status: "running" as const };
      return { role: r, status: "idle" as const };
    });
  }, [orchestrateLoading, tasks, taskRunsCache]);

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
    setFocusedTaskId(created.id);
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

      {/* Team View — replaces task list when active */}
      {teamViewTaskId && (
        <TaskTeamView
          taskId={teamViewTaskId}
          projectId={projectId || ""}
          taskStatus={tasks.find(t => t.id === teamViewTaskId)?.status || ""}
          onBack={() => setTeamViewTaskId(null)}
        />
      )}

      {/* Single-flow workspace */}
      {!loading && tasks.length > 0 && !teamViewTaskId && (
        <div className="task-workspace" data-testid="task-list">
          {/* Task switcher strip */}
          <div className="task-switcher">
            {tasks.map((task) => (
              <button
                key={task.id}
                className={`task-switcher__item ${focusedTaskId === task.id ? "task-switcher__item--active" : ""}`}
                onClick={() => {
                  setFocusedTaskId(task.id);
                  setExpandedTask(null);
                  setProposalDetailExpanded(null);
                }}
                title={task.title}
              >
                <span className="task-switcher__dot" style={{
                  background: STATUS_COLORS[task.status] || "#666"
                }} />
                <span className="task-switcher__label">
                  {task.title.length > 30 ? task.title.slice(0, 30) + "\u2026" : task.title}
                </span>
              </button>
            ))}
          </div>

          {/* Focused task workspace */}
          {(() => {
            const task = tasks.find(t => t.id === focusedTaskId);
            if (!task) return null;
            return (
              <div className="task-focused" data-testid="task-item">
                <div className="task-focused__header">
                  <div className="task-focused__title">{task.title}</div>
                  {task.description && (
                    <div className="task-desc task-desc--truncated" style={{ marginTop: 4 }}>
                      {task.description.length > 120
                        ? task.description.slice(0, 120) + "\u2026"
                        : task.description}
                    </div>
                  )}
                  <div className="task-focused__meta task-badges">
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
                </div>

              {/* Current Status Strip removed — redundant with proposal summary row and RoleStatusCards */}

              {/* Pipeline Stepper (Phase 16-1) — hidden: RoleStatusCards shows same info */}
              {false && task.status !== "pending" && projectId && (
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

              {/* ═══ PRIMARY ZONE: Agent Collaboration ═══ */}

              {/* Team Conversation — FIRST: the main visual protagonist */}
              <TeamConversation
                taskId={task.id}
                taskTitle={task.title}
                taskStatus={task.status}
                visible={task.status !== "pending"}
                isOrchestrating={orchestrateLoading === task.id}
              />

              {/* Role Status Cards — SECOND: agent workstation cards */}
              <RoleStatusCards
                roles={deriveRoleStatuses(task.id)}
                visible={!!orchestrateLoading || task.status !== "pending"}
              />

              {/* View Team + Token — compact secondary info row */}
              {task.status !== "pending" && (
                <div style={{ padding: "4px 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <TokenSummaryRow
                    taskId={task.id}
                    visible={true}
                  />
                  <button
                    className="task-team-view__back"
                    onClick={() => setTeamViewTaskId(task.id)}
                  >
                    {t("teamView.viewTeam", "View Team \u2192")}
                  </button>
                </div>
              )}

              {/* Audit Trail (Phase 6E-E) — toggled, shown after conversation */}
              {auditTrailTaskId === task.id && (
                <AuditTrailSection
                  events={auditTrailEvents}
                  count={auditTrailCount}
                  loading={auditTrailLoading}
                  error={auditTrailError}
                />
              )}

              {/* Execution Proposals (Phase 6E-A) — SECONDARY: collapsed summary by default */}
              {expandedTask === task.id && (
                <div className="proposal-section">
                  {proposalLoading && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "8px 16px" }}>
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
                      const isDetailOpen = proposalDetailExpanded === p.id;

                      // Parse proposal_data for summary
                      const pd = (() => { try { return JSON.parse(p.proposal_data); } catch { return {}; } })();
                      const summary = (pd.change_summary as string) || "Execution proposal";
                      const fileCount = Array.isArray(pd.proposed_files) ? (pd.proposed_files as unknown[]).length : 0;
                      const cmdCount = Array.isArray(pd.proposed_commands) ? (pd.proposed_commands as unknown[]).length : 0;
                      const riskLevel = (pd.risk_level as string) || "";

                      // Find the pending approval for this proposal (if any)
                      const pendingApproval = taskApprovals.find(
                        (a) => a.proposal_id === p.id && a.status === "pending"
                      );
                      const approvalStatus = taskApprovals.find((a) => a.proposal_id === p.id)?.status;

                      return (
                        <div key={p.id} className={isHistory ? "proposal-card-historical" : ""}>
                          {proposals.length > 1 && (
                            <span className={isLatest ? "proposal-latest-marker" : "proposal-history-marker"}>
                              {isLatest
                                ? t("tasks.latestProposal")
                                : t("tasks.historyProposal", { n: proposals.length - proposalIdx })}
                            </span>
                          )}

                          {/* Collapsed summary row */}
                          <div className="proposal-summary">
                            <span className="proposal-summary__text" title={summary}>
                              {summary.length > 60 ? summary.slice(0, 60) + "…" : summary}
                            </span>
                            <div className="proposal-summary__meta">
                              {riskLevel && (
                                <span className={`badge badge-risk badge-risk--${riskLevel}`}>
                                  {riskLevel}
                                </span>
                              )}
                              {approvalStatus && (
                                <span className={`badge badge-approval badge-approval--${approvalStatus}`}>
                                  {approvalStatus}
                                </span>
                              )}
                              {fileCount > 0 && (
                                <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.4)" }}>
                                  {fileCount}f
                                </span>
                              )}
                              {cmdCount > 0 && (
                                <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.4)" }}>
                                  {cmdCount}c
                                </span>
                              )}
                              {pendingApproval && (
                                <>
                                  <button
                                    className="btn btn-sm"
                                    style={{ fontSize: 11, color: "#22c55e", borderColor: "#22c55e" }}
                                    disabled={approvalLoading === pendingApproval.id}
                                    onClick={() => void resolveApproval(pendingApproval.id, "approved")}
                                  >
                                    {approvalLoading === pendingApproval.id ? "…" : t("approvals.approve", "Approve")}
                                  </button>
                                  <button
                                    className="btn btn-sm"
                                    style={{ fontSize: 11, color: "#ef4444", borderColor: "#ef4444" }}
                                    disabled={approvalLoading === pendingApproval.id}
                                    onClick={() => void resolveApproval(pendingApproval.id, "rejected")}
                                  >
                                    {t("approvals.reject", "Reject")}
                                  </button>
                                </>
                              )}
                              <button
                                className="proposal-summary__toggle"
                                onClick={() => setProposalDetailExpanded(isDetailOpen ? null : p.id)}
                              >
                                {isDetailOpen ? "Hide Details \u25be" : "Show Details \u25b8"}
                              </button>
                            </div>
                          </div>

                          {/* Full proposal detail — only when expanded */}
                          {isDetailOpen && (
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
                              strictParent={execReq ? (strictParent[execReq.id] ?? true) : true}
                              onStrictParentChange={(v) => {
                                if (execReq) {
                                  setStrictParent((prev) => ({ ...prev, [execReq.id]: v }));
                                }
                              }}
                              strictParentLocked={
                                execReq
                                  ? !!dryRun || (!!realRun && realRun !== "empty")
                                  : false
                              }
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
                          )}
                        </div>
                      );
                    })}
                </div>
              )}
              </div>
            );
          })()}
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
