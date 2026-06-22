"""ReadFileTool / ListDirectoryTool sandboxing — containment + fail-closed + shape-preservation.

Proves the tools now route through the scoped containment (read_scoped_file / list_scoped_dir):
in-workspace reads still work with the SAME ToolResult output shape, while absolute /
traversal / sensitive / out-of-workspace paths are BLOCKED, and a missing workspace root
fails closed (never an unsandboxed read).

Hermetic: real temp workspace + a file OUTSIDE it; no DB, no app.

    python tests/sandboxed_file_tools_test.py
"""
import asyncio
import os
import sys
import tempfile

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="sbx_tools_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from tools.file_tools import ReadFileTool, ListDirectoryTool  # noqa: E402
from tools.base import ToolContext  # noqa: E402

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


# ── Build a temp workspace + an OUTSIDE secret file ───────────────
WS = tempfile.mkdtemp(prefix="ws_")
with open(os.path.join(WS, "hello.txt"), "w", encoding="utf-8") as f:
    f.write("hi there")
os.makedirs(os.path.join(WS, "src"), exist_ok=True)
with open(os.path.join(WS, "src", "mod.py"), "w", encoding="utf-8") as f:
    f.write("x = 1\n")
with open(os.path.join(WS, ".env"), "w", encoding="utf-8") as f:
    f.write("SECRET=topsecret-value")

OUTSIDE_FD, OUTSIDE = tempfile.mkstemp(prefix="outside_", suffix=".txt")
os.close(OUTSIDE_FD)
OUTSIDE_SECRET = "OUTSIDE-FILE-CONTENT-MUST-NOT-LEAK"
with open(OUTSIDE, "w", encoding="utf-8") as f:
    f.write(OUTSIDE_SECRET)

CTX = ToolContext(project_id="p1", working_dir=WS)
read = ReadFileTool()
lsdir = ListDirectoryTool()


def run(tool, params, ctx=CTX):
    return asyncio.run(tool.execute(params, ctx))


# ── read_file ────────────────────────────────────────────────────
print("\n[read_file]")
r = run(read, {"path": "hello.txt"})
check("(a) in-workspace read succeeds", r.success, getattr(r, "error", ""))
check("(a) output shape {path,content,size} preserved",
      isinstance(r.output, dict) and set(r.output) == {"path", "content", "size"}, str(r.output))
check("(a) content correct", r.output.get("content") == "hi there", str(r.output))
check("(a) size = utf-8 byte length", r.output.get("size") == len("hi there".encode("utf-8")))

r = run(read, {"path": OUTSIDE})  # absolute path outside workspace — THE KEY REGRESSION GUARD
check("(b) absolute out-of-workspace read BLOCKED", r.success is False, str(r.output))
check("(b) no content leaked (output is None)", r.output is None)
check("(b) outside secret NOT in the error", OUTSIDE_SECRET not in (r.error or ""))
check("(b) reason mentions absolute", "bsolute" in (r.error or ""), r.error)

r = run(read, {"path": "../" + os.path.basename(OUTSIDE)})
check("(c) ../ traversal escaping workspace BLOCKED", r.success is False, r.error)

r = run(read, {"path": ".env"})
check("(d) sensitive in-workspace path (.env) BLOCKED", r.success is False, r.error)
check("(d) sensitive reason surfaced", "ensitive" in (r.error or ""), r.error)

r = run(read, {"path": "hello.txt"}, ctx=None)  # no context → no workspace root
check("(e1) context=None fails closed", r.success is False and r.output is None, r.error)
check("(e1) fail-closed reason is 'no workspace root'", "no workspace root" in (r.error or ""), r.error)
r = run(read, {"path": "hello.txt"}, ctx=ToolContext(project_id="p", working_dir=""))
check("(e2) empty working_dir fails closed (NOT unsandboxed)", r.success is False, r.error)


# ── list_directory ───────────────────────────────────────────────
print("\n[list_directory]")
r = run(lsdir, {"path": "."})
check("(f) list workspace root succeeds", r.success, getattr(r, "error", ""))
check("(f) output shape {path,entries,count} preserved",
      isinstance(r.output, dict) and set(r.output) == {"path", "entries", "count"}, str(r.output))
names = {e["name"] for e in r.output["entries"]}
check("(f) lists hello.txt + src", {"hello.txt", "src"}.issubset(names), str(names))
check("(f) SKIPS sensitive .env", ".env" not in names, str(names))
check("(f) per-entry type/size preserved",
      all(set(e) == {"name", "type", "size"} for e in r.output["entries"]),
      str(r.output["entries"]))
src_entry = next(e for e in r.output["entries"] if e["name"] == "src")
check("(f) src typed as directory", src_entry["type"] == "directory" and src_entry["size"] is None)

r = run(lsdir, {"path": "src"})
check("(f) list in-workspace subdir succeeds", r.success, r.error)
check("(f) subdir lists mod.py with a real byte size",
      any(e["name"] == "mod.py" and isinstance(e["size"], int) and e["size"] > 0 for e in r.output["entries"]),
      str(r.output))

r = run(lsdir, {"path": os.path.dirname(OUTSIDE)})  # absolute outside dir
check("(f) absolute out-of-workspace list BLOCKED", r.success is False, str(r.output))
r = run(lsdir, {"path": ".."})
check("(f) ../ traversal list BLOCKED", r.success is False, r.error)
r = run(lsdir, {"path": "."}, ctx=None)
check("(f) list fails closed with no workspace root", r.success is False and "no workspace root" in (r.error or ""), r.error)


print()
print("-" * 60)
total = PASS + FAIL
print(f"  sandboxed file tools: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
