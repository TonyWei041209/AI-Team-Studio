import React, { useState, useEffect, useRef } from "react"
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

interface AuditEvent {
  event_type: string
  object_type: string
  status: string
  timestamp: string
  summary: string
  detail: Record<string, unknown>
}

interface ConversationMessage {
  id: string
  timestamp: string
  role: "system" | "user" | "planner" | "builder" | "qa" | "reviewer"
  type: "event" | "output" | "decision"
  content: string
  detail?: string
  status?: string
  duration?: string
}

interface TeamConversationProps {
  taskId: string
  taskTitle: string
  taskStatus: string
  visible: boolean
  isOrchestrating: boolean
}

const ROLE_ICONS: Record<string, string> = {
  system: "⚙",
  user: "👤",
  planner: "📋",
  builder: "🔨",
  qa: "🔍",
  reviewer: "✅",
}

const ROLE_COLORS: Record<string, string> = {
  system: "rgba(255,255,255,0.5)",
  user: "#22c55e",
  planner: "#60a5fa",
  builder: "#f59e0b",
  qa: "#a78bfa",
  reviewer: "#34d399",
}

function parseOutputSummary(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw)
    if (typeof parsed === "object" && parsed !== null) return parsed as Record<string, unknown>
  } catch { /* ignore */ }
  // Try nested JSON (output_summary wraps { output: {...} })
  try {
    const outer = JSON.parse(raw)
    if (outer?.output && typeof outer.output === "object") return outer.output as Record<string, unknown>
  } catch { /* ignore */ }
  return null
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return ""
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (ms < 1000) return "<1s"
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

/** Extract rich content lines from a role's output */
function extractRoleReport(output: string, role: string): { headline: string; details: string[] } {
  const parsed = parseOutputSummary(output)
  if (!parsed) return { headline: output.slice(0, 200), details: [] }

  if (role === "planner") {
    const goal = String(parsed.goal_summary || parsed.goal || "")
    const breakdown = parsed.task_breakdown as unknown[]
    const criteria = parsed.acceptance_criteria as unknown[]
    const risks = parsed.risks as unknown[]
    const headline = goal || "Task plan ready"
    const details: string[] = []
    if (Array.isArray(breakdown)) details.push(`${breakdown.length} steps planned`)
    if (Array.isArray(criteria)) details.push(`${criteria.length} acceptance criteria`)
    if (Array.isArray(risks) && risks.length > 0) details.push(`${risks.length} risk(s) identified`)
    return { headline, details }
  }

  if (role === "builder") {
    const summary = String(parsed.change_summary || "")
    const files = parsed.proposed_files || parsed.changed_files
    const cmds = parsed.proposed_commands
    const headline = summary || "Execution proposal ready"
    const details: string[] = []
    if (Array.isArray(files)) {
      const createCount = files.filter((f: Record<string, unknown>) => f.action === "create").length
      const modifyCount = files.filter((f: Record<string, unknown>) => f.action === "modify").length
      const parts: string[] = []
      if (createCount) parts.push(`${createCount} file(s) to create`)
      if (modifyCount) parts.push(`${modifyCount} file(s) to modify`)
      if (parts.length) details.push(parts.join(", "))
      else details.push(`${files.length} file(s) proposed`)
    }
    if (Array.isArray(cmds)) details.push(`${cmds.length} command(s) planned`)
    return { headline, details }
  }

  if (role === "qa") {
    const result = String(parsed.result || "")
    const scope = String(parsed.validation_scope || "")
    const findings = parsed.findings as unknown[]
    const headline = result ? `Quality check: ${result}` : "Quality check complete"
    const details: string[] = []
    if (scope) details.push(scope.slice(0, 120))
    if (Array.isArray(findings) && findings.length > 0) details.push(`${findings.length} finding(s)`)
    return { headline, details }
  }

  if (role === "reviewer") {
    const decision = String(parsed.decision || "")
    const reason = String(parsed.reason || "")
    const headline = decision
      ? `Decision: ${decision}`
      : "Review complete"
    const details: string[] = []
    if (reason) details.push(reason.slice(0, 200))
    return { headline, details }
  }

  return { headline: JSON.stringify(parsed).slice(0, 150), details: [] }
}

export function TeamConversation({ taskId, taskTitle, taskStatus: _taskStatus, visible, isOrchestrating }: TeamConversationProps) {
  const { t } = useTranslation()
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const buildMessages = React.useCallback(async () => {
    const msgs: ConversationMessage[] = []

    // 1. User message (task creation)
    msgs.push({
      id: "user-0",
      timestamp: "",
      role: "user",
      type: "event",
      content: taskTitle,
    })

    try {
      // 2. Fetch runs → one rich message per completed role
      const runs = await tasksApi.getRuns(taskId) as AgentRun[]

      for (const run of runs) {
        const dur = formatDuration(run.started_at, run.ended_at)

        if (run.status === "completed" || run.status === "failed") {
          if (run.output_summary) {
            const report = extractRoleReport(run.output_summary, run.role)
            msgs.push({
              id: `run-output-${run.id}`,
              timestamp: run.ended_at || run.created_at,
              role: run.role as ConversationMessage["role"],
              type: run.role === "reviewer" ? "decision" : "output",
              content: report.headline,
              detail: report.details.length > 0 ? report.details.join(" · ") : undefined,
              status: run.status,
              duration: dur,
            })
          } else {
            // No output — show completion/failure status
            msgs.push({
              id: `run-status-${run.id}`,
              timestamp: run.ended_at || run.created_at,
              role: run.role as ConversationMessage["role"],
              type: "event",
              content: run.status === "completed"
                ? t("conversation.roleCompleted", { role: run.role, defaultValue: `${run.role} finished` })
                : t("conversation.roleFailed", { role: run.role, defaultValue: `${run.role} encountered an error` }),
              status: run.status,
              duration: dur,
            })
          }
        } else if (run.status === "running") {
          msgs.push({
            id: `run-active-${run.id}`,
            timestamp: run.started_at || run.created_at,
            role: run.role as ConversationMessage["role"],
            type: "event",
            content: t("conversation.roleWorking", { role: run.role, defaultValue: `${run.role} is working...` }),
          })
        }
      }

      // 3. Audit trail → system events
      const trail = await api.getOrNull<{ events: AuditEvent[] }>(`/api/tasks/${taskId}/audit-trail`)
      if (trail?.events) {
        for (const ev of trail.events) {
          if (ev.event_type === "execution_result:completed" || ev.event_type === "execution_result:failed") {
            const mode = (ev.detail?.mode as string) || ""
            if (mode === "dry_run") {
              msgs.push({
                id: `audit-${ev.event_type}-${ev.timestamp}`,
                timestamp: ev.timestamp,
                role: "system",
                type: "event",
                content: t("conversation.dryRunDone", { defaultValue: "Dry-run simulation completed" }),
                status: ev.status,
              })
            } else if (mode === "real_run") {
              const sc = (ev.detail?.success_count as number) || 0
              const fc = (ev.detail?.fail_count as number) || 0
              msgs.push({
                id: `audit-${ev.event_type}-${ev.timestamp}`,
                timestamp: ev.timestamp,
                role: "system",
                type: "event",
                content: t("conversation.executeDone", { success: sc, fail: fc, defaultValue: `Execution done: ${sc} ok, ${fc} failed` }),
                status: ev.status,
              })
            }
          }
        }
      }
    } catch { /* network error — show what we have */ }

    msgs.sort((a, b) => {
      if (!a.timestamp) return -1
      if (!b.timestamp) return 1
      return a.timestamp.localeCompare(b.timestamp)
    })

    setMessages(msgs)
  }, [taskId, taskTitle, t])

  useEffect(() => {
    if (!visible) return
    buildMessages()

    if (isOrchestrating) {
      pollRef.current = setInterval(buildMessages, 2000)
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [visible, isOrchestrating, buildMessages])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages.length])

  if (!visible || messages.length === 0) return null

  return (
    <div className="team-conversation">
      <div className="team-conversation__header">
        {t("conversation.title", "Team Activity")}
      </div>
      <div className="team-conversation__messages">
        {messages.map((msg) => (
          <div key={msg.id} className={`team-msg team-msg--${msg.role} team-msg--${msg.type}`}>
            <span className="team-msg__avatar" style={{ color: ROLE_COLORS[msg.role] || "#fff" }}>
              {ROLE_ICONS[msg.role] || "●"}
            </span>
            <div className="team-msg__body">
              <div className="team-msg__meta">
                <span className="team-msg__role" style={{ color: ROLE_COLORS[msg.role] }}>
                  {msg.role === "user" ? t("conversation.you", "You") :
                   msg.role === "system" ? t("conversation.system", "System") :
                   t(`pipeline.role_${msg.role}`, msg.role.charAt(0).toUpperCase() + msg.role.slice(1))}
                </span>
                {msg.timestamp && (
                  <span className="team-msg__time">
                    {new Date(msg.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </span>
                )}
                {msg.duration && (
                  <span className="team-msg__duration">{msg.duration}</span>
                )}
              </div>
              <div className="team-msg__content">{msg.content}</div>
              {msg.detail && (
                <div className="team-msg__detail">{msg.detail}</div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
