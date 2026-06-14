"""仓库发现:配置文件 + 目录扫描,合并后按真实路径去重。"""

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
    """配置文件格式错误或路径解析失败时抛出。"""


# --- 配置文件加载 -------------------------------------------------------------

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_comments(text: str) -> str:
    """移除 //、#、/* */ 注释,同时尊重 JSON 字符串字面量。

    朴素的正则会破坏诸如 URL('https://...')或颜色('#fff')这样的值,
    因此这里逐字符扫描,跟踪是否处于字符串内部。
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
        # 行注释:// 或 #
        if c == "#" or (c == "/" and i + 1 < n and text[i + 1] == "/"):
            while i < n and text[i] != "\n":
                i += 1
            continue
        # 块注释:/* ... */
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
    """让 JSON 配置文件可以包含 //、#、/* */ 注释以及尾随逗号。"""
    text = _strip_comments(text)
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    return text


def load_config(path: str) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(_strip_comments_and_trailing_commas(raw))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"failed to parse config {path}: {exc}") from exc


# --- 配置文件查找 -------------------------------------------------------------

DEFAULT_CONFIG_NAMES = (".qpush.json", "qpush.json")


def find_config(start: Optional[str] = None) -> Optional[str]:
    """查找配置文件:从当前目录逐级向上,再到用户主目录。"""
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


# --- 路径解析 -----------------------------------------------------------------

def resolve_path(path: str, base: Optional[str] = None) -> str:
    """展开 ~、转为绝对路径、相对 base(或当前工作目录)解析。
    返回规范化的路径;不要求该路径真实存在。"""
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    base_dir = base or os.getcwd()
    return os.path.normpath(os.path.join(base_dir, expanded))


# --- 目录扫描 -----------------------------------------------------------------

def scan_for_repos(root: str, max_depth: int = 3) -> List[str]:
    """从 `root` 开始向下最多扫描 `max_depth` 层,返回找到的每个 git 工作树的
    绝对路径。不会深入到已发现的仓库内部。"""
    root = os.path.realpath(resolve_path(root))
    if not os.path.isdir(root):
        return []

    root_depth = root.rstrip(os.sep).count(os.sep)
    found: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames or ".git" in filenames:
            found.append(dirpath)
            dirnames[:] = []  # 不再深入已发现的仓库
            continue
        rel_depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
        if rel_depth >= max_depth:
            dirnames[:] = []

    return found


# --- 合并与过滤 ---------------------------------------------------------------

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
    """发现流程所需的 CLI 参数的轻量视图。

    避免把 argparse 类型引入本模块,也便于测试用一个简单对象直接调用 discover()。
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
    """从命令行、配置文件和扫描目录收集仓库;去重、过滤、校验。"""
    base = args.cwd
    collected: List[Repo] = []

    # 1. 命令行显式指定的 --repos
    for p in args.repos:
        collected.append(Repo(path=resolve_path(p, base)))

    # 2. 配置文件(显式路径、环境变量,或自动查找)
    cfg_path = args.config_path or os.environ.get("QPUSH_CONFIG") or find_config(base)
    cfg: dict = {}
    if cfg_path:
        cfg = load_config(cfg_path)
        # 配置中的相对路径以配置文件所在目录为基准解析
        cfg_base = str(Path(cfg_path).resolve().parent)
        collected.extend(_repo_entries_from_config(cfg, cfg_base))

    default_remote = cfg.get("remote") or args.remote
    default_scan_depth = int(cfg.get("scanDepth") or args.scan_depth)

    # 3. 扫描目录:配置的 "scan" + 命令行 --scan
    scan_dirs: List[str] = list(args.scan)
    cfg_scan = cfg.get("scan")
    if isinstance(cfg_scan, str):
        scan_dirs.append(cfg_scan)
    elif isinstance(cfg_scan, list):
        scan_dirs.extend(str(s) for s in cfg_scan)
    for sd in scan_dirs:
        for found_path in scan_for_repos(sd, max_depth=default_scan_depth):
            collected.append(Repo(path=found_path))

    # 按真实路径去重,保持首次出现的顺序(从而保留针对单个仓库的覆盖项)
    seen: dict = {}
    ordered: List[Repo] = []
    for repo in collected:
        key = os.path.realpath(repo.path)
        if key in seen:
            # 合并:保留第一个,但用后续条目补全覆盖项
            existing = seen[key]
            existing.remote = existing.remote or repo.remote
            existing.branch = existing.branch or repo.branch
            continue
        repo.path = key
        seen[key] = repo
        ordered.append(repo)

    # 对未指定 remote 的仓库应用默认 remote
    for repo in ordered:
        repo.remote = repo.remote or default_remote

    # 校验:保留真正的 git 仓库,其余跳过并告警
    valid: List[Repo] = []
    for repo in ordered:
        if git.is_repo(repo.path):
            valid.append(repo)
        else:
            _warn(f"skipping {repo.name}: not a git repository ({repo.path})")

    # 按 --only / --ignore 过滤(匹配仓库名或路径)
    if args.only:
        valid = [r for r in valid if _match_any(r.name, r.path, args.only)]
    if args.ignore:
        valid = [r for r in valid if not _match_any(r.name, r.path, args.ignore)]

    return valid


def _warn(message: str) -> None:
    import sys

    print(f"warning: {message}", file=sys.stderr)
