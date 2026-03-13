#!/usr/bin/env python3
"""Phase 6G-A acceptance: action normalization & policy gate.

Pure service-level tests — no API server needed.

Usage:
    python tests/phase6ga_action_policy_acceptance.py
"""

import json
import os
import sys

# ── Add runtime to sys.path ──────────────────────────────────
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.action_policy_service import (
    ActionPlan,
    ActionType,
    NormalizedAction,
    PolicyDecision,
    build_action_plan,
    compile_actions,
    evaluate_policy,
)

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {label}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)


# ══════════════════════════════════════════════════════════════
# S1: ActionType mapping
# ══════════════════════════════════════════════════════════════
print("\n[S1]  ActionType mapping")

snap = {
    "proposed_files": [
        {"path": "src/app.py", "operation": "create"},
        {"path": "src/utils.py", "operation": "modify"},
        {"path": "old.txt", "operation": "delete"},
        {"path": "weird.txt", "operation": "teleport"},
    ],
    "proposed_commands": [
        "npm install",
        "git commit -m 'init'",
        "git checkout feature",
    ],
}
actions = compile_actions(snap)
check("file_create mapped", actions[0].type == ActionType.file_create)
check("file_modify mapped", actions[1].type == ActionType.file_modify)
check("file_delete mapped", actions[2].type == ActionType.file_delete)
check("unsupported mapped", actions[3].type == ActionType.unsupported)
check("command_run mapped", actions[4].type == ActionType.command_run)
check("git_commit mapped", actions[5].type == ActionType.git_commit)
check("git_checkout mapped", actions[6].type == ActionType.git_checkout)


# ══════════════════════════════════════════════════════════════
# S2: Command format normalization
# ══════════════════════════════════════════════════════════════
print("\n[S2]  Command format normalization")

snap2 = {
    "proposed_files": [],
    "proposed_commands": [
        "echo hello",
        {"command": "npm test"},
        "   ",
    ],
}
actions2 = compile_actions(snap2)
check("bare string extracted", actions2[0].target == "echo hello")
check("dict command extracted", actions2[1].target == "npm test")
check("whitespace-only skipped", len(actions2) == 2, f"got {len(actions2)}")


# ══════════════════════════════════════════════════════════════
# S3: File policy decisions
# ══════════════════════════════════════════════════════════════
print("\n[S3]  File policy decisions")

file_actions = compile_actions({
    "proposed_files": [
        {"path": "src/main.py", "operation": "create"},
        {"path": "src/lib.py", "operation": "modify"},
        {"path": ".env", "operation": "modify"},
        {"path": "C:\\Windows\\system32\\evil.dll", "operation": "create"},
        {"path": "normal.txt", "operation": "delete"},
    ],
    "proposed_commands": [],
})
plan_f = evaluate_policy(file_actions)

check("create normal → allow",
      plan_f.actions[0].policy_decision == "allow")
check("modify normal → allow",
      plan_f.actions[1].policy_decision == "allow")
check("modify .env → needs_confirmation",
      plan_f.actions[2].policy_decision == "needs_confirmation",
      f"got {plan_f.actions[2].policy_decision}")
check("create system dir → deny",
      plan_f.actions[3].policy_decision == "deny")
check("delete normal → needs_confirmation",
      plan_f.actions[4].policy_decision == "needs_confirmation")


# ══════════════════════════════════════════════════════════════
# S4: Command policy decisions
# ══════════════════════════════════════════════════════════════
print("\n[S4]  Command policy decisions")

cmd_actions = compile_actions({
    "proposed_files": [],
    "proposed_commands": [
        "npm install",
        "curl http://example.com",
        "rm -rf /",
        "rm file.txt",
        "git reset --hard HEAD",
    ],
})
plan_c = evaluate_policy(cmd_actions)

check("npm install → allow",
      plan_c.actions[0].policy_decision == "allow",
      f"got {plan_c.actions[0].policy_decision}")
check("curl (not whitelisted) → needs_confirmation",
      plan_c.actions[1].policy_decision == "needs_confirmation",
      f"got {plan_c.actions[1].policy_decision}")
check("rm -rf / → deny",
      plan_c.actions[2].policy_decision == "deny",
      f"got {plan_c.actions[2].policy_decision}")
check("rm file.txt → needs_confirmation (HIGH)",
      plan_c.actions[3].policy_decision == "needs_confirmation",
      f"got {plan_c.actions[3].policy_decision}")
check("git reset --hard → deny (CRITICAL)",
      plan_c.actions[4].policy_decision == "deny",
      f"got {plan_c.actions[4].policy_decision}")


# ══════════════════════════════════════════════════════════════
# S5: Git policy decisions
# ══════════════════════════════════════════════════════════════
print("\n[S5]  Git policy decisions")

git_actions = compile_actions({
    "proposed_files": [],
    "proposed_commands": [
        "git commit -m 'test'",
        "git checkout feature-branch",
        "git push --force origin main",
    ],
})
plan_g = evaluate_policy(git_actions)

check("git commit → needs_confirmation",
      plan_g.actions[0].policy_decision == "needs_confirmation",
      f"got {plan_g.actions[0].policy_decision}")
check("git checkout → needs_confirmation",
      plan_g.actions[1].policy_decision == "needs_confirmation",
      f"got {plan_g.actions[1].policy_decision}")
check("git push --force → deny",
      plan_g.actions[2].policy_decision == "deny",
      f"got {plan_g.actions[2].policy_decision}")


# ══════════════════════════════════════════════════════════════
# S6: Unsupported action
# ══════════════════════════════════════════════════════════════
print("\n[S6]  Unsupported action")

unsup_actions = compile_actions({
    "proposed_files": [{"path": "x.txt", "operation": "teleport"}],
    "proposed_commands": [],
})
plan_u = evaluate_policy(unsup_actions)
check("unsupported → deny", plan_u.actions[0].policy_decision == "deny")


# ══════════════════════════════════════════════════════════════
# S7: ActionPlan aggregation
# ══════════════════════════════════════════════════════════════
print("\n[S7]  ActionPlan aggregation")

# All-allow
plan_aa = evaluate_policy(compile_actions({
    "proposed_files": [
        {"path": "a.py", "operation": "create"},
        {"path": "b.py", "operation": "modify"},
    ],
    "proposed_commands": ["npm install"],
}))
check("all-allow: has_denied=False", plan_aa.has_denied is False)
check("all-allow: needs_confirmation_count=0", plan_aa.needs_confirmation_count == 0)

# Mixed
plan_mix = evaluate_policy(compile_actions({
    "proposed_files": [
        {"path": "a.py", "operation": "create"},
        {"path": "b.py", "operation": "delete"},
    ],
    "proposed_commands": ["rm -rf /"],
}))
check("mixed: has_denied=True", plan_mix.has_denied is True)
check("mixed: overall_risk is critical",
      plan_mix.overall_risk == "critical",
      f"got {plan_mix.overall_risk}")

# Empty
plan_empty = evaluate_policy(compile_actions({
    "proposed_files": [],
    "proposed_commands": [],
}))
check("empty: 0 actions", len(plan_empty.actions) == 0)
check("empty: summary starts with '0'", plan_empty.summary.startswith("0"))


# ══════════════════════════════════════════════════════════════
# S8: build_action_plan integration
# ══════════════════════════════════════════════════════════════
print("\n[S8]  build_action_plan integration")

snap_json = json.dumps({
    "proposed_files": [
        {"path": "src/app.py", "operation": "create"},
        {"path": "old.py", "operation": "delete"},
    ],
    "proposed_commands": ["npm install", "git commit -m 'done'"],
    "summary": "test",
})

result = build_action_plan(snap_json)
check("returns dict", isinstance(result, dict))
check("has 4 actions", len(result["actions"]) == 4, f"got {len(result['actions'])}")
check("has summary", "action(s)" in result["summary"])


# ══════════════════════════════════════════════════════════════
# S9: Serialization round-trip
# ══════════════════════════════════════════════════════════════
print("\n[S9]  Serialization")

plan_ser = evaluate_policy(compile_actions({
    "proposed_files": [{"path": "a.py", "operation": "create"}],
    "proposed_commands": [],
}))
d = plan_ser.to_dict()
check("to_dict has actions key", "actions" in d)
check("action dict has type", d["actions"][0]["type"] == "file_create")
check("action dict has policy_decision", d["actions"][0]["policy_decision"] == "allow")
check("action dict has reason (not policy_reason)", "reason" in d["actions"][0] and "policy_reason" not in d["actions"][0])
check("action dict has params", isinstance(d["actions"][0].get("params"), dict))
check("file action params has operation", d["actions"][0]["params"].get("operation") == "create")


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  6G-A action policy: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
