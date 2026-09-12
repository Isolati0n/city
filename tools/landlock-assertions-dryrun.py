"""Exercise test_landlock_confines' ASSERTION BLOCK against synthetic
boot output. NOT A TEST, and deliberately not in tests/run.py.

READ THIS BEFORE QUOTING IT. It stubs boot() -- and also run(), make_brick(),
stage_layers() and city_closed(), the last of which REMOVES a real
assertion rather than feeding it -- so every field it checks is a
string this file wrote -- "it asserts on state the test itself
created", which .claude/rules/harness.md names as one of the two ways a
green test is fake. It is fake as a test of the lid, on purpose. What it
establishes is narrower and is the only thing available on a machine
without Landlock:

  the assertions read only field names houses/brick.c can actually
  emit (cross-checked against the fixture source, not against a
  hand-written copy), and each one FAILS on the value it claims to.

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
import os, re, sys, importlib.util
os.environ.setdefault("NW_STAGE", "/tmp/nwc-op")
spec = importlib.util.spec_from_file_location("runpy_suite", "tests/run.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# Values in the fixture's OWN format. `mknod_bind_fifo="ok"` passed for
# a round because that assertion is startswith("ok"), while brick.c only
# ever emits `ok(0)` or `denied(N)`. A synthetic value the machine cannot
# produce is a third dialect, and this file exists to have one fewer.
GOOD = dict(
    id="landlock-brick", bind="token-from-the-machine", wr_bind="ok",
    wr_existing="ok(0)", mknod_root="denied(13)",
    mknod_root_fifo="denied(13)", mknod_root_sock="denied(13)",
    mknod_bind="denied(13)", mknod_bind_fifo="ok(0)",
    mknod_bind_sock="ok(0)", wr_root="denied(13)", mk_bind="ok(0)",
    trunc_root="denied(13)", trunc_bind="ok(0)",
    st_root_mkdir="denied(13)", st_root_symlink="denied(13)",
    st_root_mkblock="denied(13)", st_root_unlink="denied(13)",
    st_root_rmdir="denied(13)",
    st_bind_mkdir="ok(0)", st_bind_symlink="ok(0)",
    st_bind_mkblock="denied(13)", st_bind_unlink="ok(0)",
    st_bind_rmdir="ok(0)",
)


def fixture_fields(path="houses/brick.c"):
    """Every field name houses/brick.c can emit.

    THE CHECK THIS FILE CLAIMED TO DO AND DID NOT. Its docstring said it
    establishes that "the assertions parse the field names the fixture
    actually emits"; it never opened brick.c, so GOOD was a third
    hand-written copy that pinned agreement between this file and the
    test and nothing else. `control` renamed the socket field in the
    fixture only and got byte-identical output and exit 0 -- the exact
    round this tool exists to save.

    Two emission shapes, both nw_emit format strings:
      "%s <literal>=..."        -- a fixed name
      "%s %s_<suffix>=..."      -- the caller's key plus a suffix
    so the second needs the key literals passed at the call sites.
    """
    src = open(path).read()
    fixed = set(re.findall(r'nw_emit\("%s (\w+)=', src))
    sfx = set(re.findall(r'nw_emit\("%s %s_(\w+)=', src))
    keys = set(re.findall(r'report_\w+\(\s*"(\w+)"', src))
    return fixed | keys | {f"{k}_{x}" for k in keys for x in sfx}

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

# CROSS-CHECK FIRST, because every result below is worthless if the
# test reads a name the fixture cannot produce: field() returns "" and
# the mutations all "fail" for the wrong reason.
emits = fixture_fields()
read = set(re.findall(r'field\(f?"(\w+)"\)', open("tests/run.py").read()))
read |= {f"{p}_{o}" for p, ops in
         (("st_root", ("mkdir", "symlink", "mkblock", "unlink", "rmdir")),
          ("st_bind", ("mkdir", "symlink", "mkblock", "unlink", "rmdir")),
          ("mknod_root", ("fifo", "sock")), ("mknod_bind", ("fifo", "sock")))
         for o in ops}
ghosts = sorted(n for n in (read & set(GOOD)) | (set(GOOD) - emits)
                if n not in emits)
print("fields the test/dry-run read that the fixture cannot emit:", ghosts)
if ghosts:
    print("MISMATCH -- fix the names before reading anything below")
    sys.exit(2)

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
    "REMOVE_FILE leaked at root":    dict(st_root_unlink="ok(0)"),
    "REMOVE_DIR leaked at root":     dict(st_root_rmdir="ok(0)"),
    "MAKE_DIR leaked at root":       dict(st_root_mkdir="ok(0)"),
    "MAKE_SYM leaked at root":       dict(st_root_symlink="ok(0)"),
    "MAKE_BLOCK leaked at root":     dict(st_root_mkblock="ok(0)"),
    "MAKE_BLOCK leaked in a bind":   dict(st_bind_mkblock="ok(0)"),
    "unlink refused in a bind":      dict(st_bind_unlink="denied(13)"),
    "rmdir refused in a bind":       dict(st_bind_rmdir="denied(13)"),
    "mkdir refused in a bind":       dict(st_bind_mkdir="denied(13)"),
    "symlink refused in a bind":     dict(st_bind_symlink="denied(13)"),
    "root refusal by EPERM not lid": dict(st_root_unlink="denied(1)"),
    "truncate probe never ran":      dict(trunc_root="unprobed(2)"),
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
m_abi2_nb = attempt(dict(GOOD, trunc_root="ok(0)", trunc_bind="absent"), abi=2)
print(f"{m_abi2_ok}  <- ABI 2, truncate unrestricted (must PASS)")
print(f"{m_abi2_dn}  <- ABI 2, truncate denied (must FAIL)")
print(f"{m_abi2_nb}  <- ABI 2, trunc_bind not emitted (must FAIL)")
if m_abi2_ok != "PASS" or m_abi2_dn != "FAIL" or m_abi2_nb != "FAIL":
    bad += 1
sys.exit(1 if bad else 0)
