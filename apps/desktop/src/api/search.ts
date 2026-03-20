import { api } from "./client";

export interface SearchProject {
  id: string;
  name: string;
  description: string | null;
  local_repo_path: string;
  created_at: string;
}

export interface SearchTask {
  id: string;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  project_id: string;
  project_name: string;
  created_at: string;
  updated_at: string;
}

export interface SearchApproval {
  id: string;
  task_id: string;
  action_type: string;
  status: string;
  created_at: string;
  task_title: string;
  project_id: string;
  project_name: string;
}

export interface SearchResults {
  query: string;
  projects: SearchProject[];
  tasks: SearchTask[];
  approvals: SearchApproval[];
}

export const searchApi = {
  search: (q: string, limit = 10) =>
    api.get<SearchResults>(`/api/dashboard/search?q=${encodeURIComponent(q)}&limit=${limit}`),
};
