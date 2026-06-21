"""B1-Arch-甲-1 hermetic tests for agents.scoped_file_reader.list_scoped_tree.

Mirrors the write-sandbox test style (tempfile.mkdtemp + shutil.rmtree). Proves
the sandboxed directory walk: ALLOW in-workspace files (relative POSIX, sorted);
PRUNE sensitive dirs; do NOT follow a symlinked dir OUT (the key escape risk);
skip symlinked files; respect entry + char caps; degrade on bad workspace.

PATHS ONLY — the lister never returns file contents.

    python tests/b1_scoped_tree_test.py
"""

import os
import shutil
import sys
import tempfile

# Throwaway DB BEFORE importing the runtime (database resolves its path at import).
os.environ.setdefault(
    "RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="b1_tree_")
)

_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.scoped_file_reader import list_scoped_tree  # noqa: E402

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


def _write(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ══════════════════════════════════════════════════════════════
# ALLOW: nested in-workspace files → relative POSIX paths, deterministic
# ══════════════════════════════════════════════════════════════
print("\n[ALLOW] nested in-workspace files (relative POSIX, sorted)")
ws = tempfile.mkdtemp(prefix="b1_tree_allow_")
try:
    _write(os.path.join(ws, "README.md"))
    _write(os.path.join(ws, "src", "app.py"))
    _write(os.path.join(ws, "src", "util", "helpers.py"))

    paths, truncated = list_scoped_tree(ws)
    check("returns the 3 in-workspace files",
          paths == ["README.md", "src/app.py", "src/util/helpers.py"], str(paths))
    check("not truncated", truncated is False)
    check("relative POSIX (no backslashes, no abs)",
          all(("\\" not in p) and (not os.path.isabs(p)) for p in paths), str(paths))
    paths2, _ = list_scoped_tree(ws)
    check("deterministic across calls", paths == paths2)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# PRUNE sensitive dirs/files: .git/.env/node_modules/__pycache__/dist/build
# ══════════════════════════════════════════════════════════════
print("\n[PRUNE] sensitive dirs/files excluded and not descended")
ws = tempfile.mkdtemp(prefix="b1_tree_sens_")
try:
    _write(os.path.join(ws, "README.md"))
    _write(os.path.join(ws, "src", "app.py"))
    _write(os.path.join(ws, ".git", "config"))
    _write(os.path.join(ws, ".git", "HEAD"))
    _write(os.path.join(ws, ".env"), "API_KEY=secret")
    _write(os.path.join(ws, "node_modules", "lib", "index.js"))
    _write(os.path.join(ws, "__pycache__", "x.pyc"))
    _write(os.path.join(ws, "dist", "bundle.js"))
    _write(os.path.join(ws, "build", "out.o"))

    paths, _ = list_scoped_tree(ws)
    check("only the 2 non-sensitive files returned",
          paths == ["README.md", "src/app.py"], str(paths))
    for needle in (".git", ".env", "node_modules", "__pycache__", "dist", "build"):
        check(f"no '{needle}' path leaked",
              all(needle not in p for p in paths), str(paths))
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# SYMLINK-DIR ESCAPE: a symlink inside → an outside dir is NOT followed
# ══════════════════════════════════════════════════════════════
print("\n[ESCAPE] symlinked dir pointing outside the workspace is not followed")
parent = tempfile.mkdtemp(prefix="b1_tree_esc_")
try:
    ws = os.path.join(parent, "workspace")
    outside = os.path.join(parent, "outside")
    _write(os.path.join(ws, "real.txt"))
    _write(os.path.join(outside, "secret.txt"), "ESCAPE-SECRET-DO-NOT-LEAK")
    link = os.path.join(ws, "linkdir")
    try:
        os.symlink(outside, link, target_is_directory=True)
        link_ok = os.path.islink(link)
    except (OSError, NotImplementedError, AttributeError) as exc:
        link_ok = False
        skip("symlinked-dir escape blocked", f"cannot create symlink here: {exc}")
    if link_ok:
        paths, _ = list_scoped_tree(ws)
        check("escape: in-workspace file present", "real.txt" in paths, str(paths))
        check("escape: outside secret NOT listed",
              all("secret.txt" not in p for p in paths), str(paths))
        check("escape: symlinked dir not descended (no linkdir/ paths)",
              all(not p.startswith("linkdir/") for p in paths), str(paths))
finally:
    shutil.rmtree(parent, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# SYMLINK-FILE: a symlinked file inside the workspace is skipped
# ══════════════════════════════════════════════════════════════
print("\n[SYMLINK-FILE] symlinked file inside workspace is skipped")
ws = tempfile.mkdtemp(prefix="b1_tree_lf_")
try:
    _write(os.path.join(ws, "target.txt"))
    flink = os.path.join(ws, "link.txt")
    try:
        os.symlink(os.path.join(ws, "target.txt"), flink)
        flink_ok = os.path.islink(flink)
    except (OSError, NotImplementedError, AttributeError) as exc:
        flink_ok = False
        skip("symlinked-file skipped", f"cannot create symlink here: {exc}")
    if flink_ok:
        paths, _ = list_scoped_tree(ws)
        check("symlink-file: real target present", "target.txt" in paths, str(paths))
        check("symlink-file: symlink itself skipped", "link.txt" not in paths, str(paths))
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# ENTRY CAP: > max_entries files → exactly max_entries, truncated=True
# ══════════════════════════════════════════════════════════════
print("\n[ENTRY CAP] more files than max_entries → capped + truncated")
ws = tempfile.mkdtemp(prefix="b1_tree_cap_")
try:
    for i in range(8):
        _write(os.path.join(ws, f"f{i}.txt"))
    paths, truncated = list_scoped_tree(ws, max_entries=5, max_total_chars=12_000)
    check("entry cap: exactly 5 returned", len(paths) == 5, str(paths))
    check("entry cap: truncated True", truncated is True)
    check("entry cap: deterministic first-5 (sorted)",
          paths == ["f0.txt", "f1.txt", "f2.txt", "f3.txt", "f4.txt"], str(paths))
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# CHAR CAP: tiny max_total_chars → truncated + budget respected
# ══════════════════════════════════════════════════════════════
print("\n[CHAR CAP] tiny char budget → truncated + budget respected")
ws = tempfile.mkdtemp(prefix="b1_tree_char_")
try:
    _write(os.path.join(ws, "aaa.txt"))  # 7 chars
    _write(os.path.join(ws, "bbb.txt"))
    _write(os.path.join(ws, "ccc.txt"))
    paths, truncated = list_scoped_tree(ws, max_entries=1000, max_total_chars=10)
    used = sum(len(p) + 1 for p in paths)
    check("char cap: truncated True", truncated is True)
    check("char cap: budget respected (<=10)", used <= 10, f"used={used}, paths={paths}")
    check("char cap: at least one path kept (first sorted)", paths == ["aaa.txt"], str(paths))
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# BAD workspace_root: empty / nonexistent / file-not-dir → ([], False)
# ══════════════════════════════════════════════════════════════
print("\n[BAD] invalid workspace_root degrades to ([], False)")
empty_res = list_scoped_tree("")
check("empty workspace_root -> ([], False)", empty_res == ([], False), str(empty_res))
none_res = list_scoped_tree("/path/does/not/exist/xyz123")
check("nonexistent workspace_root -> ([], False)", none_res == ([], False), str(none_res))
tmpfile = tempfile.mktemp(suffix=".txt", prefix="b1_tree_notdir_")
try:
    with open(tmpfile, "w", encoding="utf-8") as f:
        f.write("not a dir")
    file_res = list_scoped_tree(tmpfile)
    check("file-not-dir workspace_root -> ([], False)", file_res == ([], False), str(file_res))
finally:
    try:
        os.unlink(tmpfile)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  B1 scoped_tree: {total} checks  Pass: {PASS}  Fail: {FAIL}  Skip: {SKIP}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
