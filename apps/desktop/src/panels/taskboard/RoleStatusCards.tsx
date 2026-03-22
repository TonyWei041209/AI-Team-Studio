import React from "react"
import { useTranslation } from "react-i18next"

interface RoleStatus {
  role: string
  status: "idle" | "running" | "completed" | "failed"
  duration?: string
}

interface RoleStatusCardsProps {
  roles: RoleStatus[]
  visible: boolean
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

  const statusIcon = (s: string) => {
    switch (s) {
      case "running": return "◐"
      case "completed": return "✓"
      case "failed": return "✗"
      default: return "○"
    }
  }

  const statusLabel = (s: string) => {
    switch (s) {
      case "running": return t("tasks.roleRunning", "Running...")
      case "completed": return t("tasks.roleCompleted", "Done")
      case "failed": return t("tasks.roleFailed", "Failed")
      default: return t("tasks.roleIdle", "Idle")
    }
  }

  return (
    <div className="role-status-cards">
      {displayRoles.map(r => (
        <div key={r.role} className={`role-status-card role-status-card--${r.status}`}>
          <span className="role-status-card__icon">{statusIcon(r.status)}</span>
          <span className="role-status-card__name">
            {t(`enum.role_${r.role}`, r.role.charAt(0).toUpperCase() + r.role.slice(1))}
          </span>
          <span className="role-status-card__label">{statusLabel(r.status)}</span>
        </div>
      ))}
    </div>
  )
}
