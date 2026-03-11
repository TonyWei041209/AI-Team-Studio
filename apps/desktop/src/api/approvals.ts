import type { ApprovalRequest, ApprovalResolve } from "../types/api";
import { api } from "./client";

export const approvalsApi = {
  listPending: () =>
    api.get<ApprovalRequest[]>("/api/approvals/pending"),
  resolve: (id: string, data: ApprovalResolve) =>
    api.patch<ApprovalRequest>(`/api/approvals/${id}`, data),
};
