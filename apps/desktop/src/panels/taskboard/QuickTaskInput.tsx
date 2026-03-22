import React, { useState } from "react"
import { useTranslation } from "react-i18next"

interface QuickTaskInputProps {
  projectId: string | null
  onSubmit: (text: string) => Promise<void>
  disabled?: boolean
}

export function QuickTaskInput({ projectId, onSubmit, disabled }: QuickTaskInputProps) {
  const { t } = useTranslation()
  const [text, setText] = useState("")
  const [submitting, setSubmitting] = useState(false)

  const canSubmit = !!projectId && text.trim().length > 0 && !submitting && !disabled

  const handleSubmit = async () => {
    if (!canSubmit) return
    const value = text.trim()
    setText("")
    setSubmitting(true)
    try {
      await onSubmit(value)
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
    <div className="quick-task-input">
      <input
        type="text"
        className="quick-task-input__field"
        placeholder={
          projectId
            ? t("tasks.quickInputPlaceholder", "Describe what you want to do...")
            : t("tasks.quickInputNoProject", "Select a project first")
        }
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={!projectId || submitting || disabled}
      />
      <button
        className="quick-task-input__send"
        onClick={handleSubmit}
        disabled={!canSubmit}
        title={t("tasks.quickInputSend", "Send")}
      >
        {submitting ? "..." : "→"}
      </button>
    </div>
  )
}
