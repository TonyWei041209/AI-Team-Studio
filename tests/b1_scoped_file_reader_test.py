"""B1-b hermetic sandbox tests for agents.scoped_file_reader.read_scoped_file.

Mirrors the write-sandbox test style (tempfile.mkdtemp workspace, shutil.rmtree
cleanup).  Asserts the reader ALLOWS a normal in-workspace file and DENIES every
escape: absolute path, ../ traversal, symlink target, sensitive path, missing
file, and oversized file.  Read-only; no DB required.

    python tests/b1_scoped_file_reader_test.py
"""

import os
import shutil
import sys
import tempfile

# A throwaway DB path so importing the runtime (which transitively imports
# database at module load) never touches the real dev database.  No DB
# connection is actually made by these read tests.
os.environ.setdefault(
    "RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="b1_reader_")
)

# Ensure services/runtime is importable.
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.scoped_file_reader import read_scoped_file  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label, condition, detail=""):
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


def skip(label, reason):
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label}  -- {reason}")


# ══════════════════════════════════════════════════════════════
# ALLOW: a normal file inside the workspace
# ══════════════════════════════════════════════════════════════
print("\n[ALLOW] normal in-workspace file")
ws = tempfile.mkdtemp(prefix="b1_reader_allow_")
try:
    os.makedirs(os.path.join(ws, "src"), exist_ok=True)
    root_file = os.path.join(ws, "README.md")
    with open(root_file, "w", encoding="utf-8") as f:
        f.write("# Title\nhello world\n")
    nested_file = os.path.join(ws, "src", "app.py")
    with open(nested_file, "w", encoding="utf-8") as f:
        f.write("print('hi')\n")

    ok, content = read_scoped_file("README.md", ws)
    check("root-level file allowed", ok, content)
    check("root-level content correct", ok and content == "# Title\nhello world\n")

    ok2, content2 = read_scoped_file("src/app.py", ws)
    check("nested file allowed", ok2, content2)
    check("nested content correct", ok2 and content2 == "print('hi')\n")
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# DENY: absolute path (even to a real, existing file outside the workspace)
# ══════════════════════════════════════════════════════════════
print("\n[DENY] absolute path")
ws = tempfile.mkdtemp(prefix="b1_reader_abs_")
abs_outside = tempfile.mktemp(suffix=".txt", prefix="b1_abs_target_")
try:
    with open(abs_outside, "w", encoding="utf-8") as f:
        f.write("SECRET OUTSIDE WORKSPACE\n")
    check("os.path.isabs sanity", os.path.isabs(abs_outside), abs_outside)
    ok, reason = read_scoped_file(abs_outside, ws)
    check("absolute path denied", not ok, f"unexpectedly read: {reason[:60]!r}")
    check("absolute denial mentions absolute", (not ok) and "absolute" in reason.lower(), reason)
finally:
    shutil.rmtree(ws, ignore_errors=True)
    try:
        os.unlink(abs_outside)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════
# DENY: ../ traversal escaping the workspace
# ══════════════════════════════════════════════════════════════
print("\n[DENY] ../ traversal")
parent = tempfile.mkdtemp(prefix="b1_reader_trav_")
try:
    ws = os.path.join(parent, "workspace")
    os.makedirs(ws, exist_ok=True)
    # A real secret sitting just outside the workspace.
    with open(os.path.join(parent, "secrets.txt"), "w", encoding="utf-8") as f:
        f.write("TOP SECRET\n")
    ok, reason = read_scoped_file("../secrets.txt", ws)
    check("single ../ traversal denied", not ok, reason)
    ok2, reason2 = read_scoped_file("../../etc/passwd", ws)
    check("deep ../../ traversal denied", not ok2, reason2)
finally:
    shutil.rmtree(parent, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# DENY: symlink target (skipped if the OS/user cannot create symlinks)
# ══════════════════════════════════════════════════════════════
print("\n[DENY] symlink target")
ws = tempfile.mkdtemp(prefix="b1_reader_link_")
try:
    target = os.path.join(ws, "real.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write("real content\n")
    link = os.path.join(ws, "link.txt")
    try:
        os.symlink(target, link)
        symlink_ok = os.path.islink(link)
    except (OSError, NotImplementedError, AttributeError) as exc:
        symlink_ok = False
        skip("symlink target denied", f"cannot create symlink here: {exc}")
    if symlink_ok:
        ok, reason = read_scoped_file("link.txt", ws)
        check("symlink target denied", not ok, reason)
        check("symlink denial mentions symlink", (not ok) and "symlink" in reason.lower(), reason)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# DENY: sensitive paths (.env, .git/config)
# ══════════════════════════════════════════════════════════════
print("\n[DENY] sensitive paths")
ws = tempfile.mkdtemp(prefix="b1_reader_sens_")
try:
    # Create the files so the ONLY reason for denial is the sensitive deny-list.
    with open(os.path.join(ws, ".env"), "w", encoding="utf-8") as f:
        f.write("API_KEY=supersecret\n")
    os.makedirs(os.path.join(ws, ".git"), exist_ok=True)
    with open(os.path.join(ws, ".git", "config"), "w", encoding="utf-8") as f:
        f.write("[core]\n")

    ok, reason = read_scoped_file(".env", ws)
    check(".env denied", not ok, reason)
    check(".env denial mentions sensitive", (not ok) and "sensitive" in reason.lower(), reason)

    ok2, reason2 = read_scoped_file(".git/config", ws)
    check(".git/config denied", not ok2, reason2)
    check(".git denial mentions sensitive", (not ok2) and "sensitive" in reason2.lower(), reason2)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# DENY: missing file
# ══════════════════════════════════════════════════════════════
print("\n[DENY] missing file")
ws = tempfile.mkdtemp(prefix="b1_reader_missing_")
try:
    ok, reason = read_scoped_file("nope/not_here.py", ws)
    check("missing file denied", not ok, reason)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# DENY: oversized file (> max_bytes)
# ══════════════════════════════════════════════════════════════
print("\n[DENY] oversized file")
ws = tempfile.mkdtemp(prefix="b1_reader_big_")
try:
    big = os.path.join(ws, "big.txt")
    with open(big, "w", encoding="utf-8") as f:
        f.write("x" * 5000)
    # Under the cap → allowed.
    ok_small, _ = read_scoped_file("big.txt", ws, max_bytes=10_000)
    check("under-cap file allowed", ok_small)
    # Over the cap → denied (and NOT read/truncated).
    ok_big, reason = read_scoped_file("big.txt", ws, max_bytes=100)
    check("over-cap file denied", not ok_big, reason)
    check("oversize denial mentions too large", (not ok_big) and "too large" in reason.lower(), reason)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# DENY: missing / empty workspace_root
# ══════════════════════════════════════════════════════════════
print("\n[DENY] missing workspace_root")
ok, reason = read_scoped_file("README.md", "")
check("empty workspace_root denied", not ok, reason)
ok2, reason2 = read_scoped_file("README.md", "/path/that/does/not/exist/xyz123")
check("nonexistent workspace_root denied", not ok2, reason2)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  B1 scoped_file_reader: {total} checks  Pass: {PASS}  Fail: {FAIL}  Skip: {SKIP}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
