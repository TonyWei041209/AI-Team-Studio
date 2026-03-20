# Phase 5.9 — Controlled Desktop Operations: Safety Architecture & Rollout Blueprint

**Status:** Design Freeze (implementation-ready blueprint)
**Date:** 2026-03-19
**Baseline:** `761c319` on `phase-6e-b-approved-snapshot`
**Author:** Architecture review (design only — no runtime code changes)

---

## 1. Current Blockers & Why 5.9 Cannot Be Implemented Today

### 1.1 CLAUDE.md Safety Boundaries That Block 5.9

The following rules in `CLAUDE.md` explicitly prevent desktop automation:

| Rule | Location | Impact on 5.9 |
|------|----------|----------------|
| "不要完整电脑接管" | Key Constraints (line 67) | Prohibits full machine control |
| "不自动扩展 command / file / git / network 能力" | Capability Boundaries (line 184) | Prohibits adding new execution primitives |
| "不新增高风险自动执行路径" | Capability Boundaries (line 185) | Prohibits unguarded automation paths |
| "命令白名单仅限本地 build / test / lint / inspect 工具" | Command Execution (line 173) | No desktop interaction commands in whitelist |
| "shell=True / shell 拼接风格默认 blocked" | Command Execution (line 172) | No arbitrary shell commands for GUI control |
| "无确认的高风险自动执行" | Scope Exclusions (line 69) | Every high-risk action needs human confirmation |

### 1.2 Capabilities 5.9 Must Add (That Don't Exist Today)

1. **Window discovery** — detect running Godot editor process/window
2. **Screen observation** — read Godot editor state (screenshots, accessibility tree, or process introspection)
3. **Input injection** — send mouse clicks, keyboard inputs to Godot window
4. **Session lifecycle** — start, pause, resume, terminate a desktop interaction session
5. **Action planning for GUI** — plan a sequence of GUI steps before execution

### 1.3 Why These Capabilities Are High-Risk

- **Input injection** can affect any window, not just Godot — wrong-window targeting is catastrophic
- **Continuous automation** without human presence can cause runaway damage
- **GUI state is fragile** — menus, dialogs, focus can shift unpredictably
- **No undo model** for GUI actions — clicking "Delete Scene" in Godot has no reliable programmatic undo
- **Side effects are global** — desktop automation affects the entire OS, not just a sandboxed project directory

---

## 2. Target Scope & Non-Goals

### 2.1 Goals (What 5.9 IS)

- **Godot-scoped**: Only interacts with Godot editor windows for the active project
- **Project-scoped**: Only performs actions relevant to the currently selected Studio project
- **User-supervised**: User must be present and actively monitoring
- **Interruptible**: Emergency stop available at all times
- **Auditable**: Every desktop action is logged with intent, target, and result
- **Step-gated**: Each action (or small batch) requires explicit or semi-automatic confirmation

### 2.2 Non-Goals (What 5.9 Is NOT)

- NOT full desktop automation (no browser, no file manager, no system settings)
- NOT unsupervised batch processing (no "run overnight" mode for desktop operations)
- NOT a replacement for manual Godot editing (it's an assistant, not an autopilot)
- NOT cross-application automation (no chaining Godot → browser → terminal)
- NOT available for non-Godot projects in the first version
- NOT modifying system files, registry, or OS-level configuration

---

## 3. Threat Model

### 3.1 Threat Matrix

| # | Threat | Severity | Likelihood | Mitigation Strategy |
|---|--------|----------|------------|---------------------|
| T1 | **Wrong-window targeting** — clicks land on wrong application | Critical | Medium | Window handle verification before every action; abort if Godot not in focus |
| T2 | **Wrong-project context** — actions applied to wrong Godot project | High | Low | Verify project.godot path matches active Studio project before session start |
| T3 | **Runaway sequence** — automated steps continue after unexpected state | Critical | Medium | Step confirmation; max batch size; timeout between steps |
| T4 | **Unrecoverable destructive action** — delete scene, overwrite resource | Critical | Medium | Pre-action snapshot when feasible; confirmation for destructive categories; dry-run descriptions |
| T5 | **User absence** — system continues while user is away | High | Medium | Activity heartbeat; pause on window defocus; idle timeout (configurable, default 60s) |
| T6 | **Conflict with execution chain** — desktop ops bypass approval gates | High | Low | Desktop session is a NEW action type with its own approval gate; does not reuse file-execution path |
| T7 | **Unexplainable actions** — user can't understand why system clicked somewhere | Medium | High | Every action must have intent label + target description before execution |
| T8 | **Credential exposure** — automation captures passwords/keys on screen | Medium | Low | Never OCR/capture credential fields; restrict observation to Godot viewport |
| T9 | **Resource exhaustion** — rapid GUI automation overloads system | Low | Low | Rate limit: max 1 action per 500ms default |

### 3.2 Key Invariant

> **No desktop action may execute without the user being able to see what will happen, confirm it, and stop it.**

This is the fundamental safety invariant. All architecture decisions derive from this.

---

## 4. Safety Architecture

### 4.1 Scope Boundaries

```
Desktop Session Scope
├── Application: Godot Editor ONLY (verified by process name + window title)
├── Project: Must match active AI Team Studio project's local_repo_path
├── Duration: Explicit session start → explicit session end (or timeout)
└── Fallback: If any scope check fails → abort immediately, log reason
```

### 4.2 Interaction Gating Model

```
[User requests desktop task]
        │
        ▼
   Session Start Gate
   ├── Verify Godot is running
   ├── Verify project match
   ├── User confirms session scope
   ├── Create DesktopSession record
   │
   ▼
   [Planner generates action plan]
        │
        ▼
   Plan Review Gate
   ├── Show action list with descriptions
   ├── User approves plan (or edits)
   │
   ▼
   [For each action in plan]
        │
        ▼
   Step Gate (configurable granularity)
   ├── Mode A: confirm-every-step (default for first rollout)
   ├── Mode B: confirm-per-batch (groups of 3-5 related steps)
   ├── Mode C: auto-execute-low-risk (future, requires risk classifier for GUI)
   │
   ▼
   Pre-Action Check
   ├── Verify Godot still in focus
   ├── Verify expected UI state (when observable)
   ├── Log intent + target
   │
   ▼
   Execute Single Action
   ├── Rate-limited (min 500ms between actions)
   ├── Max 1 action in flight at a time
   │
   ▼
   Post-Action Verification (when feasible)
   ├── Confirm expected state change occurred
   ├── Log result
   │
   ▼
   [Loop or complete]
```

### 4.3 Operator Presence Rules

| Condition | System Behavior |
|-----------|----------------|
| Godot window loses focus | Pause session immediately; resume requires user re-confirmation |
| AI Team Studio window loses focus | Continue if Godot still active; pause if both unfocused |
| No user interaction for 60s (configurable) | Pause session; notification |
| No user interaction for 5 min | Terminate session; log reason |
| User presses global emergency stop hotkey | Immediately terminate session; no further actions |
| System sleep / lock | Terminate session |

### 4.4 Emergency Stop

- **Global hotkey** (e.g., `Ctrl+Shift+Escape` or configurable) — kills session instantly
- **Dashboard stop button** — visible when desktop session is active
- **VS Code extension stop command** — `ats.stopDesktopSession`
- **Fail-safe**: If session process crashes, no orphaned automation continues
- **On stop**: Log termination reason; mark session as `aborted`; no further actions queued

### 4.5 Auditability

Every desktop session produces:

```
DesktopSession
├── id, task_id, project_id
├── status: active | paused | completed | aborted | timed_out
├── started_at, ended_at
├── scope: { app: "godot", project_path: "..." }
└── actions: DesktopAction[]
    ├── id, session_id
    ├── intent: "Open scene player.tscn"
    ├── action_type: "navigate" | "click" | "type" | "shortcut" | "observe"
    ├── target: { description: "FileSystem dock → player.tscn" }
    ├── status: pending | confirmed | executed | failed | skipped
    ├── confirmation: user_confirmed | auto_confirmed (if low-risk mode)
    ├── result: { success: bool, screenshot_ref?: string, error?: string }
    └── timestamp
```

### 4.6 Allowed Action Taxonomy

Actions are organized in tiers. Each tier requires progressively stronger gates.

| Tier | Category | Examples | Gate Level | 5.9 Stage |
|------|----------|----------|------------|-----------|
| 0 | **Observe** | Screenshot, read scene tree, check Godot version | No gate (read-only) | 5.9-A |
| 1 | **Navigate** | Switch tabs, open scene, expand tree nodes, scroll | Batch-confirm | 5.9-B |
| 2 | **Low-risk edit** | Rename node, change property value, toggle visibility | Step-confirm | 5.9-C |
| 3 | **Structured workflow** | Create node, add script, connect signal | Step-confirm + dry-run description | 5.9-D |
| 4 | **High-risk** | Delete node/scene, modify export settings, run project | Individual-confirm + warning | 5.9-E |
| 5 | **Forbidden** | Delete project files, modify Godot settings, install plugins, access non-Godot apps | BLOCKED — not allowed | Never |

### 4.7 Rollback / Recovery

| Action Tier | Rollback Feasibility | Strategy |
|-------------|---------------------|----------|
| Tier 0 (Observe) | N/A (no state change) | — |
| Tier 1 (Navigate) | Naturally reversible (navigate back) | Automatic |
| Tier 2 (Low-risk edit) | Godot's built-in Ctrl+Z | Document; advise user |
| Tier 3 (Structured workflow) | Partial via Ctrl+Z or scene reload | Pre-action scene save recommended |
| Tier 4 (High-risk) | May be irreversible | Mandatory pre-action save; project-level git backup |
| Tier 5 (Forbidden) | N/A (blocked) | — |

**Relationship with existing rollback model:**
- Desktop session rollback is SEPARATE from the file-execution rollback chain
- The existing `execution_results` → `rollback` model covers file writes only
- Desktop operations need their own session-scoped "undo what we can" model
- Some desktop actions are inherently non-reversible — the system must be honest about this

---

## 5. Phased Rollout Plan

### Phase 5.9-A: Read-Only Godot Session Awareness

**Goal:** Establish session lifecycle + observation-only capability.

**Allowed:**
- Detect running Godot process
- Capture Godot window screenshot (for agent context)
- Read basic Godot editor state (if accessible via accessibility API)
- Create/manage DesktopSession records

**Forbidden:** Any input injection, any state modification

**Required gates:**
- Session start confirmation
- Project path verification

**Exit criteria:**
- Can create a desktop session
- Can observe Godot state
- Session lifecycle (start/pause/complete/timeout) works
- Audit trail records observations

---

### Phase 5.9-B: Supervised Navigation-Only Mode

**Goal:** Allow the system to navigate within Godot (open scenes, switch tabs) under strict supervision.

**Allowed:**
- Tier 0 + Tier 1 actions only
- Mouse clicks for navigation (file browser, scene tabs, inspector tabs)
- Keyboard shortcuts for navigation (Ctrl+Tab, F1-F12 for Godot panels)

**Forbidden:** Any editing, any property changes, any creation/deletion

**Required gates:**
- Session start gate
- Batch confirmation (every 3-5 navigation steps)
- Focus verification before each action
- Idle timeout

**Exit criteria:**
- Can navigate Godot editor under supervision
- Wrong-window detection works
- Pause-on-defocus works
- Emergency stop works

---

### Phase 5.9-C: Bounded Low-Risk Interaction

**Goal:** Allow simple property edits and low-risk modifications.

**Allowed:**
- Tier 0 + 1 + 2 actions
- Change node properties via Inspector
- Rename nodes
- Toggle visibility flags
- Adjust simple numeric values

**Forbidden:** Creating/deleting nodes or scenes, connecting signals, running the game

**Required gates:**
- Per-step confirmation (every action)
- Pre-action intent description
- Post-action verification
- Rate limiting (500ms minimum)

**Exit criteria:**
- Can modify simple properties under per-step confirmation
- Actions are auditable with intent + result
- User can interrupt at any point

---

### Phase 5.9-D: Structured Workflow Actions

**Goal:** Support multi-step Godot workflows (create node, add script, connect signal).

**Allowed:**
- Tier 0 + 1 + 2 + 3 actions
- Create new nodes
- Attach GDScript files
- Connect signals
- Modify scene structure

**Forbidden:** Deleting scenes/resources, modifying project settings, running game in exported mode

**Required gates:**
- Plan review gate (show full workflow before execution)
- Per-step confirmation OR batch confirmation (configurable)
- Pre-workflow scene save recommendation
- Dry-run description (text description of what will happen, not actual GUI simulation)

**Exit criteria:**
- Can execute multi-step Godot workflows
- Plan-then-execute model works
- Scene save checkpoint is reliable
- Workflows are traceable in audit trail

---

### Phase 5.9-E: Guarded Semi-Automation

**Goal:** Allow supervised automation of routine Godot tasks with minimal per-step interruption.

**Allowed:**
- Tier 0 + 1 + 2 + 3 + 4 (with individual confirmation for Tier 4)
- Batch confirmation for Tier 1-3 actions
- Auto-execute for Tier 0-1 after initial session confirmation
- Run game for testing (with confirmation)

**Forbidden:** Tier 5 (always blocked); unsupervised overnight runs; cross-app automation

**Required gates:**
- Risk-classified confirmation (auto for low, confirm for high)
- Operator presence verification (heartbeat)
- Session time limits
- Per-session action count limits

**Exit criteria:**
- Routine Godot tasks can be completed with minimal friction
- High-risk actions still require explicit confirmation
- Operator presence model is reliable
- Audit trail is complete and reviewable

---

### Phase 5.9-F: Production Hardening & Operator Tooling

**Goal:** Make the system robust enough for daily use.

**Allowed:**
- All of 5.9-E capabilities
- Desktop session replay (view what happened)
- Session templates (reusable workflow definitions)
- VS Code extension session monitoring

**Required:**
- Comprehensive test coverage for session lifecycle
- Stress testing for edge cases (Godot crash, system sleep, window resize)
- User documentation
- Settings UI for session configuration (timeout, confirmation mode, hotkeys)

**Exit criteria:**
- Daily-use reliability
- Documented and configurable
- Edge cases handled gracefully

---

## 6. Integration Points in Current Codebase

### 6.1 Modules to Reuse

| Current Module | Reuse for 5.9 | How |
|----------------|---------------|-----|
| `approval_requests` | Session start approval | New action_type: `desktop_session_start` |
| `audit_trail` | Desktop action logging | New event categories for desktop actions |
| `execution_proposals` | Action plan for desktop workflows | Extend proposal format for GUI action plans |
| `PipelineStepper` | Session progress visualization | Show desktop session as a pipeline stage |
| `TaskStateSummary` | Add "Desktop Session Active" phase | New phase in existing enum |
| `skills` system | Godot-specific desktop skills | Pre-built skills for common Godot workflows |
| `roles` system | Desktop Operator role | Optional new role with desktop capabilities |
| `Dashboard` | Active sessions widget | Show running desktop sessions count |
| `VS Code extension` | Session monitoring + emergency stop | New command + status bar indicator |

### 6.2 New Modules Required

| Module | Purpose | Location |
|--------|---------|----------|
| `DesktopSessionManager` | Session lifecycle (start, pause, stop, timeout) | `services/runtime/desktop/session.py` |
| `GodotWindowLocator` | Find and verify Godot process/window | `services/runtime/desktop/godot_locator.py` |
| `ScreenObserver` | Capture Godot window state | `services/runtime/desktop/observer.py` |
| `InputDispatcher` | Send mouse/keyboard to Godot window | `services/runtime/desktop/dispatcher.py` |
| `DesktopActionPlanner` | Plan GUI action sequences | `services/runtime/desktop/planner.py` |
| `DesktopAuditLogger` | Log desktop actions with intent + result | `services/runtime/desktop/audit.py` |
| `DesktopSessionPanel` | UI for session control + monitoring | `apps/desktop/src/panels/DesktopSessionPanel.tsx` |

### 6.3 Modules That Must NOT Be Shortcuts

| Module | Why It Can't Be Reused Directly |
|--------|-------------------------------|
| `scoped_file_executor` | File execution has rollback; desktop actions don't. Different safety model. |
| `scoped_command_executor` | Command execution has eligibility gates for specific commands; desktop input injection is fundamentally different. |
| `shell_executor` | Shell execution is command-line based; desktop ops are GUI-based. Using shell to drive GUI (e.g., `xdotool`) must go through InputDispatcher, never directly. |

---

## 7. Open Questions (To Resolve Before Implementation)

1. **Which GUI automation library?** Options: `pyautogui`, `pywinauto` (Windows), Godot's built-in `--remote-debug`, or Accessibility API. Each has different safety profiles.

2. **How to verify Godot state?** Screenshot comparison vs. accessibility tree vs. Godot remote debug protocol. The right choice affects observation accuracy and performance.

3. **Should DesktopSession be a separate table or extend execution_requests?** Recommendation: separate table — the lifecycle is different.

4. **How to handle Godot version differences?** Godot 4.x has different UI layout than 3.x. Detection should include version awareness.

5. **Should the CLAUDE.md safety boundaries be formally amended?** Recommendation: Yes — add a new section for "Controlled Desktop Operations" with its own explicit rules, rather than silently relaxing existing constraints.

---

## 8. Prerequisite CLAUDE.md Amendment (Draft)

When implementation begins, the following section should be added to CLAUDE.md:

```markdown
### Controlled Desktop Operations (Phase 5.9+)

Desktop automation is allowed ONLY under these conditions:
- Scoped to Godot editor windows for the active project
- User must be present and monitoring
- Emergency stop must be available at all times
- Every action must be logged with intent and result
- High-risk actions (delete, create, modify project settings) require individual confirmation
- Session automatically pauses on: window defocus, idle timeout, system sleep
- Desktop operations are SEPARATE from file/command execution — different safety model
- Desktop automation NEVER runs unsupervised or overnight
- Desktop automation NEVER affects non-Godot applications
```

---

## 9. Summary

| Aspect | Decision |
|--------|----------|
| **Scope** | Godot-only, project-scoped, user-supervised |
| **Safety model** | Session-gated, step-confirmed, auditable, interruptible |
| **Rollout** | 6 stages (A through F), each with clear entry/exit criteria |
| **Current blocker** | CLAUDE.md safety boundaries + no automation primitives |
| **To unblock** | Amend CLAUDE.md + implement session manager + input dispatcher |
| **First implementable stage** | 5.9-A (observation only) — lowest risk, highest learning value |
| **Timeline recommendation** | Start with 5.9-A after real-use testing of current 5.1-5.8 |
