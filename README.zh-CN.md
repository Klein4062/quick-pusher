# quick-pusher

[English](README.md) | **简体中文**

一个轻量、零依赖的命令行工具，用于**把同一次改动同时提交并推送到多个 git 仓库**。每个仓库各自提交自己工作区的改动，使用同一条提交信息，然后推送到各自的远端。

```sh
qpush "release: cut v2"            # 对所有发现的仓库:暂存 → 提交 → 推送
qpush pull                         # 拉取并合并远端更新(默认 rebase)
qpush exec -- npm test             # 在每个仓库根目录执行同一条命令
qpush status                       # 总览:分支 / 是否有改动 / 领先落后情况
qpush scan                         # 列出 qpush 会操作哪些仓库
```

## 为什么需要它

当你维护镜像仓库、一批相关的微服务、或同一个项目的多个 clone 时，给每个仓库都做同一处改动非常繁琐：`cd`、`git add`、`git commit -m …`、`git push`，周而复始。`qpush` 用一条命令并行完成这一切，并给出一份汇总。

## 安装

需要 Python 3.8+，以及 `PATH` 中的 `git`。

```sh
pipx install .          # 推荐——安装 `qpush` 命令
# 或者,不要隔离环境:
pip install --user .
# 或者,完全不安装:
python -m qpush "fix: typo"
```

## 快速上手

```sh
# 1. 生成一个配置文件,列出你的仓库(和/或要扫描的目录)
qpush init                       # 会在当前目录写一个 .qpush.json

# 2. 编辑它,然后预览 qpush 会看到哪些仓库
qpush scan
qpush status

# 3. 推送到所有仓库
qpush "feat: add health check"

# 进阶:同步上游更新,或跨仓库执行命令
qpush pull
qpush exec -- "npm test"
```

## 仓库是如何被发现的

`qpush` 会合并三个来源,按真实路径去重,并跳过任何非 git 仓库:

1. **命令行的 `--repos PATH`**(可重复)。
2. **配置文件**的 `repos` 列表(`.qpush.json`,从当前目录逐级向上查找,再到 `~/.qpush.json`;可用 `--config` 或环境变量 `$QPUSH_CONFIG` 覆盖)。
3. **`--scan DIR`** 和/或配置里的 `scan` 字段——递归扫描,最深 `scanDepth` 层(默认 3),寻找 `.git`。

### 配置文件格式

JSON 格式,允许 `//`、`#`、`/* */` 注释(字符串内部不会被改动,所以 URL 和 `"#fff"` 这类颜色值是安全的):

```jsonc
{
  // 显式仓库——可以是字符串,也可以是带覆盖项的对象
  "repos": [
    "~/projects/repo-a",
    { "path": "../repo-b", "remote": "github", "branch": "main" }
  ],
  // 要扫描的目录(字符串或列表)
  "scan": "~/projects",
  "scanDepth": 3,
  "remote": "origin",
  "parallel": 4
}
```

## 用法

```
qpush "<提交信息>" [选项]              # 默认:对所有仓库 暂存 + 提交 + 推送
qpush pull   [选项]                   # 拉取并合并远端更新
qpush exec   [选项] -- <命令...>       # 在每个仓库根目录执行一条 shell 命令
qpush status [选项]                   # 查看分支 / 是否有改动 / 领先落后
qpush scan   [选项]                   # 列出发现的仓库
qpush init                           # 写一个示例 .qpush.json
```

提交信息可以作为第一个参数,也可以用 `-m` 多次给出(会按段落拼接):

```sh
qpush "fix: handle empty input"
qpush -m "fix: handle empty input" -m "Closes #42."
```

### 选项

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--config PATH` | 自动查找 | 配置文件路径 |
| `--repos PATH` | — | 显式指定仓库路径(可重复) |
| `--scan DIR` | — | 要扫描的目录(可重复) |
| `--scan-depth N` | `3` | 最大扫描深度 |
| `--remote NAME` | `origin` | 默认远端 |
| `--branch NAME` | 当前分支 | 要推送的分支 |
| `--only GLOB` | — | 只处理匹配的仓库(按名称或路径;可重复) |
| `--ignore GLOB` | — | 排除匹配的仓库(可重复) |
| `-j, --parallel N` | `4` | 最大并发仓库数 |
| `-v, --verbose` | 关 | 显示每个仓库的详细日志 |
| `--color {auto,always,never}` | `auto` | 是否启用彩色输出 |

推送专用参数:

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--no-add` | 开 | 不暂存改动 |
| `--no-commit` | 开 | 不提交 |
| `--no-push` | 开 | 不推送 |
| `--force` | 关 | 强制推送(**带租约**,即 `--force-with-lease`) |
| `--tags` | 关 | 同时推送标签 |
| `--dry-run` | 关 | 只演示会发生什么,不做任何修改 |

### 示例

```sh
# 只推送名字匹配某 glob 的服务
qpush "chore: bump deps" --only 'svc-*'

# 各处都提交,但先不推送(留待 review)
qpush "wip" --no-push

# 不提交新内容,只把已有的未推送提交推上去
qpush --no-add --no-commit "n/a"

# 预演
qpush --dry-run "feat: x" --scan ~/work
```

### `pull` —— 拉取并合并

把每个仓库与远端同步。默认用 rebase。

```sh
qpush pull                 # rebase 到 origin 之上(默认)
qpush pull --merge         # 用 merge 代替 rebase
qpush pull --ff-only       # 只允许快进,否则失败
qpush pull --prune         # 同时清理已删除的远端分支
qpush pull --dry-run
```

每个仓库的结果:`updated`(HEAD 移动了)、`up to date`(已是最新)、`skipped`(游离 HEAD / 没有远端 / **工作区有改动**——往脏工作区里 pull 太容易冲突,所以这类仓库会被跳过,并提示你先提交或 stash),或 `conflicts`(对该仓库计为失败;需手动解决冲突并 `git rebase --continue` / `git merge --continue`)。

### `exec` —— 在每个仓库根目录执行命令

以每个仓库的工作目录为 `cwd`,执行给定的 shell 命令。把命令放在 `--` 之后,避免与选项混淆:

```sh
qpush exec -- npm test
qpush exec -- git log -1 --oneline
qpush exec -- "rm -f *.log"
qpush exec -v -- pwd        # -v 打印每个仓库捕获到的输出
qpush exec --dry-run -- npm test
```

每个仓库的结果:`ok`(退出码为 0)或 `exit <N>`(非零,计为失败)。失败时会显示捕获的 stdout/stderr;加 `-v` 则对所有仓库都显示。命令经由 shell 执行,因此管道和重定向都可用——相应地,你要对自己执行的命令负责。

## 行为说明

- **一条共享信息,各自独立提交。** 每个仓库暂存*自己*工作区的改动(`git add --all`),用你给的提交信息提交。各仓库改动内容不同,共享的只有提交信息。
- **干净的仓库会被跳过。** 如果某仓库没有可提交的内容、也不领先于远端,推送步骤也会跳过。
- **游离 HEAD / 无远端** → 该仓库的推送被跳过并给出警告(提交仍然会发生)。
- **推送被拒(非快进)** → 对该仓库计为失败,并提示你 pull 或用 `--force`。其它仓库继续。
- **`--force` 用的是 `--force-with-lease`**——它不会覆盖你本地还不知道的提交。若确实要覆盖,请先 `fetch`。
- **凭据**由你的环境负责。推送时设置了 `GIT_TERMINAL_PROMPT=0`,所以缺少凭据时会快速失败,而不是让整批任务挂起。
- **退出码**:任意仓库失败为 `1`,用法/配置错误为 `2`,否则为 `0`。

## 测试

```sh
PYTHONPATH=. python -m unittest discover -s tests -t .
```

测试套件会构建真实的 git 工作仓库,并配以本地 bare 远端,因此 `stage → commit → push` 会被端到端地真正执行。

## 项目结构

```
qpush/
  cli.py       # argparse 参数解析、子命令分发、并发执行器、入口
  engine.py    # 单仓库的 stage/commit/push 编排(以及 pull/exec)
  git.py       # 对 git 的 subprocess 封装
  config.py    # 仓库发现(配置 + 扫描)、JSONC 加载
  models.py    # Repo、RepoResult、Outcome、Options 等数据模型
  ui.py        # 颜色、进度行、汇总报告
tests/         # unittest 测试套件 + 辅助函数(临时 git 沙箱)
```

## 许可证

MIT
