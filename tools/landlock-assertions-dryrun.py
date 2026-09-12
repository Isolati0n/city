"""Exercise test_landlock_confines' ASSERTION BLOCK against synthetic
boot output. NOT A TEST, and deliberately not in tests/run.py.

READ THIS BEFORE QUOTING IT. It stubs boot(), so every field it checks
is a string this file wrote -- "it asserts on state the test itself
created", which .claude/rules/harness.md names as one of the two ways a
green test is fake. It is fake as a test of the lid, on purpose. What it
establishes is narrower and is the only thing available on a machine
without Landlock:

  the assertions parse the field names the fixture actually emits, and
  each one FAILS on the value it claims to fail on.

That is a control run of the assertion logic, not of Landlock. On a
kernel with Landlock the real test runs and this file is worthless; on a
kernel without one, test_landlock_confines skips by name and nothing
else can show those lines have ever executed. It exists because the
alternative was shipping unrun assertions to the one machine that can
run them and spending its round on a typo.

Not wired into `make test`. Run it by hand after touching that test:

    NW_STAGE=<your stage> python3 tools/landlock-assertions-dryrun.py

Exit 0 means the baseline passed and every mutation below failed.
"""
import os, sys, importlib.util
os.environ.setdefault("NW_STAGE", "/tmp/nwc-op")
spec = importlib.util.spec_from_file_location("runpy_suite", "tests/run.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

GOOD = dict(
    id="landlock-brick", bind="token-from-the-machine", wr_bind="ok",
    wr_existing="ok(0)", mknod_root="denied(13)",
    mknod_root_fifo="denied(13)", mknod_root_sock="denied(13)",
    mknod_bind="denied(13)", mknod_bind_fifo="ok",
    mknod_bind_sock="ok", wr_root="denied(13)", mk_bind="ok(0)",
    trunc_root="denied(13)", trunc_bind="ok(0)",
)

def out_for(d):
    lines = ["[nw-sup] lid landlock", "[nw-root] closed houses_reaped=1 orphans=0"]
    for k, v in d.items():
        lines.append(f"[sealed] sealed {k}={v}")
    return "\n".join(lines) + "\n"

def attempt(d, abi=7):
    m.landlock_abi = lambda: abi
    m.make_brick = lambda *a, **k: "deadbeef" * 8
    m.run = lambda *a, **k: type("R", (), {"returncode": 0, "out": "", "err": ""})()
    m.boot = lambda **k: (0, out_for(d))
    m.city_closed = lambda rc, out: True
    m.stage_layers = lambda *a, **k: None
    try:
        m.test_landlock_confines()
        return "PASS"
    except SystemExit as e:
        return "FAIL"

print("baseline (all correct):", attempt(GOOD))
# Each mutation must FAIL, or the assertion it targets is not doing anything.
muts = {
    "socket created at root":        dict(mknod_root_sock="ok(0)"),
    "fifo refused by EPERM not lid": dict(mknod_root_fifo="denied(1)"),
    "device node made in a bind":    dict(mknod_bind="ok(0)"),
    "fifo refused in a bind":        dict(mknod_bind_fifo="denied(13)"),
    "socket refused in a bind":      dict(mknod_bind_sock="denied(13)"),
    "TRUNCATE leaked at the root":   dict(trunc_root="ok(0)"),
    "truncate denied in a bind":     dict(trunc_bind="denied(13)"),
    "layer unwritable":              dict(wr_existing="denied(13)"),
}
bad = 0
for why, over in muts.items():
    d = dict(GOOD); d.update(over)
    r = attempt(d)
    print(f"{r}  <- {why}")
    if r != "FAIL":
        bad += 1
# And the ABI branch: at ABI 2 the withholding is unenforceable, so a
# DENIED truncate is the wrong answer and an allowed one is right.
m_abi2_ok = attempt(dict(GOOD, trunc_root="ok(0)", trunc_bind="ok(0)"), abi=2)
m_abi2_dn = attempt(GOOD, abi=2)
print(f"{m_abi2_ok}  <- ABI 2, truncate unrestricted (must PASS)")
print(f"{m_abi2_dn}  <- ABI 2, truncate denied (must FAIL)")
if m_abi2_ok != "PASS" or m_abi2_dn != "FAIL":
    bad += 1
sys.exit(1 if bad else 0)
