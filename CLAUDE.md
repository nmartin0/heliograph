# Working on Heliograph

Heliograph is a proprietary, single-user trading **signal provider**: it
backtests strategies, paper-trades them on live data and tells a person what to
trade. It never places real-money orders. Everything concrete is a plug-in
behind a port owned by a standard-library-only core.

## Read first

`docs/` is the project's long-term memory. Start every session with:

1. [`docs/roadmap.md`](docs/roadmap.md): where we are and what comes next.
2. [`docs/workflow.md`](docs/workflow.md): how changes reach the repository.
3. [`docs/principles.md`](docs/principles.md): the rules every change follows.

Then, as the task needs: [`docs/design.md`](docs/design.md) (architecture),
[`docs/decisions.md`](docs/decisions.md) (why things are the way they are),
[`docs/licensing.md`](docs/licensing.md) (what code and libraries we may use),
[`docs/testing.md`](docs/testing.md) (how tests are written and proven),
[`docs/lessons.md`](docs/lessons.md) (mistakes not to repeat) and
[`docs/research/`](docs/research/) (findings with sources).

## Rules that are easy to break

- **Authorship.** Commits are authored by the owner, never by Claude, and carry
  no AI attribution trailers. Commit locally as
  `Heliograph Patch <patch@heliograph.invalid>`; the apply script replaces it
  with the owner's identity. Changes go out as patch series plus an apply
  script; the owner applies, reviews and pushes. Never push.
- **Licences.** Our own tree contains only code we wrote or code under a
  permissive licence. Copyleft libraries may be dependencies, used unmodified;
  their code is never copied in. Avoid AGPL and source-available licences. See
  `docs/licensing.md`.
- **Time.** Only a `Clock` reads the current time (ruff rule TID251 bans the
  common calls; do not use the others either).
  Timestamps are UTC nanoseconds; a message's `ts` is when it became known.
- **Layers.** `app` may import `plugins` and `core`; `plugins` may import
  `core`; `core` imports only the standard library. Checked on every run.
- **Every commit passes `./scripts/check.sh`**, including a test run in a
  non-UTC time zone.

## Commands

```bash
uv sync                     # install the locked environment
./scripts/check.sh          # lockfile, lint, format, types, layers, tests
uv run pytest -k name       # one test
python3 scripts/make_apply_script.py --help   # package commits for the owner
```

## Keeping the memory current

Update `docs/roadmap.md` when a phase item lands, add to `docs/decisions.md`
when a choice is made, and add to `docs/lessons.md` when something surprises
us. Edit the existing entry rather than adding a second copy of a fact.
