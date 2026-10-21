# Lessons

Things that surprised us, so they do not surprise us twice. Newest first
within each section.

## Process

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

## Python and libraries

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
- **`uv run` prints a `UV_NATIVE_TLS` deprecation notice** in Claude's cloud
  workspace (an environment variable there, not the project); filter it when
  scanning output.

## Trading platforms

- MQL5's signal average counts silent modules, so adding a filter quietly
  raises the bar to trade.
- NinjaTrader processes the primary series first on history, so a
  higher-timeframe bar ending at the same time arrives one bar late.
- Freqtrade's `merge_informative_pair` docstring disagrees with its code; the
  code is right.
- TradingView's `lookahead_on` without a `[1]` offset leaks the future on
  historical bars only, so history and live disagree.
