# Handoff to Elysium

Written 2026-11-04, when development of Heliograph stopped because it is to be
absorbed into the base Elysium project. This is a snapshot: where the roadmap
stands, what is open, and what the Elysium team needs to know. It links to the
other files in `docs/` for reasons and detail rather than repeating them.

Heliograph, a proprietary single-user trading-signal provider that never
places orders, is built through Phase 4 of its roadmap. Its live data sources
have never met the real services, and Phases 5 and 6 were never started.

## Where the roadmap stands

Phases 0 to 4 are built and their gates pass in tests.

| Phase | Theme | Status | What is left |
| --- | --- | --- | --- |
| 0 | Foundations | Done | The release tag, deferred by the owner |
| 1 | Data: CCXT crypto source, file source, store, quality checks | Built, gate passing | The live check against Kraken has never been run |
| 2 | Backtest engine and strategy pipeline | Built | For Phase 5: stage objects keep state, so optimisation must build them afresh per run |
| 3 | Graphical workspace: engine API, window, Plug-ins screen, Strategy Tester, Wizard | Built; the Wizard is a first cut | Wizard renaming and undo; the window's security and chart performance (below) |
| 4 | Signals and paper trading: sinks, paper broker with took-it/skipped answers, halt and watches, replay and resume, Alpaca source, session calendars | Built | The items below; the gate (restart with positions and clock intact, replay reproducing signals) is tested but has not been shown on a long real session |
| 5 | Research tools: grid and Optuna optimisation, walk-forward, deflated Sharpe, overfitting probability, sealed holdout | Not started | Everything |
| 6 | Advanced plug-ins: Kalman trend, regime models, meta-labelling and others | Not started | Everything |

Unscheduled: a web version for several users, and a real broker behind the
risk gate. Neither was designed. The checkboxes in [roadmap.md](roadmap.md)
are the detailed list.

## Open issues

The first three rows are the ones to settle before trusting live use. The rest
are known, and most are written down in [decisions.md](decisions.md) (D53, D56
to D62).

| Area | Open issue | Matters because |
| --- | --- | --- |
| Verification | The Kraken live tests (`uv run pytest -m live`) have never been run. | The offline pages were written from how CCXT is documented to behave. |
| Verification | The Alpaca source was tested only against fakes built from Alpaca's documentation; `tests/live/test_alpaca_live.py` needs the owner's keys. | Only a live run shows the real service still answers as read. |
| Verification | The Phase 4 gate was tested, not shown on a long real paper session. | Restart and replay are the claims the live design rests on. |
| Security | The engine's port and the window have no sign-in, and the window's socket checks no Origin, so a hostile web page could reach them by DNS rebinding. | Both must stay on `127.0.0.1` until Elysium's identity and proxy sit in front of them. |
| Live sessions | They run only from `heliograph live`, not from the engine server or the window. | An Elysium add-on server will need this. |
| Live sessions | A crash between a signal being recorded and its delivery being recorded leaves that signal unsent after a resume (D53). | A signal could be lost silently in a crash. |
| Live sessions | The silence watch starts at the first price and re-arms only on a new price, so after a clear with the feed still dead nothing halts until the next price (D56). | A dead feed can go unnoticed after a clear. |
| Live sessions | Resuming after downtime longer than the silence span halts at once (D56); a feed that gives up ends the session failed and cannot carry on (D57). | Operators will see these halts and should know they are by design. |
| Live sessions | The hand-over of unread answers to a resumed session has a small race window; found in review, not written up in the decisions. | Rare. |
| Alpaca | alpaca-py reconnects forever and hides its own errors, so a dead feed shows only as silence; times arrive to the microsecond; no extended hours, daily bars or live bars (D62). | Pair every Alpaca mount with `halt_after_silence` and a `calendar`. |
| Window | The chart draws a run's whole series and loses zoom on a toggle; a year of minute bars is slow. A node's line ends one bar after the last candle (D38). | Usability; spans and paging are the fix. |
| Window | The Wizard cannot rename a component or undo (D42). The chart attribution text was not compared with Lightweight Charts' NOTICE file. | Small; the licence check is the one to close before shipping. |
| Not built | Local days in reports; signals refused outside market hours; Windows desktop notifications; Apprise (left out on purpose, D60); the published-records sink for Elysium (D20). | None blocks use. |
| Housekeeping | Commits through patch 0115 show author `none <none>` on GitHub. | Cosmetic, but it is the history Elysium inherits. |
| Housekeeping | One window-related control test hung once in a long controls run and was not investigated. | A possible flaky test in the GUI code. |
| Housekeeping | The test run mutes pandas deprecation warnings (from exchange_calendars) and a websockets one (from alpaca-py) in `pyproject.toml`. | Remove the mutes when those libraries update. |

## What the Elysium team needs to know

### Licence and ownership

- Every source file carries `LicenseRef-Proprietary` and the owner's copyright,
  and `LICENSE` says the same. The licence of the merged tree is Elysium's
  decision.
- `THIRD_PARTY_NOTICES.md` lists every runtime dependency with its licence;
  the check fails when it is stale (`scripts/licence_report.py --write`
  regenerates it). Three are copyleft and used unmodified as dependencies
  only: bidict, certifi and orjson.
- The policy is in [licensing.md](licensing.md): no copyleft code copied in,
  AGPL and source-available licences avoided. Hosting the software for others
  changes the obligations that come with copyleft dependencies, so compare it
  with Elysium's own policy before merging.
- The Wizard vendors Vue Flow with a reproducible build. The window's
  Lightweight Charts attribution text was never compared with that library's
  NOTICE file.

### Architecture to preserve

- Layers: `core` imports only the standard library; `plugins` may import
  `core`; `app` may import both; the window is a client of the API only. Five
  import-linter contracts enforce this on every check. Elysium's own core uses
  third-party libraries, but the direction of the layers is the same.
- Everything concrete is a plug-in found through entry points (data sources,
  sinks, journals, indicators, forecasters, gates, sizers, risk gates,
  instrument providers). Elysium's adapter registries are hard-coded dicts, so
  absorption must either map them or keep the entry points.
- The engine is deterministic. Only a `Clock` reads the time, every message
  carries the time it became known, the bus is synchronous, and a journal
  replays to the same entries (D22, D43, D52). Tests and the resume feature
  depend on this; do not let a change read the wall clock directly.
- Heliograph never places an order. The paper broker is simulated, and every
  live strategy has a halt gate first in its risk gates (D55).

### Integration points already prepared

- D20 chose standalone first. [`integrations/elysium/module.json`](../integrations/elysium/module.json)
  is a draft manifest for Elysium's module contract version 1 (checked against
  Elysium `dev` at `fae3e4e`); its README explains why it uses only the six
  keys Elysium accepts. See [research/elysium.md](research/elysium.md).
- The engine is already shaped as an add-on server: `heliograph serve` takes
  port, data and configuration directories by argument or environment, binds
  `127.0.0.1`, stops cleanly on SIGTERM, serves a health check, logs JSON on
  stderr, runs one worker and sends times as ISO 8601 strings.
- Undecided and not built: how the screens appear inside Elysium (a React
  module over the API, NiceGUI in an iframe, or data only), and the optional
  published-records sink (signals, simulated fills, daily equity in a separate
  SQLite file that Elysium reads as a silo).
- What Heliograph needs from Elysium: sign-in and a proxy in front of the
  engine and the window, and a way to run live sessions under the engine
  process.

### Running it

- Python 3.12 or later (developed on 3.13) with uv: `uv sync`, then
  `./scripts/check.sh` (about four minutes: lockfile, lint, format, strict
  types, import layers, licences, the tests including a run in a non-UTC time
  zone).
- The controls in `tests/controls.toml` are deliberate breaks that prove the
  tests would notice a regression; each must fail exactly its listed tests
  ([testing.md](testing.md)). The full run takes about an hour;
  `scripts/check_controls.py --only "<text>"` runs one.
- Commands: `backtest`, `live`, `answer`, `halt`, `clear`, `report`, `replay`,
  `serve`, `gui`. `examples/trend.toml` is a runnable configuration.
- Secrets are never written in a configuration: sinks name an environment
  variable (`token_env`), and Alpaca reads `APCA_API_KEY_ID` and
  `APCA_API_SECRET_KEY`.

### Practices worth carrying over

- `docs/` is the project's memory; [CLAUDE.md](../CLAUDE.md) is kept short and
  points to it.
- Large or risky work starts from an agreed spec; risky code is read by a
  reviewer with fresh context; reports give evidence, not belief
  ([workflow.md](workflow.md)).
- Specific to this project and probably dropped on merge: commits authored as
  `Heliograph Patch <patch@heliograph.invalid>`, delivered as numbered patches
  that the owner applies and pushes.

## Suggested absorption steps

1. Tag a release (the version is still 0.1.0) so the absorbed point is fixed.
2. Run `uv run pytest -m live` once with Kraken access and Alpaca keys, and
   correct the offline pages wherever the real services answer differently.
3. Decide the licence of the merged tree and reconcile
   [licensing.md](licensing.md) with Elysium's policy.
4. Bring the history across rather than squashing it: the decisions refer to
   the work, and the controls and tests guard against regressions.
5. Choose how the screens appear in Elysium (D20) once module bundles exist,
   finish the manifest, and put Elysium's sign-in and proxy in front of the
   engine and window before they listen on anything but `127.0.0.1`.
6. Decide whether live sessions move under the engine process.
7. Move the open items above and the unfinished phases (5 and 6) into
   Elysium's own tracker.
8. Keep or retire the project's own machinery deliberately: `scripts/check.sh`,
   the controls, the patch workflow and `CLAUDE.md`.
