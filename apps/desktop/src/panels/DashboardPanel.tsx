import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { dashboardApi } from "../api/dashboard";
import { settingsApi } from "../api/settings";
import type {
  DashboardSummary,
  ProjectHealth,
  DashboardAttention,
  RecentTask,
  RecentRun,
} from "../api/dashboard";
import type { ReadinessSummary } from "../api/settings";

/** Map role keys to i18n keys */
const ROLE_I18N: Record<string, string> = {
  planner: "settings.roleName_planner",
  builder: "settings.roleName_builder",
  qa: "settings.roleName_qa",
  reviewer: "settings.roleName_reviewer",
};
import "./DashboardPanel.css";

const REFRESH_INTERVAL = 15000; // 15 seconds

interface DashboardPanelProps {
  onNavigateToProject?: (projectId: string) => void;
  onNavigateToTasks?: (projectId: string) => void;
  onNavigateToApprovals?: () => void;
  onNavigateToSettings?: () => void;
}

/** Returns a compact relative time string, e.g. "2m ago", "3h ago", "5d ago". */
function relativeTime(isoString: string): string {
  const diffMs = Date.now() - new Date(isoString).getTime();
  if (diffMs < 0) return "just now";
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

/** Formats a Date as a short time string, e.g. "14:03:22". */
function formatTime(d: Date): string {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function DashboardPanel({
  onNavigateToProject,
  onNavigateToTasks,
  onNavigateToApprovals,
  onNavigateToSettings,
}: DashboardPanelProps) {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [attention, setAttention] = useState<DashboardAttention | null>(null);
  const [, setAttentionError] = useState<string | null>(null);

  const [readiness, setReadiness] = useState<ReadinessSummary | null>(null);

  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);

  const refreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await dashboardApi.getSummary();
      setSummary(data);
      setLastRefreshed(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAttention = useCallback(async () => {
    setAttentionError(null);
    try {
      const data = await dashboardApi.getAttention();
      setAttention(data);
      setLastRefreshed(new Date());
    } catch (err) {
      setAttentionError(
        err instanceof Error ? err.message : "Failed to load attention data"
      );
    }
  }, []);

  // Silent background refresh — no loading spinner
  const silentRefresh = useCallback(async () => {
    try {
      const data = await dashboardApi.getSummary();
      setSummary(data);
    } catch { /* silent */ }
    try {
      const attn = await dashboardApi.getAttention();
      setAttention(attn);
    } catch { /* silent */ }
    setLastRefreshed(new Date());
  }, []);

  // Readiness load (non-blocking)
  const loadReadiness = useCallback(async () => {
    try {
      const data = await settingsApi.getReadiness();
      setReadiness(data);
    } catch { /* silent */ }
  }, []);

  // Initial load
  useEffect(() => {
    load();
    loadAttention();
    loadReadiness();
  }, [load, loadAttention, loadReadiness]);

  // Auto-refresh interval
  useEffect(() => {
    refreshRef.current = setInterval(silentRefresh, REFRESH_INTERVAL);
    return () => {
      if (refreshRef.current) {
        clearInterval(refreshRef.current);
        refreshRef.current = null;
      }
    };
  }, [silentRefresh]);

  return (
    <div className="dashboard-panel">
      {error && (
        <div className="panel-error">
          <span>&#9888; {error}</span>
          <button className="btn btn-secondary btn-sm" onClick={load}>
            {t("dashboard.retry")}
          </button>
        </div>
      )}

      {loading && !summary && (
        <div className="panel-loading">{t("dashboard.loading")}</div>
      )}

      {summary && (
        <>
        <div className="dashboard-refresh-row">
          {readiness && (
            <span className={`dashboard-mode-badge ${readiness.overall_ready ? "real" : "mock"}`}>
              {readiness.overall_ready ? t("dashboard.modeReal") : t("dashboard.modeMock")}
            </span>
          )}
          <div className="dashboard-refresh-right">
            {lastRefreshed && (
              <span className="dashboard-last-refreshed" title={t("dashboard.autoRefresh")}>
                {t("dashboard.lastRefreshed", { time: formatTime(lastRefreshed) })}
              </span>
            )}
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => { load(); loadAttention(); }}
              title={t("dashboard.refresh")}
            >
              &#8635;
            </button>
          </div>
        </div>
        <div className="dashboard-cards">
          {/* Projects */}
          <div
            className={`dashboard-card${onNavigateToProject ? " dashboard-card-clickable" : ""}`}
            onClick={() => {
              if (onNavigateToProject && summary.project_health.length > 0) {
                onNavigateToProject(summary.project_health[0].project_id);
              }
            }}
            title={onNavigateToProject ? t("dashboard.clickToView") : undefined}
          >
            <div className="dashboard-card-value">{summary.project_count}</div>
            <div className="dashboard-card-label">{t("dashboard.projects")}</div>
          </div>

          {/* Total Tasks */}
          <div className="dashboard-card">
            <div className="dashboard-card-value">{summary.total_tasks}</div>
            <div className="dashboard-card-label">{t("dashboard.totalTasks")}</div>
          </div>

          {/* Pending Approvals */}
          <div
            className={`dashboard-card dashboard-card-warn${onNavigateToApprovals ? " dashboard-card-clickable" : ""}`}
            onClick={() => onNavigateToApprovals?.()}
            title={onNavigateToApprovals ? t("dashboard.clickToView") : undefined}
          >
            <div className="dashboard-card-value">{summary.pending_approvals}</div>
            <div className="dashboard-card-label">
              {t("dashboard.pendingApprovals")}
            </div>
          </div>

          {/* Active Orchestrations */}
          <div
            className={`dashboard-card${summary.active_orchestrations > 0 ? " dashboard-card-active" : ""}`}
          >
            <div className="dashboard-card-value">
              {summary.active_orchestrations}
            </div>
            <div className="dashboard-card-label">
              {t("dashboard.activeOrchestrations")}
            </div>
          </div>

          {/* Failed Tasks */}
          <div
            className={`dashboard-card${summary.failed_tasks > 0 ? " dashboard-card-danger" : ""}${onNavigateToTasks && summary.project_health.length > 0 ? " dashboard-card-clickable" : ""}`}
            onClick={() => {
              if (onNavigateToTasks && summary.project_health.length > 0) {
                onNavigateToTasks(summary.project_health[0].project_id);
              }
            }}
            title={onNavigateToTasks && summary.project_health.length > 0 ? t("dashboard.clickToView") : undefined}
          >
            <div className="dashboard-card-value">{summary.failed_tasks}</div>
            <div className="dashboard-card-label">
              {t("dashboard.failedTasks")}
            </div>
          </div>
        </div>
        </>
      )}

      {/* ── Row 2: Task Breakdown (left) + Setup Status (right) ── */}
      <div className="dashboard-grid-row dashboard-grid-row-stretch">
        {/* Task status breakdown */}
        {summary && Object.keys(summary.task_by_status).length > 0 && (
          <div className="dashboard-section-card dashboard-grid-2">
            <div className="dashboard-section-title">
              {t("dashboard.taskBreakdown")}
            </div>
            <div className="dashboard-status-grid">
              {Object.entries(summary.task_by_status).map(([status, count]) => (
                <div key={status} className="dashboard-status-item">
                  <span className="dashboard-status-dot" data-status={status} />
                  <span className="dashboard-status-label">
                    {t(`dashboard.status_${status}`, status)}
                  </span>
                  <span className="dashboard-status-count">{count}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Setup Status */}
        {readiness && (
          <div className={`dashboard-section-card dashboard-grid-1${readiness.overall_ready ? " dashboard-readiness-ready" : ""}`}>
            <div className="dashboard-section-title">
              {t("dashboard.setupStatus")}
            </div>
            <div className="dashboard-readiness-body">
              <span className={`dashboard-readiness-badge ${readiness.overall_ready ? "ready" : "not-ready"}`}>
                {readiness.overall_ready ? t("dashboard.systemReady") : t("dashboard.setupNeeded")}
              </span>
              <span className="dashboard-readiness-detail">
                {t("dashboard.providersReady", {
                  count: readiness.providers.configured,
                  total: readiness.providers.total,
                })}
                {" · "}
                {t("dashboard.rolesReady", {
                  count: readiness.roles.real_model_count,
                  total: readiness.roles.total,
                })}
              </span>
            </div>
            {!readiness.overall_ready && readiness.missing_steps.length > 0 && (
              <ul className="dashboard-readiness-steps">
                {readiness.missing_steps.map((step) => (
                  <li key={step}>{t(`dashboard.step_${step}`, step)}</li>
                ))}
              </ul>
            )}
            {!readiness.overall_ready && onNavigateToSettings && (
              <button className="btn btn-primary btn-sm" onClick={onNavigateToSettings} style={{ marginTop: 8 }}>
                {t("dashboard.goToSettings")}
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Row 3: Project Health (left, scroll) + Resource + Team (right) ── */}
      {summary && (
        <div className="dashboard-grid-row dashboard-grid-row-stretch">
          {/* Project health — fixed height with scroll */}
          <div className="dashboard-section-card dashboard-grid-2 dashboard-project-card">
            <div className="dashboard-section-title">
              {t("dashboard.projectHealth")}
            </div>
            {summary.project_health.length === 0 ? (
              <div className="dashboard-empty-inline">{t("dashboard.noProjects")}</div>
            ) : (
              <div className="dashboard-project-list dashboard-project-scroll">
                {summary.project_health.map((p: ProjectHealth) => (
                  <div
                    key={p.project_id}
                    className={`dashboard-project-row${onNavigateToTasks ? " dashboard-project-row-clickable" : ""}`}
                    onClick={() => onNavigateToTasks?.(p.project_id)}
                    title={onNavigateToTasks ? t("dashboard.clickToView") : undefined}
                  >
                    <div className="dashboard-project-row-left">
                      <span className="dashboard-project-avatar">
                        {p.project_name.charAt(0).toUpperCase()}
                      </span>
                      <div className="dashboard-project-info">
                        <span className="dashboard-project-name">{p.project_name}</span>
                        <span className="dashboard-project-meta-text">
                          {t("dashboard.tasks")}: {p.total_tasks}
                        </span>
                      </div>
                    </div>
                    <div className="dashboard-project-row-right">
                      {p.active_tasks > 0 && (
                        <span className="badge badge-active">{p.active_tasks} {t("dashboard.activeTasks")}</span>
                      )}
                      {p.done_tasks > 0 && (
                        <span className="badge badge-done">{p.done_tasks} {t("dashboard.doneTasks")}</span>
                      )}
                      {p.pending_approvals > 0 && (
                        <span className="badge badge-warn">{p.pending_approvals}</span>
                      )}
                      <span className="dashboard-project-date">
                        {p.last_activity
                          ? new Date(p.last_activity).toLocaleDateString()
                          : t("dashboard.never")}
                      </span>
                      <span className="dashboard-project-chevron">&#8250;</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Right stack: Token Summary (top) + Team Availability (bottom) */}
          <div className="dashboard-grid-1 dashboard-right-stack">
            {/* Token Summary */}
            <div className="dashboard-section-card">
              <div className="dashboard-section-title">{t("dashboard.tokenSummary")}</div>
              {summary.token_summary.log_entries === 0 ? (
                <div className="dashboard-empty-inline">{t("dashboard.noTokenData")}</div>
              ) : (
                <div className="dashboard-token-grid">
                  <div className="token-stat">
                    <span className="token-stat-label">{t("dashboard.totalTokens")}</span>
                    <span className="token-stat-value">{summary.token_summary.total_tokens.toLocaleString()}</span>
                  </div>
                  <div className="token-stat">
                    <span className="token-stat-label">{t("dashboard.promptTokens")}</span>
                    <span className="token-stat-value token-prompt">{summary.token_summary.total_prompt_tokens.toLocaleString()}</span>
                  </div>
                  <div className="token-stat">
                    <span className="token-stat-label">{t("dashboard.completionTokens")}</span>
                    <span className="token-stat-value token-completion">{summary.token_summary.total_completion_tokens.toLocaleString()}</span>
                  </div>
                  <div className="token-stat">
                    <span className="token-stat-label">{t("dashboard.tokenEntries")}</span>
                    <span className="token-stat-value">{summary.token_summary.log_entries.toLocaleString()}</span>
                  </div>
                </div>
              )}
            </div>

            {/* Team Availability — vertical list */}
            <div className="dashboard-section-card">
              <div className="dashboard-section-title">{t("dashboard.teamAvailability")}</div>
              <div className="dashboard-team-list">
                {(() => {
                  const ROLE_ICONS: Record<string, string> = {
                    planner: "🎯",
                    builder: "🔨",
                    qa: "✅",
                    reviewer: "📋",
                  };
                  return (["planner", "builder", "qa", "reviewer"] as const).map((role) => {
                    const detail = readiness?.roles.details.find((d) => d.role === role);
                    const isActive = detail?.is_real ?? false;
                    return (
                      <div key={role} className="dashboard-team-list-row">
                        <span className={`dashboard-team-role-dot dashboard-team-role-dot--${role}`} />
                        <span className="dashboard-team-role-icon">{ROLE_ICONS[role]}</span>
                        <span className="dashboard-team-role-name">{t(ROLE_I18N[role])}</span>
                        <span className={`dashboard-team-role-badge dashboard-team-role-badge--${isActive ? "active" : "idle"}`}>
                          {isActive ? t("dashboard.roleActive") : t("dashboard.roleIdle")}
                        </span>
                      </div>
                    );
                  });
                })()}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Row 4: Recent Activity (left, scroll) + Resource Allocation (right) ── */}
      <div className="dashboard-grid-row dashboard-grid-row-stretch">
        {/* Recent Activity — scrollable card */}
        <div className="dashboard-section-card dashboard-grid-2 dashboard-activity-card">
          <div className="dashboard-section-title">{t("dashboard.recentActivity")}</div>
          {attention && (() => {
            const { recent_tasks, recent_runs } = attention.recent_activity;

            type FeedItem =
              | { kind: "task"; ts: number; data: RecentTask }
              | { kind: "run"; ts: number; data: RecentRun };

            const feed: FeedItem[] = [
              ...recent_tasks.map(
                (r: RecentTask): FeedItem => ({ kind: "task", ts: new Date(r.updated_at).getTime(), data: r })
              ),
              ...recent_runs.map(
                (r: RecentRun): FeedItem => ({ kind: "run", ts: new Date(r.created_at).getTime(), data: r })
              ),
            ]
              .sort((a, b) => b.ts - a.ts)
              .slice(0, 15);

            if (feed.length === 0) {
              return <div className="dashboard-activity-empty">{t("dashboard.noRecentActivity")}</div>;
            }

            return (
              <div className="dashboard-activity-list dashboard-activity-scroll">
                {feed.map((item, idx) => {
                  if (item.kind === "task") {
                    const task = item.data as RecentTask;
                    const isNew = Math.abs(new Date(task.created_at).getTime() - new Date(task.updated_at).getTime()) < 5000;
                    const descKey = isNew ? "dashboard.activityTaskCreated" : "dashboard.activityTaskUpdated";
                    return (
                      <div key={`feed-task-${task.id}-${idx}`}
                        className={`dashboard-activity-item${onNavigateToTasks ? " dashboard-activity-item-clickable" : ""}`}
                        onClick={() => onNavigateToTasks?.(task.project_id)}
                        title={onNavigateToTasks ? t("dashboard.clickToView") : undefined}
                      >
                        <span className="activity-item-time">{relativeTime(task.updated_at)}</span>
                        <span className="activity-item-desc">
                          {t(descKey)} &mdash; <span className="activity-item-title">{task.title}</span>{" "}
                          <span className="activity-item-project">{t("dashboard.inProject", { project: task.project_name })}</span>
                        </span>
                      </div>
                    );
                  } else {
                    const run = item.data as RecentRun;
                    let descKey = "dashboard.activityRunStarted";
                    if (run.status === "completed") descKey = "dashboard.activityRunCompleted";
                    else if (run.status === "failed") descKey = "dashboard.activityRunFailed";
                    return (
                      <div key={`feed-run-${run.id}-${idx}`} className="dashboard-activity-item">
                        <span className="activity-item-time">{relativeTime(run.created_at)}</span>
                        <span className="activity-item-desc">
                          {t(descKey, { role: ROLE_I18N[run.role] ? t(ROLE_I18N[run.role]) : run.role })} &mdash;{" "}
                          <span className="activity-item-title">{run.task_title}</span>
                        </span>
                      </div>
                    );
                  }
                })}
              </div>
            );
          })()}
        </div>

        {/* Resource Allocation */}
        {summary && (
          <div className="dashboard-section-card dashboard-grid-1">
            <div className="dashboard-section-title">{t("dashboard.resourceAllocation")}</div>
            <div className="dashboard-resource-list">
              <div className="dashboard-resource-item">
                <span className="dashboard-resource-label">{t("dashboard.apiCalls")}</span>
                <span className="dashboard-resource-value" style={{ color: "var(--accent-green)" }}>
                  {summary.token_summary.log_entries > 0
                    ? Math.min(Math.round((summary.token_summary.log_entries / 100) * 100), 100)
                    : 0}%
                </span>
              </div>
              <div className="dashboard-resource-bar">
                <div className="dashboard-resource-fill dashboard-resource-fill--green"
                  style={{ width: `${Math.min(summary.token_summary.log_entries, 100)}%` }} />
              </div>
              <div className="dashboard-resource-item">
                <span className="dashboard-resource-label">{t("dashboard.taskCompletion")}</span>
                <span className="dashboard-resource-value" style={{ color: "var(--accent-blue)" }}>
                  {summary.total_tasks > 0
                    ? Math.round(((summary.total_tasks - summary.failed_tasks) / summary.total_tasks) * 100)
                    : 0}%
                </span>
              </div>
              <div className="dashboard-resource-bar">
                <div className="dashboard-resource-fill dashboard-resource-fill--blue"
                  style={{ width: `${summary.total_tasks > 0 ? Math.round(((summary.total_tasks - summary.failed_tasks) / summary.total_tasks) * 100) : 0}%` }} />
              </div>
              <div className="dashboard-resource-item">
                <span className="dashboard-resource-label">{t("dashboard.failureRate")}</span>
                <span className="dashboard-resource-value" style={{ color: summary.failed_tasks > 0 ? "var(--accent-red)" : "var(--accent-green)" }}>
                  {summary.total_tasks > 0
                    ? Math.round((summary.failed_tasks / summary.total_tasks) * 100)
                    : 0}%
                </span>
              </div>
              <div className="dashboard-resource-bar">
                <div className="dashboard-resource-fill dashboard-resource-fill--red"
                  style={{ width: `${summary.total_tasks > 0 ? Math.round((summary.failed_tasks / summary.total_tasks) * 100) : 0}%` }} />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Empty state */}
      {summary && summary.total_tasks === 0 && summary.project_count === 0 && (
        <div className="dashboard-empty-guidance">
          <div className="dashboard-empty-title">{t("dashboard.emptyTitle")}</div>
          <div className="dashboard-empty-text">{t("dashboard.empty")}</div>
          <div className="dashboard-empty-steps">
            <div className="dashboard-empty-step">
              <span className="dashboard-empty-step-num">1</span>
              <span>{t("dashboard.emptyStep1")}</span>
            </div>
            <div className="dashboard-empty-step">
              <span className="dashboard-empty-step-num">2</span>
              <span>{t("dashboard.emptyStep2")}</span>
            </div>
            <div className="dashboard-empty-step">
              <span className="dashboard-empty-step-num">3</span>
              <span>{t("dashboard.emptyStep3")}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
