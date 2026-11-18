"""The delegate guard's push / merge rule: hit on real push talk, not on everyday words or a path.
claudeaudit, 2026-11-19: a read-only task naming /home/mr/... was refused as a merge request.

    python3 -m unittest discover -s tests
"""
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load

PUSH = "push / merge"


class DelegateGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name) / "state"
        home.mkdir()
        cls.cs = load(home)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def hits(self, text):
        return PUSH in self.cs.delegate_guard(text)

    def test_everyday_words_pass(self):
        for text in ("仓库：/home/mr/Documents/workspace", "/Users/MR/x", "看 control/policy.mjs 推给单元的字段",
                     "推荐方案写在表后", "推断一下原因", "cd /srv/pr/app && ls", "Mr. Smith 的邮箱规则",
                     "统计 pr 目录下的文件", "the PRNG seed", "读 ~/MR-notes.txt"):
            with self.subTest(text=text):
                self.assertFalse(self.hits(text))

    def test_push_talk_hits(self):
        for text in ("改完 git push", "然后 push 到 origin", "推送到远端", "推到远端仓库", "推到 origin",
                     "改完推上去", "开个 PR", "提 MR 给我", "merge 进 main", "合入主干", "PR #12 里"):
            with self.subTest(text=text):
                self.assertTrue(self.hits(text))


if __name__ == "__main__":
    unittest.main()
