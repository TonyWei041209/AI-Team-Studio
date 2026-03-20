/**
 * Orchestration error guidance — Phase 20-1 Part B.
 *
 * Classifies orchestration errors into known categories and shows
 * actionable guidance alongside the raw error message.
 */

import { useTranslation } from "react-i18next";

type ErrorCategory =
  | "provider_not_configured"
  | "missing_api_key"
  | "auth_failure"
  | "rate_limit"
  | "connection_failure"
  | "unknown";

interface OrchestrationErrorGuideProps {
  error: string;
  onGoToSettings?: () => void;
}

/** Best-effort classification of orchestration error strings. */
function classifyError(error: string): ErrorCategory {
  const lower = error.toLowerCase();

  // Provider not configured / mock mode
  if (lower.includes("mock") && (lower.includes("provider") || lower.includes("disabled"))) {
    return "provider_not_configured";
  }
  if (lower.includes("not configured") || lower.includes("no provider")) {
    return "provider_not_configured";
  }

  // Missing API key
  if (lower.includes("api key") || lower.includes("api_key") || lower.includes("not set")) {
    return "missing_api_key";
  }

  // Auth failure
  if (
    lower.includes("authentication") ||
    lower.includes("unauthorized") ||
    lower.includes("invalid.*key") ||
    lower.includes("403") ||
    lower.includes("401") ||
    lower.includes("permission denied")
  ) {
    return "auth_failure";
  }

  // Rate limit
  if (lower.includes("rate limit") || lower.includes("429") || lower.includes("too many requests")) {
    return "rate_limit";
  }

  // Connection failure
  if (
    lower.includes("connection") ||
    lower.includes("timeout") ||
    lower.includes("econnrefused") ||
    lower.includes("network") ||
    lower.includes("dns") ||
    lower.includes("unreachable")
  ) {
    return "connection_failure";
  }

  return "unknown";
}

export function OrchestrationErrorGuide({
  error,
  onGoToSettings,
}: OrchestrationErrorGuideProps) {
  const { t } = useTranslation();
  const category = classifyError(error);

  return (
    <div className="orch-error-guide">
      <div className="orch-error-raw">
        {t("tasks.orchestrationFailed", { error })}
      </div>
      <div className="orch-error-guidance">
        <span className="orch-error-guidance-icon">💡</span>
        <div className="orch-error-guidance-content">
          <div className="orch-error-guidance-text">
            {t(`orchError.guidance_${category}`)}
          </div>
          <div className="orch-error-guidance-action">
            {(category === "provider_not_configured" ||
              category === "missing_api_key" ||
              category === "auth_failure") &&
              onGoToSettings && (
                <button
                  className="btn btn-sm orch-error-settings-btn"
                  onClick={onGoToSettings}
                  type="button"
                >
                  {t("orchError.goToSettings")}
                </button>
              )}
            {category === "rate_limit" && (
              <span className="orch-error-hint">{t("orchError.retryLater")}</span>
            )}
            {category === "connection_failure" && (
              <span className="orch-error-hint">{t("orchError.checkConnection")}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
