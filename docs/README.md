# Heliograph documentation

These files are the project's long-term memory: what we are building, why,
how, and what we have learned. They are written for whoever works on the code
next, human or AI, and are kept short enough to read at the start of a
session.

| File | Holds | Changes when |
| --- | --- | --- |
| [roadmap.md](roadmap.md) | Phases, gates, status, the next commits | an item lands or the plan changes |
| [workflow.md](workflow.md) | How a change is made, checked, packaged and applied | the process changes |
| [principles.md](principles.md) | The rules that decide design questions; coding standards | rarely |
| [design.md](design.md) | Architecture, ports, data model, pipeline, adapters, GUI | the design changes |
| [decisions.md](decisions.md) | Numbered decisions with their reasons and alternatives | a decision is made or reversed |
| [licensing.md](licensing.md) | What code and libraries we may use, and the checks | the policy changes or a dependency is added |
| [lessons.md](lessons.md) | Surprises and mistakes, so they are not repeated | something surprises us |
| [research/](research/) | Findings from research, with sources | new research is done |

## Rules for these files

- **One home per fact.** If a fact is already written down, link to it; do not
  copy it. A decision lives in `decisions.md`; the design states the result.
- **Edit in place.** Correct an entry when it goes stale; history lives in git.
- **Say why.** A rule without its reason gets broken by the next person who
  does not see the reason.
- **Date what decays.** Library versions, prices, rate limits and terms of use
  change; record the date they were checked.
- **Cite sources** for anything learned from outside the project.

## Relationship to the design document

The design began as a shared document on claude.ai
("Heliograph — Design Document",
<https://claude.ai/artifact/Uvqtsvpk4H4m3Pgk1jpTQv>). As of 2026-10-21 these
files are the source of truth; that document is a readable snapshot and is
refreshed from here on request.
