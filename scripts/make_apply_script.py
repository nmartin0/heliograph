"""Package a range of commits as a self-contained bash script that applies them.

Heliograph changes are written in one checkout and applied in another with
``git am``. Commit hashes differ between the two (``git am`` records a new
committer and commit date, and the script rewrites the author), so the
generated script identifies the base and the result by their *tree* hashes,
which depend only on the files.

Usage::

    python3 scripts/make_apply_script.py --base origin/main --start 16 \
        --title "project memory" --out ~/out/heliograph-0016-0022.sh

The script it writes:

1. refuses to run unless the repository is on the right branch, clean, not in
   the middle of an ``am`` or rebase, and its files match the base exactly;
2. applies every patch with ``git am --3way``, with the person running it as
   the author;
3. checks that the resulting files match the source checkout exactly;
4. runs ``uv sync --frozen`` and ``./scripts/check.sh``;
5. never pushes.
"""

import argparse
import shlex
import subprocess
import tempfile
from pathlib import Path

# HEADER is filled in with str.format, so its literal braces are doubled.
# FOOTER is used as is, so its braces are single. Moving a line from one to
# the other silently breaks the generated script.
HEADER = r"""#!/usr/bin/env bash
# Heliograph patch series {series}: {title}
#
# Applies {count} commit(s) on branch '{branch}' of your Heliograph checkout,
# with you as the author, then runs the project checks. Nothing is pushed:
# review the result, then run 'git push' yourself.
#
# Usage:  bash {script_name}            (repo at ~/heliograph)
#         HELIOGRAPH_REPO=/path bash {script_name}
set -euo pipefail

REPO="${{HELIOGRAPH_REPO:-$HOME/heliograph}}"
EXPECTED_BRANCH={branch}
BASE_TREE="{base_tree}"
RESULT_TREE="{result_tree}"
SERIES="{series}"

fail() {{ printf '\n\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }}
info() {{ printf '\033[36m==>\033[0m %s\n' "$*"; }}

# ---- 1. Check the repository is in the expected state -------------------
cd "$REPO" 2>/dev/null || fail "No directory $REPO (set HELIOGRAPH_REPO)."
git rev-parse --git-dir >/dev/null 2>&1 || fail "No git repository at $REPO (set HELIOGRAPH_REPO)."

[ ! -d "$(git rev-parse --git-path rebase-apply)" ] && [ ! -d "$(git rev-parse --git-path rebase-merge)" ] \
    || fail "A git am or rebase is already in progress. Finish it or run 'git am --abort'."

branch="$(git symbolic-ref --quiet --short HEAD || true)"
[ "$branch" = "$EXPECTED_BRANCH" ] \
    || fail "On branch '$branch'; switch first: git switch $EXPECTED_BRANCH"

[ -z "$(git status --porcelain)" ] \
    || fail "Working tree has uncommitted or untracked changes. Commit or stash them first."

head="$(git rev-parse HEAD)"
[ "$(git rev-parse 'HEAD^{{tree}}')" = "$BASE_TREE" ] \
    || fail "The files at HEAD (${{head:0:7}}) are not the ones this series was written against. Apply the earlier series first, or pull the latest main."

name="$(git config user.name || true)"
email="$(git config user.email || true)"
[ -n "$name" ] && [ -n "$email" ] \
    || fail "Set your identity first: git config --global user.name '...' && git config --global user.email '...'"
info "Applying series $SERIES as $name <$email> onto ${{head:0:7}} ($EXPECTED_BRANCH)"

# ---- 2. Unpack the patches ------------------------------------------------
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
"""

PATCH_BLOCK = """
cat > "$work/{name}" <<'{delim}'
{body}{delim}
"""

FOOTER = r"""
# Make you the author of every commit in the series. A long author header may
# be folded over several lines; the continuation lines are dropped with it.
export HG_AUTHOR="$name <$email>"
for p in "$work"/*.patch; do
    awk '
        skipping && /^[ \t]/ { next }
        { skipping = 0 }
        done != 1 && /^From: / { print "From: " ENVIRON["HG_AUTHOR"]; done = 1; skipping = 1; next }
        { print }
    ' "$p" > "$p.tmp" && mv "$p.tmp" "$p"
done

# ---- 3. Apply ---------------------------------------------------------------
info "git am --3way ($(ls "$work"/*.patch | wc -l) patches)"
if ! git am --3way "$work"/*.patch; then
    cat >&2 <<'HELP'

A patch did not apply. Nothing after it was applied. Choose one:
  git am --abort       put the branch back exactly as it was before this script
  (fix the files)      then: git add <files> && git am --continue
  git am --skip        drop this one patch and carry on with the rest
HELP
    exit 1
fi

[ "$(git rev-parse 'HEAD^{tree}')" = "$RESULT_TREE" ] \
    || fail "The commits applied, but the files differ from the ones the series was built from. Undo with: git reset --hard $head"

# ---- 4. Run the project checks ---------------------------------------------
if command -v uv >/dev/null 2>&1; then
    info "uv sync --frozen"
    uv sync --frozen || fail "uv sync failed. The commits are applied; undo them with: git reset --hard $head"
    if [ -f scripts/check.sh ]; then
        info "scripts/check.sh"
        bash scripts/check.sh || fail "Checks failed. The commits are applied; undo them with: git reset --hard $head"
    else
        printf '\n\033[33mNOTE:\033[0m scripts/check.sh not found, so the checks were skipped.\n'
    fi
else
    printf '\n\033[33mNOTE:\033[0m uv is not installed, so the checks were skipped.\n'
    printf '      Install it (https://docs.astral.sh/uv/getting-started/installation/), e.g.\n'
    printf '        sudo apt install pipx && pipx install uv\n'
    printf '      then run: uv sync && ./scripts/check.sh\n'
fi

# ---- 5. Report ---------------------------------------------------------------
echo
info "Applied:"
git log --oneline "$head..HEAD"
echo
info "Review with 'git log -p ${head:0:7}..HEAD', then publish with: git push"
info "To undo everything this script did: git reset --hard ${head:0:7}"
"""


def git(repo: Path, *args: str) -> str:
    """Run a git command in ``repo`` and return its standard output, stripped."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def build_script(
    *, repo: Path, base: str, head: str, start: int, title: str, branch: str, out: Path
) -> int:
    """Write the apply script to ``out`` and return the number of patches in it."""
    if "\n" in title or "\n" in branch:
        raise SystemExit("--title and --branch must be one line")
    with tempfile.TemporaryDirectory() as tmp:
        # Pin every option a personal git config could change.
        git(
            repo,
            "format-patch",
            "--quiet",
            "--no-signature",
            "--no-cover-letter",
            "--no-signoff",
            "--suffix=.patch",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "--no-numbered",
            f"--start-number={start}",
            "-o",
            tmp,
            f"{base}..{head}",
        )
        patches = sorted(Path(tmp).glob("*.patch"))
        if not patches:
            raise SystemExit(f"no commits in {base}..{head}")
        series = f"{start:04d}-{start + len(patches) - 1:04d}"
        parts = [
            HEADER.format(
                series=series,
                title=title,
                count=len(patches),
                branch=shlex.quote(branch),
                base_tree=git(repo, "rev-parse", f"{base}^{{tree}}"),
                result_tree=git(repo, "rev-parse", f"{head}^{{tree}}"),
                script_name=out.name,
            )
        ]
        for patch in patches:
            body = patch.read_text(encoding="utf-8")
            delim = f"HELIOGRAPH_PATCH_EOF_{patch.name[:4]}"
            if delim in body:
                raise SystemExit(f"delimiter collision in {patch.name}")
            if not body.endswith("\n"):
                raise SystemExit(f"{patch.name} does not end with a newline")
            parts.append(PATCH_BLOCK.format(name=patch.name, delim=delim, body=body))
    parts.append(FOOTER)
    out.write_text("".join(parts), encoding="utf-8", newline="\n")
    out.chmod(0o755)
    return len(patches)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path(), help="source checkout")
    parser.add_argument("--base", required=True, help="commit the series starts after")
    parser.add_argument("--head", default="HEAD", help="last commit of the series")
    parser.add_argument("--start", type=int, required=True, help="number of the first patch")
    parser.add_argument("--title", required=True, help="one-line description")
    parser.add_argument("--branch", default="main", help="branch the script applies to")
    parser.add_argument("--out", type=Path, required=True, help="script to write")
    args = parser.parse_args()
    count = build_script(
        repo=args.repo,
        base=args.base,
        head=args.head,
        start=args.start,
        title=args.title,
        branch=args.branch,
        out=args.out,
    )
    print(f"wrote {args.out} ({count} patches)")


if __name__ == "__main__":
    main()
