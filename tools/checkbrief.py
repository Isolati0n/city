from __future__ import annotations
import argparse, os, re, sys
INVARIANTS_HEADING_PREFIX="Invariants"
HEADING_RE=re.compile(r"^#{1,6}\s+(.*)$")
NUMBERED_RE=re.compile(r"^\s*(\d+)\.\s+(.*)$")
ANNOTATION_RE=re.compile(r"<<([a-z-]+):(.*?)>>")
class BriefError(Exception): pass
class CheckFailure(Exception): pass
class Invariant:
    def __init__(self,n,t,l): self.number=n; self.text=t; self.line_no=l; self.annotations=[]
def find_invariant_section(text):
    lines=text.splitlines(); start=None
    for i,l in enumerate(lines):
        m=HEADING_RE.match(l)
        if m:
            h=m.group(1).strip()
            if h and h.split()[0]==INVARIANTS_HEADING_PREFIX: start=i+1; break
    if start is None:
        raise BriefError(f"no section headed '{INVARIANTS_HEADING_PREFIX} ...'; the checker "
                         f"checks only numbered items under a heading whose first word is "
                         f"{INVARIANTS_HEADING_PREFIX!r}")
    end=len(lines)
    for j in range(start,len(lines)):
        if HEADING_RE.match(lines[j]): end=j; break
    return start,end,lines
def parse_invariants(text):
    start,end,lines=find_invariant_section(text)
    invs=[]; cur=None
    for i in range(start,end):
        raw=lines[i]; m=NUMBERED_RE.match(raw)
        if m:
            if cur is not None: invs.append(cur)
            cur=Invariant(int(m.group(1)),m.group(2).strip(),i+1); continue
        if cur is None: continue
        s=raw.strip()
        if not s: continue
        if s.startswith("#"): break
        cur.text+=" "+s
    if cur is not None: invs.append(cur)
    for inv in invs:
        for m in ANNOTATION_RE.finditer(inv.text):
            inv.annotations.append((m.group(1),m.group(2),m.group(0)))
    return invs
class Outcome:
    VERIFIED="verified"; CONTRADICTED="contradicted"; UNCHECKABLE="uncheckable"
class CheckResult:
    def __init__(self,inv): self.inv=inv; self.outcome=Outcome.UNCHECKABLE; self.failed=[]; self.checked=[]
def _tree_files(root,exclude):
    for dp,dn,fn in os.walk(root):
        if ".git" in dn: dn.remove(".git")
        for name in fn:
            full=os.path.join(dp,name)
            if exclude and os.path.realpath(exclude)==os.path.realpath(full): continue
            yield full
def _read(p):
    try:
        with open(p,"rb") as f: return f.read()
    except OSError as e: raise CheckFailure(f"cannot read {p}: {e}")
def check_annotation(kind,arg,tree,exclude):
    if kind=="file":
        p=os.path.join(tree,arg)
        return (True,f"exists: {arg}") if (os.path.isfile(p) or os.path.isdir(p)) else (False,f"absent: {arg}")
    if kind=="filecontains":
        if ":" not in arg: raise CheckFailure(f"filecontains argument {arg!r} has no token")
        path,token=arg.split(":",1)
        d=_read(os.path.join(tree,path)).decode("utf-8","surrogateescape")
        return (True,f"{path} contains {token!r}") if token in d else (False,f"{path} does not contain {token!r}")
    if kind=="grep-absent":
        for full in _tree_files(tree,exclude):
            if arg in _read(full).decode("utf-8","surrogateescape"):
                return False,f"found {arg!r} in {os.path.relpath(full,tree)}"
        return True,f"{arg!r} absent from the tree"
    if kind=="grep-present":
        for full in _tree_files(tree,exclude):
            if arg in _read(full).decode("utf-8","surrogateescape"):
                return True,f"found {arg!r} in {os.path.relpath(full,tree)}"
        return False,f"{arg!r} not found in the tree"
    if kind=="symbol":
        if ":" not in arg: raise CheckFailure(f"symbol argument {arg!r} has no name")
        path,name=arg.split(":",1)
        d=_read(os.path.join(tree,path)).decode("utf-8","surrogateescape")
        pats=[rf"^\s*(?:async\s+)?def\s+{re.escape(name)}\b",rf"^\s*class\s+{re.escape(name)}\b",
              rf"^\s*{re.escape(name)}\s*=",rf"^\s*{re.escape(name)}\s*:",
              rf"^#define\s+{re.escape(name)}\b",rf"^\s*func\s+{re.escape(name)}\b"]
        for pt in pats:
            if re.search(pt,d,re.M): return True,f"{path} defines {name}"
        return False,f"{path} does not define {name}"
    if kind=="count":
        parts=arg.rsplit(":",2)
        if len(parts)!=3: raise CheckFailure(f"count argument {arg!r} must be PATH:TOKEN:N")
        path,token,ns=parts
        try: exp=int(ns)
        except ValueError: raise CheckFailure(f"count argument {arg!r}: {ns!r} not int")
        d=_read(os.path.join(tree,path)).decode("utf-8","surrogateescape")
        f=d.count(token)
        return (True,f"{path} contains {token!r} {f} times") if f==exp else (False,f"{path} contains {token!r} {f} times, expected {exp}")
    if kind=="absent-in":
        if ":" not in arg: raise CheckFailure(f"absent-in argument {arg!r} has no token")
        path,token=arg.split(":",1)
        d=_read(os.path.join(tree,path)).decode("utf-8","surrogateescape")
        return (True,f"{path} does not contain {token!r}") if token not in d else (False,f"{path} contains {token!r}")
    raise CheckFailure(f"unknown annotation kind {kind!r}")
def check_invariant(inv,tree,exclude):
    r=CheckResult(inv)
    if not inv.annotations: r.outcome=Outcome.UNCHECKABLE; return r
    bad=False
    for kind,arg,raw in inv.annotations:
        try: ok,detail=check_annotation(kind,arg,tree,exclude)
        except CheckFailure as e: bad=True; r.failed.append((kind,arg,str(e))); continue
        if ok: r.checked.append((kind,arg,detail))
        else: bad=True; r.failed.append((kind,arg,detail))
    r.outcome=Outcome.CONTRADICTED if bad else Outcome.VERIFIED
    return r


# ---------------------------------------------------------------------------
# THE DRIVER, WHICH THE TOOL ARRIVED WITHOUT.
#
# Everything above is library and is covered by test_checkbrief.py's 14
# checks, all of which pass. There was no main(), no reporting and no
# __main__ guard: `argparse` was imported and never used, so running the
# documented command
#
#     python3 checkbrief.py --brief CLAUDE.md --tree . --exclude checkbrief.py
#
# printed NOTHING and exited 0 -- indistinguishable from "ran fine, all
# verified", which is one of the four exit codes the README specifies and
# the only one that could ever occur. A green suite, a correct library, and
# a program that cannot run. Found by running it, against a README that
# said what the output should look like; the suite could not find it
# because it imports the module and calls the functions.
#
# This is the same defect the sibling tool prereport.py is recorded as
# having had ("no __main__ guard so it could not be invoked as a program at
# all"), arriving in the tool sent to check that briefs are true.
#
# Written here rather than in the library so the 14 checks still exercise
# exactly what they were written against. Exit codes are the README's.
# ---------------------------------------------------------------------------

def report(results, out=sys.stdout):
    order = {Outcome.CONTRADICTED: 0, Outcome.UNCHECKABLE: 1, Outcome.VERIFIED: 2}
    for r in sorted(results, key=lambda r: (order[r.outcome], r.inv.number)):
        head = f"{r.outcome:<13} invariant {r.inv.number}"
        print(f"{head}  (CLAUDE.md line {r.inv.line_no})", file=out)
        for kind, arg, detail in r.failed:
            print(f"    FAILED  <<{kind}:{arg}>>", file=out)
            print(f"            {detail}", file=out)
        for kind, arg, detail in r.checked:
            print(f"    ok      <<{kind}:{arg}>>  {detail}", file=out)
        if r.outcome == Outcome.UNCHECKABLE:
            print(f"            no annotation; the claim is not checked by "
                  f"anything here", file=out)
    n = len(results)
    v = sum(1 for r in results if r.outcome == Outcome.VERIFIED)
    c = sum(1 for r in results if r.outcome == Outcome.CONTRADICTED)
    u = sum(1 for r in results if r.outcome == Outcome.UNCHECKABLE)
    print(f"\n{n} invariants: {v} verified, {c} contradicted, {u} uncheckable",
          file=out)
    if v:
        print("VERIFIED MEANS THE NAMED TEXT IS WHERE THE ANNOTATION SAYS. "
              "It does not\nmean the invariant is true: every form here pins "
              "presence or absence, and\nnone pins agreement. Where a claim "
              "is that several places agree, this\ncatches a site "
              "disappearing and not a site disagreeing.", file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="checkbrief")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--tree", required=True)
    ap.add_argument("--exclude", default=None)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    try:
        text = open(a.brief).read()
    except OSError as e:
        print(f"checkbrief: cannot read brief: {e}", file=sys.stderr)
        return 2
    try:
        invs = parse_invariants(text)
    except BriefError as e:
        print(f"checkbrief: {e}", file=sys.stderr)
        return 3
    results = [check_invariant(i, a.tree, a.exclude) for i in invs]
    report(results)
    return 1 if any(r.outcome == Outcome.CONTRADICTED for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
