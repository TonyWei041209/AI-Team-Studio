"use strict";
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
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const runtimeClient_1 = require("./runtimeClient");
const statusProvider_1 = require("./statusProvider");
const projectsProvider_1 = require("./projectsProvider");
const tasksProvider_1 = require("./tasksProvider");
let refreshInterval;
function activate(context) {
    const config = vscode.workspace.getConfiguration("aiTeamStudio");
    const runtimeUrl = config.get("runtimeUrl", "http://127.0.0.1:9800");
    const intervalMs = config.get("refreshInterval", 15000);
    const client = new runtimeClient_1.RuntimeClient(runtimeUrl);
    // ── Tree data providers ──
    const statusProvider = new statusProvider_1.StatusProvider(client);
    const projectsProvider = new projectsProvider_1.ProjectsProvider(client);
    const tasksProvider = new tasksProvider_1.TasksProvider(client);
    context.subscriptions.push(vscode.window.registerTreeDataProvider("ats.status", statusProvider), vscode.window.registerTreeDataProvider("ats.projects", projectsProvider), vscode.window.registerTreeDataProvider("ats.tasks", tasksProvider));
    // ── Status bar item ──
    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    statusBar.command = "ats.checkConnection";
    statusBar.text = "$(loading~spin) ATS";
    statusBar.tooltip = "AI Team Studio — checking connection...";
    statusBar.show();
    context.subscriptions.push(statusBar);
    function updateStatusBar() {
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
        }
        else {
            statusBar.text = "$(warning) ATS Disconnected";
            statusBar.tooltip = "AI Team Studio — Runtime not connected";
            statusBar.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
        }
    }
    // ── Refresh all views ──
    async function refreshAll() {
        await statusProvider.load();
        await projectsProvider.load();
        if (projectsProvider.getSelectedProjectId()) {
            tasksProvider.setProjectId(projectsProvider.getSelectedProjectId());
            await tasksProvider.load();
        }
        updateStatusBar();
    }
    // ── Commands ──
    context.subscriptions.push(vscode.commands.registerCommand("ats.refresh", () => refreshAll()), vscode.commands.registerCommand("ats.selectProject", async (projectId) => {
        projectsProvider.selectProject(projectId);
        tasksProvider.setProjectId(projectId);
        await tasksProvider.load();
        // Find project name for message
        const project = projectsProvider.getProjects().find((p) => p.id === projectId);
        if (project) {
            vscode.window.setStatusBarMessage(`AI Team Studio: Selected project "${project.name}"`, 3000);
        }
    }), vscode.commands.registerCommand("ats.orchestrateTask", async (item) => {
        const task = item?.task;
        if (!task) {
            vscode.window.showWarningMessage("No task selected.");
            return;
        }
        const confirm = await vscode.window.showInformationMessage(`Start orchestration for "${task.title}"?`, { modal: true }, "Start");
        if (confirm !== "Start") {
            return;
        }
        try {
            await client.orchestrateTask(task.id);
            vscode.window.showInformationMessage(`Orchestration started for "${task.title}".`);
            await refreshAll();
        }
        catch (err) {
            const msg = err instanceof Error ? err.message : "Orchestration failed";
            vscode.window.showErrorMessage(`Orchestration failed: ${msg}`);
        }
    }), vscode.commands.registerCommand("ats.openInDesktop", () => {
        vscode.window.showInformationMessage("Open the AI Team Studio desktop app to access the full workstation UI.");
    }), vscode.commands.registerCommand("ats.checkConnection", async () => {
        try {
            const health = await client.health();
            vscode.window.showInformationMessage(`AI Team Studio runtime connected: v${health.version}, database: ${health.database}`);
        }
        catch (err) {
            const msg = err instanceof Error ? err.message : "Connection failed";
            vscode.window.showErrorMessage(`Cannot connect to AI Team Studio runtime at ${runtimeUrl}: ${msg}`);
        }
    }));
    // ── Configuration change listener ──
    context.subscriptions.push(vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("aiTeamStudio.runtimeUrl")) {
            const newUrl = vscode.workspace
                .getConfiguration("aiTeamStudio")
                .get("runtimeUrl", "http://127.0.0.1:9800");
            client.setBaseUrl(newUrl);
            refreshAll();
        }
    }));
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
function deactivate() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = undefined;
    }
}
//# sourceMappingURL=extension.js.map