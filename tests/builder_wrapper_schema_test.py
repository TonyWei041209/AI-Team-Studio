"""C2 real-model N: BuilderOutputSchema enforces the WRAPPER {"proposals":[...]}.

THE SAFETY-RELEVANT schema test. The real Builder must emit a wrapper of 2-3 proposals; a
single-proposal / top-level-proposed_files output is REJECTED → the Builder (blocking) aborts
rather than emitting a gate-tripping single proposal that would double-create alongside the
Comparator. The gate/transfer code is unchanged; this validation is the enforcement.

Cases:
  (1) WRAPPER VALID: {proposals:[p1,p2]} and {proposals:[p1,p2,p3]} → validate passes.
  (2) SAFETY — SINGLE-PROPOSAL REJECTED: an old-shape single proposal (top-level proposed_files,
      no "proposals" key) → validate FAILS. (This is what prevents the gate double-creation.)
  (3) COUNT BOUNDS: N=1 (<min) fails; N=4 (>max) fails; N=0 fails.
  (4) PER-PROPOSAL: a bad proposal inside the list → fails, reporting the index.
  (5) NORMALIZE-OVER-N: normalization mapped over the wrapper sets risk_level + requires_approval
      on EACH proposal (the fields the Comparator selects on).
  (6) _validate_single_proposal: the extracted helper validates one proposal correctly.

Hermetic: pure schema/normalize functions; no DB/provider/app/net.

    python tests/builder_wrapper_schema_test.py
"""
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="builder_wrap_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.model_executor import (  # noqa: E402
    BuilderOutputSchema, _normalize_builder_proposal,
    _BUILDER_MIN_PROPOSALS, _BUILDER_MAX_PROPOSALS,
)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"  -- {detail}" if detail else ""))


def _proposal(summary="impl"):
    """A valid Builder-shaped single proposal (top-level proposed_files — the OLD shape)."""
    return {
        "change_summary": summary,
        "proposed_files": [{"path": "a.py", "action": "create", "reason": "r", "content": "c"}],
        "change_steps": [{"step": 1, "description": "d"}],
        "reasoning_summary": "why",
        "validation_plan": ["run tests"],
        "risk_notes": [],
    }


# Sanity: the bounds are the locked [2, 3]
check("bounds: min=2, max=3", _BUILDER_MIN_PROPOSALS == 2 and _BUILDER_MAX_PROPOSALS == 3,
      f"{_BUILDER_MIN_PROPOSALS}/{_BUILDER_MAX_PROPOSALS}")


# ── (1) WRAPPER VALID ──────────────────────────────────────────
print("\n[1] wrapper valid (N=2 and N=3)")
ok2, err2 = BuilderOutputSchema.validate({"proposals": [_proposal("a"), _proposal("b")]})
check("1: {proposals:[p1,p2]} passes", ok2 is True, err2)
ok3, err3 = BuilderOutputSchema.validate({"proposals": [_proposal("a"), _proposal("b"), _proposal("c")]})
check("1: {proposals:[p1,p2,p3]} passes", ok3 is True, err3)


# ── (2) SAFETY — SINGLE-PROPOSAL REJECTED (the critical assertion) ──
print("\n[2] SAFETY: single-proposal (old shape, top-level proposed_files) REJECTED")
single = _proposal()  # has top-level proposed_files, NO "proposals" key
ok_s, err_s = BuilderOutputSchema.validate(single)
check("2: a single-proposal output FAILS validation (no double-creation on the real path)",
      ok_s is False, f"unexpectedly passed: {single.keys()}")
check("2: failure message points at the missing wrapper/'proposals'",
      "wrapper" in err_s.lower() or "proposals" in err_s.lower(), err_s)
# and a wrapper whose "proposals" is not a list is also rejected
ok_nl, _ = BuilderOutputSchema.validate({"proposals": "not-a-list"})
check("2: {proposals: <non-list>} rejected", ok_nl is False)
# non-dict top level rejected
ok_nd, _ = BuilderOutputSchema.validate(["p1", "p2"])
check("2: non-dict top level rejected", ok_nd is False)


# ── (3) COUNT BOUNDS ───────────────────────────────────────────
print("\n[3] count bounds [2, 3]")
ok_n1, err_n1 = BuilderOutputSchema.validate({"proposals": [_proposal()]})
check("3: N=1 (<min) fails", ok_n1 is False, err_n1)
ok_n0, _ = BuilderOutputSchema.validate({"proposals": []})
check("3: N=0 fails", ok_n0 is False)
ok_n4, err_n4 = BuilderOutputSchema.validate({"proposals": [_proposal(), _proposal(), _proposal(), _proposal()]})
check("3: N=4 (>max) fails", ok_n4 is False, err_n4)


# ── (4) PER-PROPOSAL ───────────────────────────────────────────
print("\n[4] per-proposal validation (bad element rejected, index reported)")
bad = {"change_summary": "x"}  # missing proposed_files / change_steps / ...
ok_pp, err_pp = BuilderOutputSchema.validate({"proposals": [_proposal(), bad]})
check("4: a bad proposal in the list fails", ok_pp is False)
check("4: error reports the failing index (proposals[1])", "proposals[1]" in err_pp, err_pp)


# ── (5) NORMALIZE-OVER-N ───────────────────────────────────────
print("\n[5] normalization mapped over N sets risk_level + requires_approval per proposal")
wrapper = {"proposals": [_proposal("a"), _proposal("b")]}
for p in wrapper["proposals"]:
    p.setdefault("risk_notes", [])
    _normalize_builder_proposal(p)
check("5: every proposal has risk_level after normalize-over-N",
      all("risk_level" in p for p in wrapper["proposals"]))
check("5: every proposal has requires_approval after normalize-over-N (Comparator selects on it)",
      all("requires_approval" in p for p in wrapper["proposals"]))
check("5: low-risk create/modify → requires_approval False (auto-approvable)",
      all(p["requires_approval"] is False for p in wrapper["proposals"]),
      str([p.get("risk_level") for p in wrapper["proposals"]]))


# ── (6) _validate_single_proposal helper ───────────────────────
print("\n[6] _validate_single_proposal helper (the extracted single-proposal logic)")
ok_h, _ = BuilderOutputSchema._validate_single_proposal(_proposal())
check("6: helper accepts a valid single proposal", ok_h is True)
ok_hb, err_hb = BuilderOutputSchema._validate_single_proposal({"change_summary": "x"})
check("6: helper rejects a proposal missing proposed_files", ok_hb is False, err_hb)
ok_hn, _ = BuilderOutputSchema._validate_single_proposal("not-a-dict")
check("6: helper rejects a non-dict proposal", ok_hn is False)


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  builder wrapper schema (C2 real-model N): {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
