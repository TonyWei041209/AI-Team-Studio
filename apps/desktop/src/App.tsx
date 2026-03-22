import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { TabId, ConnectionState, HealthStatus } from "./types/api";
import { StatusIndicator } from "./components/StatusIndicator";
import { CommandPalette } from "./components/CommandPalette";
import { DashboardPanel } from "./panels/DashboardPanel";
import { ProjectPanel } from "./panels/ProjectPanel";
import { TaskBoard } from "./panels/TaskBoard";
import { ApprovalsPanel } from "./panels/ApprovalsPanel";
import { LogsPanel } from "./panels/LogsPanel";
import { SettingsPanel } from "./panels/SettingsPanel";
import { SkillsPanel } from "./panels/SkillsPanel";
import { RolesPanel } from "./panels/RolesPanel";
import { approvalsApi } from "./api/approvals";
import { WorkspaceQuickComposer } from "./components/WorkspaceQuickComposer";
import "./App.css";

const RUNTIME_URL = "http://127.0.0.1:9800";
const COUNT_POLL_INTERVAL = Number(import.meta.env.VITE_APPROVAL_COUNT_POLL_MS) || 10000;

/** Sidebar tab definitions — labelKey maps to sidebar.* i18n keys */
const WORKSPACE_TABS: Array<{ id: TabId; icon: string; labelKey: string }> = [
  { id: "dashboard", icon: "\u2302", labelKey: "sidebar.dashboard" },
  { id: "projects", icon: "\u25A0", labelKey: "sidebar.projects" },
  { id: "tasks", icon: "\u25B6", labelKey: "sidebar.tasks" },
  { id: "skills", icon: "\u2726", labelKey: "sidebar.skills" },
  { id: "roles", icon: "\u263A", labelKey: "sidebar.roles" },
];

const SYSTEM_TABS: Array<{ id: TabId; icon: string; labelKey: string }> = [
  { id: "approvals", icon: "\u2713", labelKey: "sidebar.approvals" },
  { id: "logs", icon: "\u2261", labelKey: "sidebar.logs" },
  { id: "settings", icon: "\u2699", labelKey: "sidebar.settings" },
];

function App() {
  const { t } = useTranslation();
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("checking");
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("dashboard");
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(
    null,
  );
  const [pendingCount, setPendingCount] = useState(0);
  const [autoExpandTaskId, setAutoExpandTaskId] = useState<string | null>(null);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    return (localStorage.getItem("theme") as "dark" | "light") || "dark";
  });

  // Apply theme to document
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  // ── Health check polling ────────────────────────────
  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(`${RUNTIME_URL}/api/health`);
      const data: HealthStatus = await res.json();
      setHealth(data);
      setConnectionState("connected");
    } catch {
      setHealth(null);
      setConnectionState("disconnected");
    }
  }, []);

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 5000);
    return () => clearInterval(interval);
  }, [checkHealth]);

  // ── Background approval count polling ─────────────
  // Keeps sidebar badge updated even when ApprovalsPanel is unmounted.
  // When ApprovalsPanel IS mounted, its onCountChange overrides with
  // a more precise value on each of its own 5s polling ticks.
  const countMountedRef = useRef(true);
  useEffect(() => {
    countMountedRef.current = true;
    const fetchCount = async () => {
      try {
        const data = await approvalsApi.listPending();
        if (countMountedRef.current) {
          setPendingCount(data.length);
        }
      } catch {
        // silent — health check already handles connection state
      }
    };
    fetchCount();
    const interval = setInterval(fetchCount, COUNT_POLL_INTERVAL);
    return () => {
      countMountedRef.current = false;
      clearInterval(interval);
    };
  }, []);

  // ── Global Ctrl+K / Cmd+K shortcut ─────────────────
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setCommandPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // ── Project selection handler ───────────────────────
  const handleSelectProject = useCallback((id: string) => {
    setSelectedProjectId(id);
  }, []);

  // ── Quick composer task created handler ────────────
  const handleComposerTaskCreated = useCallback(
    (projectId: string, taskId: string) => {
      setSelectedProjectId(projectId);
      setAutoExpandTaskId(taskId);
      setActiveTab("tasks");
    },
    [],
  );

  // ── Render active panel ─────────────────────────────
  const renderPanel = () => {
    switch (activeTab) {
      case "dashboard":
        return (
          <DashboardPanel
            onNavigateToProject={(projectId) => {
              setSelectedProjectId(projectId);
              setActiveTab("projects");
            }}
            onNavigateToTasks={(projectId) => {
              setSelectedProjectId(projectId);
              setActiveTab("tasks");
            }}
            onNavigateToApprovals={() => {
              setActiveTab("approvals");
            }}
            onNavigateToSettings={() => {
              setActiveTab("settings");
            }}
          />
        );
      case "projects":
        return (
          <ProjectPanel
            selectedProjectId={selectedProjectId}
            onSelectProject={handleSelectProject}
          />
        );
      case "tasks":
        return (
          <TaskBoard
            projectId={selectedProjectId}
            onNavigateToSettings={() => setActiveTab("settings")}
            autoExpandTaskId={autoExpandTaskId}
            onAutoExpandConsumed={() => setAutoExpandTaskId(null)}
          />
        );
      case "skills":
        return <SkillsPanel />;
      case "roles":
        return <RolesPanel />;
      case "approvals":
        return <ApprovalsPanel onCountChange={setPendingCount} />;
      case "logs":
        return <LogsPanel />;
      case "settings":
        return <SettingsPanel />;
    }
  };

  return (
    <div className="app-container">
      {/* Title Bar */}
      <header className="title-bar" data-tauri-drag-region>
        <div className="title-bar-left">
          <span className="app-logo">&#9670;</span>
          <span className="app-name">{t("app.name")}</span>
          <span className="app-version">{t("app.version")}</span>
        </div>
        <div className="title-bar-right">
          <button
            className="title-bar-search-btn"
            onClick={() => setCommandPaletteOpen(true)}
            title={t("commandPalette.open")}
          >
            &#128269; {t("commandPalette.search")}
            <kbd className="title-bar-kbd">Ctrl+K</kbd>
          </button>
          <button
            className="title-bar-theme-btn"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          >
            {theme === "dark" ? "\u2600" : "\u263E"}
          </button>
          <StatusIndicator state={connectionState} />
        </div>
      </header>

      {/* Main Content */}
      <main className="main-content">
        {/* Sidebar */}
        <nav className="sidebar">
          <div className="sidebar-section">
            <div className="sidebar-label">{t("sidebar.workspace")}</div>
            {WORKSPACE_TABS.map((tab) => (
              <div
                key={tab.id}
                data-testid={`sidebar-${tab.id}`}
                className={`sidebar-item ${activeTab === tab.id ? "active" : ""}`}
                onClick={() => setActiveTab(tab.id)}
              >
                <span className="sidebar-icon">{tab.icon}</span>
                <span>{t(tab.labelKey)}</span>
              </div>
            ))}
            <WorkspaceQuickComposer
              selectedProjectId={selectedProjectId}
              onTaskCreated={handleComposerTaskCreated}
            />
          </div>
          <div className="sidebar-section">
            <div className="sidebar-label">{t("sidebar.system")}</div>
            {SYSTEM_TABS.map((tab) => (
              <div
                key={tab.id}
                data-testid={`sidebar-${tab.id}`}
                className={`sidebar-item ${activeTab === tab.id ? "active" : ""}`}
                onClick={() => setActiveTab(tab.id)}
              >
                <span className="sidebar-icon">{tab.icon}</span>
                <span>{t(tab.labelKey)}</span>
                {tab.id === "approvals" && pendingCount > 0 && (
                  <span className="sidebar-badge" data-testid="sidebar-badge-approvals">
                    {pendingCount}
                  </span>
                )}
              </div>
            ))}
          </div>
        </nav>

        {/* Content Area */}
        <div className="content-area">
          {connectionState === "disconnected" && (
            <div className="connection-banner">
              <span>&#9888; {t("app.connectionBanner")}</span>
              <code>{t("app.connectionBannerHint")}</code>
            </div>
          )}
          {renderPanel()}
        </div>
      </main>

      {/* Status Bar */}
      <footer className="status-bar">
        <span className="status-bar-item">{t("app.statusBarPhase")}</span>
        <span className="status-bar-item">
          {connectionState === "connected"
            ? t("app.statusBarRuntimeConnected", { version: health?.version ?? "?" })
            : t("app.statusBarRuntimeDisconnected")}
        </span>
        {selectedProjectId && (
          <span className="status-bar-item">
            {t("app.statusBarProject", { id: selectedProjectId.slice(0, 8) + "..." })}
          </span>
        )}
        {pendingCount > 0 && (
          <span className="status-bar-item status-bar-warn">
            {pendingCount > 1
              ? t("app.statusBarPendingApprovals", { count: pendingCount })
              : t("app.statusBarPendingApproval", { count: pendingCount })}
          </span>
        )}
      </footer>

      {/* Command Palette */}
      <CommandPalette
        open={commandPaletteOpen}
        onClose={() => setCommandPaletteOpen(false)}
        onNavigateToProject={(projectId) => {
          setSelectedProjectId(projectId);
          setActiveTab("projects");
        }}
        onNavigateToTasks={(projectId) => {
          setSelectedProjectId(projectId);
          setActiveTab("tasks");
        }}
        onNavigateToApprovals={() => {
          setActiveTab("approvals");
        }}
        onNavigateToSettings={() => {
          setActiveTab("settings");
        }}
        onNavigateToDashboard={() => {
          setActiveTab("dashboard");
        }}
      />
    </div>
  );
}

export default App;
