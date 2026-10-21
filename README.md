# Heliograph

Heliograph is a desktop application that generates trading signals for a
person to act on by hand. It backtests and optimizes the strategies behind
those signals, and paper-trades them on live data. Every concrete part — data
source, bar size, filter, strategy, chart tool, notifier, broker — is a
plug-in behind a contract, so parts can be added, swapped or removed without
touching the core.

Heliograph never places real-money orders.

## Status

Phase 0 (foundations) is done; Phase 1 (data) is next. See
[docs/roadmap.md](docs/roadmap.md) and [CHANGELOG.md](CHANGELOG.md).

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
src/heliograph/
  core/      contracts, messages, time, clocks, bus, engine (standard library only)
  plugins/   plug-in manifests, parameter schemas and the registry
  app/       run configuration and the composition root
tests/       the test suite, mirroring the package
scripts/     developer scripts
docs/        design, roadmap, decisions, research and lessons
integrations/  drafts for hosting Heliograph elsewhere (Elysium)
```

Dependencies point inward only: `app` may import `plugins` and `core`,
`plugins` may import `core`, and `core` imports nothing but the standard
library. `./scripts/check.sh` enforces both rules.

## Working on Heliograph

- One logical change per commit, with a
  [Conventional Commits](https://www.conventionalcommits.org/) subject such as
  `feat(data): add the CSV source`.
- `main` always passes `./scripts/check.sh`.
- Changes arrive as `git format-patch` series and are applied with
  `git am --3way`; see [docs/workflow.md](docs/workflow.md).
- The documentation in [docs/](docs/README.md) is the project's memory: read
  the roadmap before starting work and update it when an item lands.

## License

Proprietary. All rights reserved. See [LICENSE](LICENSE).
