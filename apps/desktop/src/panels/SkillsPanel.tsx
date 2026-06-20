import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { SkillCreate, SkillUpdate, Skill, SkillScopeType, AgentRole } from "../types/api";
import { useSkills } from "../hooks/useSkills";
import { ConfirmModal } from "../components/ConfirmModal";
import "./SkillsPanel.css";

const AGENT_ROLES: AgentRole[] = ["planner", "architect", "builder", "qa", "security_reviewer", "reviewer", "documentation"];

export function SkillsPanel() {
  const { t } = useTranslation();
  const { skills, loading, error, refresh, createSkill, updateSkill, deleteSkill, toggleSkill } = useSkills();

  // Form state
  const [showForm, setShowForm] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Form fields
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");
  const [scopeType, setScopeType] = useState<SkillScopeType>("global");
  const [agentRole, setAgentRole] = useState<AgentRole | "">("");
  const [isEnabled, setIsEnabled] = useState(true);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<Skill | null>(null);

  const resetForm = () => {
    setName("");
    setDescription("");
    setContent("");
    setScopeType("global");
    setAgentRole("");
    setIsEnabled(true);
    setFormError(null);
    setEditingSkill(null);
  };

  const openCreateForm = () => {
    resetForm();
    setShowForm(true);
  };

  const openEditForm = (skill: Skill) => {
    setName(skill.name);
    setDescription(skill.description);
    setContent(skill.content);
    setScopeType(skill.scope_type);
    setAgentRole(skill.agent_role ?? "");
    setIsEnabled(skill.is_enabled);
    setEditingSkill(skill);
    setFormError(null);
    setShowForm(true);
  };

  const closeForm = () => {
    setShowForm(false);
    resetForm();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    setSubmitting(true);
    setFormError(null);
    try {
      if (editingSkill) {
        // Update
        const body: SkillUpdate = {
          name: name.trim(),
          description: description.trim(),
          content: content.trim(),
          scope_type: scopeType,
          agent_role: scopeType === "agent" ? (agentRole as AgentRole) : null,
          is_enabled: isEnabled,
        };
        await updateSkill(editingSkill.id, body);
      } else {
        // Create
        const body: SkillCreate = {
          name: name.trim(),
          description: description.trim() || undefined,
          content: content.trim() || undefined,
          scope_type: scopeType,
          agent_role: scopeType === "agent" ? (agentRole as AgentRole) : undefined,
          is_enabled: isEnabled,
        };
        await createSkill(body);
      }
      closeForm();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save skill");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteSkill(deleteTarget.id);
    } catch (err) {
      // surface error briefly via form error
      setFormError(err instanceof Error ? err.message : "Failed to delete skill");
    }
    setDeleteTarget(null);
  };

  const handleToggle = async (skill: Skill) => {
    try {
      await toggleSkill(skill.id, !skill.is_enabled);
    } catch {
      // silent — refresh will restore correct state
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
    <div className="skills-panel">
      <div className="panel-header">
        <h2 className="panel-title">{t("skills.title")}</h2>
        <div className="panel-actions">
          <button className="btn btn-secondary" onClick={refresh} title={t("skills.refresh")}>
            &#8635;
          </button>
          <button
            className="btn btn-primary"
            onClick={() => (showForm ? closeForm() : openCreateForm())}
          >
            {showForm ? t("skills.cancel") : t("skills.newSkill")}
          </button>
        </div>
      </div>

      {/* Create / Edit form */}
      {showForm && (
        <form className="skill-form" onSubmit={handleSubmit}>
          <div className="skill-form-title">
            {editingSkill ? t("skills.editSkill") : t("skills.createSkill")}
          </div>
          <div className="form-field">
            <label className="form-label">{t("skills.nameLabel")}</label>
            <input
              className="form-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("skills.namePlaceholder")}
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label">{t("skills.descriptionLabel")}</label>
            <input
              className="form-input"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("skills.descriptionPlaceholder")}
            />
          </div>
          <div className="form-field">
            <label className="form-label">{t("skills.contentLabel")}</label>
            <textarea
              className="form-input form-textarea"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={t("skills.contentPlaceholder")}
              rows={4}
            />
          </div>
          <div className="form-row">
            <div className="form-field form-field-half">
              <label className="form-label">{t("skills.scopeLabel")}</label>
              <select
                className="form-input"
                value={scopeType}
                onChange={(e) => {
                  const v = e.target.value as SkillScopeType;
                  setScopeType(v);
                  if (v === "global") setAgentRole("");
                }}
              >
                <option value="global">{t("skills.scopeGlobal")}</option>
                <option value="agent">{t("skills.scopeAgent")}</option>
              </select>
            </div>
            {scopeType === "agent" && (
              <div className="form-field form-field-half">
                <label className="form-label">{t("skills.roleLabel")}</label>
                <select
                  className="form-input"
                  value={agentRole}
                  onChange={(e) => setAgentRole(e.target.value as AgentRole)}
                  required
                >
                  <option value="" disabled>{t("skills.rolePlaceholder")}</option>
                  {AGENT_ROLES.map((r) => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
              </div>
            )}
          </div>
          <div className="form-field">
            <label className="form-label-inline">
              <input
                type="checkbox"
                checked={isEnabled}
                onChange={(e) => setIsEnabled(e.target.checked)}
              />
              <span>{t("skills.enabledLabel")}</span>
            </label>
          </div>
          {formError && <div className="form-error">{formError}</div>}
          <div className="form-actions">
            <button className="btn btn-secondary" type="button" onClick={closeForm}>
              {t("skills.cancel")}
            </button>
            <button
              className="btn btn-primary"
              type="submit"
              disabled={submitting || !name.trim() || (scopeType === "agent" && !agentRole)}
            >
              {submitting ? t("skills.saving") : (editingSkill ? t("skills.save") : t("skills.createSkill"))}
            </button>
          </div>
        </form>
      )}

      {/* Error state */}
      {error && (
        <div className="panel-error">
          <span>&#9888; {error}</span>
          <button className="btn btn-secondary btn-sm" onClick={refresh}>
            {t("skills.retry")}
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && <div className="panel-loading">{t("skills.loading")}</div>}

      {/* Empty */}
      {!loading && !error && skills.length === 0 && !showForm && (
        <div className="panel-empty">{t("skills.empty")}</div>
      )}

      {/* Skills list */}
      {!loading && skills.length > 0 && (
        <div className="skill-list">
          {skills.map((skill) => (
            <div key={skill.id} className={`skill-card ${!skill.is_enabled ? "skill-disabled" : ""}`}>
              <div className="skill-card-header">
                <div className="skill-card-title">
                  <span className="skill-name">{skill.name}</span>
                  <span className={`skill-scope-badge scope-${skill.scope_type}`}>
                    {skill.scope_type === "global" ? t("skills.scopeGlobal") : skill.agent_role}
                  </span>
                  {!skill.is_enabled && (
                    <span className="skill-disabled-badge">{t("skills.disabled")}</span>
                  )}
                </div>
                <div className="skill-card-actions">
                  <button
                    className={`btn btn-sm ${skill.is_enabled ? "btn-secondary" : "btn-success"}`}
                    onClick={() => handleToggle(skill)}
                    title={skill.is_enabled ? t("skills.disable") : t("skills.enable")}
                  >
                    {skill.is_enabled ? t("skills.disable") : t("skills.enable")}
                  </button>
                  <button
                    className="btn btn-sm btn-secondary"
                    onClick={() => openEditForm(skill)}
                    title={t("skills.edit")}
                  >
                    {t("skills.edit")}
                  </button>
                  <button
                    className="btn btn-sm btn-danger"
                    onClick={() => setDeleteTarget(skill)}
                    title={t("skills.delete")}
                  >
                    {t("skills.delete")}
                  </button>
                </div>
              </div>
              {skill.description && (
                <div className="skill-description">{skill.description}</div>
              )}
              {skill.content && (
                <div className="skill-content-preview">
                  {skill.content.length > 120 ? skill.content.slice(0, 120) + "..." : skill.content}
                </div>
              )}
              <div className="skill-meta">
                <span className="skill-date">{formatDate(skill.updated_at)}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Delete confirmation modal */}
      <ConfirmModal
        open={deleteTarget !== null}
        message={t("skills.deleteConfirmMessage", { name: deleteTarget?.name ?? "" })}
        confirmLabel={t("skills.delete")}
        cancelLabel={t("confirm.cancel")}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        danger
      />
    </div>
  );
}
