import React from "react"
import { useTranslation } from "react-i18next"

export interface RoleStatus {
  role: string
  status: "idle" | "running" | "completed" | "failed"
  duration?: string
  outputSummary?: string
}

interface RoleStatusCardsProps {
  roles: RoleStatus[]
  visible: boolean
}

const ROLE_ICONS: Record<string, string> = {
  planner: "📋",
  builder: "🔨",
  qa: "🔍",
  reviewer: "✅",
}

export function RoleStatusCards({ roles, visible }: RoleStatusCardsProps) {
  const { t } = useTranslation()
  if (!visible || roles.length === 0) return null

  const defaultRoles: RoleStatus[] = [
    { role: "planner", status: "idle" },
    { role: "builder", status: "idle" },
    { role: "qa", status: "idle" },
    { role: "reviewer", status: "idle" },
  ]

  const displayRoles = defaultRoles.map(dr => {
    const found = roles.find(r => r.role === dr.role)
    return found || dr
  })

  const statusLabel = (s: string) => {
    switch (s) {
      case "running": return t("tasks.roleRunning", "Working...")
      case "completed": return t("tasks.roleCompleted", "Done")
      case "failed": return t("tasks.roleFailed", "Failed")
      default: return t("tasks.roleIdle", "Standby")
    }
  }

  return (
    <div className="role-status-cards">
      {displayRoles.map(r => (
        <div key={r.role} className={`role-status-card role-status-card--${r.status}`}>
          <div className="role-status-card__header">
            <span className="role-status-card__icon">{ROLE_ICONS[r.role] || "●"}</span>
            <span className="role-status-card__name">
              {t(`pipeline.role_${r.role}`, r.role.charAt(0).toUpperCase() + r.role.slice(1))}
            </span>
            <span className={`role-status-card__badge role-status-card__badge--${r.status}`}>
              {statusLabel(r.status)}
            </span>
          </div>
          {r.outputSummary && (
            <div className="role-status-card__output">{r.outputSummary}</div>
          )}
          {r.duration && (
            <div className="role-status-card__footer">{r.duration}</div>
          )}
        </div>
      ))}
    </div>
  )
}
