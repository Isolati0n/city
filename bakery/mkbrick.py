#!/usr/bin/env python3
"""mkbrick — pack a directory tree into a content-addressed brick image.

Not in the TCB. Offline, like the rest of the bakery: nothing on a running
machine calls this, and nothing it produces is trusted without nw-check.
Phase 1 of docs/plans/01; it changes nothing at runtime.

A brick is named by the sha256 of the image FILE, not of the tree. That is
the whole reason the flag set below is the spec rather than a convenience:
two packs of identical content must produce identical bytes, or the name
stops meaning "this content" and starts meaning "this packing run".

Usage:
    python3 bakery/mkbrick.py <tree> [--out-dir DIR] [--quiet]

Prints the hash on stdout. Writes <out-dir>/<sha256>.img.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

# The all-zero UUID. Named rather than inline because it appears in the
# flag list and in the reason for the flag, and those must not drift.
NIL_UUID = "00000000-0000-0000-0000-000000000000"

# THE FLAG SET IS THE SPEC. docs/options/08 argues each one; the reasons
# are repeated here because this is the file someone edits, and a flag
# dropped here changes the NAME of the output while nothing about the
# content moved. It does not change every byte, and the true number is the
# better argument: dropping -U leaves 20 bytes of the image different --
# 0.1% of a 20 KiB image, 0.5% of a 4 KiB one, the same 20 bytes either
# way, because it is the UUID field and its checksum. Twenty bytes are
# enough to produce a completely different content address. ("changes
# every byte" stood here until claims measured it.)
#
#   -T 0                 every file timestamp. Without it mtimes leak into
#                        the image and the same tree packed tomorrow gets a
#                        different name.
#   -U <nil>             the filesystem UUID is RANDOM BY DEFAULT: the
#                        image mounts, the content is right, and the name
#                        is different every single run. It is the negative
#                        control in tests/run.py for that reason -- not
#                        because it is the worst of the four. Dropping -T 0
#                        has an identical symptom, measured; the ranking
#                        that used to be here ("breaks reproducibility most
#                        completely and most invisibly") was never measured
#                        and is not needed by the argument.
#   --force-uid=0        ownership, which docs/options/08 Q2 excludes from
#   --force-gid=0        brick identity: the same tree packed by two
#                        different users is the same brick.
#   -zlz4hc              the compressor is part of the output, so it is
#                        part of the name. Changing it is a format change.
#
# tests/run.py imports EROFS_FLAGS rather than copying it, so the negative
# control drops a flag from THIS list instead of from a second copy that
# could drift out of step with it.
EROFS_FLAGS = [
    "-T", "0",
    "-U", NIL_UUID,
    "--force-uid=0",
    "--force-gid=0",
    "-zlz4hc",
]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def brick_dir() -> str:
    """NW_BRICK_DIR from blob.h -- where nw-sup will look for the image.

    Read, not spelled. This was `default="/nw/bricks"`, a literal, in the
    one tool that writes images for a real machine, while blob.h's comment
    claimed the constant had three readers each reading it. Changing the
    #define moved nw-sup and left mkbrick behind, with a clean compile, no
    assert and a green suite -- the suite follows the header because
    make_brick reads it. On hardware every brick house dies at
    `open brick image`. Found by `tcb-review` and `fd-auditor`."""
    return _define("NW_BRICK_DIR")


def brick_suffix() -> str:
    """NW_BRICK_SUFFIX from blob.h. Read, not spelled: nw-sup composes the
    image path from NW_BRICK_DIR, the hex hash and this suffix, so a second
    copy of ".img" here is a house that mounts nothing. It is no longer a
    LENGTH constraint -- phase 3 replaced brick[NW_BRICK_LEN] with a
    32-byte hash, so the suffix costs no room in the blob -- and
    tests/run.py's stage limit now derives from NW_PATH_LEN."""
    return _define("NW_BRICK_SUFFIX")


def _define(name: str) -> str:
    """One string #define out of blob.h. One parser, not one per constant:
    the suffix had its own open-coded loop and the directory had no reader
    at all, which is how they came to disagree with the header in different
    ways."""
    here = os.path.dirname(os.path.abspath(__file__))
    for line in open(os.path.join(here, "..", "blob.h")):
        f = line.split()
        if len(f) >= 3 and f[0] == "#define" and f[1] == name:
            return f[2].strip('"')
    raise SystemExit(f"mkbrick: blob.h has no {name}")


def pack(tree: str, out_dir: str, flags=None, quiet: bool = False) -> tuple:
    """Pack tree into out_dir/<sha256>.img. Returns (hash, path).

    flags defaults to EROFS_FLAGS. It is a parameter so the suite's
    negative control can drop one without a second copy of the list.
    """
    if not os.path.isdir(tree):
        raise SystemExit(f"mkbrick: not a directory: {tree}")
    if flags is None:
        flags = EROFS_FLAGS
    if not shutil.which("mkfs.erofs"):
        raise SystemExit(
            "mkbrick: mkfs.erofs is not installed. This is a SKIP, not a "
            "pass: no brick was packed.")

    os.makedirs(out_dir, exist_ok=True)
    # Build into a temporary name, then rename to the hash. The name cannot
    # be known until the file exists, and a half-written image must never
    # occupy a content-addressed path -- a reader that finds
    # <hash>.img is entitled to assume it hashes to <hash>.
    fd, tmp = tempfile.mkstemp(prefix=".mkbrick-", suffix=".img", dir=out_dir)
    os.close(fd)
    try:
        # There was an os.unlink(tmp) here, under a comment saying
        # "mkfs.erofs refuses to overwrite a non-empty file, and mkstemp
        # already created it". Both halves were wrong and the second did
        # not even describe the first: mkstemp creates an EMPTY file, so
        # the stated rule would not have applied to it anyway. Measured on
        # erofs-utils 1.7.1, mkfs.erofs truncates and overwrites a fresh
        # name, an existing empty file, a 1 MB junk file and a valid image
        # alike, producing identical bytes in every case.
        #
        # The line was not merely useless. Unlinking gave up the one thing
        # mkstemp exists to provide -- a name nothing else holds -- in a
        # directory that defaults to the shared /nw/bricks, and it threw
        # away mkstemp's 0600 along with it, which is why the delivered
        # image used to arrive at umask-derived 0644. It is 0600 now: a
        # brick image is read by nw-sup as root and has no reason to be
        # wider than that. fd-auditor and claims found this independently.
        cmd = ["mkfs.erofs"] + list(flags) + [tmp, tree]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(
                f"mkbrick: mkfs.erofs failed ({r.returncode})\n"
                f"  {' '.join(cmd)}\n{r.stdout}{r.stderr}")
        digest = sha256_file(tmp)
        final = os.path.join(out_dir, digest + brick_suffix())
        # Same content, same name: a rebuild is a no-op rather than an
        # error. Content-addressed storage has no update, only arrival.
        os.replace(tmp, final)
        tmp = None
        if not quiet:
            print(f"{digest}  {final}  "
                  f"{os.path.getsize(final)} bytes  from {tree}")
        return digest, final
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tree")
    ap.add_argument("--out-dir", default=brick_dir())
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    digest, _ = pack(a.tree, a.out_dir, quiet=a.quiet)
    if a.quiet:
        print(digest)


if __name__ == "__main__":
    main()
