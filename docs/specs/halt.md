# Spec: the Halted state, watches and alerts

Agreed with the owner 2026-10-31 (three choices below). Delete this file when
the last commit lands; the decisions stay in `decisions.md` (D55 onward).

**Goal.** A live session can be halted, by the owner or by a watch that notices
something wrong, and tells the owner when it is. While halted it still sends
signals that reduce risk, and refuses (and journals) the ones that add it. Only
the owner clears a halt.

**Agreed choices.**
- While halted, signals that only take risk off still leave (as with the risk
  gates, D34); entries and additions are refused as `SignalRefused`.
- Automatic halts: no price on a stream for too long (staleness); more signals
  than a set number in a window (rate); a feed that gives up after its retries.
- Cleared only by the owner, `heliograph clear <journal>`; nothing clears itself.

**Commits.**
1. `Halted(reason, by)` and `Cleared` messages, a `Halt` risk gate every live
   strategy has first, and `heliograph halt` / `heliograph clear` through the
   inbox (the answers inbox, extended). Journalled, replayed, resumed.
2. A staleness watch (alarm-driven) and a signal-rate watch that halt.
3. A feed that gives up halts the session and alerts instead of ending it.
4. Alerts: a halt or clear is delivered to the sinks.
5. The report says when the session was halted and why.

**Out of scope.** Real orders (never), halting a single strategy (a halt covers
the session), market-hours gating (needs calendars, later).

**End-to-end check.** A scripted live day in which the price stops, the session
halts and alerts, an entry is refused and an exit leaves, the owner clears it,
and signalling resumes; the journal replays byte for byte, and a resume from
the middle of the halt is still halted.
