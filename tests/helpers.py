"""共享的测试辅助函数:构建真实的 git 工作仓库,并配以本地 bare 远端。

使用本地 file:// 形式的 bare 远端,意味着 push/pull 无需任何凭据即可工作,
从而引擎可以端到端地真正执行 `git push`。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple


def git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args} failed in {path}: {proc.stderr.strip()}")
    return proc


def make_repo(parent: Path, name: str, with_remote: bool = True) -> Tuple[Path, Path | None]:
    """在 parent/name 下创建一个工作仓库,可选地配以一个 bare origin 远端。"""
    repo = parent / name
    repo.mkdir(parents=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text(f"# {name}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "init")
    git(repo, "branch", "-M", "main")  # 统一默认分支名

    origin = None
    if with_remote:
        origin = parent / f"{name}.origin.git"
        subprocess.run(["git", "init", "--bare", str(origin)],
                       capture_output=True, check=True)
        git(repo, "remote", "add", "origin", str(origin))
        git(repo, "push", "-u", "-q", "origin", "main")
    return repo, origin


def make_bundle(names: List[str]) -> tempfile.TemporaryDirectory:
    """返回一个临时目录,其中已填好若干工作仓库及对应的 bare 远端。"""
    tmp = tempfile.TemporaryDirectory()
    base = Path(tmp.name)
    for n in names:
        make_repo(base, n, with_remote=True)
    return tmp


def write_file(repo: Path, filename: str, content: str) -> None:
    (repo / filename).write_text(content)


def append_file(repo: Path, filename: str, line: str) -> None:
    with open(repo / filename, "a") as fh:
        fh.write(line + "\n")


def last_commit_message(repo: Path) -> str:
    proc = git(repo, "log", "-1", "--pretty=%B")
    return proc.stdout.strip()


def head_on_origin(repo: Path) -> str:
    """当前 origin/main 所指向的提交。"""
    proc = git(repo, "rev-parse", "origin/main", check=False)
    return proc.stdout.strip()


def local_head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()
