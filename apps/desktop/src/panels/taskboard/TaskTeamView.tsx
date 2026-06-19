import { useState, useEffect } from "react"
import { useTranslation } from "react-i18next"
import { api } from "../../api/client"
import { tasksApi } from "../../api/tasks"

interface AgentRun {
  id: string
  role: string
  status: string
  model_provider: string | null
  model_name: string | null
  output_summary: string
  started_at: string | null
  ended_at: string | null
  created_at: string
}

interface TokenRecord {
  role: string
  provider: string
  model: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

interface RoleCardData {
  role: string
  status: "idle" | "running" | "completed" | "failed" | "waiting"
  currentAction: string
  nextStep: string
  lastOutput: string
  lastUpdated: string | null
  modelInfo: string
  tokensUsed: number
  elapsed: string
}

interface TaskTeamViewProps {
  taskId: string
  projectId: string
  taskStatus: string
  onBack: () => void
}

function formatElapsed(startedAt: string | null, endedAt: string | null): string {
  if (!startedAt) return "—"
  const start = new Date(startedAt).getTime()
  const end = endedAt ? new Date(endedAt).getTime() : Date.now()
  const sec = Math.round((end - start) / 1000)
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

function parseOutput(raw: string): Record<string, unknown> | null {
  try {
    const p = JSON.parse(raw)
    return typeof p === "object" && p !== null ? p as Record<string, unknown> : null
  } catch { return null }
}

function deriveCardData(role: string, runs: AgentRun[], tokens: TokenRecord[]): RoleCardData {
  const roleRuns = runs.filter(r => r.role === role && (r.status === "completed" || r.status === "failed" || r.status === "running"))
  const latest = roleRuns[roleRuns.length - 1]
  const tokenRec = tokens.find(t => t.role === role)

  if (!latest) {
    return {
      role, status: "idle", currentAction: "—", nextStep: "Waiting for assignment",
      lastOutput: "—", lastUpdated: null, modelInfo: "—", tokensUsed: 0, elapsed: "—",
    }
  }

  const status = latest.status === "running" ? "running" : latest.status === "completed" ? "completed" : "failed"
  const elapsed = formatElapsed(latest.started_at, latest.ended_at)
  const modelInfo = latest.model_provider && latest.model_name
    ? `${latest.model_provider}/${latest.model_name}` : latest.model_provider || "mock"

  let currentAction = "—"
  let nextStep = "—"
  let lastOutput = "—"

  if (status === "running") {
    currentAction = `Processing task...`
    nextStep = "Will produce output when done"
  } else if (status === "completed") {
    currentAction = "Done"
    nextStep = "Idle — waiting for next task"
    // Extract last output
    if (latest.output_summary) {
      const parsed = parseOutput(latest.output_summary)
      if (parsed) {
        if (role === "planner") lastOutput = String(parsed.goal_summary || parsed.goal || "Plan generated")
        else if (role === "builder") lastOutput = String(parsed.change_summary || "Proposal generated")
        else if (role === "qa") lastOutput = String(parsed.result || "QA done")
        else if (role === "reviewer") lastOutput = `${String(parsed.decision || "Reviewed")}${parsed.reason ? ": " + String(parsed.reason).slice(0, 100) : ""}`
      } else {
        lastOutput = latest.output_summary.slice(0, 100)
      }
    }
  } else {
    currentAction = "Failed"
    nextStep = "Needs investigation"
    if (latest.output_summary) {
      const parsed = parseOutput(latest.output_summary)
      lastOutput = parsed?.error ? String(parsed.error).slice(0, 100) : latest.output_summary.slice(0, 100)
    }
  }

  return {
    role, status, currentAction, nextStep, lastOutput,
    lastUpdated: latest.ended_at || latest.started_at || latest.created_at,
    modelInfo, tokensUsed: tokenRec?.total_tokens || 0, elapsed,
  }
}

export function TaskTeamView({ taskId, projectId: _projectId, taskStatus: _taskStatus, onBack }: TaskTeamViewProps) {
  const { t } = useTranslation()
  const [cards, setCards] = useState<RoleCardData[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    Promise.all([
      tasksApi.getRuns(taskId),
      api.getOrNull<{ records: TokenRecord[] }>(`/api/tasks/${taskId}/token-usage`),
    ]).then(([runs, tokenResp]) => {
      if (cancelled) return
      const tokens = tokenResp?.records || []
      const roles = ["planner", "builder", "qa", "security_reviewer", "reviewer"]
      setCards(roles.map(r => deriveCardData(r, runs as AgentRun[], tokens)))
      setLoading(false)
    }).catch(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [taskId])

  const STATUS_BADGE: Record<string, { label: string; color: string }> = {
    idle: { label: t("teamView.idle", "Idle"), color: "rgba(255,255,255,0.3)" },
    running: { label: t("teamView.running", "Running"), color: "#22c55e" },
    completed: { label: t("teamView.completed", "Done"), color: "#22c55e" },
    failed: { label: t("teamView.failed", "Failed"), color: "#ef4444" },
    waiting: { label: t("teamView.waiting", "Waiting"), color: "#f59e0b" },
  }

  return (
    <div className="task-team-view">
      <div className="task-team-view__header">
        <button className="task-team-view__back" onClick={onBack}>
          ← {t("teamView.backToTask", "Back to task")}
        </button>
        <h3 className="task-team-view__title">
          {t("teamView.title", "Team Overview")}
        </h3>
      </div>

      {loading ? (
        <div className="task-team-view__loading">{t("teamView.loading", "Loading team data...")}</div>
      ) : (
        <div className="task-team-view__grid">
          {cards.map((card) => {
            const badge = STATUS_BADGE[card.status] || STATUS_BADGE.idle
            return (
              <div key={card.role} className={`role-work-card role-work-card--${card.status}`}>
                <div className="role-work-card__header">
                  <span className="role-work-card__name">
                    {t(`pipeline.role_${card.role}`, card.role)}
                  </span>
                  <span className="role-work-card__badge" style={{ color: badge.color }}>
                    {card.status === "running" ? "◐ " : ""}{badge.label}
                  </span>
                </div>

                <div className="role-work-card__rows">
                  <div className="role-work-card__row">
                    <span className="role-work-card__label">{t("teamView.currentAction", "Status")}</span>
                    <span className="role-work-card__value">{card.currentAction}</span>
                  </div>
                  <div className="role-work-card__row">
                    <span className="role-work-card__label">{t("teamView.nextStep", "Next")}</span>
                    <span className="role-work-card__value">{card.nextStep}</span>
                  </div>
                  <div className="role-work-card__row">
                    <span className="role-work-card__label">{t("teamView.lastOutput", "Output")}</span>
                    <span className="role-work-card__value role-work-card__value--output">{card.lastOutput}</span>
                  </div>
                  <div className="role-work-card__row">
                    <span className="role-work-card__label">{t("teamView.model", "Model")}</span>
                    <span className="role-work-card__value role-work-card__value--muted">{card.modelInfo}</span>
                  </div>
                </div>

                <div className="role-work-card__footer">
                  <span>{card.elapsed !== "—" ? `⏱ ${card.elapsed}` : ""}</span>
                  <span>{card.tokensUsed > 0 ? `${card.tokensUsed.toLocaleString()} tokens` : ""}</span>
                  {card.lastUpdated && (
                    <span>{new Date(card.lastUpdated).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
