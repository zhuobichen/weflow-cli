"""`promote_ideas.py`：知识升级管道（`002_Literature` → `008_MOC` / `003_Ideas`）。

这一支盯的是**生成出来的笔记在 Obsidian 里能不能用**——这个脚本此前一个测试都没有，
而 2026-09-27 实测它有四处问题，全都属于"不报错、只是看着坏"：

1. 空主题会生出一个叫 `MOC-.md` 的文件（畸形名字）；
2. 链接写成 `[[日期/文件名.md|标题]]`——既不是全路径、也不是文件名，Obsidian 多半解析不了，
   而笔记自己的链接是 `[[日期-标题|标题]]`（文件名形式，能解析）；
3. 它自己那段统计查询带着与阅读笔记相同的两处毛病（`SORT date`、`contains(hasTopic, …)`）；
4. 每天只列 8 篇。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('promote_ideas', SCRIPTS / 'promote_ideas.py')
pi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pi)


def article(date, title, topic='AI', source='某号'):
    return {
        'title': title, 'source': source, 'date': date, 'topic': '',
        'hasTopic': f'[[{topic}]]' if topic else '',
        'concepts': [], 'tags': [],
        'path': Path('002_Literature') / 'WeChat' / date / f'{date}-{title}.md',
    }


def five(date='2026-05-20', topic='AI'):
    return [article(date, '第%d篇' % i, topic=topic) for i in range(5)]


class MocTests(unittest.TestCase):
    def build(self, articles):
        tmp = tempfile.TemporaryDirectory()
        vault = Path(tmp.name)
        pi.generate_moc(articles, str(vault))
        return tmp, vault

    def test_链接用文件名_不带目录不带后缀(self):
        # Obsidian 按"最短唯一路径"解析。`日期/文件名.md` 既不是全路径也不是文件名；
        # 笔记自己写的是 `[[日期-标题|标题]]`，那才是能解析的形态。
        tmp, vault = self.build(five())
        text = next((vault / '008_MOC').glob('*.md')).read_text(encoding='utf-8')
        links = [l for l in text.splitlines() if l.startswith('- [[')]
        self.assertTrue(links, '至少要有一条链接')
        for line in links:
            target = line.split('[[')[1].split('|')[0]
            self.assertNotIn('/', target, '链接里不该带目录：%s' % line[:60])
            self.assertFalse(target.endswith('.md'), '链接里不该带 .md：%s' % line[:60])
        tmp.cleanup()

    def test_空主题不生成畸形文件名的_MOC(self):
        # 实测全库 9 篇无主题，它确实生成了 `MOC-.md`
        tmp, vault = self.build(five(topic=''))
        names = [p.name for p in (vault / '008_MOC').glob('*.md')]
        self.assertEqual(names, [], '空主题不该生成任何 MOC，实际: %s' % names)
        self.assertFalse((vault / '008_MOC' / 'MOC-.md').exists())
        tmp.cleanup()

    def test_统计查询用_published_与_string_hasTopic(self):
        tmp, vault = self.build(five())
        text = next((vault / '008_MOC').glob('*.md')).read_text(encoding='utf-8')
        self.assertIn('SORT published DESC', text)
        self.assertNotIn('SORT date', text, '阅读笔记里没有 date 字段')
        self.assertIn('contains(string(hasTopic), "AI")', text)
        self.assertNotIn('contains(hasTopic, "AI")', text,
                         'hasTopic 是嵌套列表，直接 contains 比不中')
        tmp.cleanup()

    def test_少于五篇不生成(self):
        tmp, vault = self.build(five()[:4])
        self.assertEqual([p.name for p in (vault / '008_MOC').glob('*.md')], [])
        tmp.cleanup()

    def test_已有的_MOC_名字正常(self):
        tmp, vault = self.build(five())
        self.assertEqual([p.name for p in (vault / '008_MOC').glob('*.md')], ['MOC-AI.md'])
        tmp.cleanup()


class IdeaTests(unittest.TestCase):
    def test_空主题也不生成想法文件(self):
        # 与 MOC 同一个闸门：否则就是 `想法-.md`
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pi.generate_ideas(five(topic=''), 'no-key-needed', str(vault))
            names = [p.name for p in (vault / '003_Ideas').glob('*.md')] \
                if (vault / '003_Ideas').exists() else []
            for name in names:
                self.assertNotIn('-.md', name, '畸形文件名: %s' % name)


if __name__ == '__main__':
    unittest.main()
