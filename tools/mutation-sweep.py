#!/usr/bin/env python3
"""Enumerate every refusal in the hook and the gate, delete each, report survivors.

WHY THIS EXISTS. Three review rounds in a row found, one at a time, a
refusal or an assertion that was deletable green: the census call in
install-agents.sh, the SHARED declaration requirement, the anchors, the
`where` relativisation, the rooted-but-missing diagnostic, the rc
propagation. Each was a real finding and each cost a round, and finding
them one at a time does not converge -- the same reasoning the census
itself rests on. So this enumerates instead of hunting, and the output a
reader should care about is the SURVIVOR list, not the count.

WHAT IT DOES. For each mutable line in scope it makes a scratch copy of
the tree, applies one mutation, and runs the hook tests plus
`install-agents.sh --check`. A mutant that is still green is a survivor:
either nothing pins that line, or the line does nothing.

WHAT IT DOES NOT MUTATE, and this belongs beside every citation of its
result. The operators are: delete or negate a CONDITIONAL, and delete a
REFUSAL, an exit or a failure message. It does not mutate constants, so
the floor's `40` and any threshold are untouched; it does not flip
comparison operators, so a `<` that should be `<=` is invisible; and it
does not mutate DATA, so the contents of MAP, SHARED, UNOWNED,
PATH_RULES and the anchors' expected territories are outside it. "Zero
unexplained survivors" means zero within those operators and says
nothing about the rest. A sweep whose scope is not stated reads as a
proof.

WHAT A SURVIVOR IS NOT. It is not automatically a defect. Some lines
cannot be pinned without a second copy of the thing they check -- the
territory anchors are the standing example, and `CLAUDE.md`'s "Who owns
which file" carries the reasoning. Survivors are listed in RESIDUE below
with a reason each, and the sweep reports an unexplained survivor as a
failure. A blank reason is refused, for the same reason UNOWNED's are.

ITS OWN CONTROL: --selftest plants a refusal that nothing pins and
requires the sweep to report it as a survivor. A sweep that finds
nothing is the shape this project distrusts most, so it has to be shown
finding something.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = ["tools/rules-hook.sh", "install-agents.sh"]
TESTS = ["test_rules_hook_delivers_from_any_cwd",
         "test_rules_hook_refuses_a_tree_it_cannot_root_in",
         "test_every_code_file_is_accounted_for"]

# A survivor with a reason. Keyed by the mutation's id, which is
# "<file>:<line-text>" stripped -- not a line NUMBER, which would go
# stale on the next edit above it.
RESIDUE = {
    'tools/rules-hook.sh|if not names:':
        "FAIL-SAFE, measured: with this guard alone removed the hook is "
        "still silent for an unowned file, because `if not blocks:` "
        "below it catches the same case. Correct redundancy -- each "
        "pins the conjunction and neither separately, CLAUDE.md's "
        "fds_ge3 shape, and inventing an assertion that appears to "
        "separate them is what that entry forbids. Removing BOTH is "
        "caught: the delivery test's unowned probe then gets "
        "`additionalContext: \"\"` and reports `the hook delivered for "
        "tools/review-gate.sh, which no territory owns`. Run, not "
        "reasoned.",
    'tools/rules-hook.sh|if not blocks:':
        "The other half of the pair above, same measurement: removed "
        "alone the hook stays silent, removed together the suite goes "
        "red. FAIL-SAFE.",
    'install-agents.sh|[ -e "$f" ] || continue':
        "FAIL-SAFE, measured. It guards an unmatched glob -- `for f in "
        "$RULEDIR/*.md` yields the literal pattern when the directory is "
        "empty. Run against a tree with no rules files, with all three "
        "occurrences removed, the gate exits 1 with output byte-identical "
        "to the guards being present: `FAIL plan.md missing` / `FAIL "
        "harness.md missing`. It cannot pass something it should refuse, "
        "because the missing-rules-file refusal fires either way, so a "
        "test here would assert which of two correct refusals wins. "
        "Three occurrences, one reason.",
}


def mutants(path, root=None):
    """Every line worth deleting, as (id, line_no, original, replacement).

    Deletion where it parses, negation where it does not. A conditional
    is negated rather than removed because removing it orphans its body;
    a call or an assignment is commented out.
    """
    out = []
    base = root or ROOT
    src = open(os.path.join(base, path)).read().split("\n")
    i = -1
    while i + 1 < len(src):
        i += 1
        line = src[i]
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        # A CONTINUED SHELL STATEMENT IS ONE MUTANT, not one line of one.
        # Replacing only the first line of a `fail "... \` left the rest
        # dangling, the file did not parse, and the run was counted as a
        # kill -- every one of them a refusal, all reported as covered
        # when nothing had been tested. Found by the validity guard,
        # which is what that guard is for.
        span = 1
        while (i + span - 1 < len(src) and src[i + span - 1].endswith("\\")
               and i + span < len(src)):
            t += " " + src[i + span].strip().rstrip("\\")
            span += 1
        ind = line[:len(line) - len(line.lstrip())]
        rep = None
        if re.match(r"(el)?if .*:$", t) and not t.startswith("elif not"):
            rep = ind + re.sub(r"^((?:el)?if )", r"\1False and ", t)
        elif re.match(r"^\[ .* \] \|\|", t) or re.match(r"^\[ .* \] &&", t):
            rep = ind + ": # swept"
        elif t.startswith("bad.append(") or t.startswith("fail "):
            rep = ind + ": # swept" if t.startswith("fail ") else ind + "pass"
        elif re.match(r"^(raise SystemExit|return 1|exit 1)\b", t):
            rep = ind + "pass" if path.endswith(".py") or "python" in path else ind + ":"
        elif t.startswith("grep -q") and "||" in t:
            rep = ind + ": # swept"
        if rep and rep.strip() != t:
            out.append((f"{path}|{t[:110]}", i, span, rep))
        i += span - 1
    return out


def valid(tree, path):
    """Does the mutant still parse? Returns None if yes, else why.

    A MUTANT THAT DOES NOT PARSE IS NOT A KILL. The hook traps its own
    failures -- `python3 ... || exit 0` on the event path -- so a broken
    heredoc exits 0 with no output, which the delivery test reports as a
    failure for a reason that has nothing to do with the line removed.
    Counting those as kills inflates the score in exactly the direction
    that makes a sweep look finished.
    """
    f = os.path.join(tree, path)
    r = subprocess.run(["sh", "-n", f], capture_output=True, text=True)
    if r.returncode != 0:
        return "sh -n: " + (r.stderr.strip().split("\n") or [""])[0]
    body = open(f).read()
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", body, re.S)
    if m:
        try:
            compile(m.group(1), path + " (heredoc)", "exec")
        except SyntaxError as e:
            return f"python: {e.msg} at line {e.lineno}"
    return None


def run_tests(tree):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    drv = ("import importlib.util;"
           "s=importlib.util.spec_from_file_location('r','tests/run.py');"
           "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
           + ";".join(f"m.{t}()" for t in TESTS))
    a = subprocess.run([sys.executable, "-c", drv], cwd=tree,
                       capture_output=True, env=env)
    b = subprocess.run(["sh", "install-agents.sh", "--check"], cwd=tree,
                       capture_output=True, env=env)
    return a.returncode == 0 and b.returncode == 0


def copy_tree(dst, root=None):
    """A copy that is a git repository, because --check enumerates one.

    THE FIRST VERSION OF THIS EXCLUDED .git AND DID NOT REPLACE IT, so
    `git ls-files` returned nothing in every mutant, --check's floor
    fired, and all 75 mutants were reported killed -- by a broken
    fixture, not by any assertion. The tell was the number: a sweep
    that kills everything on its first run has found nothing, and this
    project's own rule is that a control which passes is not good news.
    Hence baseline() below, which refuses to sweep at all until an
    UNMUTATED copy comes back green.
    """
    shutil.copytree(root or ROOT, dst, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__",
                                                  "boot-out", ".reviews"))
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git"] + args, cwd=dst, capture_output=True,
                       check=True)


def baseline(work, root=None):
    """An unmutated copy must pass, or nothing below means anything."""
    tree = os.path.join(work, "baseline")
    copy_tree(tree, root)
    ok = run_tests(tree)
    shutil.rmtree(tree, ignore_errors=True)
    return ok


def sweep(root=None, only=None):
    root = root or ROOT
    all_m = []
    for p in TARGETS:
        all_m += mutants(p, root)
    if only:
        all_m = [m for m in all_m if only in m[0]]
    killed, survivors, invalid = 0, [], []
    work = tempfile.mkdtemp(prefix="nw-sweep-")
    try:
        if not baseline(work, root):
            shutil.rmtree(work, ignore_errors=True)
            raise SystemExit("mutation-sweep: an UNMUTATED copy of the "
                             "tree does not pass. Every mutant would be "
                             "reported killed by whatever is wrong with "
                             "the copy. Fix the fixture first.")
        for mid, ln, span, rep in all_m:
            tree = os.path.join(work, "t")
            shutil.rmtree(tree, ignore_errors=True)
            copy_tree(tree, root)
            rel = mid.split("|", 1)[0]
            f = os.path.join(tree, rel)
            lines = open(f).read().split("\n")
            lines[ln:ln + span] = [rep]
            open(f, "w").write("\n".join(lines))
            why = valid(tree, rel)
            if why:
                invalid.append((mid, why))
            elif run_tests(tree):
                survivors.append(mid)
            else:
                killed += 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return all_m, killed, survivors, invalid


PLANT = """    if os.environ.get("NW_SWEEP_PLANTED_REFUSAL"):
        bad.append("a refusal no test exercises")
"""


def selftest():
    """Plant a refusal nothing can reach; the sweep must list it.

    THE CONTROL FOR THE CONTROL. A sweep whose answer is "nothing
    survived" is indistinguishable from a sweep that is not looking --
    which is not hypothetical here: this tool's first run reported
    75 of 75 killed because its fixture had no git repo. So it has to
    be shown finding something it was told to find.

    The planted refusal is gated on an environment variable nothing
    sets, so no test can reach it and deleting it can change no
    outcome. Scoped to that one mutant: what is under test is the
    classification, not the rest of the tree.
    """
    work = tempfile.mkdtemp(prefix="nw-sweep-self-")
    try:
        root = os.path.join(work, "repo")
        copy_tree(root)
        f = os.path.join(root, "tools", "rules-hook.sh")
        body = open(f).read()
        anchor = "def check():\n    bad = []\n"
        if body.count(anchor) != 1:
            raise SystemExit("selftest: cannot find check()'s opening in "
                             "the copy, so the plant would prove nothing")
        open(f, "w").write(body.replace(anchor, anchor + PLANT))
        print("selftest: planted an env-gated refusal in check(); nothing "
              "sets NW_SWEEP_PLANTED_REFUSAL, so no test can reach it.")
        _, killed, survivors, invalid = sweep(root=root,
                                              only="NW_SWEEP_PLANTED_REFUSAL")
        for s_id in survivors:
            print("  reported SURVIVOR: " + s_id[:100])
        print(f"selftest: killed={killed} survivors={len(survivors)} "
              f"invalid={len(invalid)}")
        if len(survivors) == 1 and killed == 0 and not invalid:
            print("selftest: PASS -- the sweep lists a refusal nothing "
                  "exercises.")
            return 0
        print("selftest: FAIL -- the sweep did not report the planted "
              "refusal as a survivor, so its survivor list is not "
              "evidence of anything.")
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="plant an unpinned refusal and require it to survive")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    all_m, killed, survivors, invalid = sweep()

    unexplained = [s for s in survivors if not (RESIDUE.get(s) or "").strip()]
    print()
    print(f"mutants   {len(all_m)}")
    print(f"killed    {killed}")
    print(f"survivors {len(survivors)}  "
          f"({len(survivors) - len(unexplained)} with a recorded reason)")
    print(f"invalid   {len(invalid)}  (did not parse; NOT counted as kills)")
    for mid, why in invalid:
        print(f"    {mid[:80]}  --  {why}")
    for s in survivors:
        why = RESIDUE.get(s)
        print("\n  SURVIVOR " + s)
        if why:
            print("    RESIDUE: " + why)
        else:
            print("    UNEXPLAINED -- pin it, or add it to RESIDUE with "
                  "the reason nothing can.")
    for k in RESIDUE:
        if k not in survivors:
            print("\n  STALE RESIDUE " + k)
            print("    it is pinned now, or the line is gone. Remove it.")
            unexplained.append(k)

    return 1 if unexplained else 0


if __name__ == "__main__":
    raise SystemExit(main())
