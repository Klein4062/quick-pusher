"""数据模型:Repo、RepoResult、Outcome,以及 Options 等配置数据包。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class Outcome(str, Enum):
    """单个仓库某一步骤的结果。"""

    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


class Repo:
    """一个目标 git 仓库,可附带针对单个仓库的覆盖项。"""

    def __init__(
        self,
        path: str,
        remote: Optional[str] = None,
        branch: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        self.path = path
        self.remote = remote
        self.branch = branch
        self.name = name or os.path.basename(os.path.normpath(path))

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Repo(name={self.name!r}, path={self.path!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Repo) and self.path == other.path

    def __hash__(self) -> int:
        return hash(self.path)


@dataclass
class RepoResult:
    """处理一个仓库后得到的结果。"""

    repo: Repo
    branch: Optional[str] = None
    detached: bool = False
    dirty: bool = False
    staged: bool = False
    committed: bool = False
    pushed: bool = False
    ahead: int = 0
    behind: int = 0
    stage_outcome: Outcome = Outcome.OK
    commit_outcome: Outcome = Outcome.OK
    push_outcome: Outcome = Outcome.OK
    error: Optional[str] = None
    # 以下为 pull/exec(以及将来任何命令)使用的通用字段:
    # `note` 覆盖单行摘要;`acted` 标记"已成功执行某动作";
    # `conflicts` 标记 pull 后工作区处于半合并/半 rebase 状态。
    acted: bool = False
    conflicts: bool = False
    note: Optional[str] = None
    # 收集到的日志行:(level, message)。level 取值 {info, warn, error, hint}。
    log: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def overall(self) -> Outcome:
        """对该仓库所做所有动作的一个总体判定。"""
        if self.error or self.conflicts:
            return Outcome.FAILED
        if self.committed or self.pushed or self.acted:
            return Outcome.OK
        return Outcome.SKIPPED

    @property
    def steps_summary(self) -> str:
        """用于进度行和汇总报告的单行可读摘要。"""
        if self.note:
            return self.note
        parts: List[str] = []
        if self.error:
            return self.error.splitlines()[0]
        if self.committed and self.pushed:
            parts.append(f"已提交并推送 {self.branch or ''}".strip())
        elif self.committed:
            parts.append("已提交(未推送)")
        elif self.pushed:
            parts.append("已推送")
        elif self.stage_outcome == Outcome.SKIPPED and not self.dirty:
            parts.append("干净,无需提交")
        else:
            parts.append("无改动")
        if self.ahead and not self.pushed:
            parts.append(f"领先 {self.ahead}")
        if self.behind:
            parts.append(f"落后 {self.behind}")
        return ", ".join(parts)


@dataclass
class Options:
    """驱动 push 引擎的已解析配置。"""

    message: Optional[str]
    add: bool = True
    commit: bool = True
    push: bool = True
    remote: str = "origin"
    branch: Optional[str] = None
    force: bool = False
    tags: bool = False
    dry_run: bool = False
    parallel: int = 4
    verbose: bool = False


@dataclass
class PullOptions:
    """驱动 `qpush pull` 的配置。"""

    rebase: bool = True
    ff_only: bool = False
    prune: bool = False
    remote: str = "origin"
    branch: Optional[str] = None
    dry_run: bool = False
    parallel: int = 4
    verbose: bool = False


@dataclass
class ExecOptions:
    """驱动 `qpush exec` 的配置。"""

    cmd: str
    dry_run: bool = False
    parallel: int = 4
    verbose: bool = False
