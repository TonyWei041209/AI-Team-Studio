"use strict";
/**
 * HTTP client for AI Team Studio local runtime.
 * All calls are read-only or trigger existing API endpoints.
 * No new write paths or dangerous operations.
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
exports.RuntimeClient = void 0;
const https = __importStar(require("https"));
const http = __importStar(require("http"));
/** Simple HTTP GET/POST client for the local runtime. */
function request(baseUrl, path, method = "GET", body) {
    return new Promise((resolve, reject) => {
        const url = new URL(path, baseUrl);
        const isHttps = url.protocol === "https:";
        const lib = isHttps ? https : http;
        const options = {
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
                    }
                    catch {
                        resolve(data);
                    }
                }
                else {
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
class RuntimeClient {
    baseUrl;
    constructor(baseUrl) {
        this.baseUrl = baseUrl;
    }
    setBaseUrl(url) {
        this.baseUrl = url;
    }
    async health() {
        return (await request(this.baseUrl, "/api/health"));
    }
    async listProjects() {
        return (await request(this.baseUrl, "/api/projects"));
    }
    async listTasks(projectId) {
        return (await request(this.baseUrl, `/api/projects/${projectId}/tasks`));
    }
    async getDashboardSummary() {
        return (await request(this.baseUrl, "/api/dashboard/summary"));
    }
    async getReadiness() {
        return (await request(this.baseUrl, "/api/settings/readiness"));
    }
    async getGodotInfo(projectId) {
        return (await request(this.baseUrl, `/api/projects/${projectId}/godot-info`));
    }
    async orchestrateTask(taskId) {
        return await request(this.baseUrl, `/api/tasks/${taskId}/orchestrate`, "POST");
    }
}
exports.RuntimeClient = RuntimeClient;
//# sourceMappingURL=runtimeClient.js.map