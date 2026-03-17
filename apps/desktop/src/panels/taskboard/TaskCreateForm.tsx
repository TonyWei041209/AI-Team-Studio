import type { TaskPriority } from "./types";

interface TaskCreateFormProps {
  title: string;
  description: string;
  priority: TaskPriority;
  formError: string | null;
  submitting: boolean;
  onTitleChange: (v: string) => void;
  onDescriptionChange: (v: string) => void;
  onPriorityChange: (v: TaskPriority) => void;
  onSubmit: (e: React.FormEvent) => void;
}

export function TaskCreateForm({
  title,
  description,
  priority,
  formError,
  submitting,
  onTitleChange,
  onDescriptionChange,
  onPriorityChange,
  onSubmit,
}: TaskCreateFormProps) {
  return (
    <form className="task-form" onSubmit={onSubmit}>
      <div className="form-field">
        <label className="form-label">Title *</label>
        <input
          className="form-input"
          data-testid="task-title-input"
          type="text"
          value={title}
          onChange={(e) => onTitleChange(e.target.value)}
          placeholder="Task title"
          required
        />
      </div>
      <div className="form-field">
        <label className="form-label">Description</label>
        <textarea
          className="form-input form-textarea"
          value={description}
          onChange={(e) => onDescriptionChange(e.target.value)}
          placeholder="Optional description"
          rows={3}
        />
      </div>
      <div className="form-field">
        <label className="form-label">Priority</label>
        <select
          className="form-input"
          value={priority}
          onChange={(e) => onPriorityChange(e.target.value as TaskPriority)}
        >
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="critical">Critical</option>
        </select>
      </div>
      {formError && <div className="form-error">{formError}</div>}
      <button
        className="btn btn-primary"
        data-testid="task-submit-btn"
        type="submit"
        disabled={submitting || !title.trim()}
      >
        {submitting ? "Creating..." : "Create Task"}
      </button>
    </form>
  );
}
