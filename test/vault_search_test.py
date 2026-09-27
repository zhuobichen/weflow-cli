"""`vault_search.py` 的取材范围。

盯的是一类**搜不到也不报错**的毛病：搜索按目录名取材，目录名写错了就是"这类东西永远
搜不到"，而命令照样返回结果、照样说"找到 N 条"。2026-09-27 实测到一处：`--type note`
读的是 `Vault/Notes/`，而那个目录**在本仓库里从来不存**（笔记在 `002_Literature`），
于是最大的一层（25,676 篇阅读笔记）对搜索完全不可见。
"""
import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('vault_search', SCRIPTS / 'vault_search.py')
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)


class CollectFilesTests(unittest.TestCase):
    def build(self, tmp):
        vault = Path(tmp) / 'vault'
        (vault / '002_Literature' / 'WeChat' / '2026-05-20').mkdir(parents=True)
        (vault / '002_Literature' / 'WeChat' / '2026-05-20' / '2026-05-20-某篇.md').write_text(
            '---\ntitle: "某篇"\npublished: 2026-05-20\n---\n\n正文\n', encoding='utf-8')
        (vault / '001_Daily').mkdir(parents=True)
        (vault / '001_Daily' / '2026-05-20.md').write_text('# 日记\n', encoding='utf-8')
        (vault / 'Wiki' / 'Concepts').mkdir(parents=True)
        (vault / 'Wiki' / 'Concepts' / '某概念.md').write_text('# 概念\n', encoding='utf-8')
        biz = Path(tmp) / 'biz'
        (biz / (datetime.now().strftime('%Y-%m-%d')) / 'AI').mkdir(parents=True)
        (biz / datetime.now().strftime('%Y-%m-%d') / 'AI' / '某号-某篇.md').write_text(
            '---\ntitle: "x"\n---\n\n正文\n', encoding='utf-8')
        return str(vault), str(biz)

    def test_note_类型要覆盖阅读笔记(self):
        """**这条是那处 bug 的回归测试。** 目录名写错时搜索不会报错，只是永远搜不到。"""
        with tempfile.TemporaryDirectory() as tmp:
            vault, biz = self.build(tmp)
            got = vs.collect_files(vault, biz, 'note', 90)
        kinds = {k for _, k, _ in got}
        names = {p.name for p, _, _ in got}
        self.assertEqual(kinds, {'note'})
        self.assertIn('2026-05-20-某篇.md', names, '阅读笔记必须能被 note 类型搜到')
        self.assertIn('2026-05-20.md', names, '日记也在笔记层里')

    def test_不再去找不存在的_Notes_目录(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, biz = self.build(tmp)
            self.assertFalse((Path(vault) / 'Notes').exists(), '这个目录本来就不该存在')
            got = vs.collect_files(vault, biz, 'note', 90)
        self.assertTrue(got, '不该因为找不到 Notes/ 就返回空')

    def test_concept_类型只取概念页(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, biz = self.build(tmp)
            got = vs.collect_files(vault, biz, 'concept', 90)
            self.assertEqual({k for _, k, _ in got}, {'concept'})
            self.assertEqual({p.name for p, _, _ in got}, {'某概念.md'})

    def test_article_类型仍受天数窗口限制(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, biz = self.build(tmp)
            (Path(biz) / '2020-01-01' / 'AI').mkdir(parents=True)
            (Path(biz) / '2020-01-01' / 'AI' / '老文章.md').write_text('x', encoding='utf-8')
            got = vs.collect_files(vault, biz, 'article', 90)
            self.assertNotIn('老文章.md', {p.name for p, _, _ in got}, '窗口外的不该进来')
            old = vs.collect_files(vault, biz, 'article', 3650)
            self.assertIn('老文章.md', {p.name for p, _, _ in old}, '窗口放宽就该进来')

    def test_all_把三层都收进来(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, biz = self.build(tmp)
            got = vs.collect_files(vault, biz, 'all', 90)
        self.assertEqual({k for _, k, _ in got}, {'article', 'concept', 'note'})


if __name__ == '__main__':
    unittest.main()
