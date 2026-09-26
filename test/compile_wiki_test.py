"""`compile_wiki.py` 的解析与聚合（纯函数，不联网、不读库）。

这个脚本此前**一个测试都没有**，而它是知识库那条文章线的核心：`output/biz-daily` 的上万篇
材料靠它聚成概念页。它的失效方式全是静默的——wikilink 解析错了就少聚合一批概念、
`desc` 丢了就退化成"用泛泛的摘要拼每一页"，都不会报错。

新加的 `scripts/chat_notes.py` 也必须满足这里的**输入契约**（同一套 frontmatter + `[[…]]`），
所以这份测试同时是那条新管线的契约：生产方写的 markdown 必须能被 `scan_articles` 认出来。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('compile_wiki', SCRIPTS / 'compile_wiki.py')
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)


def write_note(root: Path, rel: str, frontmatter: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\n{frontmatter}\n---\n\n{body}\n', encoding='utf-8')


class ScanTests(unittest.TestCase):
    def test_scan_reads_frontmatter_wikilinks_and_descriptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_note(root, '2026-09-20/AI/某号-标题.md',
                       'title: 某号-标题\nsource: 某号\ntopic: AI\ntags: [a, b]',
                       '## AI 摘要\n\n这段话说的是摘要正文。\n\n'
                       '## 主题\n\n- [[词向量]] — 这篇文章里它是这么被讲的\n- [[老王]]\n')
            notes = cw.scan_articles(str(root))
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note['title'], '某号-标题')
        self.assertEqual(note['source'], '某号')
        self.assertEqual(note['tags'], ['a', 'b'])
        self.assertIn('摘要正文', note['summary'])
        self.assertEqual(note['wikilinks'][0], ('词向量', '这篇文章里它是这么被讲的'),
                         '破折号后面那句是"关于这个概念说的那句话"，必须留下来')
        self.assertEqual(note['wikilinks'][1], ('老王', ''),
                         '没有描述的 wikilink 也要收（描述可以空）')

    def test_没有_wikilink_的材料被跳过(self):
        # 跳过是有意的：没有 wikilink 就没有概念可聚合
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_note(root, 'a.md', 'title: 甲', '## AI 摘要\n\n没有链接的材料。\n')
            self.assertEqual(cw.scan_articles(str(root)), [])

    def test_README_不参与(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_note(root, 'README.md', 'title: 说明', '- [[某概念]]')
            write_note(root, 'b.md', 'title: 乙', '- [[某概念]]')
            notes = cw.scan_articles(str(root))
        self.assertEqual([n['title'] for n in notes], ['乙'])


class AggregateTests(unittest.TestCase):
    def test_同一篇里重复提到只算一次引用(self):
        notes = [{'title': '甲', 'source': 's', 'summary': '摘要', 'file': 'a.md',
                  'wikilinks': [('概念X', '第一次'), ('概念X', '第二次')]}]
        concepts = cw.aggregate_concepts(notes)
        self.assertEqual(len(concepts['概念X']), 1, '同一篇里的第二次提及不该算成第二个来源')
        self.assertEqual(concepts['概念X'][0]['desc'], '第一次', '保留的是第一次那条描述')

    def test_多篇聚到同一个概念上(self):
        notes = [
            {'title': '甲', 'source': 's1', 'summary': 's', 'file': 'a.md', 'wikilinks': [('X', 'x1')]},
            {'title': '乙', 'source': 's2', 'summary': 's', 'file': 'b.md', 'wikilinks': [('X', 'x2')]},
        ]
        concepts = cw.aggregate_concepts(notes)
        self.assertEqual([r['title'] for r in concepts['X']], ['甲', '乙'])


class RefLineTests(unittest.TestCase):
    def test_参考行优先用_desc_而不是摘要(self):
        # 这条是"人物时间线"能不能成立的关键：desc 是"那时候发生了什么"，
        # 摘要只是那篇材料的整体摘要——只用后者的话，每个概念页都长一个样。
        refs = [{'title': '会话A', 'source': '老王', 'summary': '泛泛的摘要', 'desc': '9-20 说要把文件发我'}]
        line = cw.build_ref_lines(refs)[0]
        self.assertIn('9-20 说要把文件发我', line)
        self.assertNotIn('泛泛的摘要', line)

    def test_没有_desc_时退回摘要(self):
        refs = [{'title': '会话A', 'source': '老王', 'summary': '只有摘要', 'desc': ''}]
        self.assertIn('只有摘要', cw.build_ref_lines(refs)[0])

    def test_最多五条参考(self):
        refs = [{'title': f't{i}', 'source': 's', 'summary': 'x', 'desc': 'd'} for i in range(9)]
        self.assertEqual(len(cw.build_ref_lines(refs)), 5)

    def test_描述过长会被截断(self):
        refs = [{'title': 't', 'source': 's', 'summary': '', 'desc': '长' * 400}]
        line = cw.build_ref_lines(refs)[0]
        self.assertLessEqual(len(line), 150 + len('- [t]（s）：'))


if __name__ == '__main__':
    unittest.main()
