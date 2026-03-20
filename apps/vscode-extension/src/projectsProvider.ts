/**
 * Tree data provider for the Projects view.
 * Shows all projects from the runtime as a flat list.
 */

import * as vscode from "vscode";
import type { RuntimeClient, Project } from "./runtimeClient";

export class ProjectsProvider implements vscode.TreeDataProvider<ProjectItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<ProjectItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private projects: Project[] = [];
  private godotFlags: Record<string, boolean> = {};
  private error: string | null = null;
  private selectedProjectId: string | null = null;

  constructor(private client: RuntimeClient) {}

  refresh(): void {
    this._onDidChangeTreeData.fire(undefined);
  }

  async load(): Promise<void> {
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
        }).catch(() => { /* silent */ });
      }
    } catch (err) {
      this.error = err instanceof Error ? err.message : "Failed to load projects";
      this.projects = [];
    }
    this.refresh();
  }

  getSelectedProjectId(): string | null {
    return this.selectedProjectId;
  }

  selectProject(projectId: string): void {
    this.selectedProjectId = projectId;
    this.refresh();
  }

  getProjects(): Project[] {
    return this.projects;
  }

  getTreeItem(element: ProjectItem): vscode.TreeItem {
    return element;
  }

  getChildren(): ProjectItem[] {
    if (this.error) {
      const item = new vscode.TreeItem(this.error);
      item.iconPath = new vscode.ThemeIcon("error", new vscode.ThemeColor("charts.red"));
      return [item as ProjectItem];
    }

    if (this.projects.length === 0) {
      const item = new vscode.TreeItem("No projects found");
      item.iconPath = new vscode.ThemeIcon("info");
      return [item as ProjectItem];
    }

    return this.projects.map((p) => new ProjectItem(p, p.id === this.selectedProjectId, this.godotFlags[p.id] || false));
  }
}

export class ProjectItem extends vscode.TreeItem {
  constructor(
    public readonly project: Project,
    isSelected: boolean,
    isGodot: boolean,
  ) {
    super(project.name, vscode.TreeItemCollapsibleState.None);
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
