import { useState } from "react";
import { useTranslation } from "react-i18next";

interface TaskTemplate {
  key: string;
  icon: string;
}

const GENERAL_TEMPLATES: TaskTemplate[] = [
  { key: "feature", icon: "+" },
  { key: "bugfix", icon: "🔧" },
  { key: "refactor", icon: "♻" },
  { key: "investigate", icon: "🔍" },
  { key: "tests", icon: "✓" },
  { key: "docs", icon: "📝" },
];

const GODOT_TEMPLATES: TaskTemplate[] = [
  { key: "godot_gameplay", icon: "🎮" },
  { key: "godot_scene_bug", icon: "🔧" },
  { key: "godot_refactor", icon: "♻" },
  { key: "godot_ui", icon: "🖥" },
];

interface TaskTemplatesProps {
  onSelect: (title: string, description: string) => void;
  isGodotProject?: boolean;
}

export function TaskTemplates({ onSelect, isGodotProject }: TaskTemplatesProps) {
  const { t } = useTranslation();
  const [activeKey, setActiveKey] = useState<string | null>(null);

  const handleSelect = (tpl: TaskTemplate) => {
    const title = t(`taskTemplates.${tpl.key}_title`);
    const description = t(`taskTemplates.${tpl.key}_description`);
    setActiveKey(tpl.key);
    onSelect(title, description);
    setTimeout(() => setActiveKey(null), 600);
  };

  return (
    <div className="task-templates">
      <div className="task-templates-label">{t("taskTemplates.quickStart")}</div>
      <div className="task-templates-chips">
        {GENERAL_TEMPLATES.map((tpl) => (
          <button
            key={tpl.key}
            className={`task-template-chip${activeKey === tpl.key ? " task-template-chip-active" : ""}`}
            onClick={() => handleSelect(tpl)}
            type="button"
          >
            <span className="task-template-chip-icon">{tpl.icon}</span>
            {t(`taskTemplates.${tpl.key}_label`)}
          </button>
        ))}
      </div>
      {isGodotProject && (
        <>
          <div className="task-templates-label task-templates-label-godot">
            🎮 {t("taskTemplates.godotQuickStart")}
          </div>
          <div className="task-templates-chips">
            {GODOT_TEMPLATES.map((tpl) => (
              <button
                key={tpl.key}
                className={`task-template-chip task-template-chip-godot${activeKey === tpl.key ? " task-template-chip-active" : ""}`}
                onClick={() => handleSelect(tpl)}
                type="button"
              >
                <span className="task-template-chip-icon">{tpl.icon}</span>
                {t(`taskTemplates.${tpl.key}_label`)}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
