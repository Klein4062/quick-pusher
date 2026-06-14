"""Command-line interface: argument parsing, dispatch, and the parallel runner.

`qpush "<message>" [flags]` is the default action (commit & push every
discovered repo). `qpush status|scan|init` are alternative commands, detected
as the first token. Keeping push as the top-level default means flags may
appear in any order around the message.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from . import __version__, config, engine, ui
from .models import Options, Repo, RepoResult

_PRINT_LOCK = threading.Lock()
SUBCOMMANDS = {"status", "scan", "init", "pull", "exec"}

EXAMPLE_CONFIG = """\
{
  // Repositories to operate on (path string, or object with overrides).
  "repos": [
    "~/projects/repo-a",
    { "path": "../repo-b", "remote": "origin", "branch": "main" }
  ],
  // Directories to scan for git repos (string or list).
  "scan": "~/projects",
  "scanDepth": 3,
  "remote": "origin",
  "parallel": 4
}
"""

PROG_DESCRIPTION = (
    "Multi-repo sync commit & push. Stage every discovered repo, commit each "
    "with the SAME message, and push. Alternative commands: "
    "`qpush status` (overview), `qpush scan` (list repos), `qpush init` (write config)."
)


# --- argument parsing --------------------------------------------------------

def _add_common(p: argparse.ArgumentParser) -> None:
    """Options shared by every command (repo discovery + output)."""
    p.add_argument("--config", metavar="PATH", help="path to a qpush config file")
    p.add_argument("--repos", action="append", metavar="PATH", default=[],
                   help="explicit repository path (repeatable)")
    p.add_argument("--scan", action="append", metavar="DIR", default=[],
                   help="directory to scan for git repos (repeatable)")
    p.add_argument("--scan-depth", type=int, default=3, help="max scan depth (default 3)")
    p.add_argument("--remote", default="origin", help="default remote name (default origin)")
    p.add_argument("--branch", help="branch to push (default: current branch)")
    p.add_argument("--only", action="append", default=[], metavar="GLOB",
                   help="only include repos whose name/path matches (repeatable)")
    p.add_argument("--ignore", action="append", default=[], metavar="GLOB",
                   help="exclude repos whose name/path matches (repeatable)")
    p.add_argument("-j", "--parallel", type=int, default=4, help="max concurrent repos (default 4)")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="color output (default auto)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="show per-repo detail for all repos")


def build_push_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qpush", description=PROG_DESCRIPTION)
    _add_common(p)
    p.add_argument("--version", action="version", version=f"qpush {__version__}")
    p.add_argument("message", nargs="?", help="commit message")
    p.add_argument("-m", "--message", action="append", dest="message_flags", metavar="MSG",
                   help="commit message (repeatable; joined as paragraphs)")
    p.add_argument("--no-add", dest="add", action="store_false",
                   help="don't stage changes (staging is on by default)")
    p.add_argument("--no-commit", dest="commit", action="store_false",
                   help="don't commit (committing is on by default)")
    p.add_argument("--no-push", dest="push", action="store_false",
                   help="don't push (pushing is on by default)")
    p.add_argument("--force", action="store_true", help="force push with lease (use with care)")
    p.add_argument("--tags", action="store_true", help="also push tags")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would happen without modifying anything")
    p.set_defaults(add=True, commit=True, push=True)
    return p


def build_status_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qpush status",
                                description="Show branch / dirty / ahead-behind for each repo.")
    _add_common(p)
    return p


def build_scan_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qpush scan",
                                description="List the repositories qpush would operate on.")
    _add_common(p)
    return p


def build_pull_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qpush pull",
        description="Fetch and integrate remote changes in every repo (rebase by default).",
    )
    _add_common(p)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--rebase", dest="rebase", action="store_true",
                      help="rebase local commits on top of the remote (default)")
    mode.add_argument("--merge", dest="rebase", action="store_false",
                      help="merge the remote instead of rebasing")
    mode.add_argument("--ff-only", dest="ff_only", action="store_true",
                      help="only fast-forward; fail if not possible")
    p.add_argument("--prune", action="store_true", help="prune deleted remote branches")
    p.add_argument("--dry-run", action="store_true", help="show what would be pulled, change nothing")
    p.set_defaults(rebase=True, ff_only=False)
    return p


def build_exec_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qpush exec",
        description=("Run a shell command in each repo's root. "
                     "Put the command after `--`, e.g. `qpush exec -- npm test`."),
    )
    _add_common(p)
    p.add_argument("--dry-run", action="store_true", help="show the command, run nothing")
    return p


def _resolve_message(args) -> Optional[str]:
    if getattr(args, "message_flags", None):
        return "\n\n".join(args.message_flags)
    return getattr(args, "message", None)


def _discovery_args(args) -> config.DiscoveryArgs:
    return config.DiscoveryArgs(
        config_path=args.config,
        repos=args.repos,
        scan=args.scan,
        scan_depth=args.scan_depth,
        remote=args.remote,
        only=args.only,
        ignore=args.ignore,
        cwd=os.getcwd(),
    )


# --- subcommands -------------------------------------------------------------

def cmd_scan(repos: List[Repo]) -> int:
    ui.print_header(f"discovered {len(repos)} repo{'s' if len(repos) != 1 else ''}")
    name_w = max((len(r.name) for r in repos), default=8)
    for repo in repos:
        print(f"  {repo.name:<{name_w}}  {repo.path}")
    return 0


def cmd_init() -> int:
    target = os.path.join(os.getcwd(), ".qpush.json")
    if os.path.exists(target):
        print(f"{target} already exists; leaving it untouched.", file=sys.stderr)
        return 1
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(EXAMPLE_CONFIG)
    print(f"wrote example config to {target}")
    return 0


def cmd_status(repos: List[Repo]) -> int:
    results = _run_parallel(
        repos, engine.gather_state,
        parallel=max(1, min(4, len(repos) or 1)),
        show_progress=False,
    )
    ui.print_status_table(results)
    return 0


def cmd_push(repos: List[Repo], opts: Options) -> int:
    label = "DRY RUN — " if opts.dry_run else ""
    ui.print_header(
        f"{label}processing {len(repos)} repo{'s' if len(repos) != 1 else ''}  "
        + ui.dim(f"message={opts.message!r}")
    )
    results = _run_parallel(repos, lambda r: engine.process_repo(r, opts), parallel=opts.parallel)
    ui.print_summary(results, verbose=opts.verbose)
    failed = sum(1 for r in results if r.overall.value == "failed")
    return 1 if failed else 0


def cmd_pull(repos: List[Repo], args) -> int:
    from .models import PullOptions
    opts = PullOptions(
        rebase=args.rebase, ff_only=args.ff_only, prune=args.prune,
        remote=args.remote, branch=args.branch, dry_run=args.dry_run,
        parallel=max(1, args.parallel), verbose=args.verbose,
    )
    label = "DRY RUN — " if opts.dry_run else ""
    ui.print_header(f"{label}pulling {len(repos)} repo{'s' if len(repos) != 1 else ''}")
    results = _run_parallel(repos, lambda r: engine.process_pull(r, opts), parallel=opts.parallel)
    ui.print_summary(results, verbose=opts.verbose)
    failed = sum(1 for r in results if r.overall.value == "failed")
    return 1 if failed else 0


def cmd_exec(repos: List[Repo], cmd: str, args) -> int:
    from .models import ExecOptions
    opts = ExecOptions(cmd=cmd, dry_run=args.dry_run,
                       parallel=max(1, args.parallel), verbose=args.verbose)
    label = "DRY RUN — " if opts.dry_run else ""
    ui.print_header(f"{label}exec [{len(repos)} repo{'s' if len(repos) != 1 else ''}]: {opts.cmd}")
    results = _run_parallel(repos, lambda r: engine.process_exec(r, opts), parallel=opts.parallel)
    ui.print_summary(results, verbose=opts.verbose)
    failed = sum(1 for r in results if r.overall.value == "failed")
    return 1 if failed else 0


def _run_parallel(repos: List[Repo], worker, parallel: int, show_progress: bool = True) -> List[RepoResult]:
    """Run `worker(repo)` across repos concurrently, preserving input order."""
    results: List[Optional[RepoResult]] = [None] * len(repos)
    workers = max(1, min(parallel, len(repos) or 1))

    def _do(idx: int, repo: Repo) -> RepoResult:
        try:
            return worker(repo)
        except Exception as exc:  # never let one repo kill the run
            res = RepoResult(repo=repo, error=f"unexpected error: {exc}")
            res.log.append(("error", str(exc)))
            return res

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_do, i, r): i for i, r in enumerate(repos)}
            for fut in as_completed(futures):
                i = futures[fut]
                res = fut.result()
                results[i] = res
                if show_progress:
                    with _PRINT_LOCK:
                        ui.print_progress_line(res)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        for i, repo in enumerate(repos):
            if results[i] is None:
                results[i] = RepoResult(repo=repo, error="interrupted")
        return [r for r in results if r is not None]

    return [r for r in results if r is not None]


def _split_for_exec(rest: List[str]):
    """Split exec argv into (qpush-option tokens, command tokens) on `--`.

    Without an explicit `--`, falls back to the first positional token (so
    `qpush exec git status` works too). Returns ([], cmd_tokens) when only a
    command is present.
    """
    if "--" in rest:
        i = rest.index("--")
        return rest[:i], rest[i + 1:]
    first, idx = _first_positional(rest)
    if idx < 0:
        return rest, []
    return rest[:idx], rest[idx:]


# --- entrypoint --------------------------------------------------------------

# Options that consume the following token as their value. Used by the
# pre-scan that finds the first positional (so a subcommand can be recognized
# even when global options precede it, e.g. `qpush --color never scan`).
_VALUE_OPTS = {
    "--config", "--repos", "--scan", "--scan-depth", "--remote", "--branch",
    "--only", "--ignore", "-j", "--color", "-m", "--message",
}


def _first_positional(argv: List[str]):
    """Return (token, index) of the first positional argument, or (None, -1).

    Skips option flags and the tokens they consume as values, and stops at `--`.
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            if i + 1 < len(argv):
                return argv[i + 1], i + 1
            return None, -1
        if tok.startswith("-"):
            if "=" in tok:
                i += 1
                continue
            if tok in _VALUE_OPTS:
                i += 2
                continue
            i += 1  # boolean flag
            continue
        return tok, i
    return None, -1

def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # A subcommand is the first positional token, so global options may precede
    # it (`qpush --color never scan`). If none matches, default to push.
    command = "push"
    args = None
    exec_cmd = ""
    first, idx = _first_positional(argv)
    if first in SUBCOMMANDS:
        command = first
        rest = argv[:idx] + argv[idx + 1:]
        if command == "status":
            args = build_status_parser().parse_args(rest)
        elif command == "scan":
            args = build_scan_parser().parse_args(rest)
        elif command == "pull":
            args = build_pull_parser().parse_args(rest)
        elif command == "exec":
            opt_tokens, cmd_tokens = _split_for_exec(rest)
            args = build_exec_parser().parse_args(opt_tokens)
            exec_cmd = " ".join(cmd_tokens)
        else:  # init
            args = None
    else:
        args = build_push_parser().parse_args(argv)

    if command == "init":
        return cmd_init()
    if command == "exec" and not exec_cmd.strip():
        print("error: exec needs a command. Usage: qpush exec -- <command...>",
              file=sys.stderr)
        return 2

    assert args is not None
    ui.configure({"auto": None, "always": True, "never": False}[args.color])

    try:
        repos = config.discover(_discovery_args(args))
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not repos:
        print("no repositories found.", file=sys.stderr)
        print("configure via .qpush.json, or pass --repos / --scan.", file=sys.stderr)
        return 1

    if command == "scan":
        return cmd_scan(repos)
    if command == "status":
        return cmd_status(repos)
    if command == "pull":
        return cmd_pull(repos, args)
    if command == "exec":
        return cmd_exec(repos, exec_cmd, args)

    # default: push
    opts = Options(
        message=_resolve_message(args),
        add=args.add,
        commit=args.commit,
        push=args.push,
        remote=args.remote,
        branch=args.branch,
        force=args.force,
        tags=args.tags,
        dry_run=args.dry_run,
        parallel=max(1, args.parallel),
        verbose=args.verbose,
    )
    if opts.commit and not opts.message:
        print(
            "error: a commit message is required when committing.\n"
            "  e.g.  qpush \"fix: handle empty input\"\n"
            "        qpush -m \"fix: handle empty input\"",
            file=sys.stderr,
        )
        return 2
    return cmd_push(repos, opts)


if __name__ == "__main__":
    sys.exit(main())
