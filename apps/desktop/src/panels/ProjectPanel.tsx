import { useState } from "react";
import type { ProjectCreate } from "../types/api";
import { useProjects } from "../hooks/useProjects";
import "./ProjectPanel.css";

interface ProjectPanelProps {
  selectedProjectId: string | null;
  onSelectProject: (id: string) => void;
}

export function ProjectPanel({
  selectedProjectId,
  onSelectProject,
}: ProjectPanelProps) {
  const { projects, loading, error, refresh, createProject } = useProjects();
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Form fields
  const [name, setName] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [description, setDescription] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !repoPath.trim()) return;

    setSubmitting(true);
    setFormError(null);
    try {
      const body: ProjectCreate = {
        name: name.trim(),
        local_repo_path: repoPath.trim(),
        description: description.trim() || undefined,
      };
      const created = await createProject(body);
      onSelectProject(created.id);
      // Reset form
      setName("");
      setRepoPath("");
      setDescription("");
      setShowForm(false);
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Failed to create project",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleDateString();
    } catch {
      return iso;
    }
  };

  return (
    <div className="project-panel">
      <div className="panel-header">
        <h2 className="panel-title">Projects</h2>
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
            data-testid="project-create-btn"
            onClick={() => setShowForm(!showForm)}
          >
            {showForm ? "Cancel" : "+ New Project"}
          </button>
        </div>
      </div>

      {/* Create form */}
      {showForm && (
        <form className="project-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label className="form-label">Name *</label>
            <input
              className="form-input"
              data-testid="project-name-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Project"
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label">Local Repo Path *</label>
            <input
              className="form-input"
              data-testid="project-path-input"
              type="text"
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              placeholder="D:/projects/my-project"
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label">Description</label>
            <input
              className="form-input"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
            />
          </div>
          {formError && <div className="form-error">{formError}</div>}
          <button
            className="btn btn-primary"
            data-testid="project-submit-btn"
            type="submit"
            disabled={submitting || !name.trim() || !repoPath.trim()}
          >
            {submitting ? "Creating..." : "Create Project"}
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

      {/* Loading state */}
      {loading && <div className="panel-loading">Loading projects...</div>}

      {/* Empty state */}
      {!loading && !error && projects.length === 0 && (
        <div className="panel-empty">
          No projects yet. Create one to get started.
        </div>
      )}

      {/* Project list */}
      {!loading && projects.length > 0 && (
        <div className="project-list" data-testid="project-list">
          {projects.map((p) => (
            <div
              key={p.id}
              className={`project-item ${selectedProjectId === p.id ? "selected" : ""}`}
              onClick={() => onSelectProject(p.id)}
            >
              <div className="project-item-header">
                <span className="project-name">{p.name}</span>
                <span className="project-date">{formatDate(p.created_at)}</span>
              </div>
              <div className="project-path">{p.local_repo_path}</div>
              {p.description && (
                <div className="project-desc">{p.description}</div>
              )}
              <div className="project-meta">
                <span className="project-branch">{p.default_branch}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
