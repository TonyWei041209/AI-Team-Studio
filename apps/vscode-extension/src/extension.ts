/**
 * AI Team Studio VS Code Extension — main entry point.
 *
 * Provides a sidebar companion for the desktop workstation:
 * - Runtime connection status
 * - Projects & tasks tree views
 * - Orchestration trigger for pending tasks
 * - Status bar with key metrics
 *
 * All operations go through the existing HTTP API.
 * No new write paths. No safety boundary changes.
 */

import * as vscode from "vscode";
import { RuntimeClient } from "./runtimeClient";
import { StatusProvider } from "./statusProvider";
import { ProjectsProvider } from "./projectsProvider";
import { TasksProvider } from "./tasksProvider";

let refreshInterval: ReturnType<typeof setInterval> | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("aiTeamStudio");
  const runtimeUrl = config.get<string>("runtimeUrl", "http://127.0.0.1:9800");
  const intervalMs = config.get<number>("refreshInterval", 15000);

  const client = new RuntimeClient(runtimeUrl);

  // ── Tree data providers ──

  const statusProvider = new StatusProvider(client);
  const projectsProvider = new ProjectsProvider(client);
  const tasksProvider = new TasksProvider(client);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("ats.status", statusProvider),
    vscode.window.registerTreeDataProvider("ats.projects", projectsProvider),
    vscode.window.registerTreeDataProvider("ats.tasks", tasksProvider),
  );

  // ── Status bar item ──

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusBar.command = "ats.checkConnection";
  statusBar.text = "$(loading~spin) ATS";
  statusBar.tooltip = "AI Team Studio — checking connection...";
  statusBar.show();
  context.subscriptions.push(statusBar);

  function updateStatusBar(): void {
    if (statusProvider.isConnected()) {
      const summary = statusProvider.getSummary();
      const parts = ["$(check) ATS"];
      if (summary) {
        if (summary.pending_approvals > 0) {
          parts.push(`$(bell) ${summary.pending_approvals}`);
        }
        if (summary.active_orchestrations > 0) {
          parts.push(`$(play) ${summary.active_orchestrations}`);
        }
        if (summary.failed_tasks > 0) {
          parts.push(`$(error) ${summary.failed_tasks}`);
        }
      }
      statusBar.text = parts.join("  ");
      statusBar.tooltip = "AI Team Studio — Connected";
      statusBar.backgroundColor = undefined;
    } else {
      statusBar.text = "$(warning) ATS Disconnected";
      statusBar.tooltip = "AI Team Studio — Runtime not connected";
      statusBar.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
    }
  }

  // ── Refresh all views ──

  async function refreshAll(): Promise<void> {
    await statusProvider.load();
    await projectsProvider.load();
    if (projectsProvider.getSelectedProjectId()) {
      tasksProvider.setProjectId(projectsProvider.getSelectedProjectId());
      await tasksProvider.load();
    }
    updateStatusBar();
  }

  // ── Commands ──

  context.subscriptions.push(
    vscode.commands.registerCommand("ats.refresh", () => refreshAll()),

    vscode.commands.registerCommand("ats.selectProject", async (projectId: string) => {
      projectsProvider.selectProject(projectId);
      tasksProvider.setProjectId(projectId);
      await tasksProvider.load();

      // Find project name for message
      const project = projectsProvider.getProjects().find((p) => p.id === projectId);
      if (project) {
        vscode.window.setStatusBarMessage(`AI Team Studio: Selected project "${project.name}"`, 3000);
      }
    }),

    vscode.commands.registerCommand("ats.orchestrateTask", async (item: { task?: { id: string; title: string } }) => {
      const task = item?.task;
      if (!task) {
        vscode.window.showWarningMessage("No task selected.");
        return;
      }

      const confirm = await vscode.window.showInformationMessage(
        `Start orchestration for "${task.title}"?`,
        { modal: true },
        "Start",
      );
      if (confirm !== "Start") {
        return;
      }

      try {
        await client.orchestrateTask(task.id);
        vscode.window.showInformationMessage(`Orchestration started for "${task.title}".`);
        await refreshAll();
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Orchestration failed";
        vscode.window.showErrorMessage(`Orchestration failed: ${msg}`);
      }
    }),

    vscode.commands.registerCommand("ats.openInDesktop", () => {
      vscode.window.showInformationMessage(
        "Open the AI Team Studio desktop app to access the full workstation UI.",
      );
    }),

    vscode.commands.registerCommand("ats.checkConnection", async () => {
      try {
        const health = await client.health();
        vscode.window.showInformationMessage(
          `AI Team Studio runtime connected: v${health.version}, database: ${health.database}`,
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Connection failed";
        vscode.window.showErrorMessage(
          `Cannot connect to AI Team Studio runtime at ${runtimeUrl}: ${msg}`,
        );
      }
    }),
  );

  // ── Configuration change listener ──

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("aiTeamStudio.runtimeUrl")) {
        const newUrl = vscode.workspace
          .getConfiguration("aiTeamStudio")
          .get<string>("runtimeUrl", "http://127.0.0.1:9800");
        client.setBaseUrl(newUrl);
        refreshAll();
      }
    }),
  );

  // ── Initial load ──

  refreshAll();

  // ── Auto-refresh ──

  if (intervalMs > 0) {
    refreshInterval = setInterval(() => refreshAll(), intervalMs);
    context.subscriptions.push({
      dispose: () => {
        if (refreshInterval) {
          clearInterval(refreshInterval);
        }
      },
    });
  }
}

export function deactivate(): void {
  if (refreshInterval) {
    clearInterval(refreshInterval);
    refreshInterval = undefined;
  }
}
