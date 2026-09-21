#!/bin/sh
# rules-hook.sh — deliver a territory's rules when a file in it is edited,
# and answer "is every code file accounted for" when asked.
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
# ONE LIST. MAP below is it. There is deliberately no second machine-readable
# copy and no comparator between two copies: comparing prose to a map means
# parsing prose, which is the derivation tool the rewrap incident counted
# against, and a second list earns a comparator only if the two have
# different edit paths. These would not -- of the five commits that have
# touched this file, four also touched .claude/rules/, and the fifth
# (`a65286e`) only rewrapped MAP's whitespace without changing its
# membership. Two lists would be one list written twice.
#
# WHAT REPLACES A COMPARISON IS AN ABSENCE CHECK AGAINST REALITY. `--check`
# enumerates the tracked code files and asserts each one is accounted for:
# classified into a territory, or listed in UNOWNED with a reason. That is
# the only instrument that catches the author's blind spot, because it is
# sourced from the filesystem rather than from anything the author wrote.
#
# --check and the event path share `classify()`. They must: the attempt this
# replaces was titled "the hook never read the map it claimed to", and a
# check that re-parsed MAP from outside would be that bug with the parties
# swapped. `--check` is called by install-agents.sh, which `make test` runs,
# because the event path exits 0 always and cannot be loud.
#
# --territories prints MAP's names so install-agents.sh does not hold a
# third copy of them.
#
# PreToolUse hook. The EVENT PATH always exits 0: a rules reminder must never
# block an edit. --check does not, because refusing is its whole job.
set -eu

MODE=${1:-}

# THE ROOT COMES FROM $0, not from the cwd and not from the caller. The
# python below runs from a `python3 - <<PY` heredoc, so `__file__` is
# "<stdin>" and argv[0] is "-": nothing inside it can name this script, and
# the obvious dirname-of-__file__ walk lands on the PARENT of the cwd, which
# on this machine is a home directory. The shell does know, because $0 is
# what the caller typed -- settings.json invokes an absolute path -- so the
# shell computes the root and hands it over.
#
# Chosen over $CLAUDE_PROJECT_DIR passed as an argument, which was the other
# candidate: that makes settings.json and this script a two-file contract
# with nothing checking it, and if the argument were ever dropped the cwd
# fallback would answer in the common case and hide the drift. $0 needs no
# cooperation from the caller at all.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." 2>/dev/null && pwd) || ROOT=
[ -n "$ROOT" ] || ROOT=$(pwd)

case "$MODE" in
  --check|--territories) IN='' ;;
  *) IN=$(cat) || exit 0 ;;
esac

rc=0
python3 - "$MODE" "$ROOT" "$IN" <<'PY' || rc=$?
import json, os, re, subprocess, sys

mode, root, raw = sys.argv[1], sys.argv[2], sys.argv[3]

MAP = [
    ({"dawn.c", "pid1.c", "nwspawn.c", "nwsup.c", "lids.c", "lids.h",
      "rescue.c", "initrd-init.c", "mkboot.sh",
      "landlock-assertions-dryrun.py"}, "runtime"),
    ({"blob.h", "nwcheck.c", "nwcheck_main.c", "nw-cc.py", "plan.als",
      "Plan.tla", "stage-candidate.py", "gen-spec-limits.py",
      "caller_nw_check.c", "leaf_name_dup.c", "leaf_name_ok.c",
      "leaf_path_ok.c"}, "plan"),
    ({"run.py", "unit_probe.c", "scale-probe.py"}, "harness"),
]

# Tracked code files that no territory owns, each with the reason there is
# none. A REASON IS REQUIRED: --check refuses an entry whose reason is empty,
# because an exemption list with blank rows is how a green check ends up
# certifying the blind spot instead of catching it. An entry ending in "/"
# covers everything beneath it.
#
# These are a QUEUE, not a settled state. Several say "the rules file
# describes this at length and does not own it", which is the gap worth
# looking at first.
UNOWNED = [
    ("bakery/fold.py",
     "the fold engine. CLAUDE.md invariant 9 keeps it deliberately "
     "unguarded so the tree-level tests can exercise merging with no "
     "privileges; the rule lives on its caller. Folding has no territory."),
    ("bakery/mkbrick.py",
     "the brick packer, non-TCB by CLAUDE.md's table. plan.md owns "
     "bakery/nw-cc.py and nothing else under bakery/."),
    ("bakery/test_fold.py",
     "the fold suite. make test runs it beside tests/run.py, but "
     "harness.md's scope is the put-together suite and the fixture "
     "houses, which this is not."),
    ("install-agents.sh",
     "the brief gate, and the caller of this file's --check. No rules "
     "file describes the gate tooling."),
    ("proofs/mkcomp.py",
     "assembles a proof's compilation unit. proofs/README.md governs it "
     "and no rules file does. NOT a prefix entry for proofs/: the four "
     "harnesses beneath it are plan's, and a directory exemption would "
     "have let a new file under proofs/ through the census silently, "
     "which is the blind spot the census exists to catch."),
    ("proofs/run.sh",
     "the proof runner -- bounds, timing, the SKIP-versus-FAIL exit "
     "codes CLAUDE.md's build section is about. Same reasoning as "
     "mkcomp.py; governed by prose that is not a rules file."),
    ("tools/checkbrief.py",
     "verifies CLAUDE.md's invariant annotations. Gate tooling, no "
     "rules file."),
    ("tools/test_checkbrief.py",
     "its tests. Same gap, and not harness.md's suite."),
    ("tools/coverage-merge.sh",
     "merges coverage records across machines. Gate tooling."),
    ("tools/coverage-tcb.sh",
     "the coverage floor make test enforces. Gate tooling."),
    ("tools/fold-house.py",
     "CLAUDE.md invariant 9's actual subject -- the invariant is about "
     "this caller and says so -- and no territory owns it. The rule "
     "that governs it is a numbered invariant in CLAUDE.md, which every "
     "agent already has, so delivery would add nothing."),
    ("tools/prereport.py",
     "the pre-push heuristics. Gate tooling."),
    ("tools/review-gate.sh",
     "the review gate. Gate tooling."),
    ("tools/review-pack.sh",
     "builds the reviewer packet. Gate tooling."),
    ("tools/rules-hook.sh",
     "this file. Delivery is not itself a territory, and no rules file "
     "states the hook's contract -- which is the gap, not the "
     "circularity."),
    ("tools/stage-layers.py",
     "SPANS plan AND runtime, which is why it is here rather than in "
     "either. plan.md's sidecar rules are statements about which files "
     "it refuses and how it re-derives the blob hash; runtime.md's THE "
     "RECOVERY is a two-line procedure whose second line is this tool, "
     "and its \"nw-sup creates NEITHER\" rule is a statement about what "
     "this tool must create. Editing it wants both, and the hook "
     "delivers one. Assigning it to either would silence the other."),
]

CODE = (".c", ".h", ".py", ".sh", ".als", ".tla")


def classify(path):
    """The territory a path belongs to, or None.

    THE EVENT PATH AND --check BOTH CALL THIS, and that is the point.
    UNOWNED is deliberately not consulted here: an unowned file must get
    no delivery, so classify returns None for "no territory" whether or
    not anyone has written down why. --check is what tells those apart.
    """
    base = os.path.basename(path)
    for names, n in MAP:
        if base in names:
            return n
    if "houses/" in path:
        return "harness"
    return None


def territories():
    return [n for _, n in MAP]


def unowned_reason(rel):
    for p, why in UNOWNED:
        if (rel == p) or (p.endswith("/") and rel.startswith(p)):
            return why
    return None


def check():
    bad = []
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root,
                             capture_output=True, text=True, check=True).stdout
    except Exception as e:
        print(f"rules-hook --check: cannot enumerate the tree: {e}")
        return 1
    files = [f for f in out.split("\n")
             if f and not f.startswith("attic/") and f.endswith(CODE)]

    # A FLOOR AND THREE ANCHORS, so the check cannot pass vacuously. An
    # enumeration that returns nothing, and a classify() that returns None
    # for everything, both produce an empty `bad` list and would otherwise
    # read as success -- the same not-run-versus-nothing-found confusion
    # this whole exercise is about.
    if len(files) < 40:
        bad.append(f"only {len(files)} code files enumerated, which is fewer "
                   f"than this tree has ever had -- the enumeration is "
                   f"broken, not the ownership")
    for probe, want in (("pid1.c", "runtime"), ("nwcheck.c", "plan"),
                        ("tests/run.py", "harness")):
        got = classify(probe)
        if got != want:
            bad.append(f"classify({probe!r}) is {got!r}, expected {want!r} -- "
                       f"the classifier is broken, so every answer below it "
                       f"is worthless")

    for rel in files:
        t = classify(rel)
        why = unowned_reason(rel)
        if t and why:
            bad.append(f"{rel} is owned by {t} AND listed in UNOWNED; one of "
                       f"the two is wrong")
        elif not t and why is None:
            bad.append(f"{rel} is a code file that no territory owns and "
                       f"UNOWNED does not mention. Add it to MAP, or to "
                       f"UNOWNED with the reason there is no territory for "
                       f"it. Do not add a blank reason.")
    for p, why in UNOWNED:
        if not (why or "").strip():
            bad.append(f"UNOWNED entry {p!r} has no reason")

    for b in bad:
        print("rules-hook --check: " + b)
    if not bad:
        print(f"rules-hook: {len(files)} code files, "
              f"{sum(1 for f in files if classify(f))} owned, "
              f"{len(UNOWNED)} unowned entries each with a reason")
    return 1 if bad else 0


if mode == "--territories":
    print(" ".join(territories()))
    raise SystemExit(0)
if mode == "--check":
    raise SystemExit(check())

try:
    ev = json.loads(raw)
except Exception:
    raise SystemExit(0)

ti = ev.get("tool_input") or {}
path = ti.get("file_path") or ""
cmd = ti.get("command") or ""

name = None
if path:
    name = classify(path)
    # Relative to the root this script computed, not to a hardcoded
    # directory name. It was `path.split("/city/")[-1]`, which is the
    # checkout's own folder name baked into the trusted-ish path of a tool
    # that is supposed to work from anywhere.
    where = os.path.relpath(path, root) if os.path.isabs(path) else path
    if where.startswith(".."):
        where = path
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

src = os.path.join(root, ".claude", "rules", name + ".md")
if not os.path.isfile(src):
    raise SystemExit(0)

# Once per rules file until that file changes, so touching ten files in one
# territory does not repeat the same text ten times. Rooted like src: a
# stamp written relative to the cwd lands somewhere different on every
# invocation, which makes the suppression a silent no-op rather than a
# visible failure.
stamp = os.path.join(root, ".reviews", ".rules." + name)
try:
    os.makedirs(os.path.join(root, ".reviews"), exist_ok=True)
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
        f"Source: {os.path.relpath(src, root)}\n\n" + open(src).read()}}
print(json.dumps(out))
PY

case "$MODE" in
  --check|--territories) exit $rc ;;
  *) exit 0 ;;
esac
