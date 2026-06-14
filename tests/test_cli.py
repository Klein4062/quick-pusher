"""命令行接口的端到端测试:scan/status/pull/exec 以及默认的 push 主流程。"""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.helpers import append_file, git, make_repo

from qpush import cli


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self._cwd = os.getcwd()
        os.chdir(self.base)  # so config discovery + cwd-relative paths resolve here
        make_repo(self.base, "alpha")
        make_repo(self.base, "beta")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def repos(self):
        return [str(self.base / "alpha"), str(self.base / "beta")]

    def repo_flags(self):
        flags = []
        for r in self.repos():
            flags += ["--repos", r]
        return flags

    def last_msg(self, name):
        return git(self.base / name, "log", "-1", "--pretty=%B").stdout.strip()

    def test_push_commits_and_pushes_all(self):
        append_file(self.base / "alpha", "a.txt", "x")
        append_file(self.base / "beta", "b.txt", "y")
        code, out, err = run_cli(["--color", "never", "sync: update", *self.repo_flags()])
        self.assertEqual(code, 0, err)
        self.assertIn("sync: update", self.last_msg("alpha"))
        self.assertIn("sync: update", self.last_msg("beta"))
        # 两者都已推送:origin/main == HEAD
        for name in ("alpha", "beta"):
            r = self.base / name
            self.assertEqual(
                git(r, "rev-parse", "origin/main").stdout.strip(),
                git(r, "rev-parse", "HEAD").stdout.strip(),
            )
        self.assertIn("2 个仓库: 2 成功", out.replace("\n", " "))

    def test_flags_before_message_works(self):
        append_file(self.base / "alpha", "a.txt", "x")
        code, out, err = run_cli(
            ["--color", "never", "--repos", str(self.base / "alpha"), "msg with flags first"]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last_msg("alpha"), "msg with flags first")

    def test_message_flag_joined(self):
        append_file(self.base / "alpha", "a.txt", "x")
        code, _, err = run_cli(
            ["--color", "never", "-m", "title", "-m", "body", "--repos", str(self.base / "alpha")]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last_msg("alpha"), "title\n\nbody")

    def test_missing_message_errors(self):
        append_file(self.base / "alpha", "a.txt", "x")
        code, out, err = run_cli(["--color", "never", "--repos", str(self.base / "alpha")])
        self.assertEqual(code, 2)
        self.assertIn("需要一条提交信息", err)

    def test_no_repos_returns_1(self):
        code, out, err = run_cli(["--color", "never", "msg"])
        self.assertEqual(code, 1)
        self.assertIn("未找到任何仓库", err)

    def test_scan_command_lists_repos(self):
        code, out, err = run_cli(["scan", "--color", "never", *self.repo_flags()])
        self.assertEqual(code, 0, err)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_subcommand_after_global_flag_is_not_treated_as_message(self):
        # `qpush --color never scan` 必须执行 scan,而不是把 "scan" 当作提交信息去 push。
        append_file(self.base / "alpha", "a.txt", "x")
        code, out, err = run_cli(["--color", "never", "scan", *self.repo_flags()])
        self.assertEqual(code, 0, err)
        self.assertIn("发现", out)
        # 关键:没有任何仓库被提交
        self.assertNotIn("已提交", out)
        self.assertNotIn("alpha", git(self.base / "alpha", "log", "-1", "--pretty=%B").stdout)

    def test_status_command_runs(self):
        code, out, err = run_cli(["status", "--color", "never", *self.repo_flags()])
        self.assertEqual(code, 0, err)
        self.assertIn("干净", out)

    def test_dry_run_makes_no_changes(self):
        repo = self.base / "alpha"
        append_file(repo, "a.txt", "x")
        head_before = git(repo, "rev-parse", "HEAD").stdout.strip()
        code, out, err = run_cli(
            ["--color", "never", "--dry-run", "msg", "--repos", str(repo)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("预演", out)
        self.assertEqual(head_before, git(repo, "rev-parse", "HEAD").stdout.strip())

    def test_uses_config_file_in_cwd(self):
        cfg = self.base / ".qpush.json"
        cfg.write_text(json.dumps({"repos": [str(self.base / "alpha")]}))
        append_file(self.base / "alpha", "a.txt", "x")
        code, out, err = run_cli(["--color", "never", "from config"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last_msg("alpha"), "from config")

    def test_only_filter(self):
        append_file(self.base / "alpha", "a.txt", "x")
        append_file(self.base / "beta", "b.txt", "y")
        code, out, err = run_cli(
            ["--color", "never", "msg", "--scan", str(self.base), "--scan-depth", "1",
             "--only", "alpha"]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("alpha", out)
        self.assertNotIn("beta", out.split("summary")[0])  # beta not processed
        self.assertEqual(self.last_msg("alpha"), "msg")

    def test_pull_up_to_date_via_cli(self):
        code, out, err = run_cli(["pull", "--color", "never", *self.repo_flags()])
        self.assertEqual(code, 0, err)
        self.assertIn("已是最新", out)

    def test_pull_updates_via_cli(self):
        # 从一个 clone 推进 alpha 的 origin,使 alpha 落后于远端
        import subprocess
        origin = self.base / "alpha.origin.git"
        clone = self.base / "advclone"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
        git(clone, "config", "user.email", "c@e.com")
        git(clone, "config", "user.name", "C")
        append_file(clone, "new.txt", "x")
        git(clone, "add", "-A")
        git(clone, "commit", "-qm", "advance")
        git(clone, "push", "-q", "origin", "main")
        code, out, err = run_cli(["pull", "--color", "never", "--repos", str(self.base / "alpha")])
        self.assertEqual(code, 0, err)
        self.assertIn("已更新", out)

    def test_exec_via_cli(self):
        code, out, err = run_cli(
            ["exec", "--color", "never", *self.repo_flags(), "--", "echo", "hi"]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("成功", out)

    def test_exec_nonzero_exits_nonzero(self):
        code, out, err = run_cli(
            ["exec", "--color", "never", *self.repo_flags(), "--", "false"]
        )
        self.assertEqual(code, 1, err)

    def test_exec_needs_command(self):
        code, out, err = run_cli(["exec"])
        self.assertEqual(code, 2)
        self.assertIn("需要一条命令", err)


if __name__ == "__main__":
    unittest.main()
