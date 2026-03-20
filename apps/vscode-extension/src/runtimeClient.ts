/**
 * HTTP client for AI Team Studio local runtime.
 * All calls are read-only or trigger existing API endpoints.
 * No new write paths or dangerous operations.
 */

import * as https from "https";
import * as http from "http";

export interface HealthStatus {
  status: string;
  version: string;
  database: string;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  local_repo_path: string;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  project_id: string;
  title: string;
  description: string;
  status: "pending" | "planning" | "in_progress" | "reviewing" | "done" | "failed";
  priority: "low" | "medium" | "high" | "critical";
  assigned_agent_role: string | null;
  created_at: string;
  updated_at: string;
}

export interface DashboardSummary {
  project_count: number;
  total_tasks: number;
  pending_approvals: number;
  active_orchestrations: number;
  failed_tasks: number;
}

export interface ReadinessSummary {
  overall_ready: boolean;
  providers: { total: number; configured: number };
  roles: { total: number; real_model_count: number };
  missing_steps: string[];
}

/** Simple HTTP GET/POST client for the local runtime. */
function request(
  baseUrl: string,
  path: string,
  method: "GET" | "POST" = "GET",
  body?: unknown,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const url = new URL(path, baseUrl);
    const isHttps = url.protocol === "https:";
    const lib = isHttps ? https : http;

    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      timeout: 10000,
    };

    const req = lib.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
          try {
            resolve(JSON.parse(data));
          } catch {
            resolve(data);
          }
        } else {
          reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 200)}`));
        }
      });
    });

    req.on("error", (err) => reject(err));
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("Request timed out"));
    });

    if (body) {
      req.write(JSON.stringify(body));
    }
    req.end();
  });
}

export class RuntimeClient {
  constructor(private baseUrl: string) {}

  setBaseUrl(url: string): void {
    this.baseUrl = url;
  }

  async health(): Promise<HealthStatus> {
    return (await request(this.baseUrl, "/api/health")) as HealthStatus;
  }

  async listProjects(): Promise<Project[]> {
    return (await request(this.baseUrl, "/api/projects")) as Project[];
  }

  async listTasks(projectId: string): Promise<Task[]> {
    return (await request(
      this.baseUrl,
      `/api/projects/${projectId}/tasks`,
    )) as Task[];
  }

  async getDashboardSummary(): Promise<DashboardSummary> {
    return (await request(
      this.baseUrl,
      "/api/dashboard/summary",
    )) as DashboardSummary;
  }

  async getReadiness(): Promise<ReadinessSummary> {
    return (await request(
      this.baseUrl,
      "/api/settings/readiness",
    )) as ReadinessSummary;
  }

  async getGodotInfo(projectId: string): Promise<{ is_godot: boolean; project_name?: string | null }> {
    return (await request(
      this.baseUrl,
      `/api/projects/${projectId}/godot-info`,
    )) as { is_godot: boolean; project_name?: string | null };
  }

  async orchestrateTask(taskId: string): Promise<unknown> {
    return await request(
      this.baseUrl,
      `/api/tasks/${taskId}/orchestrate`,
      "POST",
    );
  }
}
