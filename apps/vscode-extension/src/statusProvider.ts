/**
 * Tree data provider for the Status view — shows runtime connection,
 * readiness summary, and key metrics.
 */

import * as vscode from "vscode";
import type { RuntimeClient, HealthStatus, DashboardSummary, ReadinessSummary } from "./runtimeClient";

export class StatusProvider implements vscode.TreeDataProvider<StatusItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<StatusItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private health: HealthStatus | null = null;
  private summary: DashboardSummary | null = null;
  private readiness: ReadinessSummary | null = null;
  private error: string | null = null;

  constructor(private client: RuntimeClient) {}

  refresh(): void {
    this._onDidChangeTreeData.fire(undefined);
  }

  async load(): Promise<void> {
    this.error = null;
    try {
      this.health = await this.client.health();
      this.summary = await this.client.getDashboardSummary();
      this.readiness = await this.client.getReadiness();
    } catch (err) {
      this.error = err instanceof Error ? err.message : "Connection failed";
      this.health = null;
      this.summary = null;
      this.readiness = null;
    }
    this.refresh();
  }

  getTreeItem(element: StatusItem): vscode.TreeItem {
    return element;
  }

  getChildren(): StatusItem[] {
    if (this.error) {
      return [
        new StatusItem("Runtime", `Disconnected`, vscode.TreeItemCollapsibleState.None, "error"),
        new StatusItem("Error", this.error, vscode.TreeItemCollapsibleState.None, "info"),
      ];
    }

    if (!this.health) {
      return [new StatusItem("Runtime", "Checking...", vscode.TreeItemCollapsibleState.None, "loading")];
    }

    const items: StatusItem[] = [
      new StatusItem("Runtime", `Connected (v${this.health.version})`, vscode.TreeItemCollapsibleState.None, "ok"),
      new StatusItem("Database", this.health.database, vscode.TreeItemCollapsibleState.None, "info"),
    ];

    if (this.readiness) {
      const readyLabel = this.readiness.overall_ready ? "Ready" : "Setup Needed";
      items.push(
        new StatusItem(
          "System",
          `${readyLabel} — ${this.readiness.providers.configured}/${this.readiness.providers.total} providers, ${this.readiness.roles.real_model_count}/${this.readiness.roles.total} real models`,
          vscode.TreeItemCollapsibleState.None,
          this.readiness.overall_ready ? "ok" : "warning",
        ),
      );
    }

    if (this.summary) {
      items.push(
        new StatusItem("Projects", `${this.summary.project_count}`, vscode.TreeItemCollapsibleState.None, "info"),
        new StatusItem("Tasks", `${this.summary.total_tasks}`, vscode.TreeItemCollapsibleState.None, "info"),
        new StatusItem("Pending Approvals", `${this.summary.pending_approvals}`, vscode.TreeItemCollapsibleState.None, this.summary.pending_approvals > 0 ? "warning" : "info"),
        new StatusItem("Active Runs", `${this.summary.active_orchestrations}`, vscode.TreeItemCollapsibleState.None, this.summary.active_orchestrations > 0 ? "ok" : "info"),
        new StatusItem("Failed Tasks", `${this.summary.failed_tasks}`, vscode.TreeItemCollapsibleState.None, this.summary.failed_tasks > 0 ? "error" : "info"),
      );
    }

    return items;
  }

  isConnected(): boolean {
    return this.health !== null;
  }

  getSummary(): DashboardSummary | null {
    return this.summary;
  }
}

class StatusItem extends vscode.TreeItem {
  constructor(
    label: string,
    public readonly value: string,
    collapsibleState: vscode.TreeItemCollapsibleState,
    private severity: "ok" | "warning" | "error" | "info" | "loading",
  ) {
    super(label, collapsibleState);
    this.description = value;
    this.iconPath = this.getIcon();
    this.contextValue = "status-item";
  }

  private getIcon(): vscode.ThemeIcon {
    switch (this.severity) {
      case "ok":
        return new vscode.ThemeIcon("check", new vscode.ThemeColor("charts.green"));
      case "warning":
        return new vscode.ThemeIcon("warning", new vscode.ThemeColor("charts.yellow"));
      case "error":
        return new vscode.ThemeIcon("error", new vscode.ThemeColor("charts.red"));
      case "loading":
        return new vscode.ThemeIcon("loading~spin");
      default:
        return new vscode.ThemeIcon("info");
    }
  }
}
