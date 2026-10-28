# Lessons

Things that surprised us, so they do not surprise us twice. Newest first
within each section.

## Process

- **`check.sh | grep … && git commit` committed a failing change** (2026-10-21):
  the `&&` tested grep's exit code, which succeeds on "FAILED". Gate commits
  on the check's own exit status.

- **GitHub can lag the owner's checkout.** After "committed and pushed",
  `origin/main` was still the old base on 2026-10-21. Confirm the owner's tree
  (see workflow.md, step 1) before basing a series on it; the apply script
  checks tree hashes for this reason.
- **Commit hashes differ between checkouts.** `git am` records a new committer
  and date (the script also rewrites the author), so a series cannot name its
  base by commit. The apply script uses tree hashes.
- **`git am` keeps the patch's author** unless told otherwise; that is why the
  apply script rewrites `From:`. Do not remove that step.
- **Negative controls with git commands destroyed work once.** `git checkout`
  on an untracked file failed and `git stash` stashed a different file. Back up
  with `cp` and restore with `cp`.
- **Changelog order goes wrong when commits are reordered.** Regenerating it
  per commit with `git filter-branch --tree-filter` and a sort script fixed it;
  better to add each line in its own commit in the right place from the start.
- **Session tooling may demand Claude as committer.** The owner forbids it;
  his instruction wins. Commits made locally are rewritten by the apply script
  anyway.

- **A test must not time what the process already promises.** The engine
  exits 0 only if it stopped within its own deadline, so a second clock in the
  test, started before the signal and stopped after the exit, also counted a
  loaded machine's delays and failed at 10.2 s of 10 in a thirty-minute run.
  Assert the exit status; give any outer bound slack.

## Python and libraries

- **Python trusts a cached `.pyc` by its source's modification second and
  size** (2026-10-22). Two controls shortened one file by exactly 12
  characters within one second, so the second ran the first one's bytecode
  and passed. Anything that rewrites source and re-imports it must drop the
  file's cache (`importlib.util.cache_from_source`).
- **pytest names a unittest-style test `name::runTest`**, and a parametrized
  one `name[param]`. Matching a failure by exact node id missed a Hypothesis
  state machine; match the name and anything under it.
- **A bare `@pytest.mark.skip` is an attribute, not a call.** A rule that
  inspected calls missed it; only its negative control showed that.
- **A mutation survivor is dead code or a missing test, and reasoning can
  mistake one for the other.** Three survivors in `TimeBars` looked
  unreachable; review found that one was reachable after all: a handler may
  publish a point stamped before a close already made, which re-opened the
  bar. Two were deleted, the third kept with a test. Before deleting, try to
  construct the input, and assume nothing the contract does not promise
  (here, that every point arrives in time order).

- **Replaying inside one process cannot catch hash-order nondeterminism**
  (2026-10-22). A handler publishing once per item of a set of strings
  replays equal in-process. Yet its journal digest differed in each of
  three processes with different `PYTHONHASHSEED`. Determinism needs a test
  across fresh interpreters.
- **mutmut injects an import into every mutated module,**
  `mutmut.mutation.trampoline`, so our core-purity test fails every mutant.
  Deselect the structural tests during mutation runs. The purity test was
  right.
- **A copied virtual environment runs the original's packages.** The
  scripts in `.venv/bin` keep absolute shebangs, so `uv run pytest` in a
  copy used the source tree's interpreter and missed newly installed
  plug-ins. Recreate `.venv` in a copy, or call `python -m pytest`.

- **An outside review found eight defects a full green suite missed**
  (Elysium team, 2026-10-21): frozen parameters that could not be serialised,
  a key collision, a wall clock that could step back, dropped nanoseconds,
  naive TOML times, wrong error types. Tests prove what we thought of; ask for
  review before building on a layer.
- **`datetime.fromisoformat` keeps six fractional digits and silently drops
  the rest.** Parse digits seven to nine ourselves.
- **Pydantic cannot serialise `MappingProxyType`,** and a frozen model hashes
  its fields, so a deep-frozen field needs a serializer and `__hash__`.
- **`time.time_ns()` is not monotonic;** NTP can step it back. A wall clock
  that feeds a look-ahead guard must hold still instead.
- **JavaScript numbers hold integers exactly only up to 2⁵³;** nanosecond
  timestamps cross JSON as ISO strings.
- **The non-UTC test run took 250 s against 35 s in UTC on the Elysium
  team's machine,** but 1.3 s in both here (2026-10-21). Not reproduced;
  watch the timing in CI.

- **PEP 495:** an aware datetime in an ambiguous local hour (a daylight-saving
  fall-back) compares unequal to every instant in another zone. Round-trip
  tests must compare in UTC (`moment.astimezone(UTC)`).
- **Pydantic `frozen=True` freezes the model, not a `dict` field.** Deep-freeze
  mappings into `MappingProxyType` and lists into tuples in an after-validator;
  pydantic accepts both as input.
- **Pydantic does not validate defaults unless `validate_default=True`;** a
  default outside its own bounds goes unnoticed otherwise.
- **`isinstance(None, T)`** raises `TypeError` for a protocol that is not
  runtime-checkable, which makes it a cheap up-front check in `subscribe`.
- **Generic arguments of a subclass** are in `__orig_bases__`; read them with
  `typing.get_origin` and `get_args` in `__init_subclass__`.
- **import-linter's forbidden contract only covers packages it is told about;**
  keeping third-party code out of the core needs an AST test against
  `sys.stdlib_module_names`, with relative imports resolved first.
- **A lazy-merge assumption was wrong:** predicting the exact interleaving of
  `heapq.merge` reads failed; testing the bound (read-ahead ≤ number of feeds)
  is what matters.
- **A TOML literal string (single quotes) keeps `\n` as two characters.**
  A control's `old` text with a newline needs a basic (double-quoted) or
  multi-line string.
- **A throttle asked to wait zero seconds spins for ever** on simulated
  time, and a control that broke the window boundary hung the suite. The
  simulated sleep now fails on a wait that is not positive, and
  `check_controls.py` reports a control whose tests hang.
- **pyarrow's `to_pydict()` turns a timestamp column into `datetime`s,**
  dropping nanoseconds (and overflowing on out-of-range values); convert
  timestamp columns with `pyarrow.compute.cast` to int64 first. A malformed
  Parquet file raises `ArrowInvalid`, a `ValueError`, not an `OSError`; a
  missing one raises `FileNotFoundError`.
- **`pa.Array[Any]` in an annotation fails at import** (pyarrow's classes
  are not subscriptable at run time, though `pyarrow-stubs` makes them
  generic for mypy); quote the annotation.
- **Hypothesis's default 200 ms deadline fails tests that do file I/O**
  when the machine is busy (a journal example took 271 ms once, 16 ms on
  the rerun). Set `deadline=None` on property tests that touch the disk.
- **A second review found nine defects in a green, fully controlled
  series** (2026-10-22): cancellation lost in a thread, a live bar refused
  for a few milliseconds of clock skew, a semaphore bound to one event loop,
  a real sleep paired with a simulated clock, numbers taken as dates, a
  crash that hid other problems, early exits that hid other problems, an
  encoding error without a location, overlapping bars. Controls prove the
  tests we wrote; review finds the ones we did not. Keep asking for it.
- **`asyncio.to_thread` cannot be cancelled:** the thread runs on. Closing a
  generator it is still running raises `ValueError: generator already
  executing`, which replaces the `CancelledError`. Shield the read and close
  the generator when the thread finishes.
- **An `asyncio.Semaphore` binds to the first loop that waits on it,** and
  later raises in any other. Anything that outlives a loop (a source and its
  throttle across `SourceLoop`s) must make its primitives per loop.
- **pydantic's lax `datetime` takes integers and numeric strings,** guessing
  seconds or milliseconds. `Field(strict=True)` takes only a `datetime` from
  Python (TOML) and ISO text from JSON.
- **`pkill -f pattern` matches the shell running it** when the pattern is in
  the command line, and kills it. Write the pattern as `[s]ignal\.pause`.
- **Kraken counts a page of trades from the whole second, then CCXT drops
  what precedes `since`** (`filter_by_since_limit`), so a page can be short
  and still leave trades behind; how full a page is says nothing. A page
  repeats the trades at the last instant delivered: drop those copy by copy.
  When nothing new comes, ask for the newest trade, and fail if it is later.
  CCXT also gives each page's last Kraken trade an id and the others none,
  so ids cannot tell a repeat from a new trade.
- **A fake must answer as the real library does, not as its documentation
  suggests.** Our first fake sent back the whole second unfiltered; every
  paging test passed while real CCXT would have lost or doubled trades. Read
  the library's code for the path the fake stands in for.
- **Kraken keeps only the latest 720 candles** and answers an older start
  with those, silently. A first candle a bar or more after the start is the
  only sign.
- **CCXT triggers an aiohttp `DeprecationWarning` on Python 3.13**
  (`enable_cleanup_closed`), which "warnings are errors" turns into a
  failure. Ignore that message by name, never the category.
- **The TOML literal-string trap struck twice more** in one batch. The
  controls script catches it (a break that matches nothing is refused);
  write every control with basic (double-quoted) strings.
- **Python 3.12's built-in `sum` is compensated for floats** (Neumaier):
  `sum([1e16, 1.0, -1e16])` is 1.0. A control meant to show what `fsum`
  saves had to break to a naive left-to-right loop instead; the control
  that did not fire said so.
- **Equality cutoffs suit formulas, not series.** The planned graph would
  have dropped a close equal to the last and averaged the wrong bars; a
  test of three equal closes caught the plan before the code (D27).
- **`str.replace` replaces every occurrence.** A test that edited a
  configuration's `[[streams]]` defined an indicator twice, and passed for
  the wrong reason; a control that did not fire found it. Pass a count.
- **A check that compares runs only catches what differs between them.** The
  first no-peeking check compared five runs in one process; a cache filled
  once, by any earlier check, made all five agree, and the result depended
  on test order. An independent review found it with pytest-randomly seeds.
  Checks of hidden state run in a fresh interpreter.
- **Test what the caller really does.** The gaps check fed None to every
  input at once, which the graph never does, and missed the case it does
  (one input sampled, another not).
- **Compute reference values; never type them.** The first draft of the
  TA-Lib references held plausible numbers written from memory, and three
  hand-worked talipp values were wrong too; the kit's reference check
  caught every one. Generate literals from the source the reference names,
  and check hand-worked ones against the implementation before committing.
- **A non-greedy regex can still span a file.** Replacing each reference's
  list with `\[\n(.*?)\],\n "RSI",` matched from the first list to the
  last name and deleted every class between. Mark each place to fill with
  a unique token and replace the token.
- **Libraries differ from textbooks at the edges.** talipp's Hull average
  drops its first sqrt(n) - 1 differences, so starts later than the
  definition allows; where both give a value they agree. Write the
  library's convention into the reference and say why.
- **`git checkout` cannot restore a file git does not track yet.** Checking
  two controls by hand on a new module, the second break landed on top of
  the first, which `git checkout -q ... || true` had silently failed to
  undo. Check controls with `scripts/check_controls.py`, which restores from
  its own record, or copy the file aside first.
- **A test whose case never exercises the guard passes without it.** The
  first "decides only at price closes" test put the strategy on the faster
  stream, so every instant was a price close; the "waits for every gate"
  test never had a gate warming while the forecasts were ready. The
  declared controls found both.
- **A risk check must never block taking risk off.** The first `limits`
  gate refused a stop-out because the price had jumped, and a move to flat
  because the day's signals were spent: the very cases it exists for. Risk
  gates now see only proposals that add risk. An independent review found
  it, with six other defects in the same batch.
- **`sum()` turns -0.0 into 0.0** (it starts from the integer 0), so a test
  feeding -0.0 through a summing stand-in never produced one; the declared
  control said so.
- **Check a published default before copying a design from memory.**
  Carver's buffer trades to its nearest edge (`buffer_trade_to_edge: True`
  in pysystemtrade's defaults.yaml), not to the optimal position.
- **Run the controls with `uv run python`.** `check_controls.py` runs pytest
  with its own interpreter; started with the system `python3`, which lacks
  pytest, every control reports silent. A wall of silent controls means the
  wrong interpreter before it means weak tests.
- **Grace moves every stamp of a stream, signals included.** The first
  broker filled at the first bar starting at or after the signal's instant;
  with a grace period the signal is stamped after the next bar has started,
  so nothing was ever filled and the strategy asked again at every close.
  Every test fed bars with no grace. An independent review found it.
- **A test's decoy can be caught by another guard.** The broker's
  "another size" bar first started before the signal, so the start filter
  refused it and removing the size filter changed nothing; its control said
  so.
- **A read-only SQLite reader of a WAL database leaves `-wal` and `-shm`
  files** it is not allowed to remove. A journal is now switched back to
  rollback mode as it is closed. Found by running the example by hand, as a
  person would, and listing the folder afterwards. The first fix made
  closing fail whenever another program had the journal open (the switch
  needs the only connection); it is now only tidying, and failure to tidy
  is ignored. An independent review found that, with five other defects in
  the command: a failed run reported as if complete, a broken plug-in of
  an unused kind stopping every run, `?` in a journal's name read as a
  URI's query, a file that is not a journal giving a traceback, and two
  runs in one second wanting the same journal.
- **A fallback timed from a bar's end must add the source's delay.** The
  first `BarsFromBars` closed a bar one finer bar after its end, which is
  when the next part is known only if parts come at once; with a source
  whose data is known a minute late, every hour lost its last minute,
  silently. Its tests had no delay. Review also found parts off their grid
  taken into the wrong bar, and two bars of one stream at one instant
  stopping the graph.
- **Review everything, not only the last batch.** A review of the whole
  project (four reviewers, one per part, each proving its findings) found
  defects no batch review had: a refused reversal kept a losing long with
  its stop disarmed, volatility with no floor sized a stalled market at
  241 times capital, `ewmac` blew up on a stall, many CCXT exchanges ignore
  `since` for trades, and a short re-download erased stored data. Each
  batch's review saw only its own diff.
- **A time limit in a test is a guess about the slowest computer it runs
  on.** The control script's test of a hung control gave the inner pytest 3
  seconds to start and write a pid; on the owner's computer, once
  inline-snapshot loaded at every start, it did not, and D5 could not be
  pushed. The inner run now loads no plug-ins and has 10 seconds.
- **"It stops at its next input" is only as prompt as the next input.** The
  engine's stop test sent its signal once the backtest was marked running;
  on the owner's computer the run was still reading its 300,000-row file,
  its first input came four seconds later, and the test failed (here the
  file loads before the signal lands). It now waits for entries in the
  journal, so inputs are flowing when the signal comes.
- **`uv run` prints a `UV_NATIVE_TLS` deprecation notice** in Claude's cloud
  workspace (an environment variable there, not the project); filter it when
  scanning output.

## The window

- **NiceGUI's simulated browser removes modules from `sys.modules` on exit**:
  the module of every page function it served, and its parents (so
  `heliograph` itself), unless the function's module starts with `tests.`.
  A test that registers a page with `create()` must clear
  `Client.page_routes` before the simulation ends, or every later test
  meets a second copy of Heliograph and its classes. Tests that pass a
  `root` function to the simulation register no page and are safe.
- **NiceGUI answers a page as soon as the browser has connected**, while a
  page function that asks the engine is still being built (and gives up on
  one that takes 3 s: pages here allow 30). A test that opens a page must wait
  for the builder, not for time, or it plays on a half-built screen. The
  simulation's `root` cannot set that limit, so `window.scenario` returns from
  the builder at once and builds the screen in a task: on a slow machine
  (a suite of thirty minutes) a builder that asked the engine overran the 3 s
  and six tests got a 500.
- **pytest collects a class whose name starts with `Test`** (`Tester` too),
  and warns that it cannot: name a screen class `StrategyTester`. A function
  imported into a test module by a name pytest takes for a test is collected
  as well: import the module and call it by its path.
- **Two `set_value` calls in a row fire both handlers before either runs**, so
  a handler that reads the widget's value sees only the last: to test a choice
  overtaken by another, let the first reach the engine (a gate the fake engine
  holds, `tests/support/window.py`), then make the second.
- **A handler NiceGUI calls with an argument but written without one fails
  quietly** (the error goes to the app's exception handler, not to the
  test). A change of signature needs a test that reaches the handler.
- **A second library's `mjs` may import a third.** Lightweight Charts'
  `production.mjs` imports `fancy-canvas` by name; the `standalone` build
  bundles it, and is the one that loads in a NiceGUI ESM folder.
- **Headless Chromium here reports `Invalid language tag: en-US@posix`**
  (the workspace's locale) unless the page is opened with `locale="en-US"`.

- **Use the window before trusting its tests.** The simulated browser said
  all was well while a real one showed: an empty Workspace that was just
  blank; a "chart it" link for a run that had nothing to chart; an error
  shown at the top of a page that is pages long, out of sight when the Run
  button at its bottom was pressed; a dark switch the colour of its header
  (invisible); controls that vanish below 1,024 px (Quasar folds a drawer
  there, and no button opens it); an empty name sent to the engine instead of
  its default; a JSON box that took a list where the schema says object.
  Walk each screen in a browser (`tests/` cannot see layout), with an empty
  engine, a failed run, a long form, a narrow window, the engine stopped.
  Put a message beside the control that caused it.
- **A form below a picture hides the picture.** The Wizard's first layout put a
  plug-in's settings under the graph; choosing a node scrolled the page to the
  form and the graph, the thing being edited, went out of sight. Put what
  follows a choice beside what was chosen. A view that fits its nodes once
  also loses the new one: fit again when the count changes, not on every
  change, which would undo the person's own zoom.
- **Point a browser test's engine at a scratch copy of `examples/`.** Walking
  the Wizard's Keep button wrote a configuration into the repository's own
  `examples/`. Use `--configs` on a copy.
- **Run `licence_report.py` with `uv run`.** With the system Python it finds
  no package metadata and writes "UNKNOWN" over the whole table.
- **Try a heuristic on the real data in the browser, not only on the test's.**
  "Share the price's scale if the line is within a quarter of the candles'
  range of them" passed its tests (range 4) and let an ATR of 174 through in
  the first real run, where the range was 70,000 and the margin reached below
  zero. A margin is of the level, not of the spread.

## Trading platforms

- MQL5's signal average counts silent modules, so adding a filter quietly
  raises the bar to trade.
- NinjaTrader processes the primary series first on history, so a
  higher-timeframe bar ending at the same time arrives one bar late.
- Freqtrade's `merge_informative_pair` docstring disagrees with its code; the
  code is right.
- TradingView's `lookahead_on` without a `[1]` offset leaks the future on
  historical bars only, so history and live disagree.
