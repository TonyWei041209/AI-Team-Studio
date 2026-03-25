import React, { useState, useEffect } from "react"
import { useTranslation } from "react-i18next"
import type { Project } from "../types/api"
import { projectsApi } from "../api/projects"
import { tasksApi } from "../api/tasks"

interface WorkspaceQuickComposerProps {
  selectedProjectId: string | null
  onTaskCreated: (projectId: string, taskId: string) => void
}

export function WorkspaceQuickComposer({
  selectedProjectId,
  onTaskCreated,
}: WorkspaceQuickComposerProps) {
  const { t } = useTranslation()
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState<string>(selectedProjectId || "")
  const [text, setText] = useState("")
  const [submitting, setSubmitting] = useState(false)

  // Sync with parent's selected project
  useEffect(() => {
    if (selectedProjectId) setProjectId(selectedProjectId)
  }, [selectedProjectId])

  // Load projects for selector
  useEffect(() => {
    projectsApi.list().then(setProjects).catch(() => {})
  }, [])

  const canSubmit = !!projectId && text.trim().length > 0 && !submitting

  const handleSubmit = async () => {
    if (!canSubmit) return
    const value = text.trim()
    setText("")
    setSubmitting(true)
    try {
      const task = await tasksApi.create(projectId, {
        title: value,
        description: value,
        priority: "medium",
      })
      // TaskBoard's autoExpandTaskId effect handles orchestrate — don't call here
      onTaskCreated(projectId, task.id)
    } catch {
      // Restore text on failure
      setText(value)
    } finally {
      setSubmitting(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="workspace-composer">
      <div className="workspace-composer__label">
        {t("sidebar.quickComposerLabel", "Quick Start")}
      </div>
      {projects.length > 1 && (
        <select
          className="workspace-composer__project"
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          disabled={submitting}
        >
          <option value="">{t("sidebar.selectProject", "Project...")}</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      )}
      <div className="workspace-composer__row">
        <input
          type="text"
          className="workspace-composer__input"
          placeholder={
            projectId
              ? t("tasks.quickInputPlaceholder", "Describe what you want to do...")
              : t("tasks.quickInputNoProject", "Select a project first")
          }
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={!projectId || submitting}
        />
        <button
          className="workspace-composer__send"
          onClick={handleSubmit}
          disabled={!canSubmit}
          title={t("tasks.quickInputSend", "Send")}
        >
          {submitting ? "..." : "→"}
        </button>
      </div>
    </div>
  )
}
