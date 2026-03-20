/**
 * Reusable pipeline error display with guidance — Phase 20-3.
 *
 * Shows the raw error message + a classified guidance hint.
 * Used across all pipeline steps (snapshot, request, dry-run,
 * execute, rollback) for consistent error UX.
 */

import { useTranslation } from "react-i18next";

type PipelineErrorCategory =
  | "provider"
  | "auth"
  | "rate_limit"
  | "network"
  | "policy_denied"
  | "not_found"
  | "generic";

interface PipelineErrorHintProps {
  error: string;
  /** Pipeline step context for better guidance */
  step?: "snapshot" | "request" | "dryrun" | "execute" | "rollback" | "approval" | "general";
  onRetry?: () => void;
  onGoToSettings?: () => void;
}

function classifyPipelineError(error: string): PipelineErrorCategory {
  const lower = error.toLowerCase();

  if (lower.includes("api key") || lower.includes("api_key") || lower.includes("not set") || lower.includes("not configured") || lower.includes("mock")) {
    return "provider";
  }
  if (lower.includes("auth") || lower.includes("401") || lower.includes("403") || lower.includes("permission") || lower.includes("invalid key") || lower.includes("unauthorized")) {
    return "auth";
  }
  if (lower.includes("rate limit") || lower.includes("429") || lower.includes("too many")) {
    return "rate_limit";
  }
  if (lower.includes("connection") || lower.includes("timeout") || lower.includes("network") || lower.includes("econnrefused") || lower.includes("unreachable")) {
    return "network";
  }
  if (lower.includes("denied") || lower.includes("blocked") || lower.includes("not allowed") || lower.includes("policy")) {
    return "policy_denied";
  }
  if (lower.includes("404") || lower.includes("not found")) {
    return "not_found";
  }

  return "generic";
}

export function PipelineErrorHint({
  error,
  step = "general",
  onRetry,
  onGoToSettings,
}: PipelineErrorHintProps) {
  const { t } = useTranslation();
  const category = classifyPipelineError(error);

  // Only show guidance for classifiable errors
  const showGuidance = category !== "generic" || step !== "general";

  return (
    <div className="pipeline-error-hint">
      <div className="pipeline-error-hint-raw">{error}</div>
      {showGuidance && (
        <div className="pipeline-error-hint-guidance">
          <span className="pipeline-error-hint-icon">💡</span>
          <span className="pipeline-error-hint-text">
            {t(`pipelineError.${category}`, { step: t(`pipelineError.step_${step}`) })}
          </span>
          {(category === "provider" || category === "auth") && onGoToSettings && (
            <button className="btn btn-sm pipeline-error-hint-btn" onClick={onGoToSettings} type="button">
              {t("pipelineError.goToSettings")}
            </button>
          )}
          {(category === "rate_limit" || category === "network") && onRetry && (
            <button className="btn btn-sm pipeline-error-hint-btn" onClick={onRetry} type="button">
              {t("pipelineError.retryAction")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
