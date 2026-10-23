# Testing

How Heliograph is tested, and why. The research behind this page is in
[research/testing.md](research/testing.md). Rules in
[principles.md](principles.md) still apply, and this page refines its "Tests"
section.

**Status (2026-10-22).** In place:
- unit tests and properties, strict warnings and markers, random order and
  the second time zone;
- shared builders (`tests/support`);
- a 90% branch-coverage floor per file;
- the cross-process determinism test (`tests/determinism`);
- the suite rules (`tests/meta`);
- 190 declared controls with their script and CI job;
- the mutation wrapper;
- a state machine for the engine and alarms;
- live tests for the CCXT source (`tests/live`, on request);
- the conformance kit (`heliograph.testkit`, `tests/conformance`): every
  plug-in's build and parameters, and every indicator's no-peeking, edge
  case and reference checks.

- the scenarios (`tests/scenarios`): a backtest worked out by hand, and the
  example strategy over its committed data.

The adapters' own conformance checks come later, outside-in tests in
Phase 3.

## What a test is for

A test states a property in one sentence and fails when the property stops
being true. Until a deliberate break has made it fail, it is a draft:
"a test that passes when the feature is removed tests nothing" (Elysium's
`RULES.md` §3).

Three corollaries:

- **Assert the property, not the shape.** A test written beside the code
  tends to repeat what the code does. Ask what change would make the sentence
  false, and check the test catches that change.
- **Test both directions of a guard.** A refusal needs a pair showing the
  legitimate case is still accepted.
- **An assertion over a collection says nothing about an empty one.** Show
  that the collection is non-empty before asserting `all(...)` over it.

## Layers

| Layer | Where | What it proves | Runs |
| --- | --- | --- | --- |
| Unit | `tests/core`, `tests/plugins`, `tests/app` (mirrors `src/`) | one module's behaviour through its public interface | every check |
| Properties | beside the unit tests | a rule over every input (Hypothesis `@given`) | every check |
| State machines | beside the unit tests | a component equals a slow reference model after any sequence of operations (`RuleBasedStateMachine`) | every check |
| Determinism | `tests/determinism` | the same inputs give the same journal (entry rows byte for byte, and header) in separate processes with different hash seeds and time zones | every check |
| Conformance | `tests/conformance` | every registered plug-in of a kind meets the kind's contract | every check |
| Scenarios | `tests/scenarios` | a whole backtest still gives its recorded counts, statistics and signals | every check |
| Outside-in | `tests/process` (Phase 3) | the engine process, started as a person would start it, serves what it should through its API; imports nothing from `heliograph` | every check |
| Live | `tests/live`, marker `live` | an adapter still matches the real service (CCXT exchanges) | on request and on a schedule |
| Suite rules | `tests/meta` | the suite and the check scripts themselves follow the rules on this page | every check |

### Unit tests and properties

- Drive public interfaces with real objects: a `SimulatedClock`, a real
  `MessageBus`, a real SQLite file in `tmp_path`. Replace something only
  where it reaches outside the process (a network, the wall clock), and then
  write one test that drives the real connection end to end (Elysium's "a
  mocked callback tests the component and skips the wire").
- Name tests as sentences describing behaviour:
  `test_a_late_point_is_counted_and_not_added`.
- Use Hypothesis where a rule is crisp. Examples:
  - two implementations must agree;
  - a value must survive a round trip;
  - appending data must not change earlier output.
- Compare against an independent definition, not the formula restated.

### State machines against a reference model

The engine, the alarm queue, time bars and later the indicator graph each
have an obvious slow reference: a sorted list, a loop over every point, a
recomputation from scratch. A `RuleBasedStateMachine` applies random
operations to both and checks they agree after every step. This is how
NautilusTrader tests its order books and timers (proptest), and how
Hypothesis's own documentation recommends testing a stateful system.

### Determinism across processes

The Phase 1 replay gate replays inside one process. It cannot see output
that depends on `PYTHONHASHSEED`, such as iterating a set of strings to
publish messages: each process replays itself exactly and still disagrees
with the next one (measured: three processes, three digests). So a
determinism test runs the same session in fresh interpreters with different
hash seeds and time zones, and compares the journals byte for byte. Fresh
interpreters, because only a new process shows what the process environment
changes.

### Conformance kits (Phase 2)

This follows the design Elysium agreed for its modules: common behaviour is
tested once and inherited, never copied.

- **One kit per plug-in kind.** It runs on every plug-in registered through
  entry points, on by default. A new plug-in is tested the moment it is
  registered.
- **Checks for every plug-in** (built): it builds from the parameters the kit
  is given, its schema renders, its parameters survive JSON. The kit builds
  in an empty temporary folder and closes what it builds.
- **Checks for indicators** (built; forecasters will follow the same
  pattern). Inputs are random walks, or consistent random bars for an
  indicator that names the bar fields it reads (`fields=("high", "low",
  "close")`):
  - no peeking: in a fresh interpreter, the same 400-update beginning,
    followed by different 1000-update futures in turn and alone, gives the
    same outputs every time, and those outputs hold a value. The fresh
    interpreter matters: a cache filled by another check would make every
    run agree. A peek further than 1000 updates ahead, or a warm-up longer
    than 400, is beyond the check;
  - warm-up: only finite values or None, and a value within 160 updates;
  - gaps: while some input has had no sample yet (the graph passes None for
    it), no value. A one-input indicator is never updated without a sample;
  - a constant input, and a single update;
  - reference values (TA-Lib, a published example, or worked by hand).
- **Checks for adapters** (planned; they need a scripted source per adapter):
  declared capabilities match behaviour, rate limits hold, and
  `SourceUnavailableError` is raised rather than an empty answer.
- **Each check is `error`, `todo`, `off` or `replaced`, set in the
  plug-in's `conformance = Conformance(...)`** (`plugins/conformance.py`).
  The narrowest setting wins: a check's own over the plug-in's default, and
  the default covers only the kind's checks, never `manifest` and `params`.
  A `todo` is a strict expected failure: anything the plug-in raises counts
  as the failure, and the run fails once the check passes. `todo`, `off`
  and `replaced` need a written reason; `replaced` names a test, which the
  kit finds by parsing the file, from the project root (the nearest
  pyproject.toml). One report lists every softened or replaced check.
- **The kit is tested itself.** For every check there is a sample indicator
  broken in just that way, and the check must fail on it
  (`tests/conformance/test_kit.py`). The roadmap's Phase 2 gate is one
  instance: "the conformance kit fails on a deliberately peeking indicator",
  run with its cache empty and filled by other checks.
- **Every kind registered through entry points is in the kit**
  (`tests/conformance/test_registered.py` reads the installed groups).
- **Plug-ins written outside this repository run the same kit**: install
  the `testkit` extra and write a three-line test with `cases` and `run`
  (the docstring of `heliograph.testkit`). Shared test builders for this
  repository's own tests stay in `tests/support`.

### Scenarios

These follow LEAN's regression algorithms: a whole backtest over committed
data, and what it delivered, decided and reported.

- `test_hand_checked.py` runs `tests/scenarios/hand-checked.toml`: six
  trades and a strategy simple enough to follow by hand. Its docstring
  works out every figure; the report must match them exactly. This is
  Phase 2's gate.
- `test_example.py` runs `examples/trend.toml` over the data
  `scripts/make_example_data.py` writes (`check.sh` checks the committed
  file is what the script writes). It records the count of each kind of
  journal entry, so a feed that delivers less shows as that, and the
  report.

Reports are compared as text with inline-snapshot, in the manner of Jane
Street's expect tests. A change in behaviour shows as a difference to
accept or reject; to accept one, run
`uv run pytest tests/scenarios --inline-snapshot=fix` and review the diff
it writes into the test (that run reports an error for each snapshot it
rewrote; the next run passes). A snapshot of the hand-checked report is accepted
only when the figures in its docstring say the same.

### Live tests

- They are marked `live` and excluded by default (`-m "not live"` in
  pytest's options; `uv run pytest -m live` overrides it).
- They are run on request and on a schedule against the exchange named in the
  mount.
- Offline tests use responses recorded to files.

## What fails and what skips

- **A missing dependency or service is a failure, not a skip.** Elysium's
  production read path went untested for weeks when `importorskip` met a
  Python without the wheel. A skip needs a reason that names a platform
  fact, such as "POSIX only".
- **Warnings are errors** (`filterwarnings = error`), markers are strict, and
  `xfail` is strict.
- **Tests run in random order** (pytest-randomly). The seed is printed, so a
  failure that depends on order can be reproduced with
  `--randomly-seed=<seed>`.
- **Every run is repeated in `TZ=America/St_Johns`.** That zone is behind UTC
  by a half-hour offset and observes daylight saving, so slipped dates,
  fixed offsets and whole-hour assumptions all show.
- **Nothing a test does outlives it.**
  - Files go in `tmp_path`.
  - CI fails if the working tree changed after the checks ran.
  - Module-scoped fixtures must not be changed by the tests that share them.
- **Tests never sleep or read the wall clock.** Time comes from a
  `SimulatedClock`. Concurrency is tested by forcing the interleaving with a
  stand-in that blocks at a chosen point, never by racing.

## Negative controls

Every test that guards a rule is broken once on purpose, and the break is
kept.

- **Recording.** The break goes in `scripts/check_controls.py` as a declared
  control:
  - what it breaks, in the words of the guarantee;
  - the file;
  - an exact string that must occur once;
  - its replacement;
  - the tests, named one by one, that must fail.

  The script applies each break, runs those tests, and expects every one to
  fail. It restores the file afterwards, on Ctrl-C and on SIGTERM too; after
  a harder kill, the next run restores it from a record kept on disk.
- **CI runs every control** as a separate job.
- **Commit messages report the result**, for example
  `Controls: late point added to its bar -> 1 fails`, and name what the
  control is checking for.
- **A control that fires for the wrong reason has proved nothing.** The
  script refuses a break that does not compile, and reports a control as
  WRONG when its tests fail only with a NameError or ImportError. It prints
  each control's failure kinds (`AssertionError`, `Failed: DID NOT RAISE`,
  a domain error); read them.

**Mutation testing** looks for the controls nobody thought of.
`scripts/mutation_test.py <module> <tests…>` runs mutmut in a scratch copy
of the repository. It deselects the structural tests, which reject mutmut's
injected imports by design. It lists every change no test noticed. A
survivor is a question, not a verdict: some changes mean the same either
way. Read each survivor and either write the test or record why not. It is
too slow for `check.sh`; run it when a module's tests matter or have been
rewritten.

## Coverage

Branch coverage is measured on every check (coverage.py), and
`scripts/coverage_floor.py` fails if any one source file is under 90%
(`[tool.heliograph.coverage]` in `pyproject.toml`). It is read per file, not
as a total: Elysium's 96% total hid a 63% file. A line
added and not covered has not been checked, whether or not it is correct. A
line that cannot be reached by any test is either made reachable through a
real path or deleted. It is never reached through a seam built for the
test.

## Shared builders

Helpers used by more than one test file live in `tests/support/`. They are
plain modules imported by name, never imported from a conftest (Elysium
found that two conftests on the path shadow each other). Extract a helper at
the second caller, not the first. A test meant to show the core depends on
no venue's names uses a made-up instrument, as Elysium's tests use an
Author and Book schema rather than its customers' tables.

## Commands

```bash
./scripts/check.sh                       # everything, including both time zones
uv run pytest tests/core/test_bars.py    # one file
uv run pytest -m live                    # live adapter tests, on request
uv run python scripts/check_controls.py  # every declared control
uv run python scripts/check_controls.py --only "late point"
uv run python scripts/mutation_test.py src/heliograph/core/bars.py tests/core/test_bars.py
```
