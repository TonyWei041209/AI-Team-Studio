/**
 * Tree data provider for the Tasks view.
 * Shows tasks for the currently selected project.
 */

import * as vscode from "vscode";
import type { RuntimeClient, Task } from "./runtimeClient";

const STATUS_ICONS: Record<string, { icon: string; color: string }> = {
  pending: { icon: "circle-outline", color: "charts.foreground" },
  planning: { icon: "loading~spin", color: "charts.blue" },
  in_progress: { icon: "play-circle", color: "charts.yellow" },
  reviewing: { icon: "eye", color: "charts.blue" },
  done: { icon: "check-all", color: "charts.green" },
  failed: { icon: "error", color: "charts.red" },
};

const PRIORITY_ICONS: Record<string, string> = {
  low: "arrow-down",
  medium: "dash",
  high: "arrow-up",
  critical: "flame",
};

export class TasksProvider implements vscode.TreeDataProvider<TaskItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<TaskItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private tasks: Task[] = [];
  private error: string | null = null;
  private projectId: string | null = null;

  constructor(private client: RuntimeClient) {}

  refresh(): void {
    this._onDidChangeTreeData.fire(undefined);
  }

  setProjectId(projectId: string | null): void {
    this.projectId = projectId;
  }

  async load(): Promise<void> {
    this.error = null;
    if (!this.projectId) {
      this.tasks = [];
      this.refresh();
      return;
    }
    try {
      this.tasks = await this.client.listTasks(this.projectId);
    } catch (err) {
      this.error = err instanceof Error ? err.message : "Failed to load tasks";
      this.tasks = [];
    }
    this.refresh();
  }

  getTreeItem(element: TaskItem): vscode.TreeItem {
    return element;
  }

  getChildren(): TaskItem[] {
    if (!this.projectId) {
      const item = new vscode.TreeItem("Select a project first");
      item.iconPath = new vscode.ThemeIcon("info");
      return [item as TaskItem];
    }

    if (this.error) {
      const item = new vscode.TreeItem(this.error);
      item.iconPath = new vscode.ThemeIcon("error", new vscode.ThemeColor("charts.red"));
      return [item as TaskItem];
    }

    if (this.tasks.length === 0) {
      const item = new vscode.TreeItem("No tasks for this project");
      item.iconPath = new vscode.ThemeIcon("info");
      return [item as TaskItem];
    }

    return this.tasks.map((t) => new TaskItem(t));
  }
}

export class TaskItem extends vscode.TreeItem {
  constructor(public readonly task: Task) {
    super(task.title, vscode.TreeItemCollapsibleState.None);

    const statusInfo = STATUS_ICONS[task.status] || { icon: "question", color: "charts.foreground" };
    const priorityIcon = PRIORITY_ICONS[task.priority] || "dash";

    this.description = `${task.status} · ${task.priority}`;
    this.tooltip = `${task.title}\nStatus: ${task.status}\nPriority: ${task.priority}\n${task.description || ""}`;
    this.iconPath = new vscode.ThemeIcon(statusInfo.icon, new vscode.ThemeColor(statusInfo.color));

    // Context value controls which commands appear in context menu
    this.contextValue = task.status === "pending" ? "task-pending" : `task-${task.status}`;
  }
}
