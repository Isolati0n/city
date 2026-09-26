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
# ONE LIST. MAP below is it. There is deliberately no second
# machine-readable copy and no comparator between two copies, for two
# reasons that do not depend on counting anything.
#
# Comparing prose to a map means parsing prose, and parsing prose out of a
# `Scope:` sentence is the derivation tool the rewrap incident counted
# against (`1ac0235`).
#
# And a comparator earns its place by catching a divergence between two
# lists somebody maintains for their own reasons. A second list here would
# exist only in order to be compared, so it would be maintained by the
# comparator's complaints rather than by anyone needing it -- which is one
# list written twice, plus a gate that makes you write it twice.
#
# This comment used to argue the same thing with a commit count, and
# `claims` disproved it: the count was wrong on `main`, and the very commit
# that introduced the sentence changed MAP's membership without touching
# .claude/rules/, so the claim's own commit was a counterexample to it.
# Worse, its denominator included commits on a branch this repository was
# about to delete, which would have made it permanently uncheckable. The
# history is in docs/POSTMORTEM-rules-declaration.md, which is dated and
# where a count belongs.
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

# THE ROOT COMES FROM $0, RESOLVED, and there is no fallback. The python
# below runs from a `python3 - <<PY` heredoc, so `__file__` is "<stdin>"
# and argv[0] is "-": nothing inside it can name this script, and the
# obvious dirname-of-__file__ walk lands on the PARENT of the cwd, which on
# this machine is a home directory. The shell does know, because $0 is what
# the caller typed, so it hands $0 over and python resolves it.
#
# RESOLVED THROUGH SYMLINKS, because $0 can name a symlink -- one in ~/bin,
# say -- and then the walk lands on that directory's parent instead of the
# repository. realpath finds the real file, so that shape works rather than
# needing to be rescued.
#
# AND NO CWD FALLBACK, which is the part that took two rounds to get right.
# A fallback was added when the symlink case surfaced, and it turned a
# misinstalled hook into one that quietly served a DIFFERENT tree: the
# census fixture was a stub with no .claude/rules, the hook fell back to
# the cwd, checked the real repository, and reported it clean -- so a
# planted unaccounted file read as accepted. The fixture was made a full
# copy and the behaviour was left, which fixed the test and not the
# mechanism. A tool that cannot find its own rules must say so.
#
# Chosen over $CLAUDE_PROJECT_DIR passed as an argument: that makes
# settings.json and this script a two-file contract with nothing checking
# it, and a dropped argument would have been answered by the very fallback
# this comment is about.
SELF=$0

case "$MODE" in
  --check|--territories) IN='' ;;
  --owns) IN=${2:-} ;;
  *) IN=$(cat) || exit 0 ;;
esac

rc=0
python3 - "$MODE" "$SELF" "$IN" <<'PY' || rc=$?
import json, os, re, subprocess, sys

mode, self, raw = sys.argv[1], sys.argv[2], sys.argv[3]

# realpath, then up one from tools/. If this is not a checkout of this
# repository the hook says so; it never looks anywhere else.
root = os.path.dirname(os.path.dirname(os.path.realpath(self)))
rules_dir = os.path.join(root, ".claude", "rules")
rooted = os.path.isdir(rules_dir)

MAP = [
    ({"dawn.c", "pid1.c", "nwspawn.c", "nwsup.c", "lids.c", "lids.h",
      "rescue.c", "initrd-init.c", "mkboot.sh",
      "landlock-assertions-dryrun.py", "stage-layers.py"}, "runtime"),
    ({"blob.h", "nwcheck.c", "nwcheck_main.c", "nw-cc.py", "plan.als",
      "Plan.tla", "stage-candidate.py", "gen-spec-limits.py",
      "caller_nw_check.c", "leaf_name_dup.c", "leaf_name_ok.c",
      "leaf_path_ok.c", "stage-layers.py"}, "plan"),
    ({"console-boot-test.py", "run.py", "unit_probe.c", "scale-probe.py"}, "harness"),
]

# A FILE MAY BE OWNED BY TWO TERRITORIES, and this is where that is
# declared deliberate. Appearing in two MAP sets and not here is an
# accident -- almost certainly a name added to the wrong set -- and
# --check refuses it, naming both. The two cases are indistinguishable
# from MAP alone, which is the whole reason this table exists rather
# than the code simply tolerating a name in two sets.
#
# The alternative considered and rejected was leaving such a file
# unowned. That silences BOTH rules files, which is worse than silencing
# one: the argument for ownership is that editing a file is exactly when
# its rules should arrive, and a file governed by two sets of rules
# needs both of them more than a singly-governed file needs its one.
SHARED = {
    "stage-layers.py": (
        ("plan", "runtime"),
        "plan.md's sidecar rules are statements about this tool -- which "
        "field of a .layers line it reads, that it refuses when the hash "
        "sidecar is missing beside the layer one, and that it re-derives "
        "the blob hash rather than trusting it; runtime.md's "
        "THE RECOVERY is a two-line procedure whose second line is this "
        "tool, and its \"nw-sup creates NEITHER\" rule says what it must "
        "create. Editing it wants both."),
}

# Tracked code files that no territory owns, each with the reason there is
# none. A REASON IS REQUIRED: --check refuses an entry whose reason is empty,
# because an exemption list with blank rows is how a green check ends up
# certifying the blind spot instead of catching it. Entries are exact
# paths; see unowned_reason for why there is no directory form.
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
    ("tests/poll_shape.c",
     "shape control for nw-sup's poll set: two fds, not TCB. "
     "harness.md owns tests/run.py and the fixture houses, not this."),
    ("tests/count_wait.so.c",
     "LD_PRELOAD counter used by test_wait_is_poll_not_spin. "
     "Not TCB; not a house. The assertion lives in tests/run.py."),
    ("tests/block_pidfd.so.c",
     "LD_PRELOAD shim forcing pidfd_open ENOSYS, used by "
     "test_pidfd_open_failure_falls_back. Not TCB; not a house. The "
     "assertion lives in tests/run.py."),
    ("tests/block_both.so.c",
     "LD_PRELOAD shim forcing pidfd_open AND signalfd ENOSYS, used by "
     "test_ctl_tier3_fallback_with_socket to exercise wait_house's "
     "third fallback tier. Not TCB; not a house. The assertion lives "
     "in tests/run.py."),
    ("install-agents.sh",
     "the brief gate, and the caller of this file's --check. No rules "
     "file OWNS the gate tooling; what they say about it is a sentence "
     "or two each, which is not ownership. Do not enumerate those "
     "sentences here -- this reason twice tried to, and was wrong both "
     "times: first by naming harness.md as one of them, which stopped "
     "being true when its partial-gate section was rewritten to say "
     "READ THE TARGET, and then by claiming plan.md's was the only one "
     "left, while harness.md still describes this gate without naming "
     "the file. A reason naming a sentence in another file is a second "
     "copy of that sentence, so this one names none."),
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
     "this file. Delivery is not itself a territory. All three rules "
     "files state the DELIVERY contract in their opening lines; what "
     "none states is the INVOCATION contract -- $0 rooting, exit codes, "
     "--check -- and that is the gap, not the circularity."),
]

# Ownership by directory, for a territory whose members are not worth
# naming one by one. IN THE DATA, not in classify()'s body: it used to be
# an `if "houses/" in path` arm, so --owns could not see it and left every
# fixture house out of the listing -- the stale-enumeration
# defect this change exists to remove, one level down, in the display
# that replaced the stale prose. `claims`.
PATH_RULES = [("houses/", "harness")]

CODE = (".c", ".h", ".py", ".sh", ".als", ".tla")


def _match(pred):
    """Territories whose MAP set holds a name `pred` accepts, IN MAP ORDER.

    The order is the delivery order and it lives here, once. Both entry
    points go through this, so a file owned by two territories cannot
    come back in one order from an Edit and another from a Bash command.
    """
    return [n for names, n in MAP if any(pred(b) for b in names)]


def classify(path):
    """The territories a path belongs to, in order. Empty if none.

    THE EVENT PATH AND --check BOTH CALL THIS, and that is the point.
    UNOWNED is deliberately not consulted here: an unowned file must get
    no delivery, so classify returns empty for "no territory" whether or
    not anyone has written down why. --check is what tells those apart.

    A LIST, not a name. First-match was the old shape and it silently
    picked one owner of a two-owner file -- MAP order, so plan always
    beat runtime and nobody would see which rule was missing.
    """
    base = os.path.basename(path)
    out = _match(lambda b: b == base)
    if not out:
        out = [n for pre, n in PATH_RULES if pre in path]
    return out


def classify_cmd(cmd):
    """Same, for a Bash command string with no file_path.

    It used to be a separate loop over MAP. Two loops with one rule
    between them is how the orders drift apart; there is one now, and
    the only difference is what counts as a match.
    """
    out = _match(lambda b: re.search(r"(?<![\w/.-])" + re.escape(b)
                                     + r"(?![\w-])", cmd))
    if not out:
        out = [n for pre, n in PATH_RULES
               if re.search(re.escape(pre) + r"\w", cmd)]
    return out


def territories():
    return [n for _, n in MAP]


def owns(name):
    """The names MAP gives one territory, sorted. For displays.

    install-agents.sh --list used to print only the `Scope:` sentence, so
    the only enumeration a reader saw was prose that nothing derived and
    nothing checked. It goes stale the moment MAP moves, which `claims`
    found it had: the runtime scope named six files while MAP had more.
    """
    out = [pre for pre, n in PATH_RULES if n == name]
    for names, n in MAP:
        if n == name:
            out += list(names)
    return sorted(out)


def unowned_reason(rel):
    """Exact paths only. No prefix arm, deliberately.

    There was one, for a `proofs/` entry, and splitting that entry into
    files left the arm with no subjects -- `control` deleted it and the
    suite stayed green, which is a mechanism that reads as working until
    somebody reaches for it. Exact matching is also the safer rule: a
    directory entry is a prefix exemption, so a new file beneath it
    passes the census silently, which is the blind spot the census
    exists to catch. Weakening this to `rel.startswith(p)` was green
    too -- under it an entry written as `tools/stage` would have
    exempted two unrelated tools -- and it is pinned now, by the
    shortened-UNOWNED-path case in test_every_code_file_is_accounted_for.
    That case needs BOTH of its messages: the shortened row is refused
    as untracked under either form, so only the file it used to name
    going unaccounted tells them apart.
    """
    for p, why in UNOWNED:
        if rel == p:
            return why
    return None


def check():
    bad = []
    # NOT ROOTED, NOT A RESULT. Checking whatever tree the cwd happens to
    # be is how a stub fixture got the real repository certified clean.
    if not rooted:
        print(f"rules-hook --check: not in a checkout -- no {rules_dir} "
              f"(resolved from {os.path.realpath(self)}). Refusing rather "
              f"than checking whatever tree the cwd is.")
        return 1
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root,
                             capture_output=True, text=True, check=True).stdout
    except Exception as e:
        print(f"rules-hook --check: cannot enumerate the tree: {e}")
        return 1
    # attic/ is the one exemption that is not an UNOWNED row, and it is
    # named here rather than left as a silent filter: it holds retired
    # code kept for the record, so ownership of it would be a claim that
    # somebody maintains it. An undocumented second exemption channel
    # beside a documented one is the shape this mechanism is against.
    # `claims` pointed out it had no reason where every other exemption
    # is required to carry one.
    files = [f for f in out.split("\n")
             if f and not f.startswith("attic/") and f.endswith(CODE)]

    # A FLOOR AND THE ANCHORS BELOW, so the check cannot pass vacuously.
    # An enumeration that returns nothing, and a classify() that returns
    # [] for everything, both produce an empty `bad` list and would
    # otherwise read as success -- the same not-run-versus-nothing-found
    # confusion this whole exercise is about. The count was written into
    # this comment as "three" and a fourth row was added beneath it, so
    # it says "below" now: a count of the list under it is the shape
    # CLAUDE.md names as the durable one.
    if len(files) < 40:
        bad.append(f"only {len(files)} code files enumerated, which is fewer "
                   f"than this tree has ever had -- the enumeration is "
                   f"broken, not the ownership")
    for probe, want in (("pid1.c", ["runtime"]), ("nwcheck.c", ["plan"]),
                        ("tests/run.py", ["harness"]),
                        ("tools/stage-layers.py", ["runtime", "plan"])):
        got = classify(probe)
        if got != want:
            bad.append(f"classify({probe!r}) is {got!r}, expected {want!r} -- "
                       f"the classifier is broken, so every answer below it "
                       f"is worthless")

    for rel in files:
        t = classify(rel)
        why = unowned_reason(rel)
        if t and why:
            bad.append(f"{rel} is owned by {'+'.join(t)} AND listed in "
                       f"UNOWNED; one of the two is wrong")
        elif not t and why is None:
            bad.append(f"{rel} is a code file that no territory owns and "
                       f"UNOWNED does not mention. Add it to MAP, or to "
                       f"UNOWNED with the reason there is no territory for "
                       f"it. Do not add a blank reason.")
        elif len(t) > 1:
            base = os.path.basename(rel)
            dec = SHARED.get(base)
            if dec is None:
                bad.append(f"{rel} is in the {' and '.join(t)} sets of MAP "
                           f"and SHARED does not declare it. Two owners is "
                           f"allowed and has to be deliberate: add it to "
                           f"SHARED with both territories and the reason, "
                           f"or take the name out of the set it does not "
                           f"belong in. Almost always the second.")
            elif tuple(sorted(dec[0])) != tuple(sorted(t)):
                bad.append(f"{rel} is owned by {'+'.join(t)} but SHARED "
                           f"declares {'+'.join(dec[0])}")
            elif not (dec[1] or "").strip():
                bad.append(f"SHARED entry {base!r} has no reason")

    # A STALE EXEMPTION IS AN EXEMPTION FOR NOTHING, and it passed until
    # `claims` planted one: an UNOWNED row naming a path that has never
    # existed was simply counted. That is this project's own named
    # failure -- a brief describing a rescue slot the Makefile never
    # created -- with the list moved into a tool. The same applies to a
    # SHARED entry naming a territory MAP does not have.
    tracked = set(files)
    for p, why in UNOWNED:
        if not (why or "").strip():
            bad.append(f"UNOWNED entry {p!r} has no reason")
        if p not in tracked:
            bad.append(f"UNOWNED entry {p!r} is not a tracked code file")
    # A MAP NAME FOR A FILE THAT DOES NOT EXIST, which is this project's
    # opening record -- a brief for a binary that had been deleted --
    # representable in the map itself. The reverse loop was already
    # written twice beside this one, for UNOWNED and SHARED, and not for
    # MAP. `claims`.
    bases = {os.path.basename(f) for f in tracked}
    for names, n in MAP:
        for b in sorted(names):
            if b not in bases:
                bad.append(f"MAP gives {n} the name {b!r} and no tracked "
                           f"code file is called that")
    for pre, n in PATH_RULES:
        if not any(pre in f for f in tracked):
            bad.append(f"PATH_RULES gives {n} the prefix {pre!r} and no "
                       f"tracked code file is under it")

    for base, (terrs, why) in SHARED.items():
        unknown = [x for x in terrs if x not in territories()]
        if unknown:
            bad.append(f"SHARED entry {base!r} names {unknown}, which MAP "
                       f"does not define")
        if not any(os.path.basename(f) == base for f in tracked):
            bad.append(f"SHARED entry {base!r} matches no tracked code file")

    for b in bad:
        print("rules-hook --check: " + b)
    if not bad:
        print(f"rules-hook: {root}: {len(files)} code files, "
              f"{sum(1 for f in files if classify(f))} owned "
              f"({len(SHARED)} by two territories), "
              f"{len(UNOWNED)} unowned entries each with a reason")
    return 1 if bad else 0


if mode == "--territories":
    print(" ".join(territories()))
    raise SystemExit(0)
if mode == "--owns":
    print(" ".join(owns(raw)))
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

if path:
    names = classify(path)
    # Relative to the root this script computed, not to a hardcoded
    # directory name. It was `path.split("/city/")[-1]`, which is the
    # checkout's own folder name baked into the trusted-ish path of a tool
    # that is supposed to work from anywhere.
    where = os.path.relpath(path, root) if os.path.isabs(path) else path
    if where.startswith(".."):
        # OUTSIDE THE TREE WE ROOTED IN, and this branch is the only
        # place that knows. realpath was added so a symlinked $0 finds
        # the real file; point that symlink at a SECOND checkout and the
        # hook serves the other tree's rules for this tree's file, with
        # the stamp landing over there too. It told "no repository" from
        # "a repository" and not "this one" from "some other one".
        # Silently absolutising `where` discarded the one signal that
        # separates them. `control` built the two-checkout case.
        outside = True
        where = path
    else:
        outside = False
elif cmd:
    names = classify_cmd(cmd)
    where = "a Bash command naming a file in it"
    outside = False
else:
    names = []
    outside = False

if not names:
    raise SystemExit(0)

# EVERY TERRITORY THAT OWNS IT, in MAP order, each suppressed on its own
# stamp. A file with two owners gets both rules files; dropping to the
# first match is what made two ownership silently mean one.
#
# Once per rules file until that file changes, so touching ten files in one
# territory does not repeat the same text ten times. The stamp is rooted
# like src: written relative to the cwd it lands somewhere different on
# every invocation, which makes the suppression a silent no-op rather than
# a visible failure -- and the try below swallows stamp errors by design,
# so nothing would say so.
# A HOOK THAT CANNOT FIND ITS RULES SAYS SO. Silence here is
# indistinguishable from a file no territory owns, and serving another
# tree's rules is worse than either -- so the one case that must never
# happen quietly is the machine handling its own misinstallation.
if not rooted:
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext":
            f"rules-hook: {' and '.join(names)} rules apply to what you are "
            f"touching, and the hook cannot read them. It roots at its own "
            f"location and found no {rules_dir} (resolved from "
            f"{os.path.realpath(self)}). The hook is installed outside the "
            f"repository it is meant to serve; no rules were delivered."}}
    print(json.dumps(out))
    raise SystemExit(0)

if outside:
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext":
            f"rules-hook: refusing to deliver. The file is {path}, which "
            f"is not under {root} -- the tree this hook resolved to from "
            f"its own location. It is installed in, or linked from, a "
            f"different checkout, so its rules are not the rules for "
            f"that file."}}
    print(json.dumps(out))
    raise SystemExit(0)

blocks = []
missing = []
for name in names:
    src = os.path.join(rules_dir, name + ".md")
    if not os.path.isfile(src):
        missing.append(src)
        continue
    stamp = os.path.join(root, ".reviews", ".rules." + name)
    fresh = True
    try:
        os.makedirs(os.path.join(root, ".reviews"), exist_ok=True)
        if (os.path.exists(stamp)
                and os.path.getmtime(stamp) > os.path.getmtime(src)):
            fresh = False
        else:
            open(stamp, "w").close()
    except Exception:
        pass
    if not fresh:
        continue
    owners = (f"the {name} territory" if len(names) == 1 else
              f"the {name} territory, one of {' and '.join(names)}")
    blocks.append(f"Rules for {owners} (you are touching {where}). "
                  f"Source: {os.path.relpath(src, root)}\n\n"
                  + open(src).read())

if missing:
    blocks.insert(0, "rules-hook: rooted at " + root + " but could not read "
                  + ", ".join(missing) + ". Those territories' rules were "
                  "not delivered.")

if not blocks:
    raise SystemExit(0)

out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                              "additionalContext": "\n\n".join(blocks)}}
print(json.dumps(out))
PY

case "$MODE" in
  --check|--territories|--owns) exit $rc ;;
  *) exit 0 ;;
esac
