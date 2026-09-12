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
@shape("unpaired-absence","Does the same test assert that the code which would have produced it actually ran?")
def _s2(line,path=None):
    if _is_comment(line): return None
    for rx in ABSENCE:
        m=rx.search(line)
        if m: return f"asserts an absence: {m.group(0)!r}"
    return None
COUNT_WORDS=r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|twenty|\d+)"
COUNTED=r"(?:check|test|case|input|rule|invariant|item|entry|line|step|assertion|control|instance|place|file|syscall|field)s?"
COUNT_RE=re.compile(rf"\b(?:the\s+)?{COUNT_WORDS}\s+(?:\w+\s+){{0,2}}{COUNTED}\b",re.I)
@shape("prose-count","Is this number asserted anywhere, or will it age silently?")
def _s3(line,path=None):
    if not _is_comment(line): return None
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
def scan(added,self_path=None):
    findings=[]; self_touched={}
    byfile={}
    for f,ln,t in added: byfile.setdefault(f,[]).append((ln,t))
    selfbase=os.path.basename(self_path) if self_path else None
    def _is_self(f):
        if not f or not selfbase: return False
        return os.path.basename(f)==selfbase
    if self_path:
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
    a=ap.parse_args(argv)
    if a.list_shapes:
        for n,q,_ in SHAPES: print(f"{n}\t{q}")
        return 0
    try:
        text=sys.stdin.read() if a.diff=="-" else open(a.diff).read()
    except OSError as e:
        print(f"cannot read diff: {e}",file=sys.stderr); return 2
    added=parse_diff(text)
    findings,self_touched=scan(added,self_path=os.path.abspath(__file__))
    report(findings,load_acks(a.ack_file),sys.stdout,self_touched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
