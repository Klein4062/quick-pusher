"""Data models: Repo, RepoResult, Outcome, and the Options bag."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class Outcome(str, Enum):
    """Per-step result for a single repository."""

    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


class Repo:
    """A target git repository with optional per-repo overrides."""

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

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Repo(name={self.name!r}, path={self.path!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Repo) and self.path == other.path

    def __hash__(self) -> int:
        return hash(self.path)


@dataclass
class RepoResult:
    """The outcome of processing one repository."""

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
    # Generic fields used by pull/exec (and usable by any future command):
    # `note` overrides the one-line summary; `acted` marks a successful action;
    # `conflicts` marks a pull that left the tree mid-merge/rebase.
    acted: bool = False
    conflicts: bool = False
    note: Optional[str] = None
    # Collected log lines: (level, message). level in {info, warn, error, hint}.
    log: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def overall(self) -> Outcome:
        """One verdict across whatever this repo was asked to do."""
        if self.error or self.conflicts:
            return Outcome.FAILED
        if self.committed or self.pushed or self.acted:
            return Outcome.OK
        return Outcome.SKIPPED

    @property
    def steps_summary(self) -> str:
        """Human-readable one-liner for the progress line and summary."""
        if self.note:
            return self.note
        parts: List[str] = []
        if self.error:
            return self.error.splitlines()[0]
        if self.committed and self.pushed:
            parts.append(f"committed + pushed {self.branch or ''}".strip())
        elif self.committed:
            parts.append("committed (push skipped)")
        elif self.pushed:
            parts.append("pushed")
        elif self.stage_outcome == Outcome.SKIPPED and not self.dirty:
            parts.append("clean, nothing to commit")
        else:
            parts.append("no changes")
        if self.ahead and not self.pushed:
            parts.append(f"ahead {self.ahead}")
        if self.behind:
            parts.append(f"behind {self.behind}")
        return ", ".join(parts)


@dataclass
class Options:
    """Resolved settings driving the push engine."""

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
    """Settings driving `qpush pull`."""

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
    """Settings driving `qpush exec`."""

    cmd: str
    dry_run: bool = False
    parallel: int = 4
    verbose: bool = False
