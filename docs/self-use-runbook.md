# Self-Use Runbook

This is an operational guide for using AI Team Studio to push real projects.

## 1. Start the App

**Terminal 1 — Backend:**
```bash
cd services/runtime
pip install -r requirements.txt
python main.py
```
Verify: `http://127.0.0.1:9800/api/health` should return `{"status": "ok"}`.

**Terminal 2 — Frontend:**
```bash
cd apps/desktop
npm install
npm run tauri:dev
```

Or use `scripts\dev.bat` on Windows to start both.

## 2. Configure a Model Provider

Go to **Settings** panel (gear icon in sidebar).

### API Configuration tab

1. Select a provider (e.g., Anthropic)
2. Enter your API key
3. Click **Save**
4. Click **Test Connection** to verify

Supported providers: Anthropic, OpenAI-compatible, Gemini, DeepSeek, Kimi, MiniMax.

### Role-Model Configuration tab

Each agent role can be mapped to a specific provider and model:

| Role | Purpose | Default |
|------|---------|---------|
| Planner | Breaks task into structured plan | Anthropic / claude-sonnet-4-20250514 |
| Builder | Produces execution proposals (plan-only) | mock (disabled) |
| Reviewer | Reviews Builder output, approves or requests changes | Anthropic / claude-3-5-haiku-20241022 |
| QA | Quality checks (mock-only in v1) | mock |

To use real models:
1. Enable the role toggle
2. Select provider and model
3. Make sure the provider has a valid API key configured

**If no API key is configured**, the system automatically falls back to mock executor for that role. Mock produces realistic-looking but synthetic proposals — useful for testing the full pipeline without spending API credits.

## 3. Create a Project

1. Go to **Projects** panel
2. Click **Create Project**
3. Fill in:
   - **Name**: your project name
   - **Local Repo Path**: absolute path to your project directory (e.g., `D:\my-project`)
   - **Description**: optional
4. Click **Create**

## 4. Create a Task

1. Select your project
2. In the **Task Board**, click **New Task**
3. Fill in:
   - **Title**: what you want done
   - **Description**: detailed requirements
   - **Priority**: low / medium / high / critical
4. Click **Create**

## 5. Run Orchestration

1. Find your task in the Task Board (status: `pending`)
2. Click **Orchestrate**
3. Watch progress feedback: Planning → Building → Reviewing → Done/Failed
4. On success, the task auto-expands to show proposals

The orchestrator runs: Planner → Builder → QA → Reviewer sequentially.

## 6. Review and Approve

After orchestration completes, you'll see the Builder's **execution proposal** inline:
- Change summary
- Proposed files (with action: create/modify)
- Proposed commands (with risk level)
- Approval reasons

If the proposal requires approval (most do), you'll see **Approve** / **Reject** buttons directly in the Task Board.

- **Approve**: Automatically freezes a snapshot and creates an execution request (Phase 10-1 compression)
- **Reject**: Stops here; no downstream actions

## 7. Confirm and Dry-Run

After approval, an **Execution Request** card appears with options:

| Button | What it does |
|--------|-------------|
| **Confirm Request** | Marks the request as confirmed (irreversible). Does NOT execute anything yet. |
| **Reject Request** | Cancels the execution request. |
| **Confirm & Dry-Run (irreversible)** | Confirms AND immediately runs a read-only simulation. Recommended path. |

After confirming, click **Run Dry-Run** (if you didn't use the combo button) to see:
- Planned file actions with status
- Planned command actions
- Warnings

## 8. Execute

After reviewing the dry-run results:

1. Click **Execute** to perform real file operations
2. The executor will:
   - Create/modify files as planned
   - Run whitelisted commands (build/test/lint only)
   - Record all results
3. Review execution results inline

## 9. Rollback (if needed)

If execution produced unwanted results:

1. Click **Rollback** on the execution result
2. Confirm the rollback
3. File operations will be reverted from backups
4. Note: command operations cannot be rolled back

## Safety Boundaries

The following are enforced at all times:

- **file_delete**: blocked
- **git write operations**: blocked (no commit/push/reset)
- **install/publish/network commands**: blocked
- **shell=True / shell injection**: blocked
- **Command whitelist**: only local build/test/lint/inspect tools
- **Approval required** for high-risk proposals
- **Confirm gate** is irreversible — review before confirming
- **Execute is always manual** — never auto-triggered
- **Rollback is always manual** — never auto-triggered

## Known Limitations

1. **Builder is plan-only**: Builder produces proposals but does not directly write files. Execution goes through the full approval → confirm → dry-run → execute pipeline.
2. **QA is mock-only**: QA role always uses mock executor in v1.
3. **No file browser**: Project paths must be entered manually.
4. **No cloud sync**: All data is local SQLite.
5. **Commands are not rollbackable**: Only file operations can be rolled back.
6. **Single project at a time**: Task Board shows tasks for the currently selected project.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Orchestrate produces generic/mock proposals | API key not configured or role not enabled | Go to Settings → configure API key → enable role |
| "Orchestration failed" error | API key invalid or provider unreachable | Test connection in Settings |
| Execute button not visible | Haven't confirmed the execution request yet | Click "Confirm Request" first |
| Freeze & View Snapshot not visible | Proposal not approved yet | Approve the proposal first |
| Rollback has no effect on commands | By design | Commands are not reversible; only file ops are |
