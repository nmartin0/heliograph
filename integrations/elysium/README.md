# Elysium module manifest (draft)

`module.json` is the shape Elysium's module feed expects (contract version 1,
Elysium `dev` at `fae3e4e`). Elysium will not load it until it supports
add-ons; it is written now so the shape is settled early (decision D20).

- Paths start with `/heliograph`, because a path belongs to whichever module
  claims it first.
- `subject` groups the entry in Elysium's rail; Elysium's own subjects are
  Objects, Ontology, Inbox and Settings, and an unknown subject sorts after
  them.
- Elysium refuses keys other than these six today, deliberately. Fields for a
  server process, a contract version and declared permissions are expected;
  do not add them until Elysium defines them.
- `entry` stays `null` until Elysium's step 1.2 defines how a module bundle is
  built and what `activate` receives.

Background: [docs/research/elysium.md](../../docs/research/elysium.md).
