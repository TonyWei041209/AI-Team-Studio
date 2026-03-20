"use strict";
/**
 * Tree data provider for the Status view — shows runtime connection,
 * readiness summary, and key metrics.
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
exports.StatusProvider = void 0;
const vscode = __importStar(require("vscode"));
class StatusProvider {
    client;
    _onDidChangeTreeData = new vscode.EventEmitter();
    onDidChangeTreeData = this._onDidChangeTreeData.event;
    health = null;
    summary = null;
    readiness = null;
    error = null;
    constructor(client) {
        this.client = client;
    }
    refresh() {
        this._onDidChangeTreeData.fire(undefined);
    }
    async load() {
        this.error = null;
        try {
            this.health = await this.client.health();
            this.summary = await this.client.getDashboardSummary();
            this.readiness = await this.client.getReadiness();
        }
        catch (err) {
            this.error = err instanceof Error ? err.message : "Connection failed";
            this.health = null;
            this.summary = null;
            this.readiness = null;
        }
        this.refresh();
    }
    getTreeItem(element) {
        return element;
    }
    getChildren() {
        if (this.error) {
            return [
                new StatusItem("Runtime", `Disconnected`, vscode.TreeItemCollapsibleState.None, "error"),
                new StatusItem("Error", this.error, vscode.TreeItemCollapsibleState.None, "info"),
            ];
        }
        if (!this.health) {
            return [new StatusItem("Runtime", "Checking...", vscode.TreeItemCollapsibleState.None, "loading")];
        }
        const items = [
            new StatusItem("Runtime", `Connected (v${this.health.version})`, vscode.TreeItemCollapsibleState.None, "ok"),
            new StatusItem("Database", this.health.database, vscode.TreeItemCollapsibleState.None, "info"),
        ];
        if (this.readiness) {
            const readyLabel = this.readiness.overall_ready ? "Ready" : "Setup Needed";
            items.push(new StatusItem("System", `${readyLabel} — ${this.readiness.providers.configured}/${this.readiness.providers.total} providers, ${this.readiness.roles.real_model_count}/${this.readiness.roles.total} real models`, vscode.TreeItemCollapsibleState.None, this.readiness.overall_ready ? "ok" : "warning"));
        }
        if (this.summary) {
            items.push(new StatusItem("Projects", `${this.summary.project_count}`, vscode.TreeItemCollapsibleState.None, "info"), new StatusItem("Tasks", `${this.summary.total_tasks}`, vscode.TreeItemCollapsibleState.None, "info"), new StatusItem("Pending Approvals", `${this.summary.pending_approvals}`, vscode.TreeItemCollapsibleState.None, this.summary.pending_approvals > 0 ? "warning" : "info"), new StatusItem("Active Runs", `${this.summary.active_orchestrations}`, vscode.TreeItemCollapsibleState.None, this.summary.active_orchestrations > 0 ? "ok" : "info"), new StatusItem("Failed Tasks", `${this.summary.failed_tasks}`, vscode.TreeItemCollapsibleState.None, this.summary.failed_tasks > 0 ? "error" : "info"));
        }
        return items;
    }
    isConnected() {
        return this.health !== null;
    }
    getSummary() {
        return this.summary;
    }
}
exports.StatusProvider = StatusProvider;
class StatusItem extends vscode.TreeItem {
    value;
    severity;
    constructor(label, value, collapsibleState, severity) {
        super(label, collapsibleState);
        this.value = value;
        this.severity = severity;
        this.description = value;
        this.iconPath = this.getIcon();
        this.contextValue = "status-item";
    }
    getIcon() {
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
//# sourceMappingURL=statusProvider.js.map