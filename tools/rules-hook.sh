#!/bin/sh
# rules-hook.sh — deliver a territory's rules when a file in it is edited.
#
# The three territory briefs were dispatched zero times as agents. Their
# content is load-bearing -- the liveness refusal, the seal rules, the
# staging trap -- but as reference read at the moment it applies, not as a
# worker spawned to do the work. Agent briefs load when an agent starts, and
# the agent never started, so the rules were only ever read by luck. This
# puts them in front of whoever is about to edit the file they govern.
#
# PreToolUse hook. Always exits 0: a rules reminder must never block an edit.
set -eu
exec 2>/dev/null
IN=$(cat) || exit 0
python3 - "$IN" <<'PY' || exit 0
import json, os, sys, hashlib
try:
    ev = json.loads(sys.argv[1])
except Exception:
    raise SystemExit(0)
path = (ev.get("tool_input") or {}).get("file_path") or ""
base = os.path.basename(path)
rel = path.split("/city/")[-1]

MAP = [
    ({"dawn.c","pid1.c","nwspawn.c","nwsup.c","lids.c","lids.h"}, "runtime"),
    ({"blob.h","nwcheck.c","nwcheck_main.c","nw-cc.py","plan.als","Plan.tla"}, "plan"),
    ({"run.py","unit_probe.c"}, "harness"),
]
name = None
for names, n in MAP:
    if base in names:
        name = n; break
if name is None and rel.startswith("houses/"):
    name = "harness"
if name is None:
    raise SystemExit(0)

src = os.path.join(".claude", "rules", name + ".md")
if not os.path.isfile(src):
    raise SystemExit(0)

# Once per rules file per working tree state, so editing ten files in one
# territory does not repeat the same text ten times.
stamp = os.path.join(".reviews", ".rules." + name)
try:
    os.makedirs(".reviews", exist_ok=True)
    if os.path.exists(stamp) and os.path.getmtime(stamp) > os.path.getmtime(src):
        raise SystemExit(0)
    open(stamp, "w").close()
except Exception:
    pass

text = open(src).read()
out = {"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext":
        f"Rules for the {name} territory (you are editing {rel}). "
        f"Source: {src}\n\n" + text}}
print(json.dumps(out))
PY
