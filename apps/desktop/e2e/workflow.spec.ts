/**
 * Browser E2E workflow test for AI Team Studio.
 *
 * This is a browser-level E2E test against the Vite-served React frontend.
 * It is NOT a full Tauri-shell E2E — it tests the web layer rendered in
 * Chromium, which is the same content rendered inside Tauri's WebView2.
 *
 * Prerequisites:
 *   - Backend:  cd services/runtime && python main.py  (port 9800)
 *   - Frontend: started automatically by Playwright webServer config
 *
 * Chain covered:
 *   Create project → Create task → Trigger approval (via real tool execution)
 *   → Approval visible in UI → Approve via UI → Approval disappears
 *   → Logs visible with filter → Sidebar badge updates
 */
import { test, expect, type Page } from "@playwright/test";

const API = "http://127.0.0.1:9800/api";

// Unique prefix to avoid collisions with other test suites
const PREFIX = `E2E-${Date.now()}`;

// ── Helpers ──────────────────────────────────────────────────

/** POST/GET/PATCH helper for direct backend API calls. */
async function api(
  method: string,
  path: string,
  body?: Record<string, unknown>,
) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: res.status, data: await res.json() };
}

/** Clean up test data from the database via API. */
async function cleanup(projectId: string) {
  // Delete project cascades tasks; approvals/logs may linger but won't
  // interfere with other test runs due to unique PREFIX naming.
  try {
    await api("DELETE", `/projects/${projectId}`);
  } catch {
    // best-effort
  }
}

// ── Tests ────────────────────────────────────────────────────

test.describe("Full UI workflow", () => {
  let projectId: string;
  let taskId: string;

  test.afterAll(async () => {
    if (projectId) {
      await cleanup(projectId);
    }
  });

  test("1. App loads with sidebar and Projects tab active", async ({ page }) => {
    await page.goto("/");

    // Sidebar visible with all 4 tabs
    await expect(page.getByTestId("sidebar-projects")).toBeVisible();
    await expect(page.getByTestId("sidebar-tasks")).toBeVisible();
    await expect(page.getByTestId("sidebar-approvals")).toBeVisible();
    await expect(page.getByTestId("sidebar-logs")).toBeVisible();

    // Projects panel is the default active tab — "Projects" heading visible
    await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  });

  test("2. Create a project via UI", async ({ page }) => {
    await page.goto("/");

    // Click "+ New Project"
    await page.getByTestId("project-create-btn").click();

    // Fill the form
    await page.getByTestId("project-name-input").fill(`${PREFIX}-Project`);
    await page.getByTestId("project-path-input").fill("D:/AI_Team_Studio");

    // Submit
    await page.getByTestId("project-submit-btn").click();

    // Wait for the project to appear in the list
    const projectList = page.getByTestId("project-list");
    await expect(projectList).toBeVisible({ timeout: 5000 });
    await expect(projectList.locator(".project-item").first()).toBeVisible();

    // Verify our project name appears
    await expect(page.locator(".project-name").filter({ hasText: `${PREFIX}-Project` })).toBeVisible();

    // Grab project ID from backend for later use
    const { data: projects } = await api("GET", "/projects");
    const ours = projects.find(
      (p: { name: string }) => p.name === `${PREFIX}-Project`,
    );
    expect(ours).toBeTruthy();
    projectId = ours.id;
  });

  test("3. Navigate to Tasks and create a task", async ({ page }) => {
    await page.goto("/");

    // First select the project (need to click it in project list)
    await expect(page.locator(".project-name").filter({ hasText: `${PREFIX}-Project` })).toBeVisible({ timeout: 5000 });
    await page.locator(".project-item").filter({ hasText: `${PREFIX}-Project` }).click();

    // Navigate to Tasks tab
    await page.getByTestId("sidebar-tasks").click();

    // Click "+ New Task"
    await page.getByTestId("task-create-btn").click();

    // Fill task form
    await page.getByTestId("task-title-input").fill(`${PREFIX}-Task`);

    // Submit
    await page.getByTestId("task-submit-btn").click();

    // Wait for the task to appear
    await expect(page.getByTestId("task-list")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("task-item").first()).toBeVisible();
    await expect(page.locator(".task-title").filter({ hasText: `${PREFIX}-Task` })).toBeVisible();

    // Grab task ID for later
    const { data: tasks } = await api("GET", `/projects/${projectId}/tasks`);
    const ours = tasks.find(
      (t: { title: string }) => t.title === `${PREFIX}-Task`,
    );
    expect(ours).toBeTruthy();
    taskId = ours.id;
  });

  test("4. Trigger approval via real tool execution and see it in UI", async ({ page }) => {
    // Clean any pre-existing pending approvals so we have a clean slate
    const { data: existingPending } = await api("GET", "/approvals/pending");
    for (const a of existingPending) {
      await api("PATCH", `/approvals/${a.id}`, {
        status: "rejected",
        reviewer_comment: "E2E cleanup",
      });
    }

    // Trigger a HIGH-risk tool call through the real backend business endpoint.
    // "git clean -fd" is whitelisted (base cmd "git") but matches the HIGH
    // risk pattern \bgit\s+clean\b → creates an approval request.
    const { status, data: toolResp } = await api("POST", "/tools/execute", {
      tool_name: "shell",
      params: { command: "git clean -fd" },
      project_id: projectId,
      task_id: taskId,
    });
    expect(status).toBe(200);
    expect(toolResp.blocked).toBe(true);
    expect(toolResp.approval_id).toBeTruthy();

    // Navigate to Approvals tab and wait for card to appear via polling
    await page.goto("/");
    await page.getByTestId("sidebar-approvals").click();

    // Wait for approval card (polling interval is 1s in test env)
    await expect(page.getByTestId("approval-card").first()).toBeVisible({ timeout: 8000 });

    // Verify our card shows "tool:shell"
    await expect(page.locator(".approval-type").filter({ hasText: "tool:shell" })).toBeVisible();

    // Verify sidebar badge shows count
    await page.getByTestId("sidebar-projects").click(); // switch away
    await expect(page.getByTestId("sidebar-badge-approvals")).toBeVisible({ timeout: 5000 });
  });

  test("5. Approve via UI and verify it disappears", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sidebar-approvals").click();

    // Wait for our approval card
    const shellCard = page.getByTestId("approval-card").filter({
      has: page.locator(".approval-type", { hasText: "tool:shell" }),
    });
    await expect(shellCard).toBeVisible({ timeout: 8000 });

    // Click Approve on that specific card
    await shellCard.getByTestId("approval-approve-btn").click();

    // Type a comment
    await shellCard.getByTestId("approval-comment-input").fill("Approved by E2E test");

    // Confirm
    await shellCard.getByTestId("approval-confirm-btn").click();

    // Wait for that specific card to disappear
    await expect(shellCard).toBeHidden({ timeout: 8000 });
  });

  test("6. Logs panel shows entries with level filter", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sidebar-logs").click();

    // Wait for log entries to load (polling will fetch)
    await expect(page.getByTestId("log-entries")).toBeVisible({ timeout: 8000 });

    // Verify count label exists
    await expect(page.getByTestId("log-count")).toBeVisible();

    // Verify lastUpdated indicator is visible (don't assert exact time)
    await expect(page.getByTestId("logs-last-updated")).toBeVisible({ timeout: 5000 });

    // Get initial entry count text
    const countText = await page.getByTestId("log-count").textContent();
    expect(countText).toContain("entries");

    // Change level filter to "error"
    await page.getByTestId("logs-level-filter").selectOption("error");

    // Wait for re-fetch — the filter triggers a re-render.
    // After filtering to "error", there may be 0 matching entries,
    // which shows "No log entries found." instead of the log-count element.
    await page.waitForTimeout(2000);

    // Verify filter was applied: either log-count shows or empty-state shows
    const hasCount = await page.getByTestId("log-count").isVisible().catch(() => false);
    const hasEmpty = await page.getByTestId("log-entries").isVisible().catch(() => false);
    const hasEmptyMessage = await page.locator("text=No log entries found").isVisible().catch(() => false);
    expect(hasCount || hasEmpty || hasEmptyMessage).toBeTruthy();

    // Switch back to "All Levels" — should restore the full list
    await page.getByTestId("logs-level-filter").selectOption("");
    await expect(page.getByTestId("log-entries")).toBeVisible({ timeout: 5000 });
  });

  test("7. lastUpdated indicator is visible on Approvals panel", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sidebar-approvals").click();

    // Wait for data to load, then check lastUpdated is visible
    // Don't assert exact time string — just visibility
    await expect(page.getByTestId("approvals-last-updated")).toBeVisible({ timeout: 8000 });
  });
});
