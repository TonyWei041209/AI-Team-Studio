/**
 * Playwright configuration for browser E2E tests.
 *
 * NOTE: These tests run against the Vite-served React frontend
 * in a real Chromium browser.  This is NOT a full Tauri-shell E2E —
 * it tests the web layer only, which is the same content rendered
 * inside Tauri's WebView2 at runtime.
 *
 * Prerequisites:
 *   - Backend running:  cd services/runtime && python main.py
 *   - Vite dev server:  started automatically by webServer config below
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  retries: 0,
  workers: 1, // serial — tests share backend state
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 15_000,
    env: {
      // Fast polling for E2E — avoids flaky waits
      VITE_APPROVALS_POLL_MS: "1000",
      VITE_LOGS_POLL_MS: "1000",
      VITE_APPROVAL_COUNT_POLL_MS: "1000",
    },
  },
});
