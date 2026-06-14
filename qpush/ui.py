"""输出格式化:ANSI 颜色、进度行、以及汇总报告。"""

from __future__ import annotations

import os
import sys
from typing import List

from .models import Outcome, RepoResult

# ANSI 转义码
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"

_USE_COLOR = True  # 由 configure() 设置


def configure(use_color) -> None:
    global _USE_COLOR
    if use_color is True:
        _USE_COLOR = True
    elif use_color is False:
        _USE_COLOR = False
    else:  # auto
        _USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _USE_COLOR else text


def bold(t: str) -> str:
    return _c(_BOLD, t)


def dim(t: str) -> str:
    return _c(_DIM, t)


def green(t: str) -> str:
    return _c(_GREEN, t)


def red(t: str) -> str:
    return _c(_RED, t)


def yellow(t: str) -> str:
    return _c(_YELLOW, t)


def cyan(t: str) -> str:
    return _c(_CYAN, t)


def symbol_for(outcome: Outcome) -> str:
    if outcome == Outcome.OK:
        return green("✓")
    if outcome == Outcome.FAILED:
        return red("✗")
    return yellow("•")


def branch_label(res: RepoResult) -> str:
    if res.detached:
        return dim("detached")
    return res.branch or dim("-")


def print_header(title: str) -> None:
    print()
    print(bold(title))


def print_progress_line(res: RepoResult) -> None:
    """每个仓库完成时输出的一行紧凑信息(实时反馈)。"""
    outcome = res.overall
    sym = symbol_for(outcome)
    branch = branch_label(res)
    detail = res.steps_summary
    if outcome == Outcome.FAILED:
        detail = red(detail)
    elif outcome == Outcome.SKIPPED:
        detail = dim(detail)
    print(f"  {sym} {res.repo.name:<24} {branch:<16} {detail}")


def print_summary(results: List[RepoResult], verbose: bool = False) -> None:
    ok = sum(1 for r in results if r.overall == Outcome.OK)
    skipped = sum(1 for r in results if r.overall == Outcome.SKIPPED)
    failed = sum(1 for r in results if r.overall == Outcome.FAILED)

    # 失败仓库(始终)或所有仓库(verbose)的详细日志。
    show_details = [r for r in results if verbose or r.overall == Outcome.FAILED]
    if show_details:
        print_header("details")
        for res in show_details:
            print(f"  {bold(res.repo.name)}  {dim(res.repo.path)}")
            if res.error:
                for line in res.error.splitlines():
                    print(f"    {red(line)}")
            for level, msg in res.log:
                if level == "error":
                    print(f"    {red('error')}: {msg}")
                elif level == "warn":
                    print(f"    {yellow('warn')}: {msg}")
                elif level == "hint":
                    print(f"    {cyan('hint')}: {msg}")
                else:
                    print(f"    {dim('·')} {msg}")

    print_header("summary")
    total = len(results)
    parts = []
    if ok:
        parts.append(green(f"{ok} ok"))
    if skipped:
        parts.append(yellow(f"{skipped} skipped"))
    if failed:
        parts.append(red(f"{failed} failed"))
    tally = ", ".join(parts) if parts else "no repos"
    print(f"  {bold(str(total))} repo{'s' if total != 1 else ''}: {tally}")


def print_status_table(results: List[RepoResult]) -> None:
    print_header("status")
    name_w = max((len(r.repo.name) for r in results), default=8)
    for res in results:
        branch = branch_label(res)
        flags = []
        if res.detached:
            flags.append(yellow("detached"))
        if res.dirty:
            flags.append(red("dirty"))
        if res.ahead:
            flags.append(green(f"↑{res.ahead}"))
        if res.behind:
            flags.append(yellow(f"↓{res.behind}"))
        flag_str = " ".join(flags) if flags else dim("clean")
        print(f"  {res.repo.name:<{name_w}}  {branch:<14}  {flag_str}")
    print_summary(results)
