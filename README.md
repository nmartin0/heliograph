# Heliograph

Heliograph is a desktop application that generates trading signals for a
person to act on by hand. It backtests and optimizes the strategies behind
those signals, and paper-trades them on live data. Every concrete part — data
source, bar size, filter, strategy, chart tool, notifier, broker — is a
plug-in behind a contract, so parts can be added, swapped or removed without
touching the core.

Heliograph never places real-money orders.

## Status

Phase 0 (foundations) is in progress. See [CHANGELOG.md](CHANGELOG.md).

## Requirements

- Linux, macOS or Windows
- Python 3.12 or newer (3.13 is pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/) for environments and dependencies

## Getting started

```bash
uv sync               # create .venv and install the locked dependencies
./scripts/check.sh    # lint, format, types and tests
```

## Project layout

```
src/heliograph/   the package
tests/            the test suite
scripts/          developer scripts
```

## Working on Heliograph

- One logical change per commit, with a
  [Conventional Commits](https://www.conventionalcommits.org/) subject such as
  `feat(data): add the CSV source`.
- `main` always passes `./scripts/check.sh`.
- Changes arrive as `git format-patch` series and are applied with
  `git am --3way`.

## License

Proprietary. All rights reserved. See [LICENSE](LICENSE).
