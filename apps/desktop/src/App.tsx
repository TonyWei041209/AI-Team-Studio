import { useEffect, useState } from 'react';
import './App.css';

const RUNTIME_URL = 'http://127.0.0.1:9800';

interface HealthStatus {
  status: string;
  version: string;
  database: string;
}

type ConnectionState = 'checking' | 'connected' | 'disconnected';

function App() {
  const [connectionState, setConnectionState] = useState<ConnectionState>('checking');
  const [health, setHealth] = useState<HealthStatus | null>(null);

  const checkHealth = async () => {
    try {
      const res = await fetch(`${RUNTIME_URL}/api/health`);
      const data: HealthStatus = await res.json();
      setHealth(data);
      setConnectionState('connected');
    } catch {
      setHealth(null);
      setConnectionState('disconnected');
    }
  };

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 5000);
    return () => clearInterval(interval);
  }, []);

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
        {/* Sidebar placeholder */}
        <nav className="sidebar">
          <div className="sidebar-section">
            <div className="sidebar-label">WORKSPACE</div>
            <SidebarItem icon="&#9632;" label="Projects" active />
            <SidebarItem icon="&#9654;" label="Tasks" />
            <SidebarItem icon="&#9733;" label="Agents" />
          </div>
          <div className="sidebar-section">
            <div className="sidebar-label">SYSTEM</div>
            <SidebarItem icon="&#9776;" label="Logs" />
            <SidebarItem icon="&#10003;" label="Approvals" />
            <SidebarItem icon="&#9881;" label="Settings" />
          </div>
        </nav>

        {/* Content Area */}
        <div className="content-area">
          <div className="welcome-panel">
            <h1>AI Team Studio</h1>
            <p className="welcome-subtitle">Local-first AI Workstation</p>

            <div className="status-card">
              <h2>System Status</h2>
              <div className="status-grid">
                <StatusRow
                  label="Runtime"
                  value={connectionState === 'connected' ? 'Online' : connectionState === 'checking' ? 'Checking...' : 'Offline'}
                  state={connectionState}
                />
                <StatusRow
                  label="Database"
                  value={health?.database ?? 'Unknown'}
                  state={health?.database === 'connected' ? 'connected' : 'disconnected'}
                />
                <StatusRow
                  label="Version"
                  value={health?.version ?? '-'}
                  state="neutral"
                />
              </div>
              {connectionState === 'disconnected' && (
                <div className="status-hint">
                  Start the runtime: <code>cd services/runtime && python main.py</code>
                </div>
              )}
            </div>
          </div>
        </div>
      </main>

      {/* Status Bar */}
      <footer className="status-bar">
        <span className="status-bar-item">Phase 0+1: Skeleton</span>
        <span className="status-bar-item">
          Runtime: {connectionState === 'connected' ? '127.0.0.1:9800' : 'not connected'}
        </span>
      </footer>
    </div>
  );
}

function StatusIndicator({ state }: { state: ConnectionState }) {
  const color =
    state === 'connected' ? 'var(--accent-green)' :
    state === 'checking' ? 'var(--accent-yellow)' :
    'var(--accent-red)';
  const label =
    state === 'connected' ? 'Connected' :
    state === 'checking' ? 'Connecting...' :
    'Disconnected';

  return (
    <div className="status-indicator">
      <span className="status-dot" style={{ backgroundColor: color }} />
      <span className="status-text">{label}</span>
    </div>
  );
}

function SidebarItem({ icon, label, active }: { icon: string; label: string; active?: boolean }) {
  return (
    <div className={`sidebar-item ${active ? 'active' : ''}`}>
      <span className="sidebar-icon">{icon}</span>
      <span>{label}</span>
    </div>
  );
}

function StatusRow({ label, value, state }: { label: string; value: string; state: ConnectionState | 'neutral' }) {
  const color =
    state === 'connected' ? 'var(--accent-green)' :
    state === 'disconnected' ? 'var(--accent-red)' :
    state === 'checking' ? 'var(--accent-yellow)' :
    'var(--text-secondary)';

  return (
    <div className="status-row">
      <span className="status-row-label">{label}</span>
      <span className="status-row-value" style={{ color }}>{value}</span>
    </div>
  );
}

export default App;
