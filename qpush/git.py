"""Thin subprocess wrappers around git. All git access goes through here."""

from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Tuple

# A git invocation that needs interactive credentials should fail fast rather
# than hang the whole multi-repo run waiting on a prompt.
_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "GCM_INTERACTIVE": "0",  # Git Credential Manager (Windows/macOS)
}


class GitError(Exception):
    """Raised when a checked git command fails."""


def _env() -> dict:
    env = os.environ.copy()
    env.update(_GIT_ENV)
    return env


def run(repo_path: str, args: List[str], check: bool = False) -> Tuple[int, str, str]:
    """Run `git -C repo_path <args>`, returning (code, stdout, stderr).

    Never raises unless check=True; callers usually prefer to inspect the code
    so they can report a clean per-repo failure instead of blowing up the run.
    """
    cmd = ["git", "-C", repo_path, *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_env(),
    )
    if check and proc.returncode != 0:
        raise GitError(_format_error(cmd, proc))
    return proc.returncode, proc.stdout, proc.stderr


def _format_error(cmd, proc) -> str:
    detail = (proc.stderr or proc.stdout or "").strip()
    where = " ".join(cmd)
    return f"git command failed: {where}\n{detail}"


def is_repo(path: str) -> bool:
    code, _, _ = run(path, ["rev-parse", "--is-inside-work-tree"])
    return code == 0


def toplevel(path: str) -> Optional[str]:
    code, out, _ = run(path, ["rev-parse", "--show-toplevel"])
    return out.strip() or None if code == 0 else None


def current_branch(repo_path: str) -> Optional[str]:
    """Branch name, or None if detached/unknown."""
    code, out, _ = run(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0:
        return None
    name = out.strip()
    return None if name in ("", "HEAD") else name


def has_commits(repo_path: str) -> bool:
    code, _, _ = run(repo_path, ["rev-parse", "--verify", "HEAD"])
    return code == 0


def upstream(repo_path: str) -> Optional[str]:
    """The configured upstream ref (e.g. 'origin/main'), or None."""
    code, out, _ = run(
        repo_path,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
    )
    return out.strip() or None if code == 0 else None


def is_dirty(repo_path: str) -> bool:
    """True if there are uncommitted changes (staged or unstaged)."""
    code, out, _ = run(repo_path, ["status", "--porcelain"])
    return bool(out.strip())


def has_staged_changes(repo_path: str) -> bool:
    """True if the index differs from HEAD (i.e. something is staged)."""
    code, _, _ = run(repo_path, ["diff", "--cached", "--quiet", "HEAD"], check=False)
    # exit 0 => clean index vs HEAD; 1 => staged changes; other => error/no HEAD
    if code == 0:
        return False
    if code == 1:
        return True
    # No HEAD yet (unborn branch): treat any staged content as staged.
    code2, out2, _ = run(repo_path, ["diff", "--cached", "--quiet"], check=False)
    return code2 == 1


def ahead_behind(repo_path: str, upstream_ref: Optional[str]) -> Tuple[int, int]:
    """Return (ahead, behind) counts vs upstream_ref. (0, 0) if no upstream."""
    if not upstream_ref:
        return 0, 0
    ahead = _revlist_count(repo_path, f"{upstream_ref}..HEAD")
    behind = _revlist_count(repo_path, f"HEAD..{upstream_ref}")
    return ahead, behind


def _revlist_count(repo_path: str, range_spec: str) -> int:
    code, out, _ = run(repo_path, ["rev-list", "--count", range_spec])
    if code != 0:
        return 0
    try:
        return max(0, int(out.strip()))
    except ValueError:
        return 0


def has_remote(repo_path: str, name: str) -> bool:
    code, out, _ = run(repo_path, ["remote"])
    return code == 0 and name in out.split()


def stage_all(repo_path: str) -> Tuple[int, str, str]:
    """Stage all changes (including untracked and deletions)."""
    return run(repo_path, ["add", "--all"])


def stage_paths(repo_path: str, paths: List[str]) -> Tuple[int, str, str]:
    return run(repo_path, ["add", "--", *paths])


def commit(repo_path: str, message: str) -> Tuple[int, str, str]:
    return run(repo_path, ["commit", "-m", message])


def push(
    repo_path: str,
    remote: str,
    branch: Optional[str],
    force: bool = False,
    tags: bool = False,
    set_upstream: bool = False,
) -> Tuple[int, str, str]:
    args: List[str] = ["push"]
    if force:
        args.append("--force-with-lease")
    if set_upstream:
        args.append("--set-upstream")
    if tags:
        args.append("--tags")
    args.append(remote)
    if branch:
        args.append(branch)
    return run(repo_path, args)


def fetch(repo_path: str, remote: str, prune: bool = False, tags: bool = False) -> Tuple[int, str, str]:
    args: List[str] = ["fetch"]
    if prune:
        args.append("--prune")
    if tags:
        args.append("--tags")
    args.append(remote)
    return run(repo_path, args)


def pull(
    repo_path: str,
    remote: Optional[str] = None,
    branch: Optional[str] = None,
    rebase: bool = True,
    ff_only: bool = False,
    prune: bool = False,
) -> Tuple[int, str, str]:
    """Integrate remote changes. Default: rebase. `ff_only` overrides rebase."""
    args: List[str] = ["pull"]
    if ff_only:
        args.append("--ff-only")
    elif rebase:
        args.append("--rebase")
    else:
        args.append("--no-rebase")  # merge
    if prune:
        args.append("--prune")
    if remote:
        args.append(remote)
        if branch:
            args.append(branch)
    return run(repo_path, args)
