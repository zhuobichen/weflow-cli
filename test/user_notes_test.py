"""`user_notes.py`：读**你自己写的**笔记（纯函数，不联网、不读真库）。

这条线的两条纪律必须钉死，因为违反的代价不对称：

- **你的笔记只读**：这条线读的是你自己写的东西，写坏的代价比另外三条线都大；
- **摘要标明"这是模型的理解"**：一段模型的理解被当成"我自己写的"，是最坏的一种混淆。

还有一条容易做错的：**只读人写层**。生成的东西（`Wiki/`）、管线产出（`001_Daily`、
`002_Literature`）和素材（`Sources/`）都不许被当成"你的笔记"——那样 AI 会对着自己的
产出再读一遍，越滚越偏。
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


un = load('user_notes')
cw = load('compile_wiki')

NOTE = {'path': Path('/tmp/x.md'), 'rel': '004_Permanent/某想法.md', 'title': '某想法',
        'body': '一段够长的笔记正文，写满一百二十个字以上，免得被太短规则跳过。' * 4,
        'links': [{'name': '知识库', 'desc': '我自己起的概念名'}]}


class OwnLinkTests(unittest.TestCase):
    def test_读得出自己写的链接与说明(self):
        links = un.own_links('- [[知识库]] — 我自己起的概念名\n- [[另一个]]\n')
        self.assertEqual(links[0], {'name': '知识库', 'desc': '我自己起的概念名'})
        self.assertEqual(links[1], {'name': '另一个', 'desc': ''})

    def test_同一个链接只留第一次(self):
        links = un.own_links('- [[甲]] — 第一次\n- [[甲]] — 第二次\n')
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['desc'], '第一次')


class MergeTests(unittest.TestCase):
    def test_你自己链的优先_模型只补你没提到的(self):
        own = [{'name': '知识库', 'desc': '我自己起的概念名'}]
        mined = [{'name': '知识库', 'desc': '模型版本'}, {'name': 'MCP 协议', 'desc': '模型补的'}]
        merged = un.merge_concepts(own, mined)
        self.assertEqual([m['name'] for m in merged], ['知识库', 'MCP 协议'])
        self.assertEqual(merged[0]['desc'], '我自己起的概念名', '同名的以你的为准')

    def test_没写说明的链接也给一句占位(self):
        merged = un.merge_concepts([{'name': '甲', 'desc': ''}], [])
        self.assertEqual(merged[0]['desc'], '(笔记里提到，未展开)')

    def test_有条数上限(self):
        own = [{'name': f'概念{i}', 'desc': 'd'} for i in range(9)]
        self.assertEqual(len(un.merge_concepts(own, [])), 6)


class ParseTests(unittest.TestCase):
    def test_正常_JSON(self):
        raw = json.dumps({'summary': 's', 'concepts': [{'name': '甲', 'desc': '在说甲'}]},
                         ensure_ascii=False)
        got = un.parse_note(raw)
        self.assertEqual(got['summary'], 's')
        self.assertEqual(got['mined'], [{'name': '甲', 'desc': '在说甲'}])

    def test_没有摘要就算解不出来(self):
        self.assertIsNone(un.parse_note(json.dumps({'concepts': []})))

    def test_解不出来就_None_不猜(self):
        for raw in ['', '  ', '抱歉', '[]', '{"summary": "s", "concepts": {}}', None, '{坏']:
            self.assertIsNone(un.parse_note(raw), repr(raw)[:20])


class CardTests(unittest.TestCase):
    def test_摘要标明是模型的理解(self):
        frontmatter, body = un.build_card(NOTE, '模型读出来的意思。', [{'name': '甲', 'desc': 'd'}], '2026-09-26 18:00')
        self.assertEqual(frontmatter['summary_by'], 'model')
        self.assertEqual(frontmatter['from'], '004_Permanent/某想法.md', '要记下它读的是哪篇笔记')
        self.assertIn('不是你的原话', body, '正文里也要说清——别让它看起来像你自己写的')

    def test_下游消费者读得回概念与描述(self):
        frontmatter, body = un.build_card(NOTE, '模型的理解。', [{'name': '知识库', 'desc': '我自己起的概念名'}], '')
        with tempfile.TemporaryDirectory() as tmp:
            from _utils import write_with_frontmatter
            write_with_frontmatter(str(Path(tmp) / '卡.md'), frontmatter, body)
            notes = cw.scan_articles(tmp)
        self.assertEqual(len(notes), 1)
        self.assertIn('模型的理解', notes[0]['summary'])
        self.assertEqual(dict(notes[0]['wikilinks']).get('知识库'), '我自己起的概念名')

    def test_wikilink_只出现在概念那节(self):
        _, body = un.build_card(NOTE, '摘要里不许有 [[链接]]。', [{'name': '甲', 'desc': 'd'}], '')
        head, _, tail = body.partition('## 概念')
        self.assertNotIn('[[', head)
        self.assertIn('[[甲]]', tail)


class LayerTests(unittest.TestCase):
    """只读人写层——**这条最容易做错，代价也最大**（AI 对着自己的产出再读一遍）。"""

    def build_vault(self, tmp):
        root = Path(tmp)
        for rel, text in (
            ('000_Inbox/随手记.md', '# 随手记\n' + '一段够长的内容。' * 30),
            ('004_Permanent/想法.md', '# 想法\n' + '一段够长的内容。' * 30),
            ('库根目录笔记.md', '# 根目录\n' + '一段够长的内容。' * 30),
            ('Wiki/Concepts/生成的概念.md', '# 生成的概念\n' + '生成的内容。' * 30),
            ('001_Daily/2026-09-01/日报.md', '# 日报\n' + '管线产出的内容。' * 30),
            ('002_Literature/WeChat/某文.md', '# 某文\n' + '素材内容。' * 30),
        ):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        return root

    def test_只读人写层与库根目录(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build_vault(tmp)
            titles = {note['rel'] for note in un.list_human_notes(vault=str(root))}
        self.assertIn('000_Inbox/随手记.md', titles)
        self.assertIn('004_Permanent/想法.md', titles)
        self.assertIn('库根目录笔记.md', titles, '你在 Obsidian 里直接新建的也算')
        self.assertNotIn('Wiki/Concepts/生成的概念.md', titles, '生成的页不许当成你的笔记')
        self.assertNotIn('001_Daily/2026-09-01/日报.md', titles)
        self.assertNotIn('002_Literature/WeChat/某文.md', titles)

    def test_空笔记会被算作太短_而不是报错(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '空的.md').write_text('', encoding='utf-8')
            notes = un.list_human_notes(vault=str(root))
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(notes[0]['body'].strip()), 0)


class IncrementalTests(unittest.TestCase):
    def test_已有卡且比笔记新就跳过(self):
        import os, time
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / '笔记.md'
            note.write_text('内容', encoding='utf-8')
            out = Path(tmp) / 'out'
            out.mkdir()
            un.note_path(out, '某想法').write_text('x', encoding='utf-8')
            item = dict(NOTE, path=note)
            old = time.time() - 100
            os.utime(note, (old, old))
            self.assertTrue(un.already_carded(item, str(out)))
            future = time.time() + 100
            os.utime(note, (future, future))
            self.assertFalse(un.already_carded(item, str(out)), '笔记改过了就该重做')

    def test_部分成功算成功(self):
        self.assertEqual(un.exit_code(1), 0)
        self.assertEqual(un.exit_code(0), 1)


class PromptTests(unittest.TestCase):
    def test_提示词里不许他替你把结论补完整(self):
        prompt = un.NOTE_PROMPT.format(title='甲', body='正文')
        self.assertIn('编译者', prompt)
        self.assertIn('不要替他把结论补完整', prompt, '这是与另外三条线最要紧的不同')


if __name__ == '__main__':
    unittest.main()
