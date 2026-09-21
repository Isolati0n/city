from __future__ import annotations
import argparse, os, re, sys
SHAPES=[]
def shape(name,question):
    def deco(fn):
        SHAPES.append((name,question,fn)); return fn
    return deco
COMMENT_MARK=re.compile(r"(^\s*[#*]|//|/\*|^\s*\*)")
def _is_comment(t):
    s=t.strip()
    return bool(COMMENT_MARK.search(s)) or s.startswith('"""') or s.startswith("'''")
LOADBEARING=[re.compile(r"load[- ]bearing",re.I),re.compile(r"belt[- ]and[- ]braces",re.I),
 re.compile(r"\bmust come (?:first|before|after)\b",re.I),re.compile(r"\border matters\b",re.I),
 re.compile(r"\bthis ordering\b",re.I),re.compile(r"\bdeliberate(?:ly)? (?:first|last|before|after)\b",re.I),
 re.compile(r"\bnot (?:a )?(?:mere|simple) (?:convenience|tidiness)\b",re.I),
 re.compile(r"\bis what (?:stops|prevents|catches)\b",re.I)]
@shape("mechanism-claim","Is there a check that fails when this mechanism is removed?")
def _s(line,path=None):
    if not _is_comment(line): return None
    for rx in LOADBEARING:
        m=rx.search(line)
        if m: return f"comment claims a mechanism matters: {m.group(0)!r}"
    return None
ABSENCE=[re.compile(r"\bnot\s+in\s+\w+",re.I),re.compile(r"assert\s+\w+\s+not\s+in\b"),
 re.compile(r"expect\s*\(\s*[^,]*\bnot\s+in\b"),re.compile(r"\bdoes\s+not\s+contain\b",re.I),
 re.compile(r"\.count\s*\([^)]*\)\s*==\s*0"),re.compile(r"assertNotIn\b"),
 re.compile(r"expect\s*\(\s*not\s+"),re.compile(r"==\s*\[\s*\]"),re.compile(r"is\s+None\b")]
# AN ABSENCE IS ONLY THIS SHAPE WHEN IT IS AN ASSERTION. The question the
# shape asks -- "does the same test assert that the code which would have
# produced it actually ran?" -- has no meaning for a line that is not
# asserting anything. Ordinary program logic deciding something (`if p not
# in tracked:`, `out, missing = [], [n for n in requested if n not in
# passed]`) has nothing to pair with, and every ack written for one of
# those said so in the same words: "not a test assertion -- the tool
# deciding something, with nothing to pair".
#
# The narrowing is SYNTACTIC, on the line itself. A hunk header DOES carry
# a function name -- `@@ -1557,6 +1559,968 @@ def test_term_signal():` --
# but it names the function at the hunk's START, and a hunk that long spans
# many, so it is not reliably the one containing a given added line;
# parse_diff takes the line number and drops the rest. So "is this in a
# test?" is not answerable from the input the tool has, and "is this an
# assertion?" is. (This said a diff does not supply the function at all,
# which the format contradicts. `claims`.)
#
# WHAT IT COSTS, measured rather than assumed. The gap is an absence on a
# CONTINUATION line under an `expect(` that carries no absence of its own:
# synthetic `expect(` / `"x" not in out,` is reported by neither line. That
# gap is REAL and DOES NOT OCCUR in this tree -- of the continuation-line
# absences in tests/run.py, all but one are failure-message strings, which
# this narrowing is right to drop, and the remaining one opens with
# `expect(not ...` so the assertion is still reported on its first line.
#
# Both halves were run. The first draft of this comment asserted the cost
# without checking whether it lands anywhere, which is the entry in
# CLAUDE.md this whole round is downstream of.
# require*( is in this list because tools here refuse that way --
# require_closed() in tools/fold-house.py is the live one -- and a refusal
# helper is an assertion by another name.
ASSERTION=[re.compile(r"\bexpect\s*\("),re.compile(r"\bassert\b"),
 re.compile(r"\bassert(?:Not)?(?:In|Equal|Is|None|True|False)\b"),
 re.compile(r"\bself\.assert\w*\("),re.compile(r"\brequire\w*\s*\(")]
@shape("unpaired-absence","Does the same test assert that the code which would have produced it actually ran?")
def _s2(line,path=None):
    # A CODE SHAPE, split by kind the way tool-presence already is. A
    # markdown file has no assertions, so every hit in one is prose quoting
    # code or plain English -- "not in the tree", "not in the working
    # tree", a sentence naming the tokens this matcher fires on. Measured
    # before the split: eighteen such rows were acked across CLAUDE.md, the
    # post-mortem and docs/relayed/, and NOT ONE was a genuine absence
    # assertion. The widening of prose-count is what made this worth
    # fixing rather than tolerating -- it put more prose in front of the
    # scanner, and this shape's prose hits are noise by construction.
    if bool(path) and os.path.splitext(path)[1].lower() in PROSE_EXT: return None
    if _is_comment(line): return None
    if not any(rx.search(line) for rx in ASSERTION): return None
    for rx in ABSENCE:
        m=rx.search(line)
        if m: return f"asserts an absence: {m.group(0)!r}"
    return None
COUNT_WORDS=r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|twenty|\d+)"
COUNTED=r"(?:check|test|case|input|rule|invariant|item|entry|line|step|assertion|control|instance|place|file|syscall|field)s?"
COUNT_RE=re.compile(rf"\b(?:the\s+)?{COUNT_WORDS}\s+(?:\w+\s+){{0,2}}{COUNTED}\b",re.I)
# IN A PROSE FILE, EVERY LINE IS PROSE. `_is_comment()` passes only lines
# whose first non-space character is `#` or `*`, which in markdown means a
# heading or a bullet -- so ordinary wrapped paragraphs, which is most of
# CLAUDE.md and every rules file, were never examined at all. The rule the
# shape enforces is written in those paragraphs; the tool was reading the
# headings above them.
#
# Measured on the round that found it: two counts went into one paragraph
# of CLAUDE.md, `prereport` returned `no shapes matched`, and `claims`
# found them by reading. The gate decided which lines were read.
#
# THIS ADDS FALSE POSITIVES AND THAT IS THE TRADE, not a surprise to be
# acked away later: English says "one of them" and "the two files" for
# reasons that have nothing to do with counting the tree. The before and
# after numbers are in the commit message, and the ones that survive are
# acked one at a time with a reason, which is what an ack is for.
@shape("prose-count","Is this number asserted anywhere, or will it age silently?")
def _s3(line,path=None):
    prose = bool(path) and os.path.splitext(path)[1].lower() in PROSE_EXT
    if not prose and not _is_comment(line): return None
    if re.search(r"\bbugs?\s+[\d/,\s]+\b",line,re.I): return None
    m=COUNT_RE.search(line)
    if m: return f"a count in prose: {m.group(0).strip()!r}"
    return None
# `which` FOLLOWED BY AN ENGLISH WORD IS ENGLISH. The `^` anchor below means
# any prose line that WRAPS onto the word "which" matches, and three of them
# did on the calibration commit: "which is a production decision.", "which is
# derived rather than guessed", "which is the size where...". The handover
# note describes this false-positive class in the PAST tense, as found and
# fixed over three earlier rounds -- and the source still had it. Found the
# way the note says its own defects were found: by running it, on a diff
# whose expected finding count was stated in advance. 7 against a stated 4,
# and the three extra were all this.
#
# The anchor is kept, because `which cbmc` at line start in a script is the
# thing this pattern is for. What is added is a stop-list of the words that
# can follow "which" in English but never name a command. A list is a
# hostage, so it is here and not in prose, where being wrong is silent.
NOT_A_COMMAND=r"(?!(?:is|are|was|were|the|a|an|it|this|that|they|we|you|he|she|" \
              r"makes?|made|gives?|gave|produced?|left|turned|cost|gets?|gone|gone|gone|" \
              r"cannot|could|would|will|does|did|do|has|have|had|gets|gave|gone)\b)"
# SPLIT BY KIND, because the stop-list above did not hold. It killed the
# three calibration false positives and the next run produced another --
# "which catch a checker that accepts too much" -- since no word list
# survives contact with English. A list of exceptions is a hostage; the
# class is what needs removing.
#
# So: the first four are CODE patterns and are not looked for in prose
# files, where a shell command cannot appear and an English "which" always
# can. The last two are PROSE patterns and are looked for everywhere --
# a brief claiming a capability because a tool is installed is exactly the
# harness.md defect they exist to catch, and briefs are prose files.
PROSE_EXT={".md",".txt",".rst",".adoc"}
TOOLPRESENCE_CODE=[re.compile(r"command\s+-v\s+(\S+)"),
 re.compile(rf"(?:^|[;&|]|\$\()\s*which\s+{NOT_A_COMMAND}(\S+)"),
 re.compile(r"shutil\.which\s*\(\s*['\"]([^'\"]+)"),
 re.compile(r"os\.path\.exists\s*\(\s*['\"]/usr/bin/([^'\"]+)")]
TOOLPRESENCE_PROSE=[re.compile(r"\bis\s+installed\b",re.I),
 re.compile(r"\bmkfs\.(\w+)\s+(?:is\s+)?(?:present|available|installed)",re.I)]
@shape("tool-presence","Does the kernel or the environment actually support the feature, or is only the tool present?")
def _s4(line,path=None):
    prose = bool(path) and os.path.splitext(path)[1].lower() in PROSE_EXT
    pats = TOOLPRESENCE_PROSE if prose else TOOLPRESENCE_CODE + TOOLPRESENCE_PROSE
    for rx in pats:
        m=rx.search(line)
        if m: return f"infers a capability from a tool: {m.group(0).strip()!r}"
    return None
TESTDEF=[re.compile(r"^\s*def\s+(test_\w+|check_\w+)\s*\("),re.compile(r"^\s*@check\b"),
 re.compile(r"^\s*def\s+(\w*test\w*)\s*\(",re.I)]
CONTROLHINT=[re.compile(r"\bcontrol\b",re.I),re.compile(r"\bnegative\b",re.I),
 re.compile(r"\bwhen\s+removed\b",re.I),re.compile(r"\bfails?\s+if\b",re.I),
 re.compile(r"\bmutat",re.I),re.compile(r"\bwithout\s+the\b",re.I)]
@shape("test-without-control","Has this test been run against the broken version?")
def _s5(line,path=None): return None
def _new_test_names(added):
    out=[]
    for ln,t in added:
        for rx in TESTDEF:
            m=rx.match(t)
            if m and m.lastindex: out.append((ln,m.group(1))); break
    return out
class Finding:
    def __init__(self,s,q,f,ln,t,d):
        self.shape=s; self.question=q; self.file=f; self.line_no=ln; self.text=t; self.detail=d
    def key(self): return (self.shape,self.file,self.text.strip())
def parse_diff(text):
    cur=None; added=[]; ln=0
    for raw in text.splitlines():
        if raw.startswith("+++ "):
            p=raw[4:].strip()
            if p.startswith("b/"): p=p[2:]
            cur=p; continue
        if raw.startswith("@@"):
            m=re.search(r"\+(\d+)",raw)
            ln=int(m.group(1)) if m else 0; continue
        if raw.startswith("+") and not raw.startswith("+++"):
            added.append((cur,ln,raw[1:])); ln+=1; continue
        if raw.startswith("-") or raw.startswith("--- "): continue
        ln+=1
    return added
def scan(added,self_path=None,ack_path=None):
    findings=[]; self_touched={}
    byfile={}
    for f,ln,t in added: byfile.setdefault(f,[]).append((ln,t))
    # THE ACK FILE EXCLUDES ITSELF TOO, and for a stronger reason than the
    # source does. An ack line CONTAINS the text it acknowledges, and the
    # reason beside it discusses that text, so scanning the ack file
    # re-reports every acked finding under a new key that the ack cannot
    # match. Measured: acking fourteen findings produced thirteen fresh
    # ones on the next run, all of them the ack file quoting itself.
    # Excluded the same way the source is, and the same note is printed,
    # so the exclusion stays visible rather than becoming a silent hole.
    skip={os.path.basename(p) for p in (self_path,ack_path) if p}
    def _is_self(f):
        if not f or not skip: return False
        return os.path.basename(f) in skip
    for f,lines in byfile.items():
        if _is_self(f): self_touched[f]=len(lines)
    for name,question,fn in SHAPES:
        if name=="test-without-control": continue
        for f,ln,t in added:
            if _is_self(f): continue
            d=fn(t,f)
            if d: findings.append(Finding(name,question,f,ln,t,d))
    for f,lines in byfile.items():
        if _is_self(f): continue
        blob="\n".join(t for _,t in lines)
        has=any(rx.search(blob) for rx in CONTROLHINT)
        if not has:
            for ln,nm in _new_test_names(lines):
                findings.append(Finding("test-without-control",
                    "Has this test been run against the broken version?",f,ln,nm,
                    f"new test {nm!r} and no control language anywhere in this file's added lines"))
    return findings,self_touched
def load_acks(p):
    acks=set()
    if not p or not os.path.exists(p): return acks
    with open(p) as fh:
        for line in fh:
            line=line.strip()
            if not line or line.startswith("#"): continue
            parts=line.split("\t")
            if len(parts)>=3: acks.add((parts[0],parts[1],parts[2]))
    return acks


def dead_acks(p,root="."):
    """Acks whose keyed text is no longer in the file they name.

    A DEAD ACK IS AN ACK NOBODY WILL EVER RE-EXAMINE. It silences a key
    that cannot occur, so it is not suppressing anything; and because the
    finding it was written for has moved or gone, the reason beside it is
    now attached to nothing. Rewriting a line re-keys its ack silently --
    that is how most of these are made, including by the round that first
    counted them.

    NOT A HEURISTIC, which is why this one can refuse where the shapes
    cannot. The shapes ask a question a human answers; this asks whether a
    string is in a file. `exits 0 always` is a rule about heuristics being
    routed around when they block, and it is kept for them.
    """
    out=[]
    if not p or not os.path.exists(p): return out
    cache={}
    with open(p) as fh:
        for n,line in enumerate(fh,1):
            line=line.rstrip("\n")
            if not line.strip() or line.startswith("#"): continue
            parts=line.split("\t")
            if len(parts)<3: continue
            shape,f,text=parts[0],parts[1],"\t".join(parts[2:])
            full=os.path.join(root,f)
            if full not in cache:
                try:
                    cache[full]=open(full,encoding="utf-8",errors="replace").read()
                except OSError:
                    cache[full]=None
            body=cache[full]
            if body is None: out.append((n,shape,f,text,"no such file"))
            elif text not in body: out.append((n,shape,f,text,"text not in file"))
    return out
def report(findings,acks,out,self_touched=None):
    if self_touched:
        for f,n in sorted(self_touched.items()):
            print(f"note: this diff touches {f}; {n} added line(s) in it were not scanned.",file=out)
        print(file=out)
    live=[f for f in findings if f.key() not in acks]
    acked=[f for f in findings if f.key() in acks]
    if not live and not acked:
        print("pre-report: no shapes matched in the added lines.",file=out); return
    if live:
        print(f"pre-report: {len(live)} to look at. These are heuristics, not errors.",file=out)
        print(file=out)
        bys={}
        for f in live: bys.setdefault(f.shape,[]).append(f)
        for s in sorted(bys):
            g=bys[s]; print(f"{s} -- {g[0].question}",file=out)
            for f in g:
                loc=f"{f.file}:{f.line_no}" if f.file else f"line {f.line_no}"
                print(f"  {loc}",file=out); print(f"    {f.text.strip()[:100]}",file=out)
                print(f"    {f.detail}",file=out)
            print(file=out)
    if acked: print(f"({len(acked)} previously acknowledged, not shown)",file=out)
    if live:
        print("To silence one, add a line to the ack file with the shape, file and text,",file=out)
        print("tab separated. An ack is a claim that you looked; it is not a fix.",file=out)
def main(argv=None):
    ap=argparse.ArgumentParser(prog="prereport")
    ap.add_argument("--diff",default="-"); ap.add_argument("--ack-file",default=".prereport-ack")
    ap.add_argument("--list-shapes",action="store_true")
    ap.add_argument("--root",default=".",
                    help="tree the ack file's paths resolve against")
    ap.add_argument("--check-acks",action="store_true",
                    help="exit non-zero when an ack keys text that is gone")
    a=ap.parse_args(argv)
    if a.list_shapes:
        for n,q,_ in SHAPES: print(f"{n}\t{q}")
        return 0
    try:
        text=sys.stdin.read() if a.diff=="-" else open(a.diff).read()
    except OSError as e:
        print(f"cannot read diff: {e}",file=sys.stderr); return 2
    added=parse_diff(text)
    findings,self_touched=scan(added,self_path=os.path.abspath(__file__),
                               ack_path=a.ack_file)
    report(findings,load_acks(a.ack_file),sys.stdout,self_touched)
    dead=dead_acks(a.ack_file,root=a.root)
    if dead:
        print(file=sys.stdout)
        print(f"DEAD ACKS: {len(dead)} row(s) key text that is no longer in the "
              f"file they name. Each one silences a finding that cannot occur, "
              f"and its reason is attached to nothing.",file=sys.stdout)
        for n,shape,f,t,why in dead:
            print(f"  {a.ack_file}:{n}  {shape}  {f}  ({why})",file=sys.stdout)
            print(f"    {t[:96]}",file=sys.stdout)
        print("Re-key each against the line as it now reads, or delete it if the "
              "finding is gone.",file=sys.stdout)
    # LISTED ALWAYS, REFUSED ONLY WHEN ASKED, and the split is the whole of
    # what keeps `exits 0 always` true of the TARGET. The first version
    # returned 1 from the default path, which meant `make prereport` went red
    # for a row somebody else left behind in a file the diff does not touch --
    # and the recorded rationale for exiting 0 is behavioural, not epistemic:
    # "a heuristic wired into a build gets routed around within a week". An
    # unscoped red target is exactly that stimulus. `claims` reproduced it on
    # an EMPTY diff.
    #
    # So the refusal lives behind --check-acks, which install-agents.sh
    # --check passes. Refusals belong in the gate; the heuristic stays out of
    # the build. A flag nothing calls would be the other failure this file
    # names -- a mechanism whose answer to "when did it last fire" is never.
    if dead and a.check_acks: return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
