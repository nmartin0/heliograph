# Principles

Ten rules decide design questions. When two conflict, the earlier one wins.

1. **Correctness over speed.** A fast backtest that leaks future data is
   worthless. Leakage is made structurally impossible, not avoided by care.
2. **Nothing concrete in the core.** The core owns contracts (ports) and
   messages and imports only the standard library. It never imports a data
   vendor, chart library, broker or notifier.
3. **Everything else is a plug-in,** found through the registry, configured
   through its declared parameters, and swapped by changing configuration.
4. **Strategies declare, the engine decides.** A strategy says which way and
   how strongly. Sizing, exits, risk and delivery are separate parts it never
   calls.
5. **One code path.** Backtest, paper and live differ only in the clock and the
   adapters plugged in. Strategy code is identical in all three.
6. **Causal by construction.** Everything is fed event by event and sees only
   the past. A value, once emitted, never changes when later data arrives.
7. **Data is time-stamped points, not bars.** Bar size is a resampler applied
   on the way to whoever needs it, never a property of the system.
8. **Honest results.** Pessimistic costs by default, every optimisation trial
   recorded, deflated Sharpe and overfitting probability beside every winner.
9. **Reuse, but own our code.** Use existing libraries rather than reinvent
   them; keep our own tree free of copyleft code (see
   [licensing.md](licensing.md)). Wrap each library behind our own interface.
10. **Small and explicit.** Standard library first. One source of truth per
    fact. Fail loudly on anything unknown.

## Engineering standards

**Python.** 3.12 or newer; 3.13 pinned. `src/` layout, hatchling build, uv with
a committed lockfile.

**Types and objects**

- Ports are `typing.Protocol`s owned by the layer that uses them. An abstract
  base class sits beside a port only when implementations share real logic.
- Messages and value objects are `@dataclass(frozen=True, slots=True,
  kw_only=True)` and validate themselves in `__post_init__`.
- Anything crossing a boundary (configuration, files, GUI, network) is a
  pydantic model validated once at the edge. Pydantic never enters the core.
- Composition over inheritance; inherit at most one level from our own bases.
  Exception hierarchies are exempt.
- Dependencies arrive through constructors, assembled in one composition root.
  No globals, singletons or service locators.
- Make illegal states unrepresentable: a lifecycle is a union of types, matched
  exhaustively with `typing.assert_never`, not a status string.
- Pure functions for calculations; classes for state and lifecycle.

**Errors.** Every error derives from `HeliographError` and from the builtin it
refines (`InvalidValueError(HeliographError, ValueError)`), so callers can catch
either. Messages name the offending value and what was expected. Configuration
errors are collected and reported together.

**Time.** Timestamps are `int` nanoseconds since the Unix epoch, UTC
(`heliograph.core.time.Timestamp`). Only a `Clock` reads the current time; ruff
rule TID251 bans `time.time`, `datetime.now` and friends elsewhere. Display
time zones belong to the GUI.

**Layers.** `heliograph.app` → `heliograph.plugins` → `heliograph.core`, never
the other way. import-linter enforces the direction and a test parses every core
module to keep third-party imports out.

**Tests**

- pytest with `filterwarnings = error`, strict markers and strict xfail.
- Hypothesis for properties, such as "no output at time t changes when data
  after t changes".
- Test names describe behaviour: `test_a_naive_datetime_is_refused`.
- Every test run is repeated with `TZ=America/St_Johns` (a half-hour offset).
- **Negative control:** a test that guards a rule is checked once by breaking
  the code and watching it fail. Record that it was done in the commit message.
- Snapshot tests (inline-snapshot) for readable outputs such as logs and
  reports, once those exist.

**Comments and docs.** Docstrings say what and why, not how. No essay-length
comments; explanations that need room go in `docs/`.

**Commits.** Conventional Commits, one logical change each, every commit green.
See [workflow.md](workflow.md).
