import React, { useState, useEffect } from "react"
import { useTranslation } from "react-i18next"
import { api } from "../../api/client"

interface TokenRecord {
  role: string
  provider: string
  model: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

interface TokenUsageResponse {
  task_id: string
  records: TokenRecord[]
  // Backend returns flat fields (Phase 12-1)
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  // Optional nested totals (future-compat)
  totals?: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
}

interface TokenSummaryRowProps {
  taskId: string
  visible: boolean
}

export function TokenSummaryRow({ taskId, visible }: TokenSummaryRowProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const [data, setData] = useState<TokenUsageResponse | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!visible || !taskId) return
    let cancelled = false
    setLoading(true)
    api.getOrNull<TokenUsageResponse>(`/api/tasks/${taskId}/token-usage`)
      .then(resp => {
        if (!cancelled && resp) setData(resp)
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [taskId, visible])

  if (!visible || loading || !data) return null

  // Backend returns flat fields; fall back to nested totals or manual reduce
  const totalTokens =
    data.total_tokens ??
    data.totals?.total_tokens ??
    data.records.reduce((sum, r) => sum + r.total_tokens, 0)
  const promptTokens =
    data.total_prompt_tokens ??
    data.totals?.prompt_tokens ??
    data.records.reduce((sum, r) => sum + r.prompt_tokens, 0)
  const completionTokens =
    data.total_completion_tokens ??
    data.totals?.completion_tokens ??
    data.records.reduce((sum, r) => sum + r.completion_tokens, 0)

  if (totalTokens === 0) return null

  return (
    <div className="token-summary-row">
      <button
        className="token-summary-row__toggle"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="token-summary-row__arrow">{expanded ? "▾" : "▸"}</span>
        <span className="token-summary-row__label">
          {t("tasks.tokenTotal", "Tokens")}: {totalTokens.toLocaleString()}
        </span>
        <span className="token-summary-row__detail">
          (prompt: {promptTokens.toLocaleString()} / completion: {completionTokens.toLocaleString()})
        </span>
      </button>
      {expanded && data.records.length > 0 && (
        <div className="token-summary-row__breakdown">
          {data.records.map((r, i) => (
            <div key={i} className="token-summary-row__record">
              <span className="token-summary-row__role">{r.role}</span>
              <span className="token-summary-row__model">{r.model}</span>
              <span className="token-summary-row__tokens">{r.total_tokens.toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
