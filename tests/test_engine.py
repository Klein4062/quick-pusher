"""push / pull / exec 引擎的测试:覆盖提交、推送、跳过、冲突、dry-run 等场景。"""

import subprocess
import unittest
from pathlib import Path

from tests.helpers import append_file, git, make_repo, write_file

from qpush import engine
from qpush.models import ExecOptions, Options, Outcome, PullOptions, Repo


def opts(**kw) -> Options:
    base = dict(message="sync: update", add=True, commit=True, push=True,
                remote="origin", branch=None, force=False, tags=False,
                dry_run=False, parallel=1, verbose=False)
    base.update(kw)
    return Options(**base)


class EngineTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo, self.origin = make_repo(self.base, "r")

    def tearDown(self):
        self._tmp.cleanup()

    def repo_obj(self) -> Repo:
        return Repo(path=str(self.repo))

    def test_commit_and_push_dirty(self):
        append_file(self.repo, "a.txt", "change")
        res = engine.process_repo(self.repo_obj(), opts())
        self.assertTrue(res.committed)
        self.assertTrue(res.pushed)
        self.assertEqual(res.overall, Outcome.OK)
        # origin 现在与本地 HEAD 一致
        self.assertEqual(
            git(self.repo, "rev-parse", "origin/main").stdout.strip(),
            git(self.repo, "rev-parse", "HEAD").stdout.strip(),
        )

    def test_clean_repo_skips_commit(self):
        res = engine.process_repo(self.repo_obj(), opts())
        self.assertFalse(res.committed)
        self.assertFalse(res.pushed)
        self.assertEqual(res.commit_outcome, Outcome.SKIPPED)
        self.assertEqual(res.overall, Outcome.SKIPPED)

    def test_no_push_still_commits(self):
        append_file(self.repo, "a.txt", "x")
        res = engine.process_repo(self.repo_obj(), opts(push=False))
        self.assertTrue(res.committed)
        self.assertFalse(res.pushed)
        self.assertEqual(res.push_outcome, Outcome.SKIPPED)

    def test_dry_run_makes_no_changes(self):
        append_file(self.repo, "a.txt", "x")
        head_before = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        res = engine.process_repo(self.repo_obj(), opts(dry_run=True))
        self.assertFalse(res.committed)
        self.assertFalse(res.pushed)
        head_after = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head_before, head_after)
        # 仍然是脏的:既没有暂存,也没有提交
        from qpush import git as qgit
        self.assertTrue(qgit.is_dirty(str(self.repo)))

    def test_detached_head_skips_push(self):
        append_file(self.repo, "a.txt", "x")
        git(self.repo, "checkout", "-q", "--detach")
        res = engine.process_repo(self.repo_obj(), opts())
        self.assertTrue(res.committed)  # commit on detached works
        self.assertFalse(res.pushed)
        self.assertEqual(res.push_outcome, Outcome.SKIPPED)
        self.assertTrue(res.detached)

    def test_missing_remote_skips_push(self):
        repo_noremote, _ = make_repo(self.base, "noremote", with_remote=False)
        append_file(repo_noremote, "a.txt", "x")
        res = engine.process_repo(Repo(path=str(repo_noremote)), opts())
        self.assertTrue(res.committed)
        self.assertFalse(res.pushed)
        self.assertEqual(res.push_outcome, Outcome.SKIPPED)

    def test_push_sets_upstream_when_missing(self):
        # 去掉上游跟踪;普通 push 仍能成功,但会自动加上 -u
        git(self.repo, "branch", "--unset-upstream")
        append_file(self.repo, "a.txt", "x")
        res = engine.process_repo(self.repo_obj(), opts())
        self.assertTrue(res.pushed)
        # 此时上游应已被重新设置
        from qpush import git as qgit
        self.assertEqual(qgit.upstream(str(self.repo)), "origin/main")

    def test_divergence_reports_failure(self):
        # 从第二个 clone 推进 origin,使工作仓库落后于远端
        clone = self.base / "clone"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(clone)], check=True)
        git_clone = lambda *a: git(clone, *a)
        git_clone("config", "user.email", "c@e.com")
        git_clone("config", "user.name", "C")
        append_file(clone, "README.md", "from clone")
        git_clone("add", "-A")
        git_clone("commit", "-qm", "clone commit")
        git_clone("push", "-q", "origin", "main")
        # 现在工作仓库与远端分叉
        append_file(self.repo, "a.txt", "local")
        res = engine.process_repo(self.repo_obj(), opts())
        self.assertTrue(res.committed)
        self.assertEqual(res.push_outcome, Outcome.FAILED)
        self.assertEqual(res.overall, Outcome.FAILED)
        self.assertIsNotNone(res.error)

    def test_force_push_overcomes_divergence(self):
        clone = self.base / "clone"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(clone)], check=True)
        git(clone, "config", "user.email", "c@e.com")
        git(clone, "config", "user.name", "C")
        append_file(clone, "README.md", "from clone")
        git(clone, "add", "-A")
        git(clone, "commit", "-qm", "clone")
        git(clone, "push", "-q", "origin", "main")
        # 先 fetch,让工作仓库的远端跟踪引用与实际情况一致,
        # 这正是 --force-with-lease 所校验的内容。
        git(self.repo, "fetch", "-q", "origin")
        append_file(self.repo, "a.txt", "local")
        res = engine.process_repo(self.repo_obj(), opts(force=True))
        self.assertTrue(res.pushed)
        self.assertEqual(res.push_outcome, Outcome.OK)

    def test_no_add_commits_prestaged(self):
        append_file(self.repo, "a.txt", "x")
        git(self.repo, "add", "-A")  # pre-stage manually
        res = engine.process_repo(self.repo_obj(), opts(add=False))
        self.assertTrue(res.committed)
        self.assertTrue(res.pushed)

    def test_empty_message_blocks_commit(self):
        # 引擎本身只是提交传入的信息;空信息由 CLI 拦截。
        # 这里确保提交时使用了真实的信息。
        append_file(self.repo, "a.txt", "x")
        res = engine.process_repo(self.repo_obj(), opts(message="sync: update"))
        self.assertTrue(res.committed)
        self.assertEqual(git(self.repo, "log", "-1", "--pretty=%B").stdout.strip(),
                         "sync: update")


def pullopts(**kw) -> PullOptions:
    base = dict(rebase=True, ff_only=False, prune=False, remote="origin",
                branch=None, dry_run=False, parallel=1, verbose=False)
    base.update(kw)
    return PullOptions(**base)


def adv_origin_via_clone(base, origin, content="from clone"):
    """通过一个临时 clone 往 origin 推一个新提交,使 main 前进。"""
    import subprocess
    clone = base / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    git(clone, "config", "user.email", "c@e.com")
    git(clone, "config", "user.name", "C")
    write_file(clone, "README.md", content)
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "clone change")
    git(clone, "push", "-q", "origin", "main")


class PullTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo, self.origin = make_repo(self.base, "r")

    def tearDown(self):
        self._tmp.cleanup()

    def repo_obj(self) -> Repo:
        return Repo(path=str(self.repo))

    def test_pull_up_to_date(self):
        res = engine.process_pull(self.repo_obj(), pullopts())
        self.assertIsNone(res.error)
        self.assertEqual(res.note, "up to date")
        self.assertFalse(res.acted)
        self.assertEqual(res.overall, Outcome.SKIPPED)

    def test_pull_updates_when_behind(self):
        adv_origin_via_clone(self.base, self.origin)
        head_before = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        res = engine.process_pull(self.repo_obj(), pullopts())
        self.assertTrue(res.acted)
        self.assertEqual(res.note, "updated")
        head_after = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(head_before, head_after)
        self.assertEqual(res.overall, Outcome.OK)

    def test_pull_dirty_is_skipped(self):
        append_file(self.repo, "a.txt", "x")
        res = engine.process_pull(self.repo_obj(), pullopts())
        self.assertFalse(res.acted)
        self.assertIn("dirty", res.note)

    def test_pull_detached_is_skipped(self):
        git(self.repo, "checkout", "-q", "--detach")
        res = engine.process_pull(self.repo_obj(), pullopts())
        self.assertIn("detached", res.note)

    def test_pull_missing_remote_is_skipped(self):
        repo2, _ = make_repo(self.base, "noremote", with_remote=False)
        res = engine.process_pull(Repo(path=str(repo2)), pullopts())
        self.assertIn("no remote", res.note)

    def test_pull_conflict_reports_failure(self):
        adv_origin_via_clone(self.base, self.origin, content="clone wins")
        # 对同一文件做与远端不同的本地改动
        write_file(self.repo, "README.md", "local wins")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "local change")
        res = engine.process_pull(self.repo_obj(), pullopts(rebase=True))
        self.assertTrue(res.conflicts)
        self.assertEqual(res.overall, Outcome.FAILED)

    def test_pull_dry_run_makes_no_changes(self):
        adv_origin_via_clone(self.base, self.origin)
        head_before = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        res = engine.process_pull(self.repo_obj(), pullopts(dry_run=True))
        self.assertFalse(res.acted)
        self.assertEqual(head_before, git(self.repo, "rev-parse", "HEAD").stdout.strip())


class ExecTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo, _ = make_repo(self.base, "r", with_remote=False)

    def tearDown(self):
        self._tmp.cleanup()

    def repo_obj(self) -> Repo:
        return Repo(path=str(self.repo))

    def test_exec_ok(self):
        res = engine.process_exec(self.repo_obj(), ExecOptions(cmd="echo hello"))
        self.assertTrue(res.acted)
        self.assertEqual(res.note, "ok")
        self.assertEqual(res.overall, Outcome.OK)
        self.assertTrue(any("hello" in msg for _, msg in res.log))

    def test_exec_nonzero_exit(self):
        res = engine.process_exec(self.repo_obj(), ExecOptions(cmd="false"))
        self.assertFalse(res.acted)
        self.assertEqual(res.overall, Outcome.FAILED)
        self.assertIn("exit", (res.note or ""))

    def test_exec_runs_in_repo_root(self):
        res = engine.process_exec(self.repo_obj(), ExecOptions(cmd="pwd"))
        out = "\n".join(msg for _, msg in res.log)
        self.assertIn(str(self.repo.resolve()), out)

    def test_exec_dry_run(self):
        res = engine.process_exec(self.repo_obj(), ExecOptions(cmd="echo hi", dry_run=True))
        self.assertFalse(res.acted)
        self.assertIn("would run", res.note)

    def test_exec_empty_command(self):
        res = engine.process_exec(self.repo_obj(), ExecOptions(cmd="   "))
        self.assertEqual(res.overall, Outcome.FAILED)


if __name__ == "__main__":
    unittest.main()
