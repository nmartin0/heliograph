# Changelog

All notable changes to Heliograph are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Line-ending rules and ignore list.
- Python package skeleton in a `src/` layout, built with hatchling and
  managed with uv, with a locked set of development tools.
- `scripts/check.sh`: one entry point for lint, format, strict types and
  tests, including a run in a non-UTC time zone.
- Continuous integration on GitHub Actions running the same checks.
- Core time: UTC instants as integer nanoseconds, with conversion from and to
  aware datetimes and ISO 8601 text; naive datetimes are refused.
- Core messages: `Instrument` and `Bar`, immutable and self-validating; a bar
  becomes known when its interval closes.
- Clocks: a forward-only simulated clock for backtests and a wall clock for
  live data. A lint rule bans reading the system time anywhere else.
- Message bus: typed publish and subscribe with breadth-first delivery; it
  refuses any message stamped later than the clock's current time.
- Backtest engine: merges time-ordered feeds and publishes each message at
  its own instant, proven by a property test that no component sees the
  future.
- Plug-in manifests and parameter schemas: each plug-in declares its kind,
  name, version, summary and a Pydantic parameter model whose JSON schema
  carries defaults, bounds, search steps and help text.
- First runtime dependency, pydantic, and `THIRD_PARTY_NOTICES.md` listing
  every runtime component and its license.
- Plug-in registry: plug-ins are discovered through `heliograph.<kind>` entry
  points; unknown names fail with a list of the valid ones.
- Run configuration and composition root: a TOML file names the plug-ins and
  their parameters, is validated as a whole and fingerprinted with SHA-256;
  every problem is reported at once and a bad configuration builds nothing.
- Layering checks: import-linter keeps dependencies pointing inward
  (app, then plugins, then core), and a test keeps third-party packages out of
  the core.
- One error hierarchy: every error Heliograph raises derives from
  `HeliographError` and also from the builtin it refines, such as
  `ValueError`, so callers can catch either.
- `scripts/make_apply_script.py`: packages a range of commits as a bash
  script that checks the target repository's files before and after applying
  them, makes the person running it the author, and runs the checks.
- Project documentation in `docs/`: design, roadmap, decisions, licence
  policy, principles, workflow, lessons and research notes, with
  `CLAUDE.md` as the entry point for AI-assisted sessions.

### Fixed

- `from_iso` keeps fractions of a second to the nanosecond instead of
  silently dropping digits past the microsecond, and refuses finer ones;
  `to_datetime` raises `InvalidValueError` for instants outside the years 1
  to 9999 instead of a bare `OverflowError`.
