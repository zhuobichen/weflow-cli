"""`article_notes.py`：从文章笔记里提炼概念（纯函数，不联网、不读库）。

重点同样是**对下游的契约**：产出的卡要能被 `compile_wiki.scan_articles` 读出概念与描述。
另外两条纪律在这里也要成立：`[[…]]` 只由我们的代码渲染（模型写的会被拆掉），
摘要**照抄原文**（不重新生成，就少一次编造的机会）。
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


an = load('article_notes')
cw = load('compile_wiki')


ARTICLE = {
    'path': Path('2026-08-27-某篇文章.md'),
    'rel': '2026-08-27/2026-08-27-某篇文章.md',
    'title': '某篇文章',
    'source': '开源星探',
    'topic': 'AI',
    'tags': ['source/wechat', 'ai'],
    'published': '2026-08-27',
    'body': '## 📋 摘要\n\n这篇文章讲了一个新工具。\n\n## 💡 核心观点\n\n- 观点一\n',
}


class ParseTests(unittest.TestCase):
    def test_正常_JSON(self):
        raw = json.dumps({'concepts': [{'name': 'MCP 协议', 'desc': '把时间线开放给 Agent'}]},
                         ensure_ascii=False)
        self.assertEqual(an.parse_concepts(raw),
                         [{'name': 'MCP 协议', 'desc': '把时间线开放给 Agent'}])

    def test_容忍代码块与前言(self):
        raw = '好：\n```json\n' + json.dumps({'concepts': [{'name': '甲', 'desc': '在说甲'}]},
                                            ensure_ascii=False) + '\n```'
        self.assertIsNotNone(an.parse_concepts(raw))

    def test_解不出来就_None_不猜(self):
        for raw in ['', '  ', '没有概念', '[]', '{"concepts": {}}', None, '{坏']:
            self.assertIsNone(an.parse_concepts(raw), repr(raw)[:30])

    def test_空概念列表是合法的(self):
        # "这篇确实没有概念"与"模型没按形状答"是两件事，不许混
        self.assertEqual(an.parse_concepts('{"concepts": []}'), [])

    def test_缺描述的被丢掉_上限六条_方括号被拆掉(self):
        raw = json.dumps({'concepts': [{'name': '甲'}, {'name': '[[乙]]', 'desc': '在说乙'}]
                          + [{'name': f'丙{i}', 'desc': 'd'} for i in range(9)]}, ensure_ascii=False)
        got = an.parse_concepts(raw)
        self.assertEqual(got[0], {'name': '乙', 'desc': '在说乙'})
        self.assertEqual(len(got), 6)


class CardTests(unittest.TestCase):
    def test_摘要照抄_概念只出现在那一节(self):
        frontmatter, body = an.build_card(ARTICLE, [{'name': 'MCP 协议', 'desc': '开放时间线'}],
                                          '2026-09-26 15:00')
        head, _, tail = body.partition('## 概念')
        self.assertIn('这篇文章讲了一个新工具', head, '摘要照抄原笔记，不重新生成')
        self.assertNotIn('[[', head)
        self.assertIn('[[MCP 协议]] — 开放时间线', tail)
        self.assertEqual(frontmatter['from'], ARTICLE['rel'], '要记下这张卡是从哪篇笔记来的')

    def test_下游消费者读得回概念与描述(self):
        frontmatter, body = an.build_card(ARTICLE, [{'name': 'MCP 协议', 'desc': '开放时间线'}],
                                          '2026-09-26 15:00')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '2026-08-27-某篇文章.md'
            from _utils import write_with_frontmatter
            write_with_frontmatter(str(path), frontmatter, body)
            notes = cw.scan_articles(tmp)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['topic'], 'AI')
        self.assertEqual(notes[0]['source'], '开源星探')
        self.assertIn('这篇文章讲了一个新工具', notes[0]['summary'])
        self.assertEqual(dict(notes[0]['wikilinks']).get('MCP 协议'), '开放时间线')

    def test_没有概念时不写空标题(self):
        _, body = an.build_card(ARTICLE, [], '2026-09-26 15:00')
        self.assertNotIn('## 概念', body)


class ListTests(unittest.TestCase):
    def test_按发布时间倒序_并受_limit_限制(self):
        with tempfile.TemporaryDirectory() as tmp:
            for day in ('2026-08-27', '2026-09-02', '2026-08-30'):
                note = Path(tmp) / f'{day}-文章.md'
                note.write_text(f'---\ntitle: 文章{day}\npublished: {day}\n---\n\n正文\n',
                                encoding='utf-8')
            got = an.list_articles(tmp, 2)
        self.assertEqual([a['published'] for a in got], ['2026-09-02', '2026-08-30'])

    def test_目录不存在时返回空(self):
        self.assertEqual(an.list_articles('不存在的目录', 10), [])


class GateTests(unittest.TestCase):
    def test_太短的不值得一次调用(self):
        self.assertFalse(an.worth_concepts('短'))
        self.assertFalse(an.worth_concepts('把' * (an.MIN_ARTICLE_CHARS - 1)))
        self.assertTrue(an.worth_concepts('把' * an.MIN_ARTICLE_CHARS))


class PromptTests(unittest.TestCase):
    def test_提示词说清了三件事(self):
        prompt = an.CONCEPT_PROMPT.format(title='甲', source='某号', topic='AI', body='正文')
        self.assertIn('编译者，不是作者', prompt)
        self.assertIn('不是分类词', prompt, '主题标签不许当概念——实测里它曾霸占引用榜首')
        self.assertIn('只写文章里有的', prompt)


if __name__ == '__main__':
    unittest.main()
