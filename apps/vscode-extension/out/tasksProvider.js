"use strict";
/**
 * Tree data provider for the Tasks view.
 * Shows tasks for the currently selected project.
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
exports.TaskItem = exports.TasksProvider = void 0;
const vscode = __importStar(require("vscode"));
const STATUS_ICONS = {
    pending: { icon: "circle-outline", color: "charts.foreground" },
    planning: { icon: "loading~spin", color: "charts.blue" },
    in_progress: { icon: "play-circle", color: "charts.yellow" },
    reviewing: { icon: "eye", color: "charts.blue" },
    done: { icon: "check-all", color: "charts.green" },
    failed: { icon: "error", color: "charts.red" },
};
const PRIORITY_ICONS = {
    low: "arrow-down",
    medium: "dash",
    high: "arrow-up",
    critical: "flame",
};
class TasksProvider {
    client;
    _onDidChangeTreeData = new vscode.EventEmitter();
    onDidChangeTreeData = this._onDidChangeTreeData.event;
    tasks = [];
    error = null;
    projectId = null;
    constructor(client) {
        this.client = client;
    }
    refresh() {
        this._onDidChangeTreeData.fire(undefined);
    }
    setProjectId(projectId) {
        this.projectId = projectId;
    }
    async load() {
        this.error = null;
        if (!this.projectId) {
            this.tasks = [];
            this.refresh();
            return;
        }
        try {
            this.tasks = await this.client.listTasks(this.projectId);
        }
        catch (err) {
            this.error = err instanceof Error ? err.message : "Failed to load tasks";
            this.tasks = [];
        }
        this.refresh();
    }
    getTreeItem(element) {
        return element;
    }
    getChildren() {
        if (!this.projectId) {
            const item = new vscode.TreeItem("Select a project first");
            item.iconPath = new vscode.ThemeIcon("info");
            return [item];
        }
        if (this.error) {
            const item = new vscode.TreeItem(this.error);
            item.iconPath = new vscode.ThemeIcon("error", new vscode.ThemeColor("charts.red"));
            return [item];
        }
        if (this.tasks.length === 0) {
            const item = new vscode.TreeItem("No tasks for this project");
            item.iconPath = new vscode.ThemeIcon("info");
            return [item];
        }
        return this.tasks.map((t) => new TaskItem(t));
    }
}
exports.TasksProvider = TasksProvider;
class TaskItem extends vscode.TreeItem {
    task;
    constructor(task) {
        super(task.title, vscode.TreeItemCollapsibleState.None);
        this.task = task;
        const statusInfo = STATUS_ICONS[task.status] || { icon: "question", color: "charts.foreground" };
        const priorityIcon = PRIORITY_ICONS[task.priority] || "dash";
        this.description = `${task.status} · ${task.priority}`;
        this.tooltip = `${task.title}\nStatus: ${task.status}\nPriority: ${task.priority}\n${task.description || ""}`;
        this.iconPath = new vscode.ThemeIcon(statusInfo.icon, new vscode.ThemeColor(statusInfo.color));
        // Context value controls which commands appear in context menu
        this.contextValue = task.status === "pending" ? "task-pending" : `task-${task.status}`;
    }
}
exports.TaskItem = TaskItem;
//# sourceMappingURL=tasksProvider.js.map