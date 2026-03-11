import { useEffect, useState, useCallback, useRef } from "react";
import type { TabId, ConnectionState, HealthStatus } from "./types/api";
import { StatusIndicator } from "./components/StatusIndicator";
import { ProjectPanel } from "./panels/ProjectPanel";
import { TaskBoard } from "./panels/TaskBoard";
import { ApprovalsPanel } from "./panels/ApprovalsPanel";
import { LogsPanel } from "./panels/LogsPanel";
import { approvalsApi } from "./api/approvals";
import "./App.css";

const RUNTIME_URL = "http://127.0.0.1:9800";

/** Sidebar tab definitions */
const WORKSPACE_TABS: Array<{ id: TabId; icon: string; label: string }> = [
  { id: "projects", icon: "\u25A0", label: "Projects" },
  { id: "tasks", icon: "\u25B6", label: "Tasks" },
];

const SYSTEM_TABS: Array<{ id: TabId; icon: string; label: string }> = [
  { id: "approvals", icon: "\u2713", label: "Approvals" },
  { id: "logs", icon: "\u2261", label: "Logs" },
];

function App() {
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("checking");
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("projects");
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(
    null,
  );
  const [pendingCount, setPendingCount] = useState(0);

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
    const interval = setInterval(fetchCount, 10000);
    return () => {
      countMountedRef.current = false;
      clearInterval(interval);
    };
  }, []);

  // ── Project selection handler ───────────────────────
  const handleSelectProject = useCallback((id: string) => {
    setSelectedProjectId(id);
  }, []);

  // ── Render active panel ─────────────────────────────
  const renderPanel = () => {
    switch (activeTab) {
      case "projects":
        return (
          <ProjectPanel
            selectedProjectId={selectedProjectId}
            onSelectProject={handleSelectProject}
          />
        );
      case "tasks":
        return <TaskBoard projectId={selectedProjectId} />;
      case "approvals":
        return <ApprovalsPanel onCountChange={setPendingCount} />;
      case "logs":
        return <LogsPanel />;
    }
  };

  return (
    <div className="app-container">
      {/* Title Bar */}
      <header className="title-bar" data-tauri-drag-region>
        <div className="title-bar-left">
          <span className="app-logo">&#9670;</span>
          <span className="app-name">AI Team Studio</span>
          <span className="app-version">v0.1.0</span>
        </div>
        <div className="title-bar-right">
          <StatusIndicator state={connectionState} />
        </div>
      </header>

      {/* Main Content */}
      <main className="main-content">
        {/* Sidebar */}
        <nav className="sidebar">
          <div className="sidebar-section">
            <div className="sidebar-label">WORKSPACE</div>
            {WORKSPACE_TABS.map((tab) => (
              <div
                key={tab.id}
                className={`sidebar-item ${activeTab === tab.id ? "active" : ""}`}
                onClick={() => setActiveTab(tab.id)}
              >
                <span className="sidebar-icon">{tab.icon}</span>
                <span>{tab.label}</span>
              </div>
            ))}
          </div>
          <div className="sidebar-section">
            <div className="sidebar-label">SYSTEM</div>
            {SYSTEM_TABS.map((tab) => (
              <div
                key={tab.id}
                className={`sidebar-item ${activeTab === tab.id ? "active" : ""}`}
                onClick={() => setActiveTab(tab.id)}
              >
                <span className="sidebar-icon">{tab.icon}</span>
                <span>{tab.label}</span>
                {tab.id === "approvals" && pendingCount > 0 && (
                  <span className="sidebar-badge">{pendingCount}</span>
                )}
              </div>
            ))}
          </div>
        </nav>

        {/* Content Area */}
        <div className="content-area">
          {connectionState === "disconnected" && (
            <div className="connection-banner">
              <span>&#9888; Runtime not connected.</span>
              <code>cd services/runtime && python main.py</code>
            </div>
          )}
          {renderPanel()}
        </div>
      </main>

      {/* Status Bar */}
      <footer className="status-bar">
        <span className="status-bar-item">Phase 5: Frontend</span>
        <span className="status-bar-item">
          Runtime:{" "}
          {connectionState === "connected"
            ? `${health?.version ?? "?"} @ 127.0.0.1:9800`
            : "not connected"}
        </span>
        {selectedProjectId && (
          <span className="status-bar-item">
            Project: {selectedProjectId.slice(0, 8)}...
          </span>
        )}
        {pendingCount > 0 && (
          <span className="status-bar-item status-bar-warn">
            {pendingCount} pending approval{pendingCount > 1 ? "s" : ""}
          </span>
        )}
      </footer>
    </div>
  );
}

export default App;
