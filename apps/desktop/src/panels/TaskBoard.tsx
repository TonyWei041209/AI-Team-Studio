import { useState, useEffect } from "react";
import type { TaskCreate, TaskStatus, TaskPriority } from "../types/api";
import { useTasks } from "../hooks/useTasks";
import "./TaskBoard.css";

interface TaskBoardProps {
  projectId: string | null;
}

const STATUS_COLORS: Record<TaskStatus, string> = {
  pending: "var(--text-muted)",
  planning: "var(--accent-blue)",
  in_progress: "var(--accent-yellow)",
  reviewing: "var(--accent-blue)",
  done: "var(--accent-green)",
  failed: "var(--accent-red)",
};

const PRIORITY_COLORS: Record<TaskPriority, string> = {
  low: "var(--text-muted)",
  medium: "var(--accent-blue)",
  high: "var(--accent-yellow)",
  critical: "var(--accent-red)",
};

export function TaskBoard({ projectId }: TaskBoardProps) {
  const { tasks, loading, error, refresh, createTask } = useTasks(projectId);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Form fields
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("medium");

  // Reset form state when project changes
  useEffect(() => {
    setShowForm(false);
    setTitle("");
    setDescription("");
    setPriority("medium");
    setFormError(null);
  }, [projectId]);

  if (!projectId) {
    return (
      <div className="task-board">
        <div className="panel-empty">
          Select a project first to view tasks.
        </div>
      </div>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;

    setSubmitting(true);
    setFormError(null);
    try {
      const body: TaskCreate = {
        title: title.trim(),
        description: description.trim() || undefined,
        priority,
      };
      await createTask(body);
      setTitle("");
      setDescription("");
      setPriority("medium");
      setShowForm(false);
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Failed to create task",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  return (
    <div className="task-board">
      <div className="panel-header">
        <h2 className="panel-title">Tasks</h2>
        <div className="panel-actions">
          <button
            className="btn btn-secondary"
            onClick={refresh}
            title="Refresh"
          >
            &#8635;
          </button>
          <button
            className="btn btn-primary"
            onClick={() => setShowForm(!showForm)}
          >
            {showForm ? "Cancel" : "+ New Task"}
          </button>
        </div>
      </div>

      {/* Create form */}
      {showForm && (
        <form className="task-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label className="form-label">Title *</label>
            <input
              className="form-input"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Task title"
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label">Description</label>
            <textarea
              className="form-input form-textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
              rows={3}
            />
          </div>
          <div className="form-field">
            <label className="form-label">Priority</label>
            <select
              className="form-input"
              value={priority}
              onChange={(e) => setPriority(e.target.value as TaskPriority)}
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
            type="submit"
            disabled={submitting || !title.trim()}
          >
            {submitting ? "Creating..." : "Create Task"}
          </button>
        </form>
      )}

      {/* Error state */}
      {error && (
        <div className="panel-error">
          <span>&#9888; {error}</span>
          <button className="btn btn-secondary btn-sm" onClick={refresh}>
            Retry
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && <div className="panel-loading">Loading tasks...</div>}

      {/* Empty */}
      {!loading && !error && tasks.length === 0 && (
        <div className="panel-empty">
          No tasks for this project. Create one to begin.
        </div>
      )}

      {/* Task list */}
      {!loading && tasks.length > 0 && (
        <div className="task-list">
          {tasks.map((t) => (
            <div key={t.id} className="task-item">
              <div className="task-item-header">
                <span className="task-title">{t.title}</span>
                <span className="task-date">{formatDate(t.updated_at)}</span>
              </div>
              {t.description && (
                <div className="task-desc">{t.description}</div>
              )}
              <div className="task-badges">
                <span
                  className="badge badge-status"
                  style={{
                    color: STATUS_COLORS[t.status],
                    borderColor: STATUS_COLORS[t.status],
                  }}
                >
                  {t.status.replace("_", " ")}
                </span>
                <span
                  className="badge badge-priority"
                  style={{
                    color: PRIORITY_COLORS[t.priority],
                    borderColor: PRIORITY_COLORS[t.priority],
                  }}
                >
                  {t.priority}
                </span>
                {t.assigned_agent_role && (
                  <span className="badge badge-role">
                    {t.assigned_agent_role}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
