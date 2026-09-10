#!/bin/sh
# rules-hook.sh — deliver a territory's rules when a file in it is edited.
#
# The three territory briefs were dispatched zero times as agents. Their
# content is load-bearing -- the liveness refusal, the seal rules, the
# staging trap -- but as reference read at the moment it applies, not as a
# worker spawned to do the work. This puts them in front of whoever is about
# to edit the file they govern.
#
# It matches Bash as well as Edit and Write, and that is not tidiness. The
# Edit|Write-only version worked perfectly and never once fired, because the
# edits here are mostly made through Bash with python heredocs. A mechanism
# that is correct and routed around is worse than a broken one: it looks like
# it is working. For Bash there is no file_path, so the command string is
# searched for a territory filename.
#
# False positives are cheap by construction: a mention of nwsup.c in a grep
# delivers the runtime rules once and the stamp suppresses the rest. Silence
# is the expensive failure, not noise.
#
# PreToolUse hook. Always exits 0: a rules reminder must never block an edit.
set -eu
exec 2>/dev/null
IN=$(cat) || exit 0
python3 - "$IN" <<'PY' || exit 0
import json, os, re, sys
try:
    ev = json.loads(sys.argv[1])
except Exception:
    raise SystemExit(0)

ti = ev.get("tool_input") or {}
path = ti.get("file_path") or ""
cmd = ti.get("command") or ""

MAP = [
    ({"dawn.c", "pid1.c", "nwspawn.c", "nwsup.c", "lids.c", "lids.h"}, "runtime"),
    ({"blob.h", "nwcheck.c", "nwcheck_main.c", "nw-cc.py", "plan.als",
      "Plan.tla"}, "plan"),
    ({"run.py", "unit_probe.c"}, "harness"),
]

name = None
if path:
    base = os.path.basename(path)
    for names, n in MAP:
        if base in names:
            name = n
            break
    if name is None and "houses/" in path:
        name = "harness"
    where = path.split("/city/")[-1]
elif cmd:
    for names, n in MAP:
        if any(re.search(r"(?<![\w/.-])" + re.escape(b) + r"(?![\w-])", cmd)
               for b in names):
            name = n
            break
    if name is None and re.search(r"houses/\w", cmd):
        name = "harness"
    where = "a Bash command naming a file in it"

if name is None:
    raise SystemExit(0)

src = os.path.join(".claude", "rules", name + ".md")
if not os.path.isfile(src):
    raise SystemExit(0)

# Once per rules file until that file changes, so touching ten files in one
# territory does not repeat the same text ten times.
stamp = os.path.join(".reviews", ".rules." + name)
try:
    os.makedirs(".reviews", exist_ok=True)
    if os.path.exists(stamp) and os.path.getmtime(stamp) > os.path.getmtime(src):
        raise SystemExit(0)
    open(stamp, "w").close()
except SystemExit:
    raise
except Exception:
    pass

out = {"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext":
        f"Rules for the {name} territory (you are touching {where}). "
        f"Source: {src}\n\n" + open(src).read()}}
print(json.dumps(out))
PY
