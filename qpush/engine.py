"""The push engine: stage -> commit -> push for a single repository."""

from __future__ import annotations

from typing import List, Optional

from . import git
from .models import ExecOptions, Options, Outcome, PullOptions, Repo, RepoResult


def gather_state(repo: Repo) -> RepoResult:
    """Read branch/dirty/ahead/behind without mutating anything."""
    res = RepoResult(repo=repo)
    res.dirty = git.is_dirty(repo.path)
    res.branch = git.current_branch(repo.path)
    res.detached = res.branch is None and git.has_commits(repo.path)
    up = git.upstream(repo.path)
    res.ahead, res.behind = git.ahead_behind(repo.path, up)
    return res


def process_repo(repo: Repo, opts: Options) -> RepoResult:
    """Run the configured stage/commit/push pipeline for one repo.

    Returns a RepoResult with per-step outcomes. Never raises on git failures —
    those are captured as FAILED outcomes so the parallel runner keeps going.
    """
    res = gather_state(repo)

    def log(level: str, msg: str) -> None:
        res.log.append((level, msg))

    remote = repo.remote or opts.remote
    branch = repo.branch or opts.branch or res.branch

    # --- stage ---------------------------------------------------------------
    if opts.add:
        if res.dirty:
            if opts.dry_run:
                log("info", "would stage all changes")
            else:
                code, out, err = git.stage_all(repo.path)
                if code == 0:
                    res.staged = True
                    log("info", "staged all changes")
                else:
                    res.stage_outcome = Outcome.FAILED
                    res.error = (err or out).strip() or "git add failed"
                    log("error", res.error)
                    return res
        else:
            res.stage_outcome = Outcome.SKIPPED
            log("info", "nothing to stage (clean)")
    else:
        res.stage_outcome = Outcome.SKIPPED

    # --- commit --------------------------------------------------------------
    if opts.commit:
        if git.has_staged_changes(repo.path):
            if opts.dry_run:
                log("info", f"would commit: {opts.message!r}")
            else:
                code, out, err = git.commit(repo.path, opts.message or "")
                if code == 0:
                    res.committed = True
                    summary = out.strip().splitlines()
                    log("info", summary[-1] if summary else "committed")
                else:
                    res.commit_outcome = Outcome.FAILED
                    res.error = (err or out).strip() or "git commit failed"
                    log("error", res.error)
                    return res
        else:
            res.commit_outcome = Outcome.SKIPPED
            log("info", "nothing to commit")
    else:
        res.commit_outcome = Outcome.SKIPPED

    # --- push ----------------------------------------------------------------
    if opts.push:
        if res.detached or branch is None:
            res.push_outcome = Outcome.SKIPPED
            log("warn", "skipped push: detached HEAD / no branch")
        elif not res.committed and res.ahead == 0:
            # Nothing new to send and no pre-existing unpushed commits.
            res.push_outcome = Outcome.SKIPPED
            log("info", "up to date, nothing to push")
        elif not git.has_remote(repo.path, remote):
            res.push_outcome = Outcome.SKIPPED
            log("warn", f"skipped push: no remote named {remote!r}")
        else:
            set_upstream = git.upstream(repo.path) is None
            if opts.dry_run:
                detail = " (set-upstream)" if set_upstream else ""
                log("info", f"would push {branch} -> {remote}{detail}")
            else:
                code, out, err = git.push(
                    repo.path,
                    remote,
                    branch,
                    force=opts.force,
                    tags=opts.tags,
                    set_upstream=set_upstream,
                )
                if code == 0:
                    res.pushed = True
                    log("info", f"pushed {branch} -> {remote}")
                else:
                    res.push_outcome = Outcome.FAILED
                    tail = (err or out).strip()
                    res.error = _clean_push_error(tail) or "git push failed"
                    log("error", res.error)
                    if _looks_like_divergence(tail):
                        log("hint", "remote diverged — pull/rebase, or use --force")
    else:
        res.push_outcome = Outcome.SKIPPED

    return res


def _clean_push_error(text: str) -> str:
    """Reduce noisy multi-line git push output to the useful lines."""
    if not text:
        return ""
    keep: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("remote:"):
            continue
        keep.append(line)
    # collapse progress/stat lines
    return "\n".join(keep) if keep else text.strip()


def _looks_like_divergence(text: str) -> bool:
    markers = ("non-fast-forward", "rejected", "fetch first", "fetch first")
    return any(m in text for m in markers)


# --- pull --------------------------------------------------------------------

def process_pull(repo: Repo, opts: PullOptions) -> RepoResult:
    """Fetch and integrate remote changes for one repo (rebase by default).

    Dirty trees, detached HEAD, and missing remotes are skipped with a note
    rather than attempted — pulling into a dirty tree is too likely to collide.
    """
    res = gather_state(repo)

    def log(level: str, msg: str) -> None:
        res.log.append((level, msg))

    remote = repo.remote or opts.remote
    branch = repo.branch or opts.branch or res.branch

    if res.detached or branch is None:
        res.note = "skipped: detached HEAD"
        log("warn", res.note)
        return res
    if not git.has_commits(repo.path):
        res.note = "skipped: no commits yet"
        log("warn", res.note)
        return res
    if res.dirty:
        res.note = "skipped: dirty tree (commit or stash first)"
        log("warn", res.note)
        return res
    if not git.has_remote(repo.path, remote):
        res.note = f"skipped: no remote {remote!r}"
        log("warn", res.note)
        return res
    if opts.dry_run:
        res.note = f"would pull {branch} from {remote}"
        return res

    head_before = git.run(repo.path, ["rev-parse", "HEAD"])[1].strip()
    code, out, err = git.pull(
        repo.path,
        remote=remote,
        branch=branch,
        rebase=opts.rebase,
        ff_only=opts.ff_only,
        prune=opts.prune,
    )
    text = (out or "") + (err or "")

    if code == 0:
        head_after = git.run(repo.path, ["rev-parse", "HEAD"])[1].strip()
        if head_before == head_after:
            res.note = "up to date"
        else:
            res.acted = True
            res.note = "updated"
        for line in (err or "").splitlines():
            if line.strip():
                log("info", line.strip())
        return res

    low = text.lower()
    if "conflict" in low or "could not apply" in low:
        res.conflicts = True
        res.error = "pull conflicts — resolve and continue (e.g. git rebase --continue)"
        res.note = "conflicts"
        log("error", res.error)
    else:
        cleaned = _clean_push_error(text) or "git pull failed"
        res.error = cleaned
        res.note = cleaned.splitlines()[0]
        log("error", cleaned)
    return res


# --- exec --------------------------------------------------------------------

def process_exec(repo: Repo, opts: ExecOptions) -> RepoResult:
    """Run a shell command in the repo's working-tree root."""
    import subprocess

    res = RepoResult(repo=repo)
    res.branch = git.current_branch(repo.path)

    def log(level: str, msg: str) -> None:
        res.log.append((level, msg))

    if not opts.cmd.strip():
        res.error = "no command given"
        res.note = res.error
        return res
    if opts.dry_run:
        res.note = f"would run: {opts.cmd}"
        return res

    proc = subprocess.run(
        opts.cmd,
        shell=True,
        cwd=repo.path,
        capture_output=True,
        text=True,
        env=git._env(),
    )
    if proc.stdout.strip():
        log("info", proc.stdout.rstrip())
    if proc.stderr.strip():
        log("info", proc.stderr.rstrip())
    if proc.returncode == 0:
        res.acted = True
        res.note = "ok"
    else:
        res.error = f"exit {proc.returncode}"
        res.note = res.error
    return res
