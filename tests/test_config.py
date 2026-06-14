"""配置发现与加载的测试:JSONC 注释剥离、路径解析、目录扫描、去重与过滤。"""

import json
import os
import unittest
from pathlib import Path

from tests.helpers import make_repo

from qpush import config
from qpush.config import DiscoveryArgs


class ConfigParsingTest(unittest.TestCase):
    def test_strip_comments_and_trailing_commas(self):
        text = """{
            // a comment
            "repos": [
                "a",   # hash comment
                "b",
            ],  // trailing
            "url": "https://example.com/x",  /* block */
            "color": "#fff",
        }"""
        from qpush.config import _strip_comments_and_trailing_commas
        cleaned = _strip_comments_and_trailing_commas(text)
        parsed = json.loads(cleaned)
        self.assertEqual(parsed["repos"], ["a", "b"])
        # 含有 // 和 # 的字符串必须原样保留,不被误当作注释
        self.assertEqual(parsed["url"], "https://example.com/x")
        self.assertEqual(parsed["color"], "#fff")

    def test_resolve_path_expanduser_and_relative(self):
        os.environ["HOME"] = "/tmp/fakehome"
        self.assertEqual(config.resolve_path("~/x"), "/tmp/fakehome/x")
        self.assertEqual(config.resolve_path("foo", base="/base"), "/base/foo")
        self.assertEqual(config.resolve_path("/abs/p"), "/abs/p")


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        # 两个真实仓库 + 一个非仓库目录
        make_repo(self.base, "repo-a")
        make_repo(self.base, "repo-b")
        (self.base / "not-a-repo").mkdir()
        (self.base / "not-a-repo" / "file.txt").write_text("hi")

    def tearDown(self):
        self._tmp.cleanup()

    def test_scan_finds_repos(self):
        found = config.scan_for_repos(str(self.base), max_depth=2)
        names = {Path(p).name for p in found}
        self.assertEqual(names, {"repo-a", "repo-b"})

    def test_scan_does_not_descend_into_repos(self):
        # 仓库内部的嵌套目录不应被当作独立仓库上报
        nested = self.base / "repo-a" / "nested"
        nested.mkdir()
        os.system(f"git -C {self.base/'repo-a'} init -q {nested} 2>/dev/null")
        found = config.scan_for_repos(str(self.base), max_depth=3)
        self.assertEqual(Path(self.base / "repo-a" / "nested") not in [Path(p) for p in found], True)

    def test_discover_from_config_repos_and_scan(self):
        cfg = self.base / ".qpush.json"
        cfg.write_text(json.dumps({
            "repos": [str(self.base / "repo-a")],
            "scan": str(self.base),
            "scanDepth": 2,
            "remote": "origin",
        }))
        repos = config.discover(DiscoveryArgs(config_path=str(cfg), cwd=str(self.base)))
        names = {r.name for r in repos}
        self.assertEqual(names, {"repo-a", "repo-b"})  # 已去重,扫描又补上了 repo-b

    def test_discover_skips_non_repo(self):
        repos = config.discover(DiscoveryArgs(
            repos=[str(self.base / "repo-a"), str(self.base / "not-a-repo")],
            cwd=str(self.base),
        ))
        self.assertEqual({r.name for r in repos}, {"repo-a"})

    def test_discover_only_and_ignore_filters(self):
        repos = config.discover(DiscoveryArgs(scan=[str(self.base)], scan_depth=2,
                                              only=["repo-a"], cwd=str(self.base)))
        self.assertEqual([r.name for r in repos], ["repo-a"])
        repos = config.discover(DiscoveryArgs(scan=[str(self.base)], scan_depth=2,
                                              ignore=["repo-a"], cwd=str(self.base)))
        self.assertEqual([r.name for r in repos], ["repo-b"])

    def test_discover_object_entry_overrides(self):
        cfg = self.base / ".qpush.json"
        cfg.write_text(json.dumps({
            "repos": [{"path": str(self.base / "repo-a"), "remote": "upstream", "branch": "main"}],
        }))
        repos = config.discover(DiscoveryArgs(config_path=str(cfg), cwd=str(self.base)))
        self.assertEqual(len(repos), 1)
        self.assertEqual(repos[0].remote, "upstream")
        self.assertEqual(repos[0].branch, "main")


if __name__ == "__main__":
    unittest.main()
