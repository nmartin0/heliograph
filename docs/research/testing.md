# Testing: Elysium's practice, trading engines, tools

Researched 2026-10-22. Elysium read at `dev` `8922134`; library versions and
licences from PyPI on the same day.

**Bottom line.** Elysium's suite is large (about 6,800 Python tests and 137
frontend test files), but what it teaches is not its size. It treats a test
as unproven until a deliberate break has made it fail. It keeps those breaks
in a script that CI runs. It tests the suite itself with mechanical rules.
It refuses to let a missing dependency turn into a silent skip. Its agreed
design for shared behaviour is a conformance suite that every module
inherits, with a written reason for each check softened. We adopt all five.
From the trading engines we take two more things: LEAN's
regression backtests with recorded statistics, and Jane Street's expect tests.
Our own suite was measured too (section 4). It is order-independent and at
98% branch coverage. But mutation testing finds 65 changes it does not notice,
and in-process replay cannot see one class of nondeterminism. The framework
that follows is in [../testing.md](../testing.md).

## 1. How Elysium tests

### Where the rules live

- `RULES.md` §3, "Real tests, with real negative controls": "A test that
  passes when the feature is removed tests nothing." The control "is how you
  find out whether you wrote a test or a decoration".
- `PRINCIPLES.md` §2, "Real tests, not shallow ones". Every test drives real
  interfaces (React Testing Library typing and clicking, FastAPI's
  `TestClient` over real HTTP), "never mocking so deep that the test just
  checks its own mock was called". §3 says jsdom cannot see CSS, so anything
  visual is checked in a real browser.
- `AGENTS.md`, "Verification that actually verifies" and "A mocked callback
  tests the component and skips the wire".
- `CLAUDE.md`, "The loop, every change": type-check, then the narrow test,
  then widen. Write the test, then break the code. Run the full checks before
  writing the commit message. Verify every claim the message makes.

### Negative controls, and how they are kept honest

The rule. State the property in one sentence, then name the change to the
code that would make it false. Break the code, watch the test fail, then
restore it. Restore from a copy, never by hand.

The failure the rule targets is specific. A test written beside its code
"tends to assert the code's SHAPE rather than its PROPERTY". `AGENTS.md`
lists hollow tests that only a control exposed:

- a concurrency test that passed against a racy lock;
- a batching test with a two-row fixture;
- a pagination test that passed because SQLite happened to return key order;
- an exception "escaping a DAEMON thread", which prints a warning instead of
  failing the test;
- an audit test whose fixture had two generations at the same number.

Three refinements:

- **The control must fail for the right reason, and the right tests must
  fail.** "A control that fails everything is usually broken setup". The
  commit message names the break and the count, for example
  "cap truncates silently -> 1 fail".
- **Test the other direction too.** A guard that must not fire needs a pair
  showing the legitimate case still passes.
- **When the change is about cost, not behaviour, use a tripwire.** Pin the
  mechanism at source level and say in the test why a behavioural check
  cannot work. But "a test asserting a WORD appears in source is not a test":
  deleting the behaviour and leaving the word in a comment satisfied two of
  them.

**`scripts/check_controls.py` holds the breaks.** The reason is in its
docstring. Every commit claimed its controls fired, and when somebody counted,
"NINE controls across the project had failed to fire". Each control is a
`Control(describes, path, old, new, tests)`:

- `old` must occur exactly once in the file, because "a break that matches
  nothing runs the UNMODIFIED code and passes";
- the tests are named one by one, so a control that fires for an unrelated
  reason shows up as the wrong test failing;
- the original file is saved to disk before the break, so a killed run is
  restored next time.

There are 725 controls today. They had existed unused until commit `5289c10`
added a `controls` job to CI ("Seventy declared controls existed and
NOTHING INVOKED THEM"). It is a separate job because each control takes 2 to
18 seconds. Commit messages end with a line such as
"Controls: 6 new, all firing. Measured: 4,976 unit; 226 integration".

**Mutation testing complements the controls rather than replacing them**
(`scripts/mutation_test.py`, mutmut). A control names a guarantee someone
chose; mutmut tries every small change and lists the ones no test noticed.
"A survivor is a question, not a verdict." The wrapper runs mutmut in a
scratch copy of the repository, one module and its own tests at a time,
with forkserver isolation. A per-process cache had made the second pass fail
otherwise. Its first run found a sync mark that could have moved backwards
and passed all 45 tests for that module.

### The suite is tested too

- `sim/tests/test_suite_hygiene.py` checks, from the AST:
  - no test changes a module-scoped world that other tests share;
  - no test asserts `all(...)` over something without first showing it is
    non-empty ("all() over an empty sequence is True");
  - comments are not shouted.

  Each rule also asserts it found something to check, so it cannot pass on
  an empty directory ("A rule that checks nothing would pass forever").
- `tests/unit/test_lint_script.py` checks that every step of `lint.sh` sets
  the variable the script exits with. One step had set `FAILED=1`, which
  nothing read, and drift printed a warning above "All checks passed."
- `sim/tests/consumer/` is an outside-in client. It starts the simulator as
  a subprocess "through the same command line a person would type".
  `test_outside_in.py` fails if any file in it imports the package, because
  "reaching into the package makes them test the simulator against itself".
- CI fails if the suite created any file in `deployment/`.
- `test_importing_the_app_builds_nothing.py` runs in a subprocess, because
  "a module is imported once per process, so only a fresh interpreter can
  show what importing does".

### Environment and dependencies

- **Real servers instead of mocks for anything that matters.**
  `tests/postgres_server.py` starts a throwaway PostgreSQL cluster, and if
  it cannot, the test fails. When the gating machine moved to Python 3.13,
  `pytest.importorskip` had turned a missing wheel into a skip, and the
  production read path went untested while the suite stayed green. The
  owner's ruling: "a suite that cannot test the production read path must
  say so in red."
- **A time zone that is not UTC.** "A timezone test in a UTC container
  cannot see a timezone bug." Use one behind UTC so date slippage shows, and
  one with daylight saving so a fixed-offset mistake shows. The frontend
  pins `TZ: 'America/New_York'` in `vite.config.ts`, after two controls
  failed to fire in UTC.
- **Forced interleavings, not races.** "Racing N threads at a barrier is
  not a test". Commit `69729b4` (FLAKE-1) replaced a race with an adapter
  subclass that commits a write after the first batch, asserted.
- **Shared builders live in a module, not in conftest.** `sim/tests/worlds.py`
  says importing from conftest "is a pytest anti-pattern": two conftests on
  the path shadow each other. Extract "at the second caller, not the first".
- **A test schema that is not the product's** (Author and Book, not Customer
  and Transaction), to prove the core is generic.
- **Coverage per file touched, not the total.** 96% overall hid a 63% file.
- **Say when something cannot be tested.** Write down why where the test
  would have been, rather than shipping one that passes vacuously.
- **The person running the product finds what the suite cannot.**
  `CLAUDE.md`: "Those pastes have caught more real bugs than the test suite
  has". Playwright tests in `ui/e2e/` cover what jsdom cannot see.

### The design agreed on 21 October: common behaviour tested once

`BLOCKING.md` item 3, commit `fcb18eb`. Elysium first counted what its
seven sub-apps tested, and found gaps more than duplication: three tested an
expired session, two a loading state. So "the baseline is INHERITED, never
copied", in three layers:

1. **A config preset** that each module's own test config extends.
2. **A conformance suite** generated from each `module.json` and run on
   every page, on by default (after Storybook, where "a basic story is also a
   smoke test").
3. **The module's own tests.**

Overrides:

- Each check is `error`, `todo` or `off`, and the narrowest setting wins.
  `todo` and `off` need a written reason.
- A module may supply a check's inputs (RSpec's `it_behaves_like` with a
  block). Or it may replace a check with a named, stronger test, which the
  suite confirms exists.
- One report lists every softened check.

The suite itself is tested by "a deliberately broken sample sub-app" that
must fail every check. Existing modules adopt checks as `todo` first; new
ones start at `error`. Add-on servers get the same shape, as a pytest plugin
of the HTTP contract that runs in their own repository.

## 2. How trading engines test

- **NautilusTrader** ([quality page](https://nautilustrader.io/quality/)):
  - unit, integration and acceptance tests, the last with "deterministic
    reruns confirm reproducibility across runs";
  - property tests in Rust (proptest) over order books, timers and retry
    logic;
  - benchmarks of matching, bus throughput and serialisation;
  - leak tests across the Python–Rust boundary;
  - deterministic simulation (madsim, turmoil), in which a divergence is
    saved "as a replayable JSON artifact";
  - more than 40,000 tests in all.
- **QuantConnect LEAN.** Regression algorithms implement
  `IRegressionAlgorithmDefinition`, which records:
  - `DataPoints` (3,943 for `BasicTemplateAlgorithm`);
  - `AlgorithmHistoryDataPoints`;
  - the final `AlgorithmStatus`;
  - `ExpectedStatistics`, a dictionary such as "Total Orders": "1",
    "Net Profit": "1.692%", "Sharpe Ratio": "8.854".

  The test runner backtests each algorithm and compares
  ([BasicTemplateAlgorithm.cs](https://github.com/QuantConnect/Lean/blob/master/Algorithm.CSharp/BasicTemplateAlgorithm.cs);
  [forum](https://www.quantconnect.com/forum/discussion/113/unit-testing-algorithms/):
  "50+ algorithms under regression in Python & C#"). Counting data points
  catches a feed that silently delivers less, which the statistics alone
  might not.
- **Jane Street's expect tests**
  ([blog](https://blog.janestreet.com/the-joy-of-expect-tests/)). A test
  prints state, and the tool writes the output into the test, where a person
  reviews it. "In expect tests, regressions are just diffs." Classical
  assertions "still have their place—just a much smaller one". Quickcheck adds
  random inputs where needed. The craft is "concise output with helper
  functions and custom pretty-printers". inline-snapshot is the Python
  equivalent.
- **Hypothesis stateful testing**
  ([docs](https://hypothesis.readthedocs.io/en/latest/stateful.html)). A
  `RuleBasedStateMachine` chains `@rule` steps, checks `@invariant`s after
  each one, and compares the real system against "a simplified in-memory
  model". This fits our engine, alarm queue and bars, each of which has an
  obvious slow reference.

## 3. Tools, checked 2026-10-22

All of these are development dependencies only; none ships with Heliograph.

| Tool | Version | Licence | Use | Decision |
| --- | --- | --- | --- | --- |
| pytest | 9.1 | MIT | runner | have |
| hypothesis | 6.170.5 | MPL-2.0 (file-level copyleft, used unmodified) | properties, state machines | have |
| pytest-randomly | 5.0.0 (2026-09-01) | MIT | random test order, reseeds `random` per test, prints the seed | adopt |
| coverage | 7.16.2 | Apache-2.0 | branch coverage per file | adopt |
| mutmut | 3.8.0 (2026-09-12) | BSD-3-Clause | mutation testing, on demand | adopt (on demand, not in `check.sh`) |
| inline-snapshot | 0.36.1 (2026-10-12) | MIT | expect tests for reports and rendered journals | when the first report exists (Phase 2) |
| pytest-timeout | 2.4.0 | MIT | fail a hung test | with the asyncio bridge (batch C) |
| syrupy | 6.1.1 | MIT | snapshots in files | not needed; inline-snapshot's `external()` covers it |
| pytest-xdist | 3.8.0 | MIT | parallel runs | not needed at 4 s |
| pytest-benchmark | 5.3.0 | BSD-2-Clause | timing | when a cost needs pinning |
| cosmic-ray | 8.7.0 | MIT | mutation testing | rejected: heavier (SQLAlchemy, aiohttp, a database of results) |
| pytest-asyncio | 1.4.0 | Apache-2.0 | async tests | rejected for now: `asyncio.run` in a test is enough |
| time-machine, freezegun | — | MIT | patch the clock | rejected: only a `Clock` reads time, so tests pass a `SimulatedClock` |

## 4. Our suite, measured 2026-10-22

At `72a1bd0`: 324 tests, all run in 3.5–7.6 s.

- **Branch coverage 98%** (964 statements, 216 branches). Ten lines are
  missed, among them:
  - the carried bar when no close exists yet (`bars.py:117`);
  - an alarm queue returning nothing (`alarms.py:157`);
  - the journal refusing an unencodable value (`sqlite_journal.py:56`);
  - the journal being closed twice (`sqlite_journal.py:125`);
  - an `OSError` while creating the journal file (`sqlite_journal.py:90`).
- **Order-independent.** Eight random orders (pytest-randomly seeds 1 to 8)
  all passed.
- **Deterministic across processes today.** The journal test session, given
  300 random points and run in three processes with `PYTHONHASHSEED` 0, 1
  and 12345 under `TZ=Asia/Kathmandu`, wrote 592 identical rows.
- **But in-process replay cannot see hash-order nondeterminism.** A handler
  that publishes once for each name in a set of strings replays equal in
  every process. Its journal digests still differed between the three
  processes (`f2e12fd24ec3`, `32278ff07656`, `ca672179ca6f`). The Phase 1
  replay gate runs inside one process, so it would pass such a handler.
  Only a cross-process comparison catches it.
- **The purity test rejects mutmut.** mutmut injects
  `mutmut.mutation.trampoline` into each mutated module, and
  `test_the_core_imports_only_the_standard_library_and_itself` fails for
  every mutant. Mutation runs must deselect the structural tests (core
  purity, headers).
- **Mutation testing over seven core modules** (bars, bus, engine, alarms,
  streams, journal, time). There were 668 mutants: 592 killed, 11 timed out
  and 65 survived (1.34 mutants a second; about 25 minutes with the timeouts).
  - **Most survivors are in the engine:** 13 in `_advance`, 9 in `run` and
    8 in `__init__`. Most of the rest are in the alarm queue's `_push`, the
    bus's `publish` and the time-span parsing.
  - **About half need no test.** Some only change an error message's text,
    or write `None` where `False` means the same. Others change the
    alarm-queue compaction threshold, which is a cost and not a behaviour.
  - **The rest are real gaps:**
    - the late-point count set to 1 instead of incremented, because no
      test has two late points;
    - bars with `volume=None` given volume 1.0 instead of 0.0;
    - the loop limit of 10,000 alarms per instant changed from `>` to
      `>=`, or counted two at a time;
    - an alarm due exactly at the settling instant treated as later
      (`<=` to `<`);
    - `received_at` dropped for a bar closed exactly at its end;
    - the closing loop stopping at a missing bar instead of skipping it;
    - a span of exactly 2⁶³ ns accepted.

  Coverage reached every one of these lines; the tests did not check what
  they did. Elysium's first mutation run found the same kind of thing.
- **Duplicated test builders.**
  - The same `Instrument` constant is defined in five files.
  - A `build()` helper (clock, bus, engine) appears in four files.
  - A `ticks()` helper appears in three files.

  Elysium's rule ("extract at the second caller") says this is due.
