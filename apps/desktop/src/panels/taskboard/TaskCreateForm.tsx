import { useTranslation } from "react-i18next";
import type { TaskPriority } from "./types";
import { TaskTemplates } from "./TaskTemplates";

interface TaskCreateFormProps {
  title: string;
  description: string;
  priority: TaskPriority;
  formError: string | null;
  submitting: boolean;
  isGodotProject?: boolean;
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
  isGodotProject,
  onTitleChange,
  onDescriptionChange,
  onPriorityChange,
  onSubmit,
}: TaskCreateFormProps) {
  const { t } = useTranslation();

  const handleTemplateSelect = (templateTitle: string, templateDescription: string) => {
    onTitleChange(templateTitle);
    onDescriptionChange(templateDescription);
  };

  return (
    <form className="task-form" onSubmit={onSubmit}>
      <TaskTemplates onSelect={handleTemplateSelect} isGodotProject={isGodotProject} />
      <div className="form-field">
        <label className="form-label">{t("tasks.titleLabel")}</label>
        <input
          className="form-input"
          data-testid="task-title-input"
          type="text"
          value={title}
          onChange={(e) => onTitleChange(e.target.value)}
          placeholder={t("tasks.titlePlaceholder")}
          required
        />
      </div>
      <div className="form-field">
        <label className="form-label">{t("tasks.descriptionLabel")}</label>
        <textarea
          className="form-input form-textarea"
          value={description}
          onChange={(e) => onDescriptionChange(e.target.value)}
          placeholder={t("tasks.descriptionPlaceholder")}
          rows={3}
        />
      </div>
      <div className="form-field">
        <label className="form-label">{t("tasks.priorityLabel")}</label>
        <select
          className="form-input"
          value={priority}
          onChange={(e) => onPriorityChange(e.target.value as TaskPriority)}
        >
          <option value="low">{t("tasks.priorityLow")}</option>
          <option value="medium">{t("tasks.priorityMedium")}</option>
          <option value="high">{t("tasks.priorityHigh")}</option>
          <option value="critical">{t("tasks.priorityCritical")}</option>
        </select>
      </div>
      {formError && <div className="form-error">{formError}</div>}
      <button
        className="btn btn-primary"
        data-testid="task-submit-btn"
        type="submit"
        disabled={submitting || !title.trim()}
      >
        {submitting ? t("tasks.creating") : t("tasks.createTask")}
      </button>
    </form>
  );
}
