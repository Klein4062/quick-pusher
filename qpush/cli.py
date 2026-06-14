"""命令行界面:参数解析、子命令分发、并发执行器。

`qpush "<message>" [flags]` 是默认动作(对所有发现的仓库提交并推送)。
`qpush status|scan|init|pull|exec` 是其它子命令,按第一个位置参数识别。
让 push 作为顶层默认动作,意味着各 flag 可以出现在信息前后任意位置。
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
  // 要操作的仓库(路径字符串,或带覆盖项的对象)。
  "repos": [
    "~/projects/repo-a",
    { "path": "../repo-b", "remote": "origin", "branch": "main" }
  ],
  // 要扫描的 git 仓库目录(字符串或列表)。
  "scan": "~/projects",
  "scanDepth": 3,
  "remote": "origin",
  "parallel": 4
}
"""

PROG_DESCRIPTION = (
    "多仓库同步提交与推送。对每个发现的仓库执行暂存,用【同一条】信息分别提交,再推送。\n"
    "其它子命令:`qpush status`(总览)、`qpush scan`(列出仓库)、"
    "`qpush init`(写配置)、`qpush pull`(拉取)、`qpush exec`(执行命令)。"
)


# --- 中文本地化的 argparse ---------------------------------------------------

class _CnFormatter(argparse.HelpFormatter):
    """把 argparse 帮助输出中的 usage:/分组标题本地化为中文。"""

    _HEADINGS = {
        "positional arguments": "位置参数",
        "optional arguments": "可选参数",  # Python 3.9
        "options": "选项",                # Python 3.10+
    }

    def add_usage(self, usage, actions, groups, prefix=None):
        if prefix is None:
            prefix = "用法:"
        return super().add_usage(usage, actions, groups, prefix)

    def start_section(self, heading):
        super().start_section(self._HEADINGS.get(heading, heading))


class _CnParser(argparse.ArgumentParser):
    """使用中文 formatter,并把非法输入时的 'error:' 改为 '错误:'。"""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", _CnFormatter)
        super().__init__(*args, **kwargs)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}:错误:{message}\n")


def _new_parser(prog: str, description: str) -> _CnParser:
    return _CnParser(prog=prog, description=description, add_help=False)


def _add_help(p: argparse.ArgumentParser) -> None:
    p.add_argument("-h", "--help", action="help", help="显示此帮助信息并退出")


# --- 参数解析 -----------------------------------------------------------------

def _add_common(p: argparse.ArgumentParser) -> None:
    """所有子命令共享的选项(仓库发现 + 输出)。"""
    p.add_argument("--config", metavar="PATH", help="qpush 配置文件路径")
    p.add_argument("--repos", action="append", metavar="PATH", default=[],
                   help="显式指定的仓库路径(可重复)")
    p.add_argument("--scan", action="append", metavar="DIR", default=[],
                   help="要扫描 git 仓库的目录(可重复)")
    p.add_argument("--scan-depth", type=int, default=3, help="最大扫描深度(默认 3)")
    p.add_argument("--remote", default="origin", help="默认远端名(默认 origin)")
    p.add_argument("--branch", help="要推送的分支(默认:当前分支)")
    p.add_argument("--only", action="append", default=[], metavar="GLOB",
                   help="只处理名称/路径匹配的仓库(可重复)")
    p.add_argument("--ignore", action="append", default=[], metavar="GLOB",
                   help="排除名称/路径匹配的仓库(可重复)")
    p.add_argument("-j", "--parallel", type=int, default=4, help="最大并发仓库数(默认 4)")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="是否启用彩色输出(默认 auto)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="显示每个仓库的详细日志")


def build_push_parser() -> argparse.ArgumentParser:
    p = _new_parser("qpush", PROG_DESCRIPTION)
    p.add_argument("--version", action="version", version=f"qpush {__version__}",
                   help="显示版本号并退出")
    _add_common(p)
    p.add_argument("message", nargs="?", help="提交信息")
    p.add_argument("-m", "--message", action="append", dest="message_flags", metavar="MSG",
                   help="提交信息(可重复;按段落拼接)")
    p.add_argument("--no-add", dest="add", action="store_false",
                   help="不暂存改动(默认会暂存)")
    p.add_argument("--no-commit", dest="commit", action="store_false",
                   help="不提交(默认会提交)")
    p.add_argument("--no-push", dest="push", action="store_false",
                   help="不推送(默认会推送)")
    p.add_argument("--force", action="store_true", help="带租约强制推送(--force-with-lease,谨慎使用)")
    p.add_argument("--tags", action="store_true", help="同时推送标签")
    p.add_argument("--dry-run", action="store_true",
                   help="只演示会发生什么,不做任何修改")
    p.set_defaults(add=True, commit=True, push=True)
    _add_help(p)
    return p


def build_status_parser() -> argparse.ArgumentParser:
    p = _new_parser("qpush status",
                    description="查看每个仓库的分支 / 是否有改动 / 领先落后情况。")
    _add_common(p)
    _add_help(p)
    return p


def build_scan_parser() -> argparse.ArgumentParser:
    p = _new_parser("qpush scan",
                    description="列出 qpush 会操作的仓库。")
    _add_common(p)
    _add_help(p)
    return p


def build_pull_parser() -> argparse.ArgumentParser:
    p = _new_parser("qpush pull",
                    description="在每个仓库中拉取并合并远端更新(默认 rebase)。")
    _add_common(p)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--rebase", dest="rebase", action="store_true",
                      help="把本地提交 rebase 到远端之上(默认)")
    mode.add_argument("--merge", dest="rebase", action="store_false",
                      help="用 merge 代替 rebase")
    mode.add_argument("--ff-only", dest="ff_only", action="store_true",
                      help="只允许快进,否则失败")
    p.add_argument("--prune", action="store_true", help="同时清理已删除的远端分支")
    p.add_argument("--dry-run", action="store_true", help="只演示会拉取什么,不做修改")
    p.set_defaults(rebase=True, ff_only=False)
    _add_help(p)
    return p


def build_exec_parser() -> argparse.ArgumentParser:
    p = _new_parser(
        "qpush exec",
        description=("在每个仓库根目录执行一条 shell 命令。"
                     "命令放在 `--` 之后,例如 `qpush exec -- npm test`。"),
    )
    _add_common(p)
    p.add_argument("--dry-run", action="store_true", help="只显示命令,不执行")
    _add_help(p)
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


# --- 子命令 -------------------------------------------------------------------

def cmd_scan(repos: List[Repo]) -> int:
    ui.print_header(f"发现 {len(repos)} 个仓库")
    name_w = max((len(r.name) for r in repos), default=8)
    for repo in repos:
        print(f"  {repo.name:<{name_w}}  {repo.path}")
    return 0


def cmd_init() -> int:
    target = os.path.join(os.getcwd(), ".qpush.json")
    if os.path.exists(target):
        print(f"{target} 已存在,保持不变。", file=sys.stderr)
        return 1
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(EXAMPLE_CONFIG)
    print(f"已写入示例配置:{target}")
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
    label = "预演 —— " if opts.dry_run else ""
    ui.print_header(
        f"{label}处理 {len(repos)} 个仓库  " + ui.dim(f"信息={opts.message!r}")
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
    label = "预演 —— " if opts.dry_run else ""
    ui.print_header(f"{label}拉取 {len(repos)} 个仓库")
    results = _run_parallel(repos, lambda r: engine.process_pull(r, opts), parallel=opts.parallel)
    ui.print_summary(results, verbose=opts.verbose)
    failed = sum(1 for r in results if r.overall.value == "failed")
    return 1 if failed else 0


def cmd_exec(repos: List[Repo], cmd: str, args) -> int:
    from .models import ExecOptions
    opts = ExecOptions(cmd=cmd, dry_run=args.dry_run,
                       parallel=max(1, args.parallel), verbose=args.verbose)
    label = "预演 —— " if opts.dry_run else ""
    ui.print_header(f"{label}exec [{len(repos)} 个仓库]: {opts.cmd}")
    results = _run_parallel(repos, lambda r: engine.process_exec(r, opts), parallel=opts.parallel)
    ui.print_summary(results, verbose=opts.verbose)
    failed = sum(1 for r in results if r.overall.value == "failed")
    return 1 if failed else 0


def _run_parallel(repos: List[Repo], worker, parallel: int, show_progress: bool = True) -> List[RepoResult]:
    """并发地对每个仓库执行 `worker(repo)`,并保持输入顺序。"""
    results: List[Optional[RepoResult]] = [None] * len(repos)
    workers = max(1, min(parallel, len(repos) or 1))

    def _do(idx: int, repo: Repo) -> RepoResult:
        try:
            return worker(repo)
        except Exception as exc:  # 不让单个仓库拖垮整批任务
            res = RepoResult(repo=repo, error=f"意外错误:{exc}")
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
        print("\n已中断", file=sys.stderr)
        for i, repo in enumerate(repos):
            if results[i] is None:
                results[i] = RepoResult(repo=repo, error="已中断")
        return [r for r in results if r is not None]

    return [r for r in results if r is not None]


def _split_for_exec(rest: List[str]):
    """按 `--` 把 exec 的 argv 拆分为 (qpush 选项 token, 命令 token)。

    如果没有显式的 `--`,则回退到第一个位置参数(因此 `qpush exec git status`
    也能工作)。当只给出命令时,返回 ([], 命令 token)。
    """
    if "--" in rest:
        i = rest.index("--")
        return rest[:i], rest[i + 1:]
    first, idx = _first_positional(rest)
    if idx < 0:
        return rest, []
    return rest[:idx], rest[idx:]


# --- 入口 ---------------------------------------------------------------------

# 会消耗后一个 token 作为其取值的选项。用于查找第一个位置参数的预扫描,
# 以便即使全局选项出现在子命令之前(如 `qpush --color never scan`)也能识别出子命令。
_VALUE_OPTS = {
    "--config", "--repos", "--scan", "--scan-depth", "--remote", "--branch",
    "--only", "--ignore", "-j", "--color", "-m", "--message",
}


def _first_positional(argv: List[str]):
    """返回第一个位置参数的 (token, index);没有则返回 (None, -1)。

    会跳过选项 flag 及其作为取值的 token,并在遇到 `--` 时停下。
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
            i += 1  # 布尔 flag
            continue
        return tok, i
    return None, -1

def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 子命令是第一个位置参数,因此全局选项可以出现在它之前(如 `qpush --color never scan`)。
    # 如果没有匹配的子命令,则默认为 push。
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
        print("错误:exec 需要一条命令。用法:qpush exec -- <命令...>", file=sys.stderr)
        return 2

    assert args is not None
    ui.configure({"auto": None, "always": True, "never": False}[args.color])

    try:
        repos = config.discover(_discovery_args(args))
    except config.ConfigError as exc:
        print(f"错误:{exc}", file=sys.stderr)
        return 2

    if not repos:
        print("未找到任何仓库。", file=sys.stderr)
        print("请通过 .qpush.json 配置,或使用 --repos / --scan。", file=sys.stderr)
        return 1

    if command == "scan":
        return cmd_scan(repos)
    if command == "status":
        return cmd_status(repos)
    if command == "pull":
        return cmd_pull(repos, args)
    if command == "exec":
        return cmd_exec(repos, exec_cmd, args)

    # 默认:push
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
            "错误:提交时需要一条提交信息。\n"
            "  例如  qpush \"fix: 处理空输入\"\n"
            "        qpush -m \"fix: 处理空输入\"",
            file=sys.stderr,
        )
        return 2
    return cmd_push(repos, opts)


if __name__ == "__main__":
    sys.exit(main())
