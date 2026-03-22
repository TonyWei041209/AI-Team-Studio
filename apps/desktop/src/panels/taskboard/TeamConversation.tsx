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
  reviewer: "✓",
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
  return null
}

function extractSummaryText(output: string, role: string): string {
  const parsed = parseOutputSummary(output)
  if (!parsed) return output.slice(0, 200)

  if (role === "planner") {
    const goal = parsed.goal_summary || parsed.goal || ""
    const breakdown = parsed.task_breakdown
    if (goal && Array.isArray(breakdown)) {
      return `${String(goal)} (${breakdown.length} steps)`
    }
    return String(goal || JSON.stringify(parsed).slice(0, 150))
  }
  if (role === "builder") {
    const summary = parsed.change_summary || ""
    const files = parsed.proposed_files
    const cmds = parsed.proposed_commands
    const parts = [String(summary)]
    if (Array.isArray(files)) parts.push(`${files.length} file(s)`)
    if (Array.isArray(cmds)) parts.push(`${cmds.length} command(s)`)
    return parts.filter(Boolean).join(" · ")
  }
  if (role === "qa") {
    const result = parsed.result || ""
    const scope = parsed.validation_scope || ""
    return `${String(result)}${scope ? " — " + String(scope) : ""}`
  }
  if (role === "reviewer") {
    const decision = parsed.decision || ""
    const reason = parsed.reason || ""
    return `${String(decision)}${reason ? ": " + String(reason).slice(0, 150) : ""}`
  }
  return JSON.stringify(parsed).slice(0, 150)
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
      // 2. Fetch runs
      const runs = await tasksApi.getRuns(taskId) as AgentRun[]

      for (const run of runs) {
        if (run.status === "completed" || run.status === "failed") {
          // Role started
          if (run.started_at) {
            msgs.push({
              id: `run-start-${run.id}`,
              timestamp: run.started_at,
              role: run.role as ConversationMessage["role"],
              type: "event",
              content: t("conversation.roleStarted", { role: run.role, defaultValue: `${run.role} started working` }),
            })
          }
          // Role output
          if (run.output_summary) {
            const summary = extractSummaryText(run.output_summary, run.role)
            msgs.push({
              id: `run-output-${run.id}`,
              timestamp: run.ended_at || run.created_at,
              role: run.role as ConversationMessage["role"],
              type: run.role === "reviewer" ? "decision" : "output",
              content: summary,
              status: run.status,
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

      // 3. Fetch audit trail for system events
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

    // Sort by timestamp (keep user-0 first)
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
                   t(`pipeline.role_${msg.role}`, msg.role)}
                </span>
                {msg.timestamp && (
                  <span className="team-msg__time">
                    {new Date(msg.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </span>
                )}
              </div>
              <div className="team-msg__content">{msg.content}</div>
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
