# attic — kept because recoverable is not the same as findable

Every file here is in git history and always was. That is not the point.
**Recoverable if you know the commit hash is not the same as findable**, and
nobody is going to think to look. An attic makes the record visible without
making it live.

Nothing here is on a build path. No `Makefile` target, no test, no import.
They are a **record**, not code.

## Do not wire these in

- They are frozen at the moment they left the tree. They do not track the
  tree and several will not build against it — `bakeoff.py` hardcodes
  `ZIG = "/tmp/zig/zig"`, a path that did not exist on the machine that last
  ran it, and the electricians are written against a plan format that no
  longer has edges in it (`HISTORY.md` §17).
- If some tool starts picking a `.c` file up from here, **exclude this
  directory** rather than editing the file. An edited record is not a record.
- `git log --follow` still works on the original paths and is the better tool
  for "how did this change over time". This directory answers "what did it
  look like at the end, and why was it kept".

## What each one is

### The polyglot experiment — `electrician.c`, `electrician.rs`, `electrician.zig`

One component, the boot spawner, written three times. The **Zig** one is what
actually booted on 2026-09-06. The **C** one became the version in the TCB.
The **Rust** one was the twin — a second spelling kept so the two could be
compared against each other rather than against a reviewer's confidence.

They are kept because the conclusion drawn from them outlived them: **the TCB
stays C, and twins are evidence rather than mayors.** That conclusion is in
`HISTORY.md`; the three things it was drawn from were not, until now.

### `bakeoff.py`

The harness that ran the same city through all three and compared them. It is
the tool that produced the conclusion above.

This is the one most worth keeping, and for a reason this repository states as
a rule elsewhere: **a proof kept outside the tree is a sentence**
(`CLAUDE.md`, on `proofs/`). The conclusion was recorded and the instrument
was not, which left a load-bearing claim with no way to re-run it. Keeping the
instrument does not make the claim re-runnable today — see *Do not wire these
in* — but it makes the claim **inspectable**, which is the difference between
a result and an assertion.

### `nwsup.rs`

The Rust supervisor. Deleted because it had drifted two generations behind and
could not be trusted to mean what a plan said: **no restart loop, no
signal-mask fix, and no Landlock** — so a plan declaring `landlock` would have
got silently nothing.

That last clause is why it is kept rather than merely deleted. "A house that
runs unconfined while the plan says it is confined is the plan lying"
(`CLAUDE.md`, invariant 6) is a rule with a worked example, and this is the
worked example. `lids.h` still carries the note that its FFI counterpart went
with it.

## Provenance, exact

Each file is byte-identical to its last state in the tree. Verify:

```
git show fbefb29^:electrician.c     | cmp - attic/electrician.c
git show fbefb29^:electrician.rs    | cmp - attic/electrician.rs
git show fbefb29^:electrician.zig   | cmp - attic/electrician.zig
git show 598f056^:nwsup.rs          | cmp - attic/nwsup.rs
git show fbefb29^:tests/bakeoff.py  | cmp - attic/bakeoff.py
```

**Two of those paths are not what the record says, and both were found by
running the commands rather than trusting the list:**

- **`bakeoff.py` was never deleted in `1ae3a06`.** That commit *renamed* it
  from `bakeoff.py` to `tests/bakeoff.py`; the deletion is in `fbefb29` with
  everything else edge-related. Filtering a diff to a single path makes a
  rename look exactly like a deletion, which is how the wrong commit got into
  the list in the first place. The content is identical at both points, so the
  bytes here would have been right either way — the provenance would not.
- **`electrician.c` was not deleted at all.** Git records
  `rename electrician.c => nwspawn.c (58%)`, and `nwspawn.c` first appears in
  `fbefb29`. The C electrician did not die; it **is** `nwspawn.c`, which is in
  the tree today and in the TCB. `HISTORY.md` §17's "What was deleted" list
  names it anyway, and that is now half-true in the way an attic makes worse:
  a reader who finds `attic/electrician.c` and reads "deleted" concludes the
  lineage stopped. It did not. Corrected at that heading.

`electrician.rs`, `electrician.zig` (both `fbefb29`) and `nwsup.rs`
(`598f056`) are true deletions.
