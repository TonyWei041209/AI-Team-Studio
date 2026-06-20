import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { tasksApi } from "../../api/tasks";
import { projectsApi } from "../../api/projects";
import type { TaskStatus, ProjectParticipant } from "../../types/api";
import type { AgentRunSummary } from "./types";
import { STATUS_COLORS } from "./types";
import "./PipelineStepper.css";

// The canonical pipeline order
const PIPELINE_ROLES = ["planner", "architect", "builder", "qa", "security_reviewer", "reviewer", "documentation"] as const;

// Derive per-role visual status from available data
type RoleVisualStatus = "disabled" | "waiting" | "active" | "completed" | "failed" | "skipped";

interface RoleStep {
  role: string;
  displayName: string;
  visualStatus: RoleVisualStatus;
  isMandatory: boolean;
  runCount: number;
  retryCount: number;
  elapsedMs: number | null;
  isCurrentlyActive: boolean;
}

interface PipelineStepperProps {
  taskId: string;
  taskStatus: TaskStatus;
  projectId: string;
}

export function PipelineStepper({ taskId, taskStatus, projectId }: PipelineStepperProps) {
  const { t } = useTranslation();
  const [participants, setParticipants] = useState<ProjectParticipant[]>([]);
  const [runs, setRuns] = useState<AgentRunSummary[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [parts, taskRuns] = await Promise.all([
          projectsApi.getParticipants(projectId),
          tasksApi.getRuns(taskId),
        ]);
        if (!cancelled) {
          setParticipants(parts);
          setRuns(taskRuns);
          setLoaded(true);
        }
      } catch {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [taskId, projectId, taskStatus]); // re-fetch when taskStatus changes (during orchestration)

  // Compute hasRunning before any early returns so the hook below is unconditional
  const hasRunning = runs.some((r) => r.status === "running");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Auto-poll runs every 2 seconds while any role is actively running
  useEffect(() => {
    if (hasRunning) {
      pollRef.current = setInterval(async () => {
        try {
          const freshRuns = await tasksApi.getRuns(taskId);
          setRuns(freshRuns);
        } catch {
          // Non-fatal: keep showing stale data
        }
      }, 2000);
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [hasRunning, taskId]);

  if (!loaded) return null; // Don't show anything until data loads

  // Build enabled roles set
  const enabledRoles = new Set(
    participants.filter((p) => p.is_enabled).map((p) => p.role_name)
  );
  // If no participants loaded (old project, no config), default all enabled
  const hasParticipantConfig = participants.length > 0;

  // Build enriched maps from runs (runs are ASC ordered, so last in array = latest)
  const runStatusMap = new Map<string, string>();
  const runCountMap = new Map<string, number>();
  const latestRunMap = new Map<string, AgentRunSummary>();

  for (const run of runs) {
    // Track count per role
    runCountMap.set(run.role, (runCountMap.get(run.role) ?? 0) + 1);
    // Latest run: ASC order means last iteration wins
    runStatusMap.set(run.role, run.status);
    latestRunMap.set(run.role, run);
  }

  const MANDATORY = new Set(["planner", "builder"]);
  const now = Date.now();

  // Determine which role is THE currently active one (only one at a time)
  // It is the role whose latest run has status "running"
  let currentlyActiveRole: string | null = null;
  for (const [role, status] of runStatusMap.entries()) {
    if (status === "running") {
      currentlyActiveRole = role;
      break;
    }
  }

  // Derive visual status + enriched data for each role
  const steps: RoleStep[] = PIPELINE_ROLES.map((role) => {
    const isEnabled = hasParticipantConfig ? enabledRoles.has(role) : true;
    const isMandatory = MANDATORY.has(role);
    const displayNameKey = `pipeline.role_${role}`;
    const runCount = runCountMap.get(role) ?? 0;
    const retryCount = Math.max(0, runCount - 1);
    const latestRun = latestRunMap.get(role) ?? null;

    // Compute elapsed time from the latest run
    let elapsedMs: number | null = null;
    if (latestRun && latestRun.started_at) {
      const startedAt = new Date(latestRun.started_at).getTime();
      if (!isNaN(startedAt)) {
        if (latestRun.ended_at) {
          const endedAt = new Date(latestRun.ended_at).getTime();
          elapsedMs = isNaN(endedAt) ? null : Math.max(0, endedAt - startedAt);
        } else if (latestRun.status === "running") {
          // Still running — compute against current time
          elapsedMs = Math.max(0, now - startedAt);
        }
      }
    }

    if (!isEnabled) {
      return {
        role,
        displayName: t(displayNameKey),
        visualStatus: "disabled" as const,
        isMandatory,
        runCount,
        retryCount,
        elapsedMs,
        isCurrentlyActive: false,
      };
    }

    const runStatus = runStatusMap.get(role);

    let visualStatus: RoleVisualStatus;
    if (runStatus === "completed") {
      visualStatus = "completed";
    } else if (runStatus === "running") {
      visualStatus = "active";
    } else if (runStatus === "failed") {
      visualStatus = "failed";
    } else if (taskStatus === "pending") {
      visualStatus = "waiting";
    } else {
      // No run exists yet for this role — use task status heuristic
      const roleIdx = PIPELINE_ROLES.indexOf(role);
      const statusIdx = getStatusProgressIndex(taskStatus);

      if (taskStatus === "failed" && !runStatus) {
        visualStatus = "waiting";
      } else if (statusIdx > roleIdx) {
        visualStatus = "completed";
      } else if (statusIdx === roleIdx) {
        visualStatus = "active";
      } else {
        visualStatus = "waiting";
      }
    }

    const isCurrentlyActive = currentlyActiveRole === role;

    return {
      role,
      displayName: t(displayNameKey),
      visualStatus,
      isMandatory,
      runCount,
      retryCount,
      elapsedMs,
      isCurrentlyActive,
    };
  });

  // Overall progress: count of completed + active roles out of enabled roles
  const enabledSteps = steps.filter((s) => s.visualStatus !== "disabled");
  const completedCount = enabledSteps.filter((s) => s.visualStatus === "completed").length;
  const totalEnabled = enabledSteps.length;

  return (
    <div className={`pipeline-stepper${taskStatus === "failed" ? " pipeline-stepper-failed" : ""}`}>
      <div className="pipeline-stepper-header">
        <span className="pipeline-stepper-title">{t("pipeline.title")}</span>
        <div className="pipeline-stepper-header-right">
          {hasRunning && (
            <span className="pipeline-stepper-live-badge">{t("pipeline.polling")}</span>
          )}
          <span
            className="pipeline-stepper-task-status"
            style={{ color: STATUS_COLORS[taskStatus] }}
          >
            {t(`pipeline.task_status_${taskStatus}`)}
          </span>
          <span className="pipeline-stepper-progress">
            {completedCount}/{totalEnabled}
          </span>
        </div>
      </div>
      <div className="pipeline-stepper-track">
        {steps.map((step, i) => (
          <div key={step.role} className="pipeline-step-wrapper">
            {i > 0 && (
              <div className={`pipeline-connector ${step.visualStatus === "disabled" ? "connector-disabled" : ""} ${steps[i - 1].visualStatus === "completed" ? "connector-done" : ""}`} />
            )}
            <div
              className={`pipeline-step pipeline-step-${step.visualStatus}${step.isCurrentlyActive ? " pipeline-step-current" : ""}`}
              title={t(`pipeline.status_${step.visualStatus}`)}
            >
              <div className="pipeline-step-icon-wrapper">
                <div className="pipeline-step-icon">
                  {getStatusIcon(step.visualStatus)}
                </div>
                {step.retryCount > 0 && (
                  <span className="pipeline-retry-badge">
                    {t("pipeline.retry_badge", { count: step.retryCount })}
                  </span>
                )}
              </div>
              <div className="pipeline-step-label">{step.displayName}</div>
              <div className={`pipeline-step-status-tag status-${step.visualStatus}`}>
                {t(`pipeline.status_${step.visualStatus}`)}
              </div>
              {step.elapsedMs !== null && (
                <div className="pipeline-step-elapsed">
                  {formatElapsed(step.elapsedMs, t)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Map task status to a progress index (which role stage we're at or past) */
function getStatusProgressIndex(status: TaskStatus): number {
  // Index corresponds to PIPELINE_ROLES: planner=0, architect=1, builder=2, qa=3, security_reviewer=4, reviewer=5, documentation=6
  switch (status) {
    case "pending": return -1;    // before planner
    case "planning": return 0;    // planner done (success_task_status = planning); architect also runs in PLANNING (tracked via run records)
    case "in_progress": return 2; // builder done (success_task_status = in_progress)
    case "reviewing": return 3;   // qa done (success_task_status = reviewing); security_reviewer + reviewer run here
    case "done": return 7;        // all done (past documentation at index 6; documentation runs in the DONE phase)
    case "failed": return -1;     // indeterminate
    default: return -1;
  }
}

function getStatusIcon(status: RoleVisualStatus): string {
  switch (status) {
    case "completed": return "✓";
    case "active": return "▶";
    case "failed": return "✗";
    case "disabled": return "—";
    case "waiting": return "○";
    case "skipped": return "⊘";
    default: return "○";
  }
}

/** Format elapsed milliseconds into a short human-readable string using i18n */
function formatElapsed(ms: number, t: (key: string, opts?: Record<string, number>) => string): string {
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 60) {
    return t("pipeline.elapsed_seconds", { seconds: totalSeconds });
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return t("pipeline.elapsed_minutes", { minutes, seconds });
}
