# tools/jars — the two solvers, committed

`plan.als` and `Plan.tla` are checked by `test_specs_are_checked` in
`tests/run.py`. These jars are what checks them.

| jar | version | sha256 | source |
|---|---|---|---|
| `tla2tools.jar` | TLC 2026.09.10.191157 (v1.8.0 release) | `957b23b2bb31d08f19346e105e23585f93fea9a139a712b0ac347eedaf26afea` | `https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar` |
| `alloy.jar` | Alloy 6.2.0 | `6b8c1cb5bc93bedfc7c61435c4e1ab6e688a242dc702a394628d9a9801edb78d` | `https://github.com/AlloyTools/org.alloytools.alloy/releases/download/v6.2.0/org.alloytools.alloy.dist.jar` |

Verify with `sha256sum tools/jars/*.jar`.

## Why they are committed rather than fetched

Because the alternative was what this repository had for its whole life:
two spec files that nothing ran, whose comments read as verification.
Both turned out not even to PARSE — Alloy refused `plan.als` for a
missing scope, TLC refused `Plan.tla` for a use-before-definition — and
neither fact was discoverable without the tool present. A fetch script
would put the checks one network failure away from silently becoming
prose again.

They cost about 25 MB, which is most of this repository. That is a real
price and the reason is written above rather than assumed; if it becomes
the wrong trade, replace this directory with a fetch script pinned to
the hashes in the table, and keep the SKIP path in `test_specs_are_checked`
exactly as it is — it already says, loudly, that an unverified run must
not be cited.

## Running them by hand

The limits both specs use are generated from `blob.h`:

```
python3 tools/gen-spec-limits.py          # writes specs/limits.als, specs/Plan.cfg
```

Then, from a directory holding `Plan.tla` + `Plan.cfg`:

```
java -cp tools/jars/tla2tools.jar tlc2.TLC -config Plan.cfg Plan.tla
```

and from one holding `plan.als` + `limits.als`:

```
java -Xss512m -jar tools/jars/alloy.jar exec -f plan.als
```

**`-Xss512m` is not optional.** The existential `run sealed` overflows the
default JVM stack at 12-bit Int (`StackOverflowError` inside Kodkod's CNF
translator). Measured, not guessed.

## Reading the output

Alloy inverts the sense you expect, and getting it backwards would make
every failure look like a pass:

- for a **`check`**, `SAT` means a **counterexample was found** — the
  assertion is false. `UNSAT` means it holds within the scope.
- for a **`run`**, `SAT` means an instance exists. `UNSAT` means the facts
  admit no model at all, so every `check` above it passed **vacuously**.
  The suite asserts this one too, for that reason.

TLC is the ordinary way round: `Model checking completed. No error has
been found.`

Both results are **bounded**. Alloy's scope is 8 of each signature with
12-bit integers; TLC explores one state per legal unit count. Neither is
a proof about 64 houses at runtime, and `plan.als` still declares no
upper bound on `#House` at all — see the note at the foot of that file.
