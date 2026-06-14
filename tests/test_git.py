"""git 子进程封装的测试:仓库判定、分支/上游、暂存、提交、推送、领先落后。"""

import subprocess
import unittest
from pathlib import Path

from tests.helpers import append_file, git, make_repo

from qpush import git as qgit


class GitWrappersTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo, self.origin = make_repo(self.base, "r")

    def tearDown(self):
        self._tmp.cleanup()

    def test_is_repo_and_toplevel(self):
        self.assertTrue(qgit.is_repo(str(self.repo)))
        self.assertFalse(qgit.is_repo(str(self.base)))
        self.assertEqual(Path(qgit.toplevel(str(self.repo))).resolve(), self.repo.resolve())

    def test_branch_and_upstream(self):
        self.assertEqual(qgit.current_branch(str(self.repo)), "main")
        self.assertEqual(qgit.upstream(str(self.repo)), "origin/main")

    def test_dirty_and_staged(self):
        self.assertFalse(qgit.is_dirty(str(self.repo)))
        append_file(self.repo, "README.md", "change")
        self.assertTrue(qgit.is_dirty(str(self.repo)))
        self.assertFalse(qgit.has_staged_changes(str(self.repo)))  # not staged yet
        qgit.stage_all(str(self.repo))
        self.assertTrue(qgit.has_staged_changes(str(self.repo)))

    def test_stage_commit_push_roundtrip(self):
        append_file(self.repo, "f.txt", "x")
        code, _, _ = qgit.stage_all(str(self.repo))
        self.assertEqual(code, 0)
        code, out, _ = qgit.commit(str(self.repo), "change f")
        self.assertEqual(code, 0)
        code, _, err = qgit.push(str(self.repo), "origin", "main")
        self.assertEqual(code, 0)
        # origin 已前进到我们的提交
        origin_main = git(self.repo, "rev-parse", "origin/main").stdout.strip()
        head = qgit.run(str(self.repo), ["rev-parse", "HEAD"])[1].strip()
        self.assertEqual(origin_main, head)

    def test_has_remote(self):
        self.assertTrue(qgit.has_remote(str(self.repo), "origin"))
        self.assertFalse(qgit.has_remote(str(self.repo), "nope"))

    def test_ahead_behind(self):
        # 已是最新,推送无变化 -> 0/0
        self.assertEqual(qgit.ahead_behind(str(self.repo), "origin/main"), (0, 0))
        append_file(self.repo, "README.md", "x")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "ahead")
        self.assertEqual(qgit.ahead_behind(str(self.repo), "origin/main"), (1, 0))

    def test_push_missing_remote_fails_cleanly(self):
        code, _, err = qgit.push(str(self.repo), "nope", "main")
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
