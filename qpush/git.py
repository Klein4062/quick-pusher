"""对 git 的轻量 subprocess 封装。所有 git 调用都经由此模块。"""

from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Tuple

# 需要交互式凭据的 git 调用应当快速失败,而不是让整个多仓库任务卡在提示符上。
_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "GCM_INTERACTIVE": "0",  # Git Credential Manager(Windows/macOS)
}


class GitError(Exception):
    """当 check=True 的 git 命令失败时抛出。"""


def _env() -> dict:
    env = os.environ.copy()
    env.update(_GIT_ENV)
    return env


def run(repo_path: str, args: List[str], check: bool = False) -> Tuple[int, str, str]:
    """执行 `git -C repo_path <args>`,返回 (退出码, stdout, stderr)。

    除 check=True 外不会抛异常;调用方通常更希望自己检查退出码,
    以便针对单个仓库给出清晰的失败报告,而不是让整批任务崩溃。
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
    """返回当前分支名;若处于游离 HEAD 或未知则返回 None。"""
    code, out, _ = run(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0:
        return None
    name = out.strip()
    return None if name in ("", "HEAD") else name


def has_commits(repo_path: str) -> bool:
    code, _, _ = run(repo_path, ["rev-parse", "--verify", "HEAD"])
    return code == 0


def upstream(repo_path: str) -> Optional[str]:
    """已配置的上游引用(例如 'origin/main');没有则返回 None。"""
    code, out, _ = run(
        repo_path,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
    )
    return out.strip() or None if code == 0 else None


def is_dirty(repo_path: str) -> bool:
    """工作区是否有未提交改动(已暂存或未暂存)则返回 True。"""
    code, out, _ = run(repo_path, ["status", "--porcelain"])
    return bool(out.strip())


def has_staged_changes(repo_path: str) -> bool:
    """暂存区与 HEAD 不同(即有内容被暂存)则返回 True。"""
    code, _, _ = run(repo_path, ["diff", "--cached", "--quiet", "HEAD"], check=False)
    # 退出码 0 => 暂存区与 HEAD 一致;1 => 有已暂存改动;其它 => 出错或尚无 HEAD
    if code == 0:
        return False
    if code == 1:
        return True
    # 尚无 HEAD(未诞生的分支):把任何已暂存内容视为"有暂存"。
    code2, out2, _ = run(repo_path, ["diff", "--cached", "--quiet"], check=False)
    return code2 == 1


def ahead_behind(repo_path: str, upstream_ref: Optional[str]) -> Tuple[int, int]:
    """返回相对于 upstream_ref 的 (领先数, 落后数)。无上游则返回 (0, 0)。"""
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
    """暂存所有改动(包含未跟踪文件和删除)。"""
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
    """合并远端更新。默认用 rebase;`ff_only` 会覆盖 rebase 选项。"""
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
