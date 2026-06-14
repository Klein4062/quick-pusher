# quick-pusher

A small, zero-dependency CLI for committing and pushing the **same change across many git repositories at once**. Each repo commits its own working-tree changes with one shared commit message, then pushes to its own remote.

```sh
qpush "release: cut v2"            # stage → commit → push every discovered repo
qpush pull                         # fetch + integrate remote changes (rebase)
qpush exec -- npm test             # run a command in every repo root
qpush status                       # overview: branch, dirty, ahead/behind
qpush scan                         # list the repos qpush would touch
```

## Why

When you keep mirror repos, a fleet of related microservices, or several clones of the same project, applying a one-line change everywhere is tedious: `cd`, `git add`, `git commit -m …`, `git push`, repeat. `qpush` does it in parallel with a single command and a single summary.

## Install

Requires Python 3.8+ and `git` on your `PATH`.

```sh
pipx install .          # recommended — installs the `qpush` command
# or, without isolation:
pip install --user .
# or, no install at all:
python -m qpush "fix: typo"
```

## Quick start

```sh
# 1. Generate a config listing your repos (and/or scan dirs)
qpush init                       # writes ./.qpush.json

# 2. Edit it, then preview what qpush sees
qpush scan
qpush status

# 3. Push everywhere
qpush "feat: add health check"

# Bonus: sync upstream changes, or run a command across repos
qpush pull
qpush exec -- "npm test"
```

## How repos are discovered

`qpush` merges three sources, de-duplicates by real path, and skips anything that isn't a git repo:

1. **`--repos PATH`** flags on the command line (repeatable).
2. **Config file** `repos` list (`.qpush.json` in the current dir or any parent, then `~/.qpush.json`; override with `--config` or `$QPUSH_CONFIG`).
3. **`--scan DIR`** flags and/or the config's `scan` field — recurses up to `scanDepth` (default 3) looking for `.git`.

### Config format

JSON with `//`, `#`, and `/* */` comments allowed (strings are left intact, so URLs and colors like `"#fff"` are safe):

```jsonc
{
  // explicit repos — string, or object with overrides
  "repos": [
    "~/projects/repo-a",
    { "path": "../repo-b", "remote": "github", "branch": "main" }
  ],
  // directories to scan (string or list)
  "scan": "~/projects",
  "scanDepth": 3,
  "remote": "origin",
  "parallel": 4
}
```

## Usage

```
qpush "<message>" [options]              # default: stage + commit + push all repos
qpush pull   [options]                   # fetch + integrate remote changes
qpush exec   [options] -- <cmd...>       # run a shell command in every repo root
qpush status [options]                   # show branch / dirty / ahead-behind
qpush scan   [options]                   # list discovered repos
qpush init                               # write an example .qpush.json
```

The commit message may be the first argument or repeated via `-m` (joined as paragraphs):

```sh
qpush "fix: handle empty input"
qpush -m "fix: handle empty input" -m "Closes #42."
```

### Options

| Flag | Default | Description |
| --- | --- | --- |
| `--config PATH` | auto-search | config file path |
| `--repos PATH` | — | explicit repo path (repeatable) |
| `--scan DIR` | — | directory to scan for repos (repeatable) |
| `--scan-depth N` | `3` | max scan depth |
| `--remote NAME` | `origin` | default remote |
| `--branch NAME` | current | branch to push |
| `--only GLOB` | — | include only matching repos (by name or path; repeatable) |
| `--ignore GLOB` | — | exclude matching repos (repeatable) |
| `-j, --parallel N` | `4` | max concurrent repos |
| `-v, --verbose` | off | show per-repo detail for all repos |
| `--color {auto,always,never}` | `auto` | color output |

Push-specific:

| Flag | Default | Description |
| --- | --- | --- |
| `--no-add` | on | don't stage changes |
| `--no-commit` | on | don't commit |
| `--no-push` | on | don't push |
| `--force` | off | force-push **with lease** (`--force-with-lease`) |
| `--tags` | off | also push tags |
| `--dry-run` | off | show what would happen, change nothing |

### Examples

```sh
# Push only the services that match a name glob
qpush "chore: bump deps" --only 'svc-*'

# Commit everywhere but hold the push (review first)
qpush "wip" --no-push

# Push pre-existing unpushed commits without committing anything new
qpush --no-add --no-commit "n/a"

# Preview
qpush --dry-run "feat: x" --scan ~/work
```

### `pull` — fetch + integrate

Brings every repo up to date with its remote. Rebase by default.

```sh
qpush pull                 # rebase onto origin (default)
qpush pull --merge         # merge instead of rebase
qpush pull --ff-only       # only fast-forward; fail otherwise
qpush pull --prune         # also prune deleted remote branches
qpush pull --dry-run
```

Per-repo outcomes: `updated` (HEAD moved), `up to date` (already in sync), `skipped` (detached HEAD / missing remote / **dirty tree** — pull into a dirty tree is too likely to collide, so those are skipped with a hint to commit or stash first), or `conflicts` (reported as a failure for that repo; resolve and `git rebase --continue` / `git merge --continue` manually).

### `exec` — run a command in every repo root

Runs the given shell command with each repo's working tree as `cwd`. Put the command after `--` so options aren't ambiguous:

```sh
qpush exec -- npm test
qpush exec -- git log -1 --oneline
qpush exec -- "rm -f *.log"
qpush exec -v -- pwd        # -v prints each repo's captured output
qpush exec --dry-run -- npm test
```

Per-repo outcome is `ok` (exit 0) or `exit <N>` (nonzero, counted as a failure). Captured stdout/stderr is shown for failures, or for all repos with `-v`. The command runs through a shell, so pipes and redirects work — and you are responsible for what it does.

## Behavior notes

- **One shared message, independent commits.** Every repo stages its *own* working-tree changes (`git add --all`) and commits them with the message you gave. Files differ per repo; only the message is shared.
- **Clean repos are skipped.** If a repo has nothing to commit and nothing ahead of its remote, the push step is skipped too.
- **Detached HEAD / missing remote** → that repo's push is skipped with a warning (the commit still happens).
- **Push rejected (non-fast-forward)** → reported as a failure for that repo with a hint to pull or `--force`. Other repos continue.
- **`--force` uses `--force-with-lease`** — it refuses to overwrite commits your local view doesn't know about. `fetch` first if you intend to overwrite.
- **Credentials** are your environment's responsibility. Pushes run with `GIT_TERMINAL_PROMPT=0`, so missing credentials fail fast instead of hanging the run.
- **Exit code** is `1` if any repo failed, `2` on usage/config errors, `0` otherwise.

## Testing

```sh
PYTHONPATH=. python -m unittest discover -s tests -t .
```

The suite builds real git working repos backed by local bare remotes, so `stage → commit → push` is exercised end-to-end.

## Project layout

```
qpush/
  cli.py       # argparse, dispatch, parallel runner, entrypoint
  engine.py    # per-repo stage/commit/push orchestration
  git.py       # subprocess wrappers around git
  config.py    # repo discovery (config + scan), JSONC loading
  models.py    # Repo, RepoResult, Outcome, Options
  ui.py        # colors, progress lines, summary report
tests/         # unittest suite + helpers (temp git sandboxes)
```

## License

MIT
