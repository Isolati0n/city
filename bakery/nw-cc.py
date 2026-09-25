#!/usr/bin/env python3
"""nw-cc stand-in (Haskell/OCaml baker). Not in the TCB.

Refuses what nwcheck.c refuses, and refuses it independently: the derived fd
budget, the closed lid set, a brick forcing newns, a bind requiring a brick,
and unique names. The baker refuses; it does not repair.

This said "encodes the Alloy assertions: unique names, derived fd budget,
closed lid set" until 2026-09-11. Two of the three are in plan.als
(`fdNeed`, the lid set); **unique names is not, and never was** -- `grep` for
"name" in plan.als returns a comment and `fact namesAreHouses`, which despite
its identifier is a nonemptiness fact about `#House`. `House` has no name
field, so there is nothing there for a uniqueness fact to be about. Found by
`drift`. Rewritten to describe what this file does rather than to claim
provenance it does not have.

Lockfile = the blob. Never rebuild-switch.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mkbrick                                             # noqa: E402

NAME_LEN, PATH_LEN, BRICK_HASH = 32, 128, 32
# Derived, the way blob.h derives NW_BRICK_HEX: the hex spelling is the same
# hash, so a literal 64 beside a literal 32 is the second copy the header's
# own comment argues against, on the one side its _Static_asserts cannot
# reach. `drift`.
BRICK_HEX = BRICK_HASH * 2
MAX_UNITS, MAX_BINDS, FD_RESERVED, MAX_FDS = 64, 128, 8, 1024
KIND_ONESHOT, KIND_LONGRUN = 0, 1
KINDS = {"oneshot": KIND_ONESHOT, "longrun": KIND_LONGRUN}
LID_SECCOMP, LID_LANDLOCK, LID_NEWNS, LID_NEWNET = 1, 2, 4, 8
KNOWN_LIDS = LID_SECCOMP | LID_LANDLOCK | LID_NEWNS | LID_NEWNET
LID_NAMES = {
    "none": 0, "seccomp": LID_SECCOMP, "landlock": LID_LANDLOCK,
    "newns": LID_NEWNS, "newnet": LID_NEWNET,
}

# The resource block. Every field UNSET by default: 0 means "no limit
# declared", never a limit of zero. See struct nw_res in blob.h for why
# there is no default and why nothing here is a machine property.
# READ FROM blob.h, not spelled. Every one of these is quoted by the
# checker or defines a byte the checker compares against, so a literal
# here is the second copy of a limit that invariant 3 is about --
# `drift` and `claims` have each caught a fresh one of those in this
# tree already. `mkbrick._define` is the reader that exists; a fifth
# copy of "parse a #define" would be the same defect one level down,
# which is the argument its own docstring makes.
#
# THE SCHEDULER POLICIES WERE HAND-WRITTEN HERE for one round --
# `SCHED_UNSET, SCHED_OTHER, SCHED_BATCH, SCHED_IDLE = 0, 1, 2, 3`,
# directly under this comment. `tcb-review` swapped BATCH and IDLE in
# blob.h alone and `make test` stayed green while a plan saying
# `sched=batch` baked to the byte the TCB calls IDLE: bug 4/9/13's
# silent-wrong-routing shape moved from a descriptor to a policy byte.
# So they are read too, and there is no second table left to check.


def _const(name):
    """An INTEGER #define out of blob.h, resolving what C writes and
    _define does not: a `u`/`U`/`l`/`L` suffix, a parenthesised negative,
    and one level of alias (`NW_SCHED_MAX` is `NW_SCHED_IDLE`).

    This is a hand-written reader for a corner of C's constant syntax and
    that is a real cost -- tests/run.py solves the same problem by
    COMPILING, which cannot be wrong, and the baker cannot because it
    must run where no compiler does. What closes the gap is
    test_baker_constants_match_the_header, which compares every value
    this function returns against the compiler's answer. Widen this and
    the test says so."""
    raw = mkbrick._define(name)
    seen = set()
    while not re.fullmatch(r"\(?-?\d+\)?[uUlL]*", raw):
        if raw in seen or not re.fullmatch(r"NW_\w+", raw):
            raise SystemExit(
                f"nw-cc: blob.h's {name} is {raw!r}, which this reader "
                f"cannot resolve. It handles an integer, an optional "
                f"suffix, a parenthesised negative and an alias chain, "
                f"and nothing else -- deliberately, because guessing at "
                f"more of C here is how the value and the header come "
                f"to disagree quietly.")
        seen.add(raw)
        raw = mkbrick._define(raw)
    return int(raw.rstrip("uUlL").strip("()"))


CPU_WEIGHT_MIN = _const("NW_CPU_WEIGHT_MIN")
CPU_WEIGHT_MAX = _const("NW_CPU_WEIGHT_MAX")
NICE_MIN = _const("NW_NICE_MIN")
NICE_MAX = _const("NW_NICE_MAX")
SCHED_UNSET = _const("NW_SCHED_UNSET")
SCHED_OTHER = _const("NW_SCHED_OTHER")
SCHED_BATCH = _const("NW_SCHED_BATCH")
SCHED_IDLE = _const("NW_SCHED_IDLE")
SCHED_NAMES = {"other": SCHED_OTHER, "batch": SCHED_BATCH, "idle": SCHED_IDLE}
# cpu_mask is a uint64, so a CPU index is bounded by its width rather than
# by a number anybody picked. The baker names the bound when it refuses.
CPU_INDEX_MAX = 63
RES_FIELDS = ("cpu_mask", "mem_high", "mem_max", "io_rbps", "io_wbps",
              "layer_bytes", "cpu_weight", "nice", "sched_policy")


def empty_res():
    return dict.fromkeys(RES_FIELDS, 0)


def pack_res(r):
    """Must match struct nw_res in blob.h: six u64, one u16, i8, u8."""
    return struct.pack("<QQQQQQHbB",
                       r["cpu_mask"], r["mem_high"], r["mem_max"],
                       r["io_rbps"], r["io_wbps"], r["layer_bytes"],
                       r["cpu_weight"], r["nice"], r["sched_policy"])


def parse_bytes(v, what):
    """A size with an optional K/M/G/T suffix, binary rather than decimal.

    Spelled out because a resource limit written as 2000000000 and meant
    as 2G is the kind of number nobody re-reads."""
    mult, digits = 1, v
    if v and v[-1].upper() in "KMGT":
        mult = {"K": 1 << 10, "M": 1 << 20,
                "G": 1 << 30, "T": 1 << 40}[v[-1].upper()]
        digits = v[:-1]
    # isdecimal, NOT isdigit: '\u00b2'.isdigit() is True and int() rejects it,
    # so the refusal below was walked past into a traceback for an input
    # class it exists for. `control`. ('\u00bd'.isdigit() is False, which is
    # the paired positive -- that one always refused correctly.)
    if not digits.isdecimal():
        raise SystemExit(
            f"{what}={v}: a size in bytes, optionally suffixed K, M, G or "
            f"T (binary). 0 is not 'unlimited' -- omit the key for that.")
    n = int(digits) * mult
    # The field is a uint64. Above that, struct.pack raises rather than
    # wrapping -- loud, and still a traceback past a refusal that should
    # have named the key. `control`.
    if n > 0xFFFFFFFFFFFFFFFF:
        raise SystemExit(
            f"{what}={v}: {n} does not fit the 64-bit field. The bound is "
            f"the width of the field in struct nw_res, not a number chosen "
            f"here.")
    if n == 0:
        raise SystemExit(
            f"{what}=0: zero is not how a limit is removed, because 0 is "
            f"the byte an unset field already holds and the two would be "
            f"indistinguishable in the blob. Omit {what}= instead.")
    return n


def parse_cpus(v):
    """`0,2-3` -> a mask. Indices, bounded by the mask's width."""
    mask = 0
    for part in v.split(","):
        part = part.strip()
        lo, _, hi = part.partition("-")
        try:
            lo_i = int(lo)
            hi_i = int(hi) if hi else lo_i
        except ValueError:
            raise SystemExit(
                f"cpus={v}: a comma-separated list of indices and ranges, "
                f"like 0,2-3")
        if lo_i > hi_i:
            raise SystemExit(f"cpus={v}: range {part} runs backwards")
        for i in range(lo_i, hi_i + 1):
            if not 0 <= i <= CPU_INDEX_MAX:
                raise SystemExit(
                    f"cpus={v}: CPU {i} is outside 0..{CPU_INDEX_MAX}. The "
                    f"bound is the width of cpu_mask in struct nw_res, not "
                    f"a number chosen here.")
            mask |= 1 << i
    # NO EMPTY-MASK REFUSAL, and its absence is the point. One stood
    # here -- `if mask == 0: raise ... "names no CPU, and an empty mask
    # is the value an unset field already holds"` -- and `control`
    # deleted it for a green `make test`, then showed why no test could
    # have pinned it: the branch is UNREACHABLE. `str.split(",")` yields
    # at least one part; a part that is not an integer is already
    # refused, a backwards range is already refused, and an index
    # outside 0..CPU_INDEX_MAX is already refused, so every surviving
    # part sets a bit. Measured by restoring the branch as a raise and
    # driving it with a cross-product corpus of one-, two- and
    # three-part lists over the alphabet the parser accepts: no input
    # reached it. (A corpus SIZE stood here -- a number nothing in the
    # tree generates or stores, which is a claim about how hard somebody
    # looked. `claims`. The named guards below are what a reader can
    # re-run.)
    #
    # That is NW_E_RESZERO one level down -- a refusal whose message
    # argues its own necessity, that nothing can perform -- shipped in
    # the same commit that removed exactly that shape from blob.h.
    # HISTORY.md 75.
    #
    # It would become reachable if the `runs backwards` guard went, so
    # do not read this as "an empty mask is fine": an empty mask is the
    # unset value and must never be baked. What makes that true is the
    # three guards above, not a fourth one nothing can enter. If you
    # loosen any of them, this is the paragraph to re-read.
    return mask


def pad(s: str, n: int) -> bytes:
    b = s.encode("ascii")
    if len(b) >= n:
        raise SystemExit(f"too long: {s}")
    return b + b"\x00" * (n - len(b))


def path_clean(p: str) -> bool:
    """Absolute, with no '..' component. Same rule as path_ok_len in
    nwcheck.c, which enforces it independently -- the baker is not in the
    TCB. Closes traversal only; a symlink escapes it. See docs/options/07."""
    return p.startswith("/") and ".." not in p.split("/")


def check(houses, binds):
    names = [h["name"] for h in houses]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate name")
    if not (1 <= len(houses) <= MAX_UNITS):
        raise SystemExit("unit count")
    if len(binds) > MAX_BINDS:
        raise SystemExit("bind count")
    idx = {n: i for i, n in enumerate(names)}
    need = FD_RESERVED + len(houses) * 2
    if need > MAX_FDS:
        raise SystemExit("fd budget")
    for h in houses:
        if h["kind"] not in (KIND_ONESHOT, KIND_LONGRUN):
            raise SystemExit("kind")
        if h["lids"] & ~KNOWN_LIDS:
            raise SystemExit("lids")
        if not path_clean(h["exec"]):
            raise SystemExit(
                f"house {h['name']}: exec path must be absolute with no '..' "
                "component")
        if not h["name"] or not h["name"].replace("-", "x").replace("_", "x").isalnum():
            raise SystemExit("name")
        # Landlock grants beneath the house's root; that is only a
        # restriction when the root is a brick.
        if (h["lids"] & LID_LANDLOCK) and not h["brick"]:
            raise SystemExit(
                f"house {h['name']}: lids=...,landlock needs brick=; on the "
                "machine root the lid grants read and execute beneath / and "
                "confines nothing")
        if h["brick"]:
            # PHASE 3: brick= IS A HASH. The `..` check that was here is gone
            # because the thing it defended against cannot be written any
            # more -- 64 hex characters have no separator and no relative
            # component. The check below is a shape check, not a safety one:
            # a wrong hash names a file that is not there and the house dies
            # at `open brick image`, loudly, which is a different failure
            # from a house rooted somewhere it should not be.
            if not _is_hex64(h["brick"]):
                raise SystemExit(
                    f"house {h['name']}: brick= must be {BRICK_HEX} hex "
                    f"characters (the sha256 of the image, as mkbrick "
                    f"prints it), not a path. Phase 3 moved the plan from "
                    f"a path to a hash; nw-sup composes the path itself.")
            if h["brick"] == "0" * BRICK_HEX:
                # ALL-ZERO IS THE ONE VALUE THAT IS NOT A HASH. blob.h spends
                # the field's all-zero state on "no brick", so this plan
                # declares a brick and gets a house on the machine root: the
                # baker accepted it, the checker read it as brickless, and
                # the city booted with no `lid brick` line while every reader
                # reported success. Invariant 6's "the plan lying", reached
                # through the one value the 2^256 argument does not cover.
                # Found by `tcb-review`.
                #
                # THIS CAN ONLY LIVE HERE, and it is the exception that shows
                # what plan.md's "any rule the runtime relies on must be in
                # nwcheck.c too" is actually about. By the time the blob
                # exists the distinction is GONE -- 32 zero bytes IS the
                # no-brick encoding, and no checker can tell a plan that
                # meant it from one that did not. The representation is the
                # enforcement; the baker is the last place the intent still
                # exists.
                raise SystemExit(
                    f"house {h['name']}: brick= is all zeros, which is how "
                    f"the blob spells NO brick -- so this plan would boot a "
                    f"house on the machine root while saying it is in a "
                    f"brick. No image hashes to zero; mkbrick never prints "
                    f"this.")
            if not (h["lids"] & LID_NEWNS):
                raise SystemExit(
                    f"house {h['name']}: brick= needs lids=...,newns; a brick "
                    "is a root and pivoting without a private mount namespace "
                    "would repoint the machine's root")
            if not h["layer"]:
                raise SystemExit(
                    f"house {h['name']}: brick= needs layer=. Every house "
                    f"with a brick has exactly one writable layer -- the "
                    f"brick is what it can see and the layer is what it can "
                    f"keep. Without one its writes vanish at exit while the "
                    f"plan says it has data, and nothing errors.")
        elif h["layer"]:
            raise SystemExit(
                f"house {h['name']}: layer= without brick=; there is no "
                f"lower to overlay, so the layer would name a directory "
                f"nothing ever mounts")
        elif h["binds"]:
            raise SystemExit(
                f"house {h['name']}: bind= without brick=; there is no root "
                "to bind into")
    # ONE LAYER PER HOUSE. Two houses with the same id share one upperdir
    # and one workdir: each appends to the other's data, and the kernel
    # calls the workdir sharing undefined behaviour -- into dmesg, which
    # nothing here reads. Refused in nwcheck.c too; this is the bake-time
    # half, and it can name the houses, which the checker cannot.
    seen = {}
    for h in houses:
        if not h["layer"]:
            continue
        if h["layer"] in seen:
            raise SystemExit(
                f"house {h['name']}: layer={h['layer']} is already used by "
                f"house {seen[h['layer']]}. A layer is one house's data: "
                f"sharing one means both append to the same files, and the "
                f"kernel calls a shared overlay workdir undefined behaviour.")
        seen[h["layer"]] = h["name"]
    # THE RESOURCE BLOCK'S CROSS-FIELD RULES. Each is a pair that means
    # something different together than either does alone, which is the
    # same argument as brick=/layer= and is why they are structural
    # rather than left to whoever reads the plan.
    for h in houses:
        r = h["res"]
        # A throttle above its backstop is a throttle that can never
        # fire: memory.high slows a house down so an operator does not
        # lose work, memory.max kills it. Declaring high >= max asks for
        # the kill without the warning, which is almost certainly the
        # two numbers the wrong way round.
        if r["mem_high"] and r["mem_max"] and r["mem_high"] >= r["mem_max"]:
            raise SystemExit(
                f"house {h['name']}: mem-high={r['mem_high']} is not below "
                f"mem-max={r['mem_max']}. The throttle exists to fire "
                f"BEFORE the backstop; at or above it the house is killed "
                f"with no warning pass, which is what omitting mem-high "
                f"would have given you anyway.")
        # nice only means anything under SCHED_OTHER, so it requires a
        # DECLARED one. Not merely "not batch and not idle": with no
        # policy declared the house keeps whatever it inherits, and
        # nothing at bake time knows what that is -- so a nice under an
        # undeclared policy is a number that may or may not be
        # discarded, which is the plan lying with a coin toss in it.
        if r["nice"] and r["sched_policy"] != SCHED_OTHER:
            name = ([k for k, v in SCHED_NAMES.items()
                     if v == r["sched_policy"]] or ["(none declared)"])[0]
            raise SystemExit(
                f"house {h['name']}: nice={r['nice']} with sched={name}. "
                f"nice means nothing outside SCHED_OTHER, so declare "
                f"sched=other beside it or drop the nice. With no sched= "
                f"the house keeps the policy it inherits and nothing "
                f"here knows which that is.")
        # A layer capacity with no layer bounds nothing.
        if r["layer_bytes"] and not h["layer"]:
            raise SystemExit(
                f"house {h['name']}: layer-bytes= without layer=. There is "
                f"no writable area to bound -- a house with no layer keeps "
                f"nothing across a restart, so a capacity for it names "
                f"nothing.")

    for unit, path in binds:
        if not path_clean(path):
            raise SystemExit(
                f"bind path must be absolute with no '..' component: {path}")
        if len(path.encode("ascii")) >= PATH_LEN:
            raise SystemExit(f"bind path too long: {path}")
    return idx


def bake(path, houses):
    binds = [(i, p) for i, h in enumerate(houses) for p in h["binds"]]
    check(houses, binds)
    unit = b""
    for h in houses:
        unit += pad(h["name"], NAME_LEN) + pad(h["exec"], PATH_LEN)
        unit += (bytes.fromhex(h["brick"]) if h["brick"]
                 else b"\0" * BRICK_HASH)
        unit += pad(h["layer"], NAME_LEN)
        # kind (the byte that was "critical" until 2026-09-10), then _pad,
        # which must stay zero -- nwcheck.c rejects a nonzero spare.
        unit += struct.pack("<BBBB", h["kind"], h["budget"], h["lids"], 0)
        unit += pack_res(h["res"])
    table = b""
    for u, p in binds:
        table += struct.pack("<H", u) + pad(p, PATH_LEN)
    # Must equal NW_MAGIC in blob.h. tests/run.py asserts that agreement;
    # the version moves when the layout moves -- see the comment there.
    prefix = b"NWPLAN09" + struct.pack("<II", len(houses), len(binds))
    crc = zlib.crc32(prefix + struct.pack("<I", 0) + unit + table) & 0xFFFFFFFF
    blob = prefix + struct.pack("<I", crc) + unit + table
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "wb").write(blob)
    digest = hashlib.sha256(blob).hexdigest()
    open(path + ".sha256", "w").write(digest + "\n")
    # THE LAYER SIDECAR, so that whatever stages a plan does not need a
    # second copy of the blob layout to find out which directories to
    # create. Staging reads this; nw-sup does NOT create layer dirs, and
    # adding a creator there is what this file exists to avoid.
    # THE ID, THEN THE BRICK IT IS STACKED OVER. The brick
    # is not decoration -- `tools/stage-candidate.py` refuses a candidate
    # that reuses a live layer id, and the reason it gives is a FOLD:
    # the folded brick already contains that layer's contents, so
    # reusing the id stacks them over themselves and the old whiteouts
    # re-delete files now baked in. That reason is about the folded
    # house and nothing else, and the refusal was over every shared id,
    # so a two-house city could never fold one house and keep the
    # other's data. Distinguishing them needs the brick, and the
    # alternative -- the fold helper telling the stager which ids it did
    # not fold -- is an override rather than a check. The baker has both
    # values here, so the stager can establish the property itself.
    #
    # No "no brick" spelling, because NW_E_LAYERPAIR makes a layer
    # without a brick unrepresentable in both directions.
    # THIRD FIELD, LAYER_BYTES: the value is already in the sealed blob
    # (nwcheck.c validates it, nwspawn.c forwards it to nw-sup), and
    # this is the same reason the brick is the second field rather than
    # left for the reader to re-derive -- the reader that needs it
    # (tools/stage-layers.py, sizing the backing store before boot)
    # must not parse the blob to get it, or that is a third copy of the
    # unit layout. 0 means unset, the same convention the field uses
    # everywhere else.
    open(path + ".layers", "w").write(
        "".join(f'{h["layer"]} {h["brick"]} {h["res"]["layer_bytes"]}\n'
                for h in houses if h["layer"]))
    print(f"wrote {path} units={len(houses)} binds={len(binds)} "
          f"crc=0x{crc:08x} bytes={len(blob)} sha256={digest}")
    # WHICH HOUSES HAVE NO BLOCK, NAMED. The absence has to be visible
    # without inventing a number to make it visible -- a default would
    # be a limit nobody chose, failing in the direction hardest to
    # diagnose. Named rather than counted: a count here would be a claim
    # about how hard the baker looked.
    unbounded = [h["name"] for h in houses if h["res"] == empty_res()]
    if unbounded:
        print(f"  no resource block: {' '.join(unbounded)}")


def _is_layer_id(v):
    """A layer-id is a NAME, with the same closed alphabet as a house name.

    Not a path, for the reason phase 3 retired the brick path: nw-sup
    composes NW_LAYER_DIR "/" <id> "/" upper itself, and a fixed alphabet
    with no separator cannot express a traversal. The length bound is
    NAME_LEN because that is the field's width."""
    return (isinstance(v, str) and 0 < len(v) < NAME_LEN
            and all(c.isalnum() or c in "_-" for c in v)
            and all(ord(c) < 128 for c in v))


def _is_hex64(v):
    """Exactly BRICK_HEX lowercase hex characters. A SHAPE check only.

    Closed alphabet and fixed length, so no separator and no relative
    component can appear -- that is the whole phase-3 argument, and it is
    checked here as well as in nwcheck.c because the baker is not in the TCB
    and a blob can arrive from anywhere.

    All-zero passes this and is refused separately at the call site, with
    its own reason. Folding it in here was tried first and produced
    `brick= must be 64 hex characters ... not a path` for a value that IS 64
    hex characters -- a true rejection under a false reason, which is this
    project's characteristic failure wearing the fix's clothes."""
    return (isinstance(v, str) and len(v) == BRICK_HEX
            and all(c in "0123456789abcdef" for c in v))


def house(name, exe, kind, budget, lids, brick="", binds=(), layer="",
          res=None):
    return {"name": name, "exec": exe, "kind": kind, "budget": budget,
            "lids": lids, "brick": brick, "layer": layer,
            "res": res if res is not None else empty_res(),
            "binds": list(binds)}


def default_city(probe: str, lids: int):
    return [
        house("alpha", probe, KIND_ONESHOT, 3, lids),
        house("beta", probe, KIND_ONESHOT, 3, lids),
        house("gamma", probe, KIND_ONESHOT, 3, lids),
        house("delta", probe, KIND_ONESHOT, 1, lids),
    ]


def parse_lids(s: str) -> int:
    lids = 0
    for tok in s.split(","):
        tok = tok.strip().lower()
        if tok not in LID_NAMES:
            raise SystemExit(
                f"unknown lid {tok!r}: one of {', '.join(sorted(LID_NAMES))}")
        lids |= LID_NAMES[tok]
    return lids


def load_city(path: str):
    houses = []
    for raw in open(path):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] == "house":
            name, exe = parts[1], parts[2]
            budget = 3
            lids = None
            kind = None
            brick, layer, binds = "", "", []
            res = empty_res()
            for kv in parts[3:]:
                k, _, v = kv.partition("=")
                if k == "critical":
                    raise SystemExit(
                        "critical= was removed on 2026-09-10: nothing a house "
                        "does halts the city")
                elif k == "budget":
                    budget = int(v)
                elif k == "window":
                    raise SystemExit(
                        "window= was removed 2026-09-11 (D18): the restart "
                        "budget is a hard total for the life of nw-sup, "
                        "not a sliding window")
                elif k == "lids":
                    lids = parse_lids(v)   # `none` is a legal token
                elif k == "brick":
                    brick = v
                elif k == "layer":
                    if not _is_layer_id(v):
                        raise SystemExit(
                            f"house {name}: layer= must be a name of up to "
                            f"{NAME_LEN - 1} characters from "
                            f"[A-Za-z0-9_-], not a path -- nw-sup composes "
                            f"the directory itself: {v!r}")
                    layer = v
                elif k == "bind":
                    binds.append(v)
                elif k == "cpus":
                    res["cpu_mask"] = parse_cpus(v)
                elif k == "cpu-weight":
                    n = int(v) if v.isdecimal() else -1
                    if not CPU_WEIGHT_MIN <= n <= CPU_WEIGHT_MAX:
                        raise SystemExit(
                            f"house {name}: cpu-weight={v} must be "
                            f"{CPU_WEIGHT_MIN}..{CPU_WEIGHT_MAX}, which is "
                            f"cgroup v2's own range for cpu.weight. Omit the "
                            f"key for the default share.")
                    res["cpu_weight"] = n
                elif k == "mem-high":
                    res["mem_high"] = parse_bytes(v, "mem-high")
                elif k == "mem-max":
                    res["mem_max"] = parse_bytes(v, "mem-max")
                elif k == "io-rbps":
                    res["io_rbps"] = parse_bytes(v, "io-rbps")
                elif k == "io-wbps":
                    res["io_wbps"] = parse_bytes(v, "io-wbps")
                elif k == "layer-bytes":
                    res["layer_bytes"] = parse_bytes(v, "layer-bytes")
                elif k == "sched":
                    if v not in SCHED_NAMES:
                        raise SystemExit(
                            f"house {name}: sched={v} must be one of "
                            f"{', '.join(sorted(SCHED_NAMES))}. Omit the key "
                            f"to inherit nw-sup's policy -- which is not the "
                            f"same as choosing `other` on the house's "
                            f"behalf.")
                    res["sched_policy"] = SCHED_NAMES[v]
                elif k == "nice":
                    try:
                        n = int(v)
                    except ValueError:
                        n = NICE_MIN - 1
                    if not NICE_MIN <= n <= NICE_MAX:
                        raise SystemExit(
                            f"house {name}: nice={v} must be "
                            f"{NICE_MIN}..{NICE_MAX}")
                    # nice=0 IS THE UNSET BYTE, and it is legal in the
                    # kernel's range, which is what made it the one
                    # declared zero this baker accepted. `control` found
                    # it: `nice=0 sched=idle` baked clean while
                    # `nice=1 sched=idle` was refused by a message
                    # asserting the general rule -- one value apart,
                    # opposite verdicts, and nothing told the author the
                    # 0 was discarded. Worse, the house was then listed
                    # under `no resource block:`, which plan.md says is
                    # printed for a house that declares nothing.
                    #
                    # Refused here for the same reason as cpu-weight=0
                    # and the five sizes: bake time only, because the
                    # blob genuinely cannot tell it from unset. The
                    # CHECKER still accepts nice=0 under any policy --
                    # at blob level it IS unset, and
                    # test_checker_rejects_crafted_resources pins that.
                    if n == 0:
                        raise SystemExit(
                            f"house {name}: nice=0 is the byte an unset "
                            f"field already holds, so the blob cannot "
                            f"tell it from no nice= at all -- and the "
                            f"kernel's default IS 0. Omit nice= instead.")
                    res["nice"] = n
                elif k == "kind":
                    if v not in KINDS:
                        raise SystemExit(
                            f"kind={v}: must be oneshot or longrun")
                    kind = KINDS[v]
                else:
                    raise SystemExit(f"house {name}: unknown key {k}=")
            if kind is None:
                raise SystemExit(
                    f"house {name}: kind= is required and has no default. "
                    "Use kind=oneshot (exit 0 completes, never restarted) or "
                    "kind=longrun (any exit is unexpected, including 0).")
            # LIDS= IS REQUIRED, AND `none` IS THE WAY TO SAY NO LIDS.
            # Omitting it used to mean 0, which is the same byte a
            # deliberate `lids=none` bakes -- so a reader of a city file
            # could not tell a bare house somebody chose from one
            # somebody forgot. The floor's HEIGHT is not the defect;
            # its height being unrecorded is.
            #
            # What a bare house is, and the measurement is NARROWER than
            # the claim it was first used for. The operator measured a
            # house with `lids=newns` on a real boot (2026-09-13):
            # mknod, mount, unshare of mount and user namespaces, and
            # chroot all returned 0. Nothing here drops privilege --
            # `grep -nE "setuid|setgid|capset" *.c` returns nothing --
            # so uid 0 is checkable in this tree; the syscall results
            # are the operator's.
            #
            # `lids=none` IS WORSE THAN THAT MEASUREMENT, NOT EQUAL TO
            # IT. `CLONE_NEWNS` appears once in the TCB, gated on the
            # bit (`nwsup.c`, `lid_newns`), so a house with no lids has
            # no mount namespace of its own and those verbs land on the
            # CITY's. "inside its own namespace" was the qualifier that
            # made the sentence sound survivable, and it is exactly the
            # one that does not hold for the case the message is about.
            # `claims` caught it in the string printed to whoever just
            # wrote a bare house.
            #
            # BAKE TIME ONLY, and that is the honest scope rather than a
            # gap: the blob has one lids byte and 0 is 0, so nothing in
            # `nwcheck.c` can tell an omission from a declaration. The
            # distinction exists in the city file and dies at the seal.
            # `.claude/rules/plan.md` says a rule the RUNTIME relies on
            # must be in the checker too -- nothing at runtime relies on
            # this one, so there is no second site to add.
            if lids is None:
                raise SystemExit(
                    f"house {name}: lids= is required and has no default. "
                    f"Say lids=none to declare a house with no lids -- "
                    f"which is uid 0 with no mount namespace of its own, "
                    f"so a mount, mknod or chroot it makes lands on the "
                    f"CITY's namespace. Omitting the key baked the "
                    f"identical byte, so a deliberate bare house and a "
                    f"forgotten one read the same.")
            # exec_path is resolved inside the brick, so it is already the
            # path the house will see and must not be rewritten against the
            # baker's cwd. Without a brick it names a machine path.
            if not brick:
                exe = os.path.abspath(exe)
            elif not exe.startswith("/"):
                raise SystemExit(
                    f"house {name}: exec path must be absolute inside the "
                    "brick")
            houses.append(house(name, exe, kind, budget, lids,
                                brick, binds, layer, res))
        else:
            raise SystemExit(f"bad city line: {line}")
    return houses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="plan.blob")
    ap.add_argument("--probe", default="")
    ap.add_argument("--city", default="")
    # NO DEFAULT HERE EITHER. `--probe` bakes four houses, and a default
    # here made "lids= is required and has no default" true of a house
    # line and false of the program -- the same silent choice the house
    # rule removes, on the path nobody was reading. `claims`. Both
    # in-tree callers (the Makefile and tools/mkboot.sh) already pass it.
    ap.add_argument("--lids")
    args = ap.parse_args()
    if args.city:
        houses = load_city(args.city)
    else:
        if not args.probe:
            raise SystemExit("--probe or --city required")
        if args.lids is None:
            raise SystemExit(
                "--lids is required with --probe and has no default. The "
                "probe city declares nothing itself, so a default here "
                "would choose a lid set for four houses on your behalf -- "
                "which is what lids= being required in a city file exists "
                "to stop. Use --lids none to say no lids.")
        houses = default_city(os.path.abspath(args.probe), parse_lids(args.lids))
    bake(args.out, houses)


if __name__ == "__main__":
    main()
