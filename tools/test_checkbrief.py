import os, shutil, sys, tempfile
import checkbrief as cb
class SelfTestFailure(Exception): pass
def expect(c,m):
    if not c: raise SelfTestFailure(m)
CHECKS=[]
def check(fn): CHECKS.append(fn); return fn
class Fixture:
    def __init__(self):
        self.root=tempfile.mkdtemp(prefix="cb-test-"); self.tree=os.path.join(self.root,"tree")
        os.makedirs(self.tree)
    def file(self,rel,c):
        p=os.path.join(self.tree,rel); os.makedirs(os.path.dirname(p),exist_ok=True)
        open(p,"w").write(c); return p
    def brief(self,t):
        p=os.path.join(self.root,"BRIEF.md"); open(p,"w").write(t); return p
    def cleanup(self): shutil.rmtree(self.root,ignore_errors=True)
def run(bp,tree,exclude=None):
    invs=cb.parse_invariants(open(bp).read())
    return [cb.check_invariant(i,tree,exclude) for i in invs]
@check
def check_invariant_naming_file_that_lacks_what_it_says():
    f=Fixture()
    try:
        f.file("sched/main.py","def run():\n    pass\n")
        b=f.brief("## Invariants\n\n1. Reads input from main.py. <<filecontains:sched/main.py:INPUT_PATH>>\n")
        r=run(b,f.tree)
        expect(r[0].outcome==cb.Outcome.CONTRADICTED,f"{r[0].outcome}")
        expect(any("INPUT_PATH" in d for _,_,d in r[0].failed),f"{r[0].failed}")
    finally: f.cleanup()
@check
def check_invariant_citing_grep_that_now_returns_a_hit():
    f=Fixture()
    try:
        f.file("a.py","# TODO: remove the legacy path\n")
        b=f.brief("## Invariants\n\n1. No legacy path remains. <<grep-absent:legacy path>>\n")
        r=run(b,f.tree)
        expect(r[0].outcome==cb.Outcome.CONTRADICTED,f"{r[0].outcome}")
        expect(any("found" in d for _,_,d in r[0].failed),f"{r[0].failed}")
    finally: f.cleanup()
@check
def check_invariant_true_but_uncheckable_is_reported():
    f=Fixture()
    try:
        f.file("a.py","x = 1\n")
        b=f.brief("## Invariants\n\n1. Holds no state between runs.\n2. Tree contains a.py. <<file:a.py>>\n")
        r=run(b,f.tree)
        expect([x.outcome for x in r]==[cb.Outcome.UNCHECKABLE,cb.Outcome.VERIFIED],f"{[x.outcome for x in r]}")
    finally: f.cleanup()
@check
def check_refusal_section_is_not_checked():
    f=Fixture()
    try:
        f.file("a.py","x = 1\n")
        b=f.brief("## Invariants\n\n1. Tree contains a.py. <<file:a.py>>\n\n## Refusals\n\n"
                  "1. Does not delete stores. <<grep-absent:rmtree>>\n\n## Waiting on\n\n"
                  "1. Names a missing file. <<file:nonexistent.txt>>\n")
        r=run(b,f.tree)
        expect(len(r)==1,f"expected 1 invariant, got {len(r)}")
        expect(r[0].outcome==cb.Outcome.VERIFIED,f"{r[0].outcome}")
    finally: f.cleanup()
@check
def check_refusal_control_would_be_checked_under_wrong_heading():
    f=Fixture()
    try:
        f.file("a.py","x = 1\n")
        b=f.brief("## Invariants\n\n1. Tree contains a.py. <<file:a.py>>\n"
                  "2. Does not delete stores. <<grep-absent:rmtree>>\n")
        r=run(b,f.tree)
        expect(len(r)==2,f"control: expected 2, got {len(r)}")
        expect(r[1].outcome==cb.Outcome.VERIFIED,f"{r[1].outcome}")
    finally: f.cleanup()
@check
def check_missing_heading_is_loud():
    f=Fixture()
    try:
        b=f.brief("## Something else\n\n1. A claim.\n")
        try: run(b,f.tree)
        except cb.BriefError as e: expect("Invariants" in str(e),f"{e}"); return
        raise SelfTestFailure("missing heading did not raise")
    finally: f.cleanup()
@check
def check_count_annotation_detects_wrong_count():
    f=Fixture()
    try:
        f.file("a.py","x\nx\nx\n")
        b=f.brief("## Invariants\n\n1. Three. <<count:a.py:x:3>>\n2. Four. <<count:a.py:x:4>>\n")
        r=run(b,f.tree)
        expect(r[0].outcome==cb.Outcome.VERIFIED,f"#1 {r[0].outcome}")
        expect(r[1].outcome==cb.Outcome.CONTRADICTED,f"#2 {r[1].outcome}")
        expect(any("3 times" in d and "expected 4" in d for _,_,d in r[1].failed),f"{r[1].failed}")
    finally: f.cleanup()
@check
def check_symbol_distinguishes_def_from_use():
    f=Fixture()
    try:
        f.file("a.py","def present():\n    pass\n"); f.file("b.py","present()\n")
        b=f.brief("## Invariants\n\n1. a.py defines it. <<symbol:a.py:present>>\n"
                  "2. b.py defines it. <<symbol:b.py:present>>\n")
        r=run(b,f.tree)
        expect(r[0].outcome==cb.Outcome.VERIFIED,f"#1 {r[0].outcome}")
        expect(r[1].outcome==cb.Outcome.CONTRADICTED,f"#2 {r[1].outcome}: a call site is not a definition")
    finally: f.cleanup()
@check
def check_unknown_kind_is_contradicted_not_skipped():
    f=Fixture()
    try:
        f.file("a.py","x\n")
        b=f.brief("## Invariants\n\n1. A claim. <<not-a-kind:whatever>>\n")
        r=run(b,f.tree)
        expect(r[0].outcome==cb.Outcome.CONTRADICTED,f"{r[0].outcome}")
        expect(any("unknown annotation kind" in d for _,_,d in r[0].failed),f"{r[0].failed}")
    finally: f.cleanup()
@check
def check_grep_absent_self_hit_when_checker_is_in_tree():
    f=Fixture()
    try:
        src=os.path.abspath(cb.__file__)
        f.file("checkbrief_copy.py",open(src).read())
        tok="grep-absent"
        b=f.brief(f"## Invariants\n\n1. Token absent. <<grep-absent:{tok}>>\n")
        r1=run(b,f.tree)
        expect(r1[0].outcome==cb.Outcome.CONTRADICTED,f"without exclude: {r1[0].outcome}")
        r2=run(b,f.tree,exclude=os.path.join(f.tree,"checkbrief_copy.py"))
        expect(r2[0].outcome==cb.Outcome.VERIFIED,f"with exclude: {r2[0].outcome} {r2[0].failed}")
    finally: f.cleanup()
@check
def check_outcomes_partition_the_invariants():
    f=Fixture()
    try:
        f.file("a.py","x\n")
        b=f.brief("## Invariants\n\n1. Verifies. <<file:a.py>>\n2. Contradicts. <<file:nope>>\n"
                  "3. Uncheckable.\n4. Also uncheckable.\n")
        r=run(b,f.tree)
        v=sum(1 for x in r if x.outcome==cb.Outcome.VERIFIED)
        c=sum(1 for x in r if x.outcome==cb.Outcome.CONTRADICTED)
        u=sum(1 for x in r if x.outcome==cb.Outcome.UNCHECKABLE)
        expect(v+c+u==len(r),f"{v}+{c}+{u} != {len(r)}")
        expect((v,c,u)==(1,1,2),f"counts {v},{c},{u}")
    finally: f.cleanup()
@check
def check_heading_with_descriptive_suffix_is_found():
    f=Fixture()
    try:
        f.file("a.py","x\n")
        b=f.brief("## Invariants that must not be broken\n\n1. Has a.py. <<file:a.py>>\n")
        r=run(b,f.tree)
        expect(len(r)==1 and r[0].outcome==cb.Outcome.VERIFIED,f"{[x.outcome for x in r]}")
    finally: f.cleanup()
@check
def check_heading_control_different_word_not_found():
    f=Fixture()
    try:
        b=f.brief("## Limits\n\n1. A claim. <<file:a.py>>\n")
        try: run(b,f.tree)
        except cb.BriefError: return
        raise SelfTestFailure("'## Limits' matched; the prefix match became match-anything")
    finally: f.cleanup()
@check
def check_absent_in_scoped_negative():
    f=Fixture()
    try:
        f.file("a.py","clean\n"); f.file("b.py","BUDGET_TOKEN\n")
        b=f.brief("## Invariants\n\n1. Not in a.py. <<absent-in:a.py:BUDGET_TOKEN>>\n"
                  "2. Not in b.py. <<absent-in:b.py:BUDGET_TOKEN>>\n"
                  "3. Tree-wide would be false. <<grep-absent:BUDGET_TOKEN>>\n")
        r=run(b,f.tree)
        expect(r[0].outcome==cb.Outcome.VERIFIED,f"#1 {r[0].outcome}")
        expect(r[1].outcome==cb.Outcome.CONTRADICTED,f"#2 {r[1].outcome}")
        expect(r[2].outcome==cb.Outcome.CONTRADICTED,f"#3 {r[2].outcome}: scoping is what makes #1 true")
    finally: f.cleanup()
def main():
    fl=[]
    for fn in CHECKS:
        try: fn(); print(f"PASS  {fn.__name__}")
        except SelfTestFailure as e: fl.append(fn.__name__); print(f"FAIL  {fn.__name__}  {e}")
        except BaseException as e: fl.append(fn.__name__); print(f"FAIL  {fn.__name__}  unexpected {type(e).__name__}: {e}")
    print(); print(f"{len(CHECKS)} checks: {len(CHECKS)-len(fl)} pass, {len(fl)} fail")
    return 1 if fl else 0
sys.exit(main())
