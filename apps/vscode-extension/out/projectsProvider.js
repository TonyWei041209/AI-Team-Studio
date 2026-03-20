"use strict";
/**
 * Tree data provider for the Projects view.
 * Shows all projects from the runtime as a flat list.
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
exports.ProjectItem = exports.ProjectsProvider = void 0;
const vscode = __importStar(require("vscode"));
class ProjectsProvider {
    client;
    _onDidChangeTreeData = new vscode.EventEmitter();
    onDidChangeTreeData = this._onDidChangeTreeData.event;
    projects = [];
    godotFlags = {};
    error = null;
    selectedProjectId = null;
    constructor(client) {
        this.client = client;
    }
    refresh() {
        this._onDidChangeTreeData.fire(undefined);
    }
    async load() {
        this.error = null;
        try {
            this.projects = await this.client.listProjects();
            // Load Godot detection for each project (non-blocking)
            for (const p of this.projects) {
                this.client.getGodotInfo(p.id).then((info) => {
                    if (info.is_godot !== this.godotFlags[p.id]) {
                        this.godotFlags[p.id] = info.is_godot;
                        this.refresh();
                    }
                }).catch(() => { });
            }
        }
        catch (err) {
            this.error = err instanceof Error ? err.message : "Failed to load projects";
            this.projects = [];
        }
        this.refresh();
    }
    getSelectedProjectId() {
        return this.selectedProjectId;
    }
    selectProject(projectId) {
        this.selectedProjectId = projectId;
        this.refresh();
    }
    getProjects() {
        return this.projects;
    }
    getTreeItem(element) {
        return element;
    }
    getChildren() {
        if (this.error) {
            const item = new vscode.TreeItem(this.error);
            item.iconPath = new vscode.ThemeIcon("error", new vscode.ThemeColor("charts.red"));
            return [item];
        }
        if (this.projects.length === 0) {
            const item = new vscode.TreeItem("No projects found");
            item.iconPath = new vscode.ThemeIcon("info");
            return [item];
        }
        return this.projects.map((p) => new ProjectItem(p, p.id === this.selectedProjectId, this.godotFlags[p.id] || false));
    }
}
exports.ProjectsProvider = ProjectsProvider;
class ProjectItem extends vscode.TreeItem {
    project;
    constructor(project, isSelected, isGodot) {
        super(project.name, vscode.TreeItemCollapsibleState.None);
        this.project = project;
        const godotLabel = isGodot ? " [Godot]" : "";
        this.description = (project.description || project.local_repo_path) + godotLabel;
        this.tooltip = `${project.name}${isGodot ? " (Godot Project)" : ""}\n${project.local_repo_path}\n${project.description || ""}`;
        this.contextValue = isGodot ? "project-godot" : "project";
        this.iconPath = isGodot
            ? new vscode.ThemeIcon("game", new vscode.ThemeColor("charts.purple"))
            : isSelected
                ? new vscode.ThemeIcon("folder-opened", new vscode.ThemeColor("charts.blue"))
                : new vscode.ThemeIcon("folder");
        this.command = {
            command: "ats.selectProject",
            title: "Select Project",
            arguments: [project.id],
        };
    }
}
exports.ProjectItem = ProjectItem;
//# sourceMappingURL=projectsProvider.js.map