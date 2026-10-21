# Heliograph and Elysium's module system

Heliograph can become an add-on module of Elysium, the owner's other project,
but the parts it would need (add-on servers, identity, a proxy) are not built
yet. Heliograph stays standalone, keeps every screen behind its own JSON API,
and integrates when Elysium's module API settles (decision D20).

Exchanged 2026-10-21: our request document "Generic platform features
Heliograph needs from Elysium" (<https://claude.ai/artifact/1FUjSNjE7FnDCD9xHDkQWk>)
and the Elysium team's reply "Building Heliograph for Elysium", checked against
Elysium `dev` at `fae3e4e` and Heliograph `main` at `df9b3dc`.

## Elysium's module plan (`BLOCKING.md` item 3)

| Step | Delivers | State at `fae3e4e` |
| --- | --- | --- |
| 1.1 Shell and contract | `shell/` (`@shell/core`); `GET /api/shell/session`, `GET /api/shell/modules` from a `module.json` beside each sub-app; `modules.yaml` chooses which load | Built |
| 1.2 First real module | A separately built bundle with `entry`, shared React and router by import map, `activate`/`deactivate` | Next |
| 1.3 Frame into the shell | Frame, sign-in and palette move into `@shell/core` | Planned |
| 1.4 Module manager | Load, unload, refresh while running; safe mode | Planned |
| 1.5 Slots | A module contributes to another's screen | Planned |
| 2 Simulator as add-on | First sub-app with its own server, started and watched by Elysium | Planned |
| 3 Remaining sub-apps | Elysium's own sub-apps moved onto the module API | Planned |
| 4 Versioned API | The sub-app API written down and versioned | Planned |

Decisions that bind add-ons: modules are trusted (no sandbox yet); static
contributions in the manifest, code loaded lazily; shared libraries fixed at
page load; add-on servers are separate processes Elysium starts, watches and
restarts; no add-on code runs inside Elysium's server.

The feed today (contract 1): `{"contract": 1, "home": …, "modules": [{"id",
"name", "version", "entry", "contributes": {"pages", "nav"}}]}`, filtered on
the server so a module whose `permission` the person lacks is absent.

## Our sixteen requests, as Elysium answered

| # | Request | Status | Lands |
| --- | --- | --- | --- |
| 1 | Add-on server learns who the user is | Proposed (signed header from the proxy preferred) | Step 2 |
| 2 | Same-origin proxy with SSE and WebSocket upgrades | Proposed | Step 2 |
| 3 | Process lifecycle written down | Partly (simulator's control server, `health.py`, `lifecycle.py`) | Step 2 |
| 4 | Versioned contract | Partly (`contract: 1`; manifest field and compatibility promise missing) | Step 1 |
| 5 | Background jobs | Partly (mirror sync runs in the background); generic job record proposed | Step 3 |
| 6 | Durable timers for add-ons | Partly (`/api/reminders` uses them) | Steps 2–3 |
| 7 | Post to the Inbox | Proposed | Step 2 |
| 8 | Declared permissions | Largely exists (any string grant; module `permission`) | Step 2 |
| 9 | Time-series reads | Largely exists (see below) | Extensions proposed |
| 10 | Time-series chart in the kit | Partly (`OverTimeChart` in Browse) | Step 3 |
| 11 | Add-on database as a silo | Exists by configuration | Manifest form: step 3 |
| 12 | Private bundle rules | Planned | Step 1.2 |
| 13 | Virtualised table | Partly (`DataTable` sorts, sticky header) | Step 3 |
| 14 | Forms from JSON Schema | Partly (forms from parameter specs, not JSON Schema) | Step 3 |
| 15 | Slots | Planned | Step 1.5 |
| 16 | Light single-user profile | Partly (SQLite throughout; an `llm:` block is mandatory) | After step 4 |

"Proposed" is the Elysium team's recommendation to its owner, not a
commitment.

## Time series in Elysium

- `POST /api/objects/{type}/aggregate` with `period` = day, ISO week or month:
  count, sum, avg, min, max over visible data, at most 1,000 periods
  (`core/periods.py`). Missing: hour and minute buckets, first and last, a
  time-zone parameter (buckets are UTC days).
- Buckets are labelled by their start; empty buckets are zero for counts and
  sums and gaps for averages; `/forecast` separates finished periods
  (`history`) from the filling one (`running`).
- Range filters (`date_range`, `relative_date`) exist; a declared time
  property and a tail cursor do not.
- The agent has a server-side aggregate step (`core/agent/agentic_loop.py`)
  but cannot group by period yet.
- Count triggers support `above`, `at_most`, `gained`, `fell` with a "last N
  hours" filter.
- **No as-of reads from snapshots, by design.** Erasure rewrites every table
  holding an erased person's keys and drops older snapshots, so "nothing in
  the lake can show them again, time travel included"; gold keeps 30
  publications. "What did we know then" is answered per object by the change
  history (`/published-history`, `/history`).

## What Elysium asked Heliograph to do

Done or recorded on 2026-10-21:

- Put every screen behind a JSON application API and make the process
  supervisable (D18).
- Send times as ISO 8601 strings over JSON (D19).
- Draft a manifest: `integrations/elysium/module.json`.
- Fix the eight defects its review found (all fixed; the configuration digest
  over canonical JSON is on the roadmap as later work).
- Correct D2, D5, D13 and `adapters.md`.

Optional any time: publish signals, simulated fills and daily equity to a
separate SQLite file and declare it as a silo in Elysium's `data_silos.yaml`
and `ontology_schema.yaml`; Elysium then gives search, object pages,
day/week/month aggregates, the over-time chart, count triggers and the agent
over them, read-only, with no code on either side. Store times as ISO 8601 UTC
text.

## Deferred until Elysium decides

| Do not build yet | Unblocks at |
| --- | --- |
| A front-end bundle (`entry`, `activate`) | Step 1.2 |
| Contributions into other modules' screens | Step 1.5 |
| A launch spec for Elysium to start Heliograph | Step 2 |
| Multi-user sign-in or a verifier for Elysium identity | Step 2 |
| Routing our WebSocket through Elysium | Step 2 |
| Writing to Elysium's Inbox, timers or jobs | Steps 2–3 |
| Relying on library versions or kit components | Steps 1.2 and 3 |
| Declaring our silo from the manifest | Step 3 |

Contract 1 may change before step 4; each change is named in `BLOCKING.md`
item 3 with its commit, so always check against a named commit.

## Heliograph's screens inside Elysium (open, decide at Phase 3)

| Option | How | Cost |
| --- | --- | --- |
| A. React module over our API | A React bundle calls the JSON API through the proxy | A second front end unless it replaces NiceGUI; exact fit with the shell |
| B. NiceGUI in an iframe | The module frames Heliograph's own UI | Needs the owner's decision (iframes are reserved for untrusted code); theme, navigation and sign-in cross the frame |
| C. Data only | Heliograph stays a desktop app; Elysium reads the published silo | Nothing interactive inside Elysium |

## Effort estimates made before the reply (engineer-days, rough)

Thin integration 21–28; full GUI inside Elysium 54–74; the NiceGUI plan
32–43 plus 10–15 for a later multi-user web version. The saving from Elysium
is sign-in, users, shell and Inbox, not charting.

## Sources

- Elysium `dev` at `fae3e4e`: `BLOCKING.md` item 3 (lines 333–422) and
  refusals (line 524), `shell/README.md`, `core/modules.py`,
  `core/periods.py`, `core/mirror/erasure.py`, `core/agent/agentic_loop.py`,
  `api/routes.py`, `ui/packages/app-browse/src/OverTimeChart.tsx`
- "Building Heliograph for Elysium", the Elysium team's reply, 2026-10-21
  (PDF from the owner)
- <https://github.com/nmartin0/elysium/tree/dev>
