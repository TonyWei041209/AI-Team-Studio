import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { searchApi } from "../api/search";
import type { SearchResults } from "../api/search";
import "./CommandPalette.css";

// ── Recent items ──────────────────────────────────────────────────────────────

const RECENTS_KEY = "ats-command-palette-recents";
const MAX_RECENTS = 8;

interface RecentItem {
  id: string;
  type: "project" | "task" | "approval";
  label: string;
  sublabel?: string;
  projectId?: string; // for task navigation
  timestamp: number;
}

function getRecents(): RecentItem[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as RecentItem[];
  } catch {
    return [];
  }
}

function addRecent(item: RecentItem): void {
  const recents = getRecents().filter((r) => r.id !== item.id);
  recents.unshift({ ...item, timestamp: Date.now() });
  if (recents.length > MAX_RECENTS) recents.length = MAX_RECENTS;
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(recents));
  } catch {
    // localStorage full — silently ignore
  }
}

function typeIcon(type: string): string {
  switch (type) {
    case "project":
      return "\u25A0"; // ■
    case "task":
      return "\u25B6"; // ▶
    case "approval":
      return "\u2713"; // ✓
    default:
      return "\u25CB"; // ○
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  onNavigateToProject?: (projectId: string) => void;
  onNavigateToTasks?: (projectId: string) => void;
  onNavigateToApprovals?: () => void;
  onNavigateToSettings?: () => void;
  onNavigateToDashboard?: () => void;
}

interface FlatItem {
  id: string;
  type: "project" | "task" | "approval" | "nav";
  action: () => void;
}

export function CommandPalette({
  open,
  onClose,
  onNavigateToProject,
  onNavigateToTasks,
  onNavigateToApprovals,
  onNavigateToSettings,
  onNavigateToDashboard,
}: CommandPaletteProps) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [recents, setRecents] = useState<RecentItem[]>([]);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectedItemRef = useRef<HTMLDivElement | null>(null);

  // Focus input when opened; load recents; reset state
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50);
      setRecents(getRecents());
      setQuery("");
      setResults(null);
      setError(null);
      setSelectedIndex(0);
    }
  }, [open]);

  // Debounced search
  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await searchApi.search(q);
      setResults(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleInputChange = (value: string) => {
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(value), 300);
  };

  // Navigate helpers that also record recents
  const navigateToProject = useCallback(
    (id: string, label: string, sublabel?: string) => {
      addRecent({ id, type: "project", label, sublabel, timestamp: Date.now() });
      onNavigateToProject?.(id);
      onClose();
    },
    [onNavigateToProject, onClose]
  );

  const navigateToTask = useCallback(
    (id: string, label: string, sublabel: string | undefined, projectId: string) => {
      addRecent({ id, type: "task", label, sublabel, projectId, timestamp: Date.now() });
      onNavigateToTasks?.(projectId);
      onClose();
    },
    [onNavigateToTasks, onClose]
  );

  const navigateToApprovals = useCallback(
    (id: string, label: string, sublabel?: string) => {
      addRecent({ id, type: "approval", label, sublabel, timestamp: Date.now() });
      onNavigateToApprovals?.();
      onClose();
    },
    [onNavigateToApprovals, onClose]
  );

  // Handle click on a recent item
  const handleRecentClick = useCallback(
    (r: RecentItem) => {
      // Re-add to bump timestamp to top
      addRecent({ ...r, timestamp: Date.now() });
      if (r.type === "project") {
        onNavigateToProject?.(r.id);
      } else if (r.type === "task" && r.projectId) {
        onNavigateToTasks?.(r.projectId);
      } else if (r.type === "approval") {
        onNavigateToApprovals?.();
      }
      onClose();
    },
    [onNavigateToProject, onNavigateToTasks, onNavigateToApprovals, onClose]
  );

  const hasQuery = query.trim().length > 0;

  // Quick navigation items (always available when no query)
  const quickNavItems = useMemo<FlatItem[]>(() => {
    const items: FlatItem[] = [];
    if (onNavigateToDashboard) {
      items.push({ id: "_nav_dashboard", type: "nav", action: () => { onNavigateToDashboard(); onClose(); } });
    }
    if (onNavigateToSettings) {
      items.push({ id: "_nav_settings", type: "nav", action: () => { onNavigateToSettings(); onClose(); } });
    }
    if (onNavigateToApprovals) {
      items.push({ id: "_nav_approvals", type: "nav", action: () => { onNavigateToApprovals(); onClose(); } });
    }
    return items;
  }, [onNavigateToDashboard, onNavigateToSettings, onNavigateToApprovals, onClose]);

  // Build flat items list — from search results when query is active,
  // from recents + quick nav when query is empty.
  const flatItems = useMemo<FlatItem[]>(() => {
    if (!hasQuery) {
      // Recents + quick nav
      const items: FlatItem[] = recents.map((r) => ({
        id: r.id,
        type: r.type,
        action: () => handleRecentClick(r),
      }));
      return [...items, ...quickNavItems];
    }
    if (!results) return [];
    const items: FlatItem[] = [];
    for (const p of results.projects) {
      items.push({
        id: p.id,
        type: "project",
        action: () => navigateToProject(p.id, p.name, p.description ?? undefined),
      });
    }
    for (const task of results.tasks) {
      items.push({
        id: task.id,
        type: "task",
        action: () =>
          navigateToTask(task.id, task.title, task.project_name, task.project_id),
      });
    }
    for (const a of results.approvals) {
      items.push({
        id: a.id,
        type: "approval",
        action: () => navigateToApprovals(a.id, a.task_title, a.project_name),
      });
    }
    return items;
  }, [hasQuery, recents, results, navigateToProject, navigateToTask, navigateToApprovals, handleRecentClick, quickNavItems]);

  // Reset selectedIndex when flat items change
  useEffect(() => {
    setSelectedIndex(0);
  }, [flatItems]);

  // Scroll selected item into view
  useEffect(() => {
    selectedItemRef.current?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  // Keyboard handler
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (!flatItems.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % flatItems.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + flatItems.length) % flatItems.length);
      } else if (e.key === "Enter") {
        e.preventDefault();
        flatItems[selectedIndex]?.action();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose, flatItems, selectedIndex]);

  // Click backdrop to close
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose();
  };

  if (!open) return null;

  const hasResults =
    results &&
    (results.projects.length > 0 ||
      results.tasks.length > 0 ||
      results.approvals.length > 0);

  const totalCount = flatItems.length;

  // Compute flat index offset for each section to assign selectedIndex correctly
  const projectOffset = 0;
  const taskOffset = results ? results.projects.length : 0;
  const approvalOffset = results ? results.projects.length + results.tasks.length : 0;

  return (
    <div className="cmd-palette-backdrop" onClick={handleBackdropClick}>
      <div className="cmd-palette">
        <div className="cmd-palette-header">
          <span className="cmd-palette-icon">&#128269;</span>
          <input
            ref={inputRef}
            className="cmd-palette-input"
            type="text"
            value={query}
            onChange={(e) => handleInputChange(e.target.value)}
            placeholder={t("commandPalette.placeholder")}
            autoComplete="off"
            spellCheck={false}
          />
          <kbd className="cmd-palette-kbd">ESC</kbd>
        </div>

        <div className="cmd-palette-body">
          {loading && (
            <div className="cmd-palette-loading">
              {t("commandPalette.searching")}
            </div>
          )}

          {error && (
            <div className="cmd-palette-error">
              <span>{error}</span>
              <button className="cmd-palette-retry-btn" onClick={() => doSearch(query)}>
                {t("commandPalette.retry")}
              </button>
            </div>
          )}

          {/* Recents — shown when query is empty and there are recents */}
          {!loading && !error && !hasQuery && recents.length > 0 && (
            <div className="cmd-palette-section">
              <div className="cmd-palette-section-title">
                {t("commandPalette.recent")}
              </div>
              {recents.map((r, i) => {
                const isSelected = i === selectedIndex;
                return (
                  <div
                    key={r.id}
                    ref={isSelected ? selectedItemRef : null}
                    className={`cmd-palette-item${isSelected ? " cmd-palette-item-selected" : ""}`}
                    onClick={() => handleRecentClick(r)}
                  >
                    <span className="cmd-palette-item-icon">{typeIcon(r.type)}</span>
                    <div className="cmd-palette-item-content">
                      <div className="cmd-palette-item-title">{r.label}</div>
                      {r.sublabel && (
                        <div className="cmd-palette-item-subtitle">{r.sublabel}</div>
                      )}
                    </div>
                    <span className="cmd-palette-item-type-badge">{r.type}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Quick navigation — shown when query is empty */}
          {!loading && !error && !hasQuery && quickNavItems.length > 0 && (
            <div className="cmd-palette-section">
              <div className="cmd-palette-section-title">
                {t("commandPalette.quickNav")}
              </div>
              {quickNavItems.map((nav, i) => {
                const flatIdx = recents.length + i;
                const isSelected = flatIdx === selectedIndex;
                const navLabel = nav.id === "_nav_dashboard" ? t("commandPalette.navDashboard")
                  : nav.id === "_nav_settings" ? t("commandPalette.navSettings")
                  : t("commandPalette.navApprovals");
                const navIcon = nav.id === "_nav_dashboard" ? "\u2302"
                  : nav.id === "_nav_settings" ? "\u2699"
                  : "\u2713";
                return (
                  <div
                    key={nav.id}
                    ref={isSelected ? selectedItemRef : null}
                    className={`cmd-palette-item${isSelected ? " cmd-palette-item-selected" : ""}`}
                    onClick={nav.action}
                  >
                    <span className="cmd-palette-item-icon">{navIcon}</span>
                    <div className="cmd-palette-item-content">
                      <div className="cmd-palette-item-title">{navLabel}</div>
                    </div>
                    <span className="cmd-palette-item-type-badge">nav</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Hint — shown when query is empty and no recents and no quick nav */}
          {!loading && !error && !hasQuery && recents.length === 0 && quickNavItems.length === 0 && (
            <div className="cmd-palette-hint">{t("commandPalette.hint")}</div>
          )}

          {!loading && !error && hasQuery && !hasResults && (
            <div className="cmd-palette-empty">
              {t("commandPalette.noResultsFor", { query })}
            </div>
          )}

          {!loading && hasResults && results && (
            <>
              {/* Projects */}
              {results.projects.length > 0 && (
                <div className="cmd-palette-section">
                  <div className="cmd-palette-section-title">
                    {t("commandPalette.projects")}
                    <span className="cmd-palette-section-count">{results.projects.length}</span>
                  </div>
                  {results.projects.map((p, i) => {
                    const flatIdx = projectOffset + i;
                    const isSelected = flatIdx === selectedIndex;
                    return (
                      <div
                        key={p.id}
                        ref={isSelected ? selectedItemRef : null}
                        className={`cmd-palette-item${isSelected ? " cmd-palette-item-selected" : ""}`}
                        onClick={() =>
                          navigateToProject(p.id, p.name, p.description ?? undefined)
                        }
                      >
                        <span className="cmd-palette-item-icon">&#9632;</span>
                        <div className="cmd-palette-item-content">
                          <div className="cmd-palette-item-title">{p.name}</div>
                          {p.description && (
                            <div className="cmd-palette-item-subtitle">
                              {p.description}
                            </div>
                          )}
                          {p.local_repo_path && (
                            <div className="cmd-palette-item-path">{p.local_repo_path}</div>
                          )}
                        </div>
                        <span className="cmd-palette-item-action">{t("commandPalette.openProject")}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Tasks */}
              {results.tasks.length > 0 && (
                <div className="cmd-palette-section">
                  <div className="cmd-palette-section-title">
                    {t("commandPalette.tasks")}
                    <span className="cmd-palette-section-count">{results.tasks.length}</span>
                  </div>
                  {results.tasks.map((task, i) => {
                    const flatIdx = taskOffset + i;
                    const isSelected = flatIdx === selectedIndex;
                    return (
                      <div
                        key={task.id}
                        ref={isSelected ? selectedItemRef : null}
                        className={`cmd-palette-item${isSelected ? " cmd-palette-item-selected" : ""}`}
                        onClick={() =>
                          navigateToTask(
                            task.id,
                            task.title,
                            task.project_name,
                            task.project_id
                          )
                        }
                      >
                        <span className="cmd-palette-item-icon">&#9654;</span>
                        <div className="cmd-palette-item-content">
                          <div className="cmd-palette-item-title">
                            {task.title}
                          </div>
                          <div className="cmd-palette-item-meta">
                            <span
                              className="cmd-palette-item-status"
                              data-status={task.status}
                            >
                              {t(`commandPalette.taskStatus_${task.status}`, task.status)}
                            </span>
                            <span
                              className="cmd-palette-item-priority"
                              data-priority={task.priority}
                            >
                              {task.priority}
                            </span>
                            <span className="cmd-palette-item-project">
                              {task.project_name}
                            </span>
                          </div>
                        </div>
                        <span className="cmd-palette-item-action">{t("commandPalette.openTask")}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Approvals */}
              {results.approvals.length > 0 && (
                <div className="cmd-palette-section">
                  <div className="cmd-palette-section-title">
                    {t("commandPalette.approvals")}
                    <span className="cmd-palette-section-count">{results.approvals.length}</span>
                  </div>
                  {results.approvals.map((a, i) => {
                    const flatIdx = approvalOffset + i;
                    const isSelected = flatIdx === selectedIndex;
                    return (
                      <div
                        key={a.id}
                        ref={isSelected ? selectedItemRef : null}
                        className={`cmd-palette-item${isSelected ? " cmd-palette-item-selected" : ""}`}
                        onClick={() =>
                          navigateToApprovals(a.id, a.task_title, a.project_name)
                        }
                      >
                        <span className="cmd-palette-item-icon">&#10003;</span>
                        <div className="cmd-palette-item-content">
                          <div className="cmd-palette-item-title">
                            {a.task_title}
                          </div>
                          <div className="cmd-palette-item-meta">
                            <span
                              className="cmd-palette-item-badge"
                              data-status={a.status}
                            >
                              {t(`commandPalette.taskStatus_${a.status}`, a.status)}
                            </span>
                            <span className="cmd-palette-item-type">
                              {a.action_type}
                            </span>
                            <span className="cmd-palette-item-project">
                              {a.project_name}
                            </span>
                          </div>
                        </div>
                        <span className="cmd-palette-item-action">{t("commandPalette.openApprovals")}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        {(hasResults || (!hasQuery && recents.length > 0)) && (
          <div className="cmd-palette-footer">
            <span className="cmd-palette-footer-count">
              {t("commandPalette.resultCount", { count: totalCount })}
            </span>
            <span className="cmd-palette-footer-hints">
              <kbd>&#8593;&#8595;</kbd> {t("commandPalette.navigate")}
              <kbd>&#8629;</kbd> {t("commandPalette.select")}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
