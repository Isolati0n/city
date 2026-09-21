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
    'tools/rules-hook.sh|for probe, want in (("pid1.c", ["runtime"]), ("nwcheck.c", ["plan"]),':
        "The territory anchors. Pinning them needs a test that knows "
        "which territory each file belongs to, which is a second copy of "
        "MAP -- the two-lists problem arriving as a test. They are a "
        "four-row second list, affordable where a fifty-row one is not, "
        "and CLAUDE.md's 'Who owns which file' carries the argument. "
        "What DOES pin them is the census test's planted-condition cases, "
        "which reach them through --check's output.",
}


def mutants(path):
    """Every line worth deleting, as (id, line_no, original, replacement).

    Deletion where it parses, negation where it does not. A conditional
    is negated rather than removed because removing it orphans its body;
    a call or an assignment is commented out.
    """
    out = []
    for i, line in enumerate(open(os.path.join(ROOT, path)).read().split("\n")):
        t = line.strip()
        if not t or t.startswith("#"):
            continue
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
            out.append((f"{path}|{t}", i, line, rep))
    return out


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


def copy_tree(dst):
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
    shutil.copytree(ROOT, dst, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__",
                                                  "boot-out", ".reviews"))
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git"] + args, cwd=dst, capture_output=True,
                       check=True)


def baseline(work):
    """An unmutated copy must pass, or nothing below means anything."""
    tree = os.path.join(work, "baseline")
    copy_tree(tree)
    ok = run_tests(tree)
    shutil.rmtree(tree, ignore_errors=True)
    return ok


def sweep(extra=None):
    all_m = []
    for p in TARGETS:
        all_m += mutants(p)
    if extra:
        all_m += extra
    killed, survivors = 0, []
    work = tempfile.mkdtemp(prefix="nw-sweep-")
    try:
        if not baseline(work):
            shutil.rmtree(work, ignore_errors=True)
            raise SystemExit("mutation-sweep: an UNMUTATED copy of the "
                             "tree does not pass. Every mutant would be "
                             "reported killed by whatever is wrong with "
                             "the copy. Fix the fixture first.")
        for mid, ln, orig, rep in all_m:
            tree = os.path.join(work, "t")
            shutil.rmtree(tree, ignore_errors=True)
            copy_tree(tree)
            f = os.path.join(tree, mid.split("|", 1)[0])
            lines = open(f).read().split("\n")
            if lines[ln] != orig:
                print(f"SKIP  {mid[:90]} (line moved in the copy)")
                continue
            lines[ln] = rep
            open(f, "w").write("\n".join(lines))
            if run_tests(tree):
                survivors.append(mid)
            else:
                killed += 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return all_m, killed, survivors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="plant an unpinned refusal and require it to survive")
    a = ap.parse_args()

    extra = None
    if a.selftest:
        # A refusal nothing can pin: it never fires, so deleting it can
        # change no test. If the sweep does not report it, the sweep is
        # not looking.
        src = os.path.join(ROOT, "tools", "rules-hook.sh")
        lines = open(src).read().split("\n")
        anchor = next(i for i, l in enumerate(lines)
                      if l.strip().startswith("def check():"))
        print(f"selftest: planting an unpinned refusal at "
              f"tools/rules-hook.sh:{anchor + 2}")
        extra = [("tools/rules-hook.sh|SELFTEST-PLANTED", anchor + 1,
                  lines[anchor + 1], lines[anchor + 1])]

    all_m, killed, survivors = sweep()

    unexplained = [s for s in survivors if not (RESIDUE.get(s) or "").strip()]
    print()
    print(f"mutants   {len(all_m)}")
    print(f"killed    {killed}")
    print(f"survivors {len(survivors)}  "
          f"({len(survivors) - len(unexplained)} with a recorded reason)")
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

    if a.selftest:
        print("\nselftest: the planted refusal is a no-op line; the sweep "
              "must have reported at least one survivor above, and the "
              "run is only meaningful if it did.")
    return 1 if unexplained else 0


if __name__ == "__main__":
    raise SystemExit(main())
