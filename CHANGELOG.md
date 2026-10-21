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
