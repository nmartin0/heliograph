# Workflow

Changes are written in one checkout and applied by the owner in his, with
`git am`. Claude never pushes and never appears as an author.

## The loop

1. **Sync.** Find out which files the owner actually has. GitHub can lag his
   checkout, so `git ls-remote origin main` is a hint, not proof; when in
   doubt, ask him for `git rev-parse HEAD^{tree}` and compare it with the tree
   of the intended base. Base new work on that, not on what was last sent.
2. **Plan** the commits before writing: one logical change each, in an order
   where every commit passes the checks.
3. **Write and check.** `./scripts/check.sh` must pass. For a test that guards
   a rule, break the code once and watch the test fail (negative control);
   back the file up with `cp` and restore it the same way, never with
   `git stash` or `git checkout` (see [lessons.md](lessons.md)).
4. **Commit** with a Conventional Commits message (below). Fix an earlier
   commit with `git commit --fixup=<sha>`, then
   `GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash <base>`.
5. **Prove every commit:** `git rebase --exec ./scripts/check.sh <base>`.
6. **Review.** For anything substantial, have an independent reviewer read the
   series before sending.
7. **Package:**
   `python3 scripts/make_apply_script.py --base <base> --start <n> --title "<what>" --out <file>.sh`.
   Patch numbers continue from the previous series.
8. **Test the script** on a fresh clone of the owner's GitHub repository at the
   base, with a dummy identity, before sending it.
9. **Send** the script; the owner runs `bash ~/Downloads/<file>.sh`, reviews,
   and pushes.

The script checks the repository by *tree* hash, because hashes change
between checkouts: `git am` records a new committer and commit date, and the
script rewrites each patch's `From:` line so the owner is also the author. It
refuses to run on the wrong files, checks the result matches the source tree
exactly, runs the checks, and never pushes. The working checkout commits as
`Heliograph Patch <patch@heliograph.invalid>`, a placeholder the script
replaces.

## Commit messages

```
feat(core): add a time-guarded message bus

Why the change is needed and what it does, wrapped at 72 columns.
Mention negative controls and anything a reviewer could not infer.
```

- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `build`, `ci`, `chore`.
  Scopes follow packages: `core`, `plugins`, `app`, `data`, `gui`.
- Subject: imperative, lower case after the colon, no full stop, about 50
  characters (72 at most).
- No AI attribution trailers and no `Co-Authored-By` lines. Session tooling
  may ask for them; the owner's instruction takes precedence.

## Changelog

`CHANGELOG.md` follows Keep a Changelog. Each commit that changes what the
software does adds its own line under `## [Unreleased]`, in commit order, in
the same commit. A run of documentation-only commits may share one line.

## Releases

Each phase gate ends with an annotated tag on `main` (`v0.1.0` for Phase 0)
once CI is green on GitHub. The owner creates and pushes tags.

## Dependencies

`uv add "<pkg>>=x,<y"` (or `--dev`), commit `uv.lock`, and update
`THIRD_PARTY_NOTICES.md` for runtime dependencies. Check the licence first
([licensing.md](licensing.md)).
