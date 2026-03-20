/**
 * Next-step hint label — Phase 20-3.
 *
 * Shows a small, contextual hint about what the user should do next
 * for a given task state. Purely display — no state changes.
 */

import { useTranslation } from "react-i18next";
import type { TaskPhase } from "./TaskStateSummary";

interface NextStepHintProps {
  phase: TaskPhase;
}

export function NextStepHint({ phase }: NextStepHintProps) {
  const { t } = useTranslation();

  // Only show hint for actionable phases
  const hintKey = `nextStep.${phase}`;
  const hint = t(hintKey, "");

  // Don't render if no hint text (completed / failed have no next step)
  if (!hint || phase === "completed" || phase === "failed") return null;

  return (
    <div className="next-step-hint">
      <span className="next-step-hint-arrow">→</span>
      <span className="next-step-hint-text">{hint}</span>
    </div>
  );
}
