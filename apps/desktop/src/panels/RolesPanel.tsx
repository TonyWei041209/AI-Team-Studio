import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { RoleCreate, RoleUpdate, Role } from "../types/api";
import { useRoles } from "../hooks/useRoles";
import { ConfirmModal } from "../components/ConfirmModal";
import "./RolesPanel.css";

type RoleFilter = "all" | "system" | "custom";

/** Map system role names to i18n keys for localized display names */
const SYSTEM_ROLE_I18N: Record<string, string> = {
  planner: "settings.roleName_planner",
  builder: "settings.roleName_builder",
  qa: "settings.roleName_qa",
  security_reviewer: "settings.roleName_security_reviewer",
  reviewer: "settings.roleName_reviewer",
};

export function RolesPanel() {
  const { t } = useTranslation();
  const { roles, loading, error, refresh, createRole, updateRole, deleteRole } = useRoles();

  // Filter
  const [filter, setFilter] = useState<RoleFilter>("all");

  // Form state
  const [showForm, setShowForm] = useState(false);
  const [editingRole, setEditingRole] = useState<Role | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Form fields
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [department, setDepartment] = useState("engineering");
  const [isEnabled, setIsEnabled] = useState(true);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<Role | null>(null);

  const filteredRoles = roles.filter((r) => {
    if (filter === "system") return r.is_system;
    if (filter === "custom") return !r.is_system;
    return true;
  });

  const resetForm = () => {
    setName("");
    setDisplayName("");
    setDescription("");
    setDepartment("engineering");
    setIsEnabled(true);
    setFormError(null);
    setEditingRole(null);
  };

  const openCreateForm = () => {
    resetForm();
    setShowForm(true);
  };

  const openEditForm = (role: Role) => {
    setName(role.name);
    setDisplayName(role.display_name);
    setDescription(role.description);
    setDepartment(role.department);
    setIsEnabled(role.is_enabled);
    setEditingRole(role);
    setFormError(null);
    setShowForm(true);
  };

  const closeForm = () => {
    setShowForm(false);
    resetForm();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingRole && !name.trim()) return;
    if (!editingRole && !displayName.trim()) return;

    setSubmitting(true);
    setFormError(null);
    try {
      if (editingRole) {
        // Update — system roles: only description + is_enabled
        const body: RoleUpdate = editingRole.is_system
          ? { description: description.trim(), is_enabled: isEnabled }
          : {
              display_name: displayName.trim(),
              description: description.trim(),
              department: department.trim() || "engineering",
              is_enabled: isEnabled,
            };
        await updateRole(editingRole.id, body);
      } else {
        // Create
        const body: RoleCreate = {
          name: name.trim().toLowerCase(),
          display_name: displayName.trim(),
          description: description.trim() || undefined,
          department: department.trim() || undefined,
          is_enabled: isEnabled,
        };
        await createRole(body);
      }
      closeForm();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save role");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteRole(deleteTarget.id);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to delete role");
    }
    setDeleteTarget(null);
  };

  const isEditingSystem = editingRole?.is_system ?? false;

  return (
    <div className="roles-panel">
      <div className="panel-header">
        <h2 className="panel-title">{t("roles.title")}</h2>
        <div className="panel-actions">
          <button className="btn btn-secondary" onClick={refresh} title={t("roles.refresh")}>
            &#8635;
          </button>
          <button
            className="btn btn-primary"
            onClick={() => (showForm ? closeForm() : openCreateForm())}
          >
            {showForm ? t("roles.cancel") : t("roles.newRole")}
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="roles-filter-bar">
        {(["all", "system", "custom"] as RoleFilter[]).map((f) => (
          <button
            key={f}
            className={`btn btn-sm ${filter === f ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setFilter(f)}
          >
            {t(`roles.filter${f.charAt(0).toUpperCase() + f.slice(1)}`)}
          </button>
        ))}
      </div>

      {/* Create / Edit form */}
      {showForm && (
        <form className="role-form" onSubmit={handleSubmit}>
          <div className="role-form-title">
            {editingRole
              ? (isEditingSystem ? t("roles.editSystemRole") : t("roles.editRole"))
              : t("roles.createRole")}
          </div>
          {/* Name — only for create, or read-only for edit */}
          <div className="form-field">
            <label className="form-label">{t("roles.nameLabel")}</label>
            <input
              className="form-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("roles.namePlaceholder")}
              disabled={!!editingRole}
              required={!editingRole}
            />
            {!editingRole && (
              <span className="form-hint">{t("roles.nameHint")}</span>
            )}
          </div>
          {/* Display Name — disabled for system roles */}
          <div className="form-field">
            <label className="form-label">{t("roles.displayNameLabel")}</label>
            <input
              className="form-input"
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t("roles.displayNamePlaceholder")}
              disabled={isEditingSystem}
              required={!editingRole}
            />
          </div>
          {/* Description */}
          <div className="form-field">
            <label className="form-label">{t("roles.descriptionLabel")}</label>
            <input
              className="form-input"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("roles.descriptionPlaceholder")}
            />
          </div>
          {/* Department — disabled for system roles */}
          <div className="form-field">
            <label className="form-label">{t("roles.departmentLabel")}</label>
            <input
              className="form-input"
              type="text"
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              placeholder={t("roles.departmentPlaceholder")}
              disabled={isEditingSystem}
            />
          </div>
          {/* Enabled */}
          <div className="form-field">
            <label className="form-label-inline">
              <input
                type="checkbox"
                checked={isEnabled}
                onChange={(e) => setIsEnabled(e.target.checked)}
              />
              <span>{t("roles.enabledLabel")}</span>
            </label>
          </div>
          {formError && <div className="form-error">{formError}</div>}
          <div className="form-actions">
            <button className="btn btn-secondary" type="button" onClick={closeForm}>
              {t("roles.cancel")}
            </button>
            <button
              className="btn btn-primary"
              type="submit"
              disabled={
                submitting ||
                (!editingRole && (!name.trim() || !displayName.trim()))
              }
            >
              {submitting
                ? t("roles.saving")
                : editingRole
                  ? t("roles.save")
                  : t("roles.createRole")}
            </button>
          </div>
        </form>
      )}

      {/* Error state */}
      {error && (
        <div className="panel-error">
          <span>&#9888; {error}</span>
          <button className="btn btn-secondary btn-sm" onClick={refresh}>
            {t("roles.retry")}
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && <div className="panel-loading">{t("roles.loading")}</div>}

      {/* Empty */}
      {!loading && !error && filteredRoles.length === 0 && !showForm && (
        <div className="panel-empty">{t("roles.empty")}</div>
      )}

      {/* Roles list */}
      {!loading && filteredRoles.length > 0 && (
        <div className="role-list">
          {filteredRoles.map((role) => (
            <div key={role.id} className={`role-card ${!role.is_enabled ? "role-disabled" : ""}`}>
              <div className="role-card-header">
                <div className="role-card-title">
                  <span className="role-display-name">
                    {role.is_system && SYSTEM_ROLE_I18N[role.name]
                      ? t(SYSTEM_ROLE_I18N[role.name])
                      : role.display_name}
                  </span>
                  <span className="role-name-tag">{role.name}</span>
                  <span className={`role-type-badge ${role.is_system ? "badge-system" : "badge-custom"}`}>
                    {role.is_system ? t("roles.systemBadge") : t("roles.customBadge")}
                  </span>
                  <span className="role-dept-badge">{role.department}</span>
                  {!role.is_enabled && (
                    <span className="role-disabled-badge">{t("roles.disabled")}</span>
                  )}
                </div>
                <div className="role-card-actions">
                  <button
                    className="btn btn-sm btn-secondary"
                    onClick={() => openEditForm(role)}
                    title={t("roles.edit")}
                  >
                    {t("roles.edit")}
                  </button>
                  {!role.is_system && (
                    <button
                      className="btn btn-sm btn-danger"
                      onClick={() => setDeleteTarget(role)}
                      title={t("roles.delete")}
                    >
                      {t("roles.delete")}
                    </button>
                  )}
                </div>
              </div>
              {role.description && (
                <div className="role-description">{role.description}</div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Delete confirmation modal */}
      <ConfirmModal
        open={deleteTarget !== null}
        message={t("roles.deleteConfirmMessage", { name: deleteTarget?.display_name ?? "" })}
        confirmLabel={t("roles.delete")}
        cancelLabel={t("confirm.cancel")}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        danger
      />
    </div>
  );
}
