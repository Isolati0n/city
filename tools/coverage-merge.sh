#!/bin/sh
# coverage-merge.sh — what is covered, and what is covered nowhere.
#
# No single environment has ever run every test in this project. Landlock is
# ABI 7 on one machine and ENOSYS on another; neither has a FAT driver, so
# vfat-esp runs in none. That made the coverage claim a union of machines and
# it lived only in prose, which is exactly the shape this project keeps
# finding defects in. Each run of the suite drops a record in coverage/; this
# merges them so the union is an artifact rather than a memory.
#
#   sh tools/coverage-merge.sh
set -eu
D=coverage
[ -d "$D" ] || { echo "coverage-merge: no records in $D/ -- run make test"; exit 0; }
python3 - "$D" <<'PY'
import json, os, sys
d = sys.argv[1]
recs = []
for f in sorted(os.listdir(d)):
    if f.endswith(".json"):
        recs.append((f, json.load(open(os.path.join(d, f)))))
if not recs:
    print("coverage-merge: no records"); raise SystemExit(0)

print(f"environments on record: {len(recs)}")
for f, r in recs:
    caps = ", ".join(k for k, v in sorted(r.get("capabilities", {}).items()) if v) or "none"
    print(f"  {f[:-5]:<28} kernel {r.get('kernel','?'):<16} has: {caps}")

ran, skipped = set(), {}
for _, r in recs:
    ran |= set(r.get("passed", []))
    for name, why in r.get("skipped", {}).items():
        skipped.setdefault(name, why)

nowhere = sorted(n for n in skipped if n not in ran)
print()
print(f"covered somewhere : {len(ran)}")
if nowhere:
    print(f"covered NOWHERE   : {len(nowhere)}")
    for n in nowhere:
        print(f"  {n}\n      {skipped[n]}")
    print()
    print("These are untested in every environment on record. That is a")
    print("property of the set of machines, not of any one of them.")
else:
    print("covered NOWHERE   : none")
PY
