/**
 * ConfirmModal — translatable replacement for window.confirm().
 *
 * Renders a dark-themed modal overlay with a message, confirm, and cancel buttons.
 * All text is passed as props so callers can use t() for i18n.
 *
 * Safety note: This component does NOT change any execution semantics.
 * It is a pure presentation wrapper — the caller still decides what happens
 * on confirm/cancel, exactly like window.confirm() did before.
 */

interface ConfirmModalProps {
  /** Whether the modal is visible */
  open: boolean;
  /** Main message shown in the modal body */
  message: string;
  /** Text for the confirm (proceed) button */
  confirmLabel: string;
  /** Text for the cancel button */
  cancelLabel: string;
  /** Called when user clicks Confirm */
  onConfirm: () => void;
  /** Called when user clicks Cancel or backdrop */
  onCancel: () => void;
  /** Optional: make the confirm button visually dangerous (red) */
  danger?: boolean;
}

export function ConfirmModal({
  open,
  message,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
  danger = false,
}: ConfirmModalProps) {
  if (!open) return null;

  return (
    <div className="confirm-modal-overlay" onClick={onCancel}>
      <div
        className="confirm-modal-box"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="confirm-modal-message">{message}</div>
        <div className="confirm-modal-buttons">
          <button
            className="btn btn-secondary confirm-modal-cancel"
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            className={`btn confirm-modal-confirm ${danger ? "confirm-modal-danger" : "btn-primary"}`}
            onClick={onConfirm}
            autoFocus
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
