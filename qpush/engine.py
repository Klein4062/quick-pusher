"""推送引擎:对单个仓库执行 stage -> commit -> push(以及 pull/exec)。"""

from __future__ import annotations

from typing import List, Optional

from . import git
from .models import ExecOptions, Options, Outcome, PullOptions, Repo, RepoResult


def gather_state(repo: Repo) -> RepoResult:
    """只读地获取分支/是否有改动/领先落后情况,不做任何修改。"""
    res = RepoResult(repo=repo)
    res.dirty = git.is_dirty(repo.path)
    res.branch = git.current_branch(repo.path)
    res.detached = res.branch is None and git.has_commits(repo.path)
    up = git.upstream(repo.path)
    res.ahead, res.behind = git.ahead_behind(repo.path, up)
    return res


def process_repo(repo: Repo, opts: Options) -> RepoResult:
    """对单个仓库执行配置好的 stage/commit/push 流水线。

    返回带有各步骤结果的 RepoResult。git 失败时不会抛异常——而是记为
    FAILED 结果,以便并发执行器能继续处理其余仓库。
    """
    res = gather_state(repo)

    def log(level: str, msg: str) -> None:
        res.log.append((level, msg))

    remote = repo.remote or opts.remote
    branch = repo.branch or opts.branch or res.branch

    # --- 暂存(stage)------------------------------------------------------
    if opts.add:
        if res.dirty:
            if opts.dry_run:
                log("info", "将暂存所有改动")
            else:
                code, out, err = git.stage_all(repo.path)
                if code == 0:
                    res.staged = True
                    log("info", "已暂存所有改动")
                else:
                    res.stage_outcome = Outcome.FAILED
                    res.error = (err or out).strip() or "git add 失败"
                    log("error", res.error)
                    return res
        else:
            res.stage_outcome = Outcome.SKIPPED
            log("info", "无需暂存(干净)")
    else:
        res.stage_outcome = Outcome.SKIPPED

    # --- 提交(commit)-----------------------------------------------------
    if opts.commit:
        if git.has_staged_changes(repo.path):
            if opts.dry_run:
                log("info", f"将提交:{opts.message!r}")
            else:
                code, out, err = git.commit(repo.path, opts.message or "")
                if code == 0:
                    res.committed = True
                    summary = out.strip().splitlines()
                    log("info", summary[-1] if summary else "已提交")
                else:
                    res.commit_outcome = Outcome.FAILED
                    res.error = (err or out).strip() or "git commit 失败"
                    log("error", res.error)
                    return res
        else:
            res.commit_outcome = Outcome.SKIPPED
            log("info", "无需提交")
    else:
        res.commit_outcome = Outcome.SKIPPED

    # --- 推送(push)-------------------------------------------------------
    if opts.push:
        if res.detached or branch is None:
            res.push_outcome = Outcome.SKIPPED
            log("warn", "跳过推送:游离 HEAD / 无分支")
        elif not res.committed and res.ahead == 0:
            # 既没有新提交要发送,也没有预先存在的未推送提交。
            res.push_outcome = Outcome.SKIPPED
            log("info", "已是最新,无需推送")
        elif not git.has_remote(repo.path, remote):
            res.push_outcome = Outcome.SKIPPED
            log("warn", f"跳过推送:没有名为 {remote!r} 的远端")
        else:
            set_upstream = git.upstream(repo.path) is None
            if opts.dry_run:
                detail = "(设置上游)" if set_upstream else ""
                log("info", f"将推送 {branch} -> {remote} {detail}".strip())
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
                    log("info", f"已推送 {branch} -> {remote}")
                else:
                    res.push_outcome = Outcome.FAILED
                    tail = (err or out).strip()
                    res.error = _clean_push_error(tail) or "git push 失败"
                    log("error", res.error)
                    if _looks_like_divergence(tail):
                        log("hint", "远端已分叉——请 pull/rebase,或使用 --force")
    else:
        res.push_outcome = Outcome.SKIPPED

    return res


def _clean_push_error(text: str) -> str:
    """把多行的 git push 噪声输出精简为有用的那几行。"""
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
    # 折叠进度/统计行
    return "\n".join(keep) if keep else text.strip()


def _looks_like_divergence(text: str) -> bool:
    markers = ("non-fast-forward", "rejected", "fetch first", "fetch first")
    return any(m in text for m in markers)


# --- pull --------------------------------------------------------------------

def process_pull(repo: Repo, opts: PullOptions) -> RepoResult:
    """拉取并合并单个仓库的远端更新(默认 rebase)。

    脏工作区、游离 HEAD、缺少远端的仓库会被跳过并附说明,而不是强行尝试——
    往脏工作区里 pull 太容易产生冲突。
    """
    res = gather_state(repo)

    def log(level: str, msg: str) -> None:
        res.log.append((level, msg))

    remote = repo.remote or opts.remote
    branch = repo.branch or opts.branch or res.branch

    if res.detached or branch is None:
        res.note = "跳过:游离 HEAD"
        log("warn", res.note)
        return res
    if not git.has_commits(repo.path):
        res.note = "跳过:尚无提交"
        log("warn", res.note)
        return res
    if res.dirty:
        res.note = "跳过:工作区有改动(请先提交或 stash)"
        log("warn", res.note)
        return res
    if not git.has_remote(repo.path, remote):
        res.note = f"跳过:没有远端 {remote!r}"
        log("warn", res.note)
        return res
    if opts.dry_run:
        res.note = f"将从 {remote} 拉取 {branch}"
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
            res.note = "已是最新"
        else:
            res.acted = True
            res.note = "已更新"
        for line in (err or "").splitlines():
            if line.strip():
                log("info", line.strip())
        return res

    low = text.lower()
    if "conflict" in low or "could not apply" in low:
        res.conflicts = True
        res.error = "拉取产生冲突——请解决后继续(如 git rebase --continue)"
        res.note = "有冲突"
        log("error", res.error)
    else:
        cleaned = _clean_push_error(text) or "git pull 失败"
        res.error = cleaned
        res.note = cleaned.splitlines()[0]
        log("error", cleaned)
    return res


# --- exec --------------------------------------------------------------------

def process_exec(repo: Repo, opts: ExecOptions) -> RepoResult:
    """在仓库的工作树根目录执行一条 shell 命令。"""
    import subprocess

    res = RepoResult(repo=repo)
    res.branch = git.current_branch(repo.path)

    def log(level: str, msg: str) -> None:
        res.log.append((level, msg))

    if not opts.cmd.strip():
        res.error = "未给出命令"
        res.note = res.error
        return res
    if opts.dry_run:
        res.note = f"将执行:{opts.cmd}"
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
        res.note = "成功"
    else:
        res.error = f"退出码 {proc.returncode}"
        res.note = res.error
    return res
