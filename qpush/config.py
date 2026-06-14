"""Repository discovery: config file + directory scan, merged and de-duplicated."""

from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Iterable, List, Optional

from . import git
from .models import Repo


class ConfigError(Exception):
    """Raised for malformed config or resolution failures."""


# --- config file loading -----------------------------------------------------

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_comments(text: str) -> str:
    """Remove //, #, and /* */ comments while respecting JSON string literals.

    A naive regex would corrupt values like URLs ('https://...') or colors
    ('#fff'), so we walk the text tracking whether we're inside a string.
    """
    out = []
    i, n = 0, len(text)
    in_str = False
    escaped = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        # line comment: // or #
        if c == "#" or (c == "/" and i + 1 < n and text[i + 1] == "/"):
            while i < n and text[i] != "\n":
                i += 1
            continue
        # block comment: /* ... */
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _strip_comments_and_trailing_commas(text: str) -> str:
    """Allow //, #, /* */ comments and trailing commas in JSON config files."""
    text = _strip_comments(text)
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    return text


def load_config(path: str) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(_strip_comments_and_trailing_commas(raw))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"failed to parse config {path}: {exc}") from exc


# --- config file search ------------------------------------------------------

DEFAULT_CONFIG_NAMES = (".qpush.json", "qpush.json")


def find_config(start: Optional[str] = None) -> Optional[str]:
    """Search for a config file: cwd upward, then the user home."""
    start_dir = Path(start or os.getcwd()).resolve()
    for directory in (start_dir, *start_dir.parents):
        for name in DEFAULT_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    for name in DEFAULT_CONFIG_NAMES:
        home_candidate = Path.home() / name
        if home_candidate.is_file():
            return str(home_candidate)
    return None


# --- path resolution ---------------------------------------------------------

def resolve_path(path: str, base: Optional[str] = None) -> str:
    """Expand ~, make absolute, resolve relative to base (or cwd). Returns a
    normalized path; does NOT require the path to exist."""
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    base_dir = base or os.getcwd()
    return os.path.normpath(os.path.join(base_dir, expanded))


# --- directory scanning ------------------------------------------------------

def scan_for_repos(root: str, max_depth: int = 3) -> List[str]:
    """Walk `root` up to `max_depth` levels deep, returning the absolute paths
    of every git working tree found. Does not descend into found repos."""
    root = os.path.realpath(resolve_path(root))
    if not os.path.isdir(root):
        return []

    root_depth = root.rstrip(os.sep).count(os.sep)
    found: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames or ".git" in filenames:
            found.append(dirpath)
            dirnames[:] = []  # don't recurse into a discovered repo
            continue
        rel_depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
        if rel_depth >= max_depth:
            dirnames[:] = []

    return found


# --- merge + filter ----------------------------------------------------------

def _repo_entries_from_config(cfg: dict, base: str) -> Iterable[Repo]:
    for entry in cfg.get("repos", []) or []:
        if isinstance(entry, str):
            yield Repo(path=resolve_path(entry, base))
        elif isinstance(entry, dict):
            path = entry.get("path")
            if not path:
                raise ConfigError("repo entry missing 'path'")
            yield Repo(
                path=resolve_path(str(path), base),
                remote=entry.get("remote"),
                branch=entry.get("branch"),
                name=entry.get("name"),
            )
        else:
            raise ConfigError(f"invalid repo entry: {entry!r}")


def _match_any(name: str, path: str, patterns: List[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(path, pat):
            return True
    return False


class DiscoveryArgs:
    """Lightweight view over CLI args needed for discovery.

    Avoids importing argparse types into this module and keeps discover()
    callable from tests with a simple object.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        repos: Optional[List[str]] = None,
        scan: Optional[List[str]] = None,
        scan_depth: int = 3,
        remote: str = "origin",
        only: Optional[List[str]] = None,
        ignore: Optional[List[str]] = None,
        cwd: Optional[str] = None,
    ) -> None:
        self.config_path = config_path
        self.repos = repos or []
        self.scan = scan or []
        self.scan_depth = scan_depth
        self.remote = remote
        self.only = only or []
        self.ignore = ignore or []
        self.cwd = cwd or os.getcwd()


def discover(args: DiscoveryArgs) -> List[Repo]:
    """Collect repos from CLI, config, and scans; dedupe; filter; validate."""
    base = args.cwd
    collected: List[Repo] = []

    # 1. explicit --repos
    for p in args.repos:
        collected.append(Repo(path=resolve_path(p, base)))

    # 2. config file (explicit path, env, or searched)
    cfg_path = args.config_path or os.environ.get("QPUSH_CONFIG") or find_config(base)
    cfg: dict = {}
    if cfg_path:
        cfg = load_config(cfg_path)
        # paths in the config are relative to the config file's directory
        cfg_base = str(Path(cfg_path).resolve().parent)
        collected.extend(_repo_entries_from_config(cfg, cfg_base))

    default_remote = cfg.get("remote") or args.remote
    default_scan_depth = int(cfg.get("scanDepth") or args.scan_depth)

    # 3. scan directories: config "scan" + CLI --scan
    scan_dirs: List[str] = list(args.scan)
    cfg_scan = cfg.get("scan")
    if isinstance(cfg_scan, str):
        scan_dirs.append(cfg_scan)
    elif isinstance(cfg_scan, list):
        scan_dirs.extend(str(s) for s in cfg_scan)
    for sd in scan_dirs:
        for found_path in scan_for_repos(sd, max_depth=default_scan_depth):
            collected.append(Repo(path=found_path))

    # dedupe by real path, preserving first-seen order (keeps per-repo overrides)
    seen: dict = {}
    ordered: List[Repo] = []
    for repo in collected:
        key = os.path.realpath(repo.path)
        if key in seen:
            # merge: keep first, but fill in overrides from later entries
            existing = seen[key]
            existing.remote = existing.remote or repo.remote
            existing.branch = existing.branch or repo.branch
            continue
        repo.path = key
        seen[key] = repo
        ordered.append(repo)

    # apply default remote where none specified
    for repo in ordered:
        repo.remote = repo.remote or default_remote

    # validate: keep real repos, skip+warn others
    valid: List[Repo] = []
    for repo in ordered:
        if git.is_repo(repo.path):
            valid.append(repo)
        else:
            _warn(f"skipping {repo.name}: not a git repository ({repo.path})")

    # filter by --only / --ignore (match repo name or path)
    if args.only:
        valid = [r for r in valid if _match_any(r.name, r.path, args.only)]
    if args.ignore:
        valid = [r for r in valid if not _match_any(r.name, r.path, args.ignore)]

    return valid


def _warn(message: str) -> None:
    import sys

    print(f"warning: {message}", file=sys.stderr)
