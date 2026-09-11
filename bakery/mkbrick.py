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
# dropped here changes every byte of the output while nothing about the
# content moved.
#
#   -T 0                 every file timestamp. Without it mtimes leak into
#                        the image and the same tree packed tomorrow gets a
#                        different name.
#   -U <nil>             the filesystem UUID is RANDOM BY DEFAULT. This is
#                        the flag whose absence breaks reproducibility most
#                        completely and most invisibly -- the image mounts,
#                        the content is right, and the name is different
#                        every single run. It is the negative control in
#                        tests/run.py for exactly that reason.
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
        # mkfs.erofs refuses to overwrite a non-empty file, and mkstemp
        # already created it.
        os.unlink(tmp)
        cmd = ["mkfs.erofs"] + list(flags) + [tmp, tree]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(
                f"mkbrick: mkfs.erofs failed ({r.returncode})\n"
                f"  {' '.join(cmd)}\n{r.stdout}{r.stderr}")
        digest = sha256_file(tmp)
        final = os.path.join(out_dir, digest + ".img")
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
    ap.add_argument("--out-dir", default="/nw/bricks")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    digest, _ = pack(a.tree, a.out_dir, quiet=a.quiet)
    if a.quiet:
        print(digest)


if __name__ == "__main__":
    main()
