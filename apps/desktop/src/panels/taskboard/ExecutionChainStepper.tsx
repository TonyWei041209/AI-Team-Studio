import { useTranslation } from "react-i18next";
import "./ExecutionChainStepper.css";

// ── Types ──────────────────────────────────────────────

type ChainStepStatus = "completed" | "active" | "waiting" | "skipped" | "failed";

export interface ExecutionChainStepperProps {
  proposalStatus: string;       // "pending" | "approved" | "rejected" | "auto_approved"
  requiresApproval: boolean;
  hasApproval: boolean;         // whether a linked approval exists
  approvalStatus?: string;      // "pending" | "approved" | "rejected" | "consumed"
  hasSnapshot: boolean;
  snapshotStatus?: string;      // "frozen"
  hasExecRequest: boolean;
  execRequestStatus?: string;   // "requested" | "confirmed" | "rejected"
  hasDryRun: boolean;
  dryRunStatus?: string;        // "completed" | "failed"
  hasRealRun: boolean;
  realRunStatus?: string;       // "completed" | "failed"
  hasRollback: boolean;
  rollbackStatus?: string;      // "completed" | "failed"
}

// ── Status derivation helpers ──────────────────────────

function deriveApprovalStatus(props: ExecutionChainStepperProps): ChainStepStatus {
  if (!props.requiresApproval) return "skipped";
  if (!props.hasApproval) return "waiting";
  const s = props.approvalStatus;
  if (s === "approved" || s === "consumed") return "completed";
  if (s === "pending") return "active";
  if (s === "rejected") return "failed";
  return "waiting";
}

function deriveSnapshotStatus(props: ExecutionChainStepperProps, approvalStatus: ChainStepStatus): ChainStepStatus {
  if (props.hasSnapshot) return "completed";
  if (approvalStatus === "completed" || approvalStatus === "skipped") return "active";
  return "waiting";
}

function deriveRequestStatus(props: ExecutionChainStepperProps, snapshotStatus: ChainStepStatus): ChainStepStatus {
  if (!props.hasExecRequest) {
    if (snapshotStatus === "completed") return "active";
    return "waiting";
  }
  const s = props.execRequestStatus;
  if (s === "confirmed") return "completed";
  if (s === "requested") return "active";
  if (s === "rejected") return "failed";
  return "waiting";
}

function deriveDryRunStatus(props: ExecutionChainStepperProps, requestStatus: ChainStepStatus): ChainStepStatus {
  if (!props.hasDryRun) {
    if (requestStatus === "completed") return "active";
    return "waiting";
  }
  const s = props.dryRunStatus;
  if (s === "completed") return "completed";
  if (s === "failed") return "failed";
  return "active";
}

function deriveExecuteStatus(props: ExecutionChainStepperProps, dryRunStatus: ChainStepStatus): ChainStepStatus {
  if (!props.hasRealRun) {
    if (dryRunStatus === "completed") return "active";
    return "waiting";
  }
  const s = props.realRunStatus;
  if (s === "completed") return "completed";
  if (s === "failed") return "failed";
  return "active";
}

function deriveRollbackStatus(props: ExecutionChainStepperProps, executeStatus: ChainStepStatus): ChainStepStatus {
  if (!props.hasRollback) {
    if (executeStatus === "completed" || executeStatus === "failed") return "active";
    return "waiting";
  }
  const s = props.rollbackStatus;
  if (s === "completed") return "completed";
  if (s === "failed") return "failed";
  return "active";
}

// ── Component ──────────────────────────────────────────

export function ExecutionChainStepper(props: ExecutionChainStepperProps) {
  const { t } = useTranslation();

  // Derive each step status in dependency order
  const approvalStatus = deriveApprovalStatus(props);
  const snapshotStatus = deriveSnapshotStatus(props, approvalStatus);
  const requestStatus = deriveRequestStatus(props, snapshotStatus);
  const dryRunStatus = deriveDryRunStatus(props, requestStatus);
  const executeStatus = deriveExecuteStatus(props, dryRunStatus);
  const rollbackStatus = deriveRollbackStatus(props, executeStatus);

  const steps: Array<{ key: string; label: string; status: ChainStepStatus }> = [
    { key: "proposal", label: t("execChain.proposal"), status: "completed" },
    { key: "approval", label: t("execChain.approval"), status: approvalStatus },
    { key: "snapshot", label: t("execChain.snapshot"), status: snapshotStatus },
    { key: "request", label: t("execChain.request"), status: requestStatus },
    { key: "dryRun", label: t("execChain.dryRun"), status: dryRunStatus },
    { key: "execute", label: t("execChain.execute"), status: executeStatus },
    { key: "rollback", label: t("execChain.rollback"), status: rollbackStatus },
  ];

  return (
    <div className="exec-chain">
      {steps.map((step, i) => (
        <div key={step.key} style={{ display: "flex", alignItems: "flex-start" }}>
          {i > 0 && (
            <div
              className={`exec-chain-connector${steps[i - 1].status === "completed" ? " done" : ""}`}
            />
          )}
          <div
            className={`exec-chain-step exec-chain-step-${step.status}`}
            title={`${step.label}: ${step.status}`}
          >
            <div className="exec-chain-dot" />
            <div className="exec-chain-label">{step.label}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
