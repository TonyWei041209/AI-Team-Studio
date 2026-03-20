import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import type { ProjectCreate, ProjectParticipant } from "../types/api";
import { useProjects } from "../hooks/useProjects";
import { projectsApi } from "../api/projects";
import type { GodotInfo } from "../api/projects";
import "./ProjectPanel.css";

const MANDATORY_ROLES = new Set(["planner", "builder"]);

interface ProjectPanelProps {
  selectedProjectId: string | null;
  onSelectProject: (id: string) => void;
}

export function ProjectPanel({
  selectedProjectId,
  onSelectProject,
}: ProjectPanelProps) {
  const { t } = useTranslation();
  const { projects, loading, error, refresh, createProject } = useProjects();
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Participant state
  const [participants, setParticipants] = useState<ProjectParticipant[]>([]);
  const [participantsLoading, setParticipantsLoading] = useState(false);
  const [participantsSaving, setParticipantsSaving] = useState(false);
  const [participantsSaved, setParticipantsSaved] = useState(false);
  const [participantsError, setParticipantsError] = useState<string | null>(null);

  // Godot detection (Phase 5.8)
  const [godotInfo, setGodotInfo] = useState<Record<string, GodotInfo>>({});

  // Load Godot info for all projects
  useEffect(() => {
    if (projects.length === 0) return;
    projects.forEach(async (p) => {
      try {
        const info = await projectsApi.getGodotInfo(p.id);
        setGodotInfo((prev) => ({ ...prev, [p.id]: info }));
      } catch {
        // Silent — Godot detection is non-critical
      }
    });
  }, [projects]);

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

  const loadParticipants = useCallback(async (projectId: string) => {
    setParticipantsLoading(true);
    setParticipantsError(null);
    setParticipantsSaved(false);
    try {
      const data = await projectsApi.getParticipants(projectId);
      setParticipants(data);
    } catch {
      setParticipantsError(t("projects.participantError"));
    } finally {
      setParticipantsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (selectedProjectId) {
      loadParticipants(selectedProjectId);
    } else {
      setParticipants([]);
    }
  }, [selectedProjectId, loadParticipants]);

  const handleToggleParticipant = async (roleName: string, currentEnabled: boolean) => {
    if (!selectedProjectId) return;
    if (MANDATORY_ROLES.has(roleName) && currentEnabled) return; // can't disable mandatory

    const updated = participants.map((p) =>
      p.role_name === roleName ? { ...p, is_enabled: !currentEnabled } : p
    );
    setParticipants(updated);
    setParticipantsSaving(true);
    setParticipantsSaved(false);
    setParticipantsError(null);
    try {
      const result = await projectsApi.updateParticipants(selectedProjectId, {
        participants: updated,
      });
      setParticipants(result);
      setParticipantsSaved(true);
    } catch {
      setParticipantsError(t("projects.participantError"));
      // Revert optimistic update
      if (selectedProjectId) {
        await loadParticipants(selectedProjectId);
      }
    } finally {
      setParticipantsSaving(false);
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
        <h2 className="panel-title">{t("projects.title")}</h2>
        <div className="panel-actions">
          <button
            className="btn btn-secondary"
            onClick={refresh}
            title={t("projects.refresh")}
          >
            &#8635;
          </button>
          <button
            className="btn btn-primary"
            data-testid="project-create-btn"
            onClick={() => setShowForm(!showForm)}
          >
            {showForm ? t("projects.cancel") : t("projects.newProject")}
          </button>
        </div>
      </div>

      {/* Create form */}
      {showForm && (
        <form className="project-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label className="form-label">{t("projects.nameLabel")}</label>
            <input
              className="form-input"
              data-testid="project-name-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("projects.namePlaceholder")}
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label">{t("projects.repoPathLabel")}</label>
            <input
              className="form-input"
              data-testid="project-path-input"
              type="text"
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              placeholder={t("projects.repoPathPlaceholder")}
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label">{t("projects.descriptionLabel")}</label>
            <input
              className="form-input"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("projects.descriptionPlaceholder")}
            />
          </div>
          {formError && <div className="form-error">{formError}</div>}
          <button
            className="btn btn-primary"
            data-testid="project-submit-btn"
            type="submit"
            disabled={submitting || !name.trim() || !repoPath.trim()}
          >
            {submitting ? t("projects.creating") : t("projects.createProject")}
          </button>
        </form>
      )}

      {/* Error state */}
      {error && (
        <div className="panel-error">
          <span>&#9888; {error}</span>
          <button className="btn btn-secondary btn-sm" onClick={refresh}>
            {t("projects.retry")}
          </button>
        </div>
      )}

      {/* Loading state */}
      {loading && <div className="panel-loading">{t("projects.loading")}</div>}

      {/* Empty state */}
      {!loading && !error && projects.length === 0 && (
        <div className="panel-empty">
          {t("projects.empty")}
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
                {godotInfo[p.id]?.is_godot && (
                  <span className="project-godot-badge" title={godotInfo[p.id].project_name || "Godot Project"}>
                    {t("projects.godotBadge")}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Team Configuration */}
      <div className="team-config">
        <div className="team-config-title">{t("projects.teamConfig")}</div>
        {!selectedProjectId ? (
          <div className="team-config-hint">{t("projects.selectProjectForTeam")}</div>
        ) : (
          <>
            <div className="team-config-hint">{t("projects.teamConfigHint")}</div>
            {participantsLoading && (
              <div className="panel-loading">{t("projects.loading")}</div>
            )}
            {!participantsLoading && participants.length > 0 && (
              <div className="participant-list">
                {participants.map((p) => {
                  const isMandatory = MANDATORY_ROLES.has(p.role_name);
                  return (
                    <div key={p.role_name} className="participant-row">
                      <div className="participant-info">
                        <span className="participant-role-name">{p.role_name}</span>
                        {isMandatory && (
                          <span className="participant-mandatory-badge">
                            {t("projects.participantMandatory")}
                          </span>
                        )}
                      </div>
                      <div
                        className={`participant-toggle ${p.is_enabled ? "enabled" : ""} ${isMandatory ? "disabled-toggle" : ""}`}
                        onClick={() => handleToggleParticipant(p.role_name, p.is_enabled)}
                        title={isMandatory ? t("projects.participantMandatory") : (p.is_enabled ? t("projects.participantEnabled") : t("projects.participantDisabled"))}
                      >
                        <div className="participant-toggle-knob" />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {(participantsSaving || participantsSaved || participantsError) && (
              <div className={`team-config-status ${participantsError ? "error" : ""}`}>
                {participantsSaving
                  ? t("projects.participantSaving")
                  : participantsError
                  ? participantsError
                  : t("projects.participantSaved")}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
