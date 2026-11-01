# Spec: the paper broker on live data and a person's answers

Agreed with the owner 2026-10-31. Delete this file when the last commit lands;
the decisions stay in `decisions.md` (D50 onward).

**Goal.** A live session paper-trades its strategy's signals. The person answers
each signal (took it, skipped it; unanswered ones expire), and the strategy's
position held follows what they did. Restarting a session keeps positions and
the clock.

**Agreed choices.** Until a signal is answered the strategy assumes it was taken
(as in the backtest); a signal that expires unanswered counts as skipped.

**Commits.**
1. `SignalAnswer` (took/skipped, price, quantity) journalled as an input; an
   inbox folder beside the journal and `heliograph answer` to fill it.
2. The strategy's position held follows the answers; the broker's fills stay as
   the signal's own paper result; unanswered signals expire as skipped.
3. A live session's journal replays to the same entries (a replayed day
   reproduces its signals), so its state can be rebuilt from it.
4. A live session resumes from an earlier journal by replaying it into its
   components, then going live: positions, the answers given and the clock
   carry over.
5. A report comparing the person's result with the signal's paper result.

**Out of scope.** Real orders (never), the risk gate and `Halted` state (next
item), buttons in the window (later, on the same message).

**End-to-end check (the Phase 4 gate).** A scripted live day with answers is
stopped, restarted and finished; the result has the same positions and clock as
an uninterrupted run, and replaying its journal reproduces the same signals and
the same position held.
