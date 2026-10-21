# Jane Street engineering ideas

Jane Street publishes its engineering ideas openly. Their code is OCaml, so we
take ideas, not libraries: a replayable message log, an incremental
recomputation graph, received-at timestamps, expect tests, illegal states made
unrepresentable, and independent safety checks.

Researched 2026-10-21.

| Idea | Jane Street | Heliograph |
| --- | --- | --- |
| One replayable log (Concord) | A sequencer stamps every message into one ordered stream; apps are deterministic state machines; recovery is replay ("an exact copy of what you were when you crashed"); no snapshots | Bus stamps sequence and time and journals before delivery; header records versions; handlers return messages rather than doing I/O |
| Recompute what changed (Incremental) | `Var.set` then `stabilize()`; recompute heap ordered by node height; cutoffs (default physical equality); only observed nodes computed; `bind` reshapes the graph; explicit `advance_clock`; ~30 ns per node | Indicator graph stabilised once per timestamp; cutoffs (`==` for discrete, `isclose` for floats); observers; coarse nodes since Python is far slower |
| Simulated time (Async `Time_source`) | A clock that moves only when advanced; `advance_by_alarms` fires timers in order; read-only views for consumers | Our `SimulatedClock` gains alarms; strategies get read-only time |
| Received-at times | "Save all the data … along with the time we received it" | `received_at` on every point and bar |
| Expect tests | Tests print output; diffs are accepted by promoting a corrected file; `[@remember_failures]` for property failures | inline-snapshot and syrupy; Hypothesis example database |
| Illegal states unrepresentable | Each state its own record type; exhaustive matches; `find` vs `find_exn` naming | Union types with `assert_never`; strict mypy |
| Consistent errors (`Or_error`) | One error type, context added as errors propagate | One error hierarchy; messages name the value |
| Safety | Automatic halts; "similar alerts in different systems written by different teams"; fill-too-good alert; "we basically don't trade unless it's being monitored" | Independent risk gate; `Halted` state cleared by the owner; backtest-too-good warning |
| Bonsai | UI components as pure state machines over Incremental; renders only visible rows | View as a function of state; actions, not scattered mutation |
| Iron code review | Scrutiny tiers; critical code gets a seconder and several reviewers | Strictest review for bus, journal, clock, risk gate |

## Concord trade-offs worth remembering

- Versioning is brittle: "When you change the world, you have to change the
  whole world at once." Version message schemas and keep old ones readable.
- Every app sees every message; they filter by a message-type tree.
- Failover is manual; they accept seconds of downtime.

## Knight Capital (2012), as they retell it

A repurposed configuration flag activated dead code on one of eight servers
that missed an upgrade; failure notices were ignored; rolling back the healthy
servers made losses worse. Lessons: never reuse a parameter name, delete dead
code, refuse to start on version mismatches, and do not rely on people being
"repeatedly careful over and over".

## Research practice

- Research in Python; production code reimplemented and checked against shared
  sample outputs.
- Notebooks are useful but out-of-order cells break reproducibility; notebooks
  should call the engine's replay API.
- "Being sloppy with data science is fatal"; include delisted names, hold out
  data, avoid leakage; markets are anti-inductive.

## Not applicable at our scale

Multicast, retransmitters, sequencer failover, allocation-free tuning,
FPGAs and Hardcaml, magic-trace, adopting OCaml.

## Unverified

"Favour readers over writers" as an exact phrase; pre-trade check design
beyond the production-engineering talk; Knight details against the SEC order.

## Sources

- https://signalsandthreads.com/state-machine-replication-and-why-you-should-care/
- https://signalsandthreads.com/building-a-ui-framework/
- https://signalsandthreads.com/why-testing-is-hard-and-how-to-fix-it/
- https://signalsandthreads.com/python-ocaml-and-machine-learning/
- https://signalsandthreads.com/finding-signal-in-the-noise/
- https://signalsandthreads.com/wrestling-the-world-into-rows/
- https://signalsandthreads.com/swapping-the-engine-out-of-a-moving-race-car/
- https://signalsandthreads.com/solving-puzzles-in-production/
- https://blog.janestreet.com/introducing-incremental/
- https://blog.janestreet.com/testing-with-expectations/
- https://blog.janestreet.com/effective-ml-revisited/
- https://blog.janestreet.com/how-to-fail-introducing-or-error-dot-t/
- https://blog.janestreet.com/quickcheck-for-core/
- https://blog.janestreet.com/code-review-that-isnt-boring/
- https://www.janestreet.com/tech-talks/production-engineering-when-trading-billions-of-dollars-a-day
- https://cacm.acm.org/practice/ocaml-for-the-masses/
- https://raw.githubusercontent.com/janestreet/incremental/master/src/incremental_intf.ml
- https://ocaml.janestreet.com/ocaml-core/v0.13/doc/async_kernel/Async_kernel/Time_source/index.html
