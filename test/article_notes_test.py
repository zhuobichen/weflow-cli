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


class DateWindowTests(unittest.TestCase):
    """时间窗口：**"把 9 月的跑一遍"是人话，`--limit` 表达不了它**。

    用 limit 去凑月份，要么漏掉（月内多于 limit）要么带上隔壁月份（少于 limit）——
    所以按 `published` 过滤才是精确的那种。
    """

    def build(self, tmp):
        for day in ('2026-08-31', '2026-09-01', '2026-09-15', '2026-10-01'):
            (Path(tmp) / f'{day}-甲.md').write_text(
                '---\ntitle: 甲-%s\npublished: %s\n---\n\n正文\n' % (day, day), encoding='utf-8')

    def test_only_september(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp)
            got = an.list_articles(tmp, 0, since='2026-09-01', until='2026-09-30')
        self.assertEqual([a['published'] for a in got], ['2026-09-15', '2026-09-01'],
                         '倒序、且两个端点都含在内')

    def test_since_alone_是开区间(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp)
            got = an.list_articles(tmp, 0, since='2026-09-01')
        self.assertEqual(len(got), 3, '9/1 之后（含当天）的三篇')

    def test_不传窗口就是全部(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp)
            self.assertEqual(len(an.list_articles(tmp, 0)), 4)


class TopicFilterTests(unittest.TestCase):
    """`--topic`：只做关心的主题。

    **为什么值得单独一组**：这一步是纯花钱的（一篇一次调用），而主题分布实测是
    新闻 57% / AI 28% / 学术 13%。所以"只做 AI"不是锦上添花，是省掉一半以上的钱。
    另外这两种写法都要认：Vault 里那批笔记写的是 `hasTopic: [[AI]]`，新的写 `topic: AI`
    （读法在 `compile_wiki.article_topic`，这里只是确认筛选确实走了它）。
    """

    def build(self, tmp):
        rows = [('2026-09-05', 'AI', ['source/wechat', 'ai']),
                ('2026-09-04', '新闻', []),
                ('2026-09-03', '学术', []),
                ('2026-09-02', 'AI', [])]
        for day, topic, tags in rows:
            tag_line = ('tags: [%s]\n' % ', '.join(tags)) if tags else ''
            (Path(tmp) / f'{day}-甲.md').write_text(
                '---\ntitle: 甲-%s\ntopic: %s\npublished: %s\n%s---\n\n正文\n'
                % (day, topic, day, tag_line), encoding='utf-8')

    def test_只留指定主题(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp)
            got = an.list_articles(tmp, 0, topics=['AI'])
        self.assertEqual(sorted(a['published'] for a in got), ['2026-09-02', '2026-09-05'])

    def test_多个主题(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp)
            got = an.list_articles(tmp, 0, topics=['AI', '学术'])
        self.assertEqual(len(got), 3)

    def test_大小写不敏感(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp)
            self.assertEqual(len(an.list_articles(tmp, 0, topics=['ai'])), 2)

    def test_读得懂_hasTopic_那种写法(self):
        # Vault 里现存的 1633 篇用的是 `hasTopic: [[AI]]`，不是 `topic:`
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '2026-09-05-甲.md').write_text(
                '---\ntitle: 甲\nhasTopic: [[AI]]\npublished: 2026-09-05\n---\n\n正文\n',
                encoding='utf-8')
            got = an.list_articles(tmp, 0, topics=['AI'])
        self.assertEqual(len(got), 1)

    def test_不传主题就是全部(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp)
            self.assertEqual(len(an.list_articles(tmp, 0)), 4)

    def test_筛选发生在截断之前(self):
        """`--limit 1` 的语义是"最新的一篇 AI"，不是"最新一篇恰好是 AI 才要"。

        顺序反了不会报错，只会静默少给——而且越往后翻越挑不满，看着像"库就这样"。
        """
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp)
            got = an.list_articles(tmp, 1, topics=['AI'])
        self.assertEqual([a['published'] for a in got], ['2026-09-05'],
                         '最新的 AI 是 09-05；若先截断就会拿到 09-05 的…新闻')

    def test_筛选后一篇都没有时返回空_不是全部(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp)
            self.assertEqual(an.list_articles(tmp, 0, topics=['不存在的主题']), [])


class IncrementalTests(unittest.TestCase):
    """默认增量：不然每跑一次都把同样的文章重问一遍（三十篇=三十次白花的调用）。"""

    def test_已有卡就跳过_原笔记改了才重做(self):
        import os, time
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'src'
            source.mkdir()
            note = source / '2026-09-05-甲.md'
            note.write_text('---\ntitle: 甲\n---\n\n正文\n', encoding='utf-8')
            out = Path(tmp) / 'out'
            article = {'path': note}

            self.assertFalse(an.already_carded(article, str(out)), '还没卡 → 要做')
            out.mkdir()
            an.note_path(out, note.stem).write_text('x', encoding='utf-8')

            old = time.time() - 100
            os.utime(note, (old, old))
            self.assertTrue(an.already_carded(article, str(out)), '卡比原笔记新 → 跳过')

            future = time.time() + 100
            os.utime(note, (future, future))
            self.assertFalse(an.already_carded(article, str(out)), '原笔记改过了 → 重做')


class GateTests(unittest.TestCase):
    def test_太短的不值得一次调用(self):
        self.assertFalse(an.worth_concepts('短'))
        self.assertFalse(an.worth_concepts('把' * (an.MIN_ARTICLE_CHARS - 1)))
        self.assertTrue(an.worth_concepts('把' * an.MIN_ARTICLE_CHARS))


class AdResidueTests(unittest.TestCase):
    """微信正文里的界面残留——**用真串钉的**，不是我想象的形状。

    实测：库里 1633 篇笔记有 **330 篇**（20%）的摘要里混着这几样。而原来那份清理表
    （`classify_daily.AD_PATTERNS`）对着真串比对时**漏了一条**：真串是
    「在**公众号**小说中沉浸阅读」，表里写的是「在小说阅读器**中**沉浸阅读」；
    文末的「原创 + 公众号名重复三遍 + 下划线长串」也一条都没覆盖。
    """
    REAL_TAIL = ('正文第一句。 原创 开源星探 开源星探 开源星探 ______ '
                 '在小说阅读器读本章 去阅读 在公众号小说中沉浸阅读')
    # **真实的形状是"在中间"**：摘要拼的是文章前 10 行，阅读器控件插在段落之间。
    # 我第一版只测了行尾那种，于是"中间那串"一直没被清掉也不红——所以两种都要钉。
    REAL_MIDDLE = ('正文第一句。 原创 开源星探 开源星探 开源星探 ______ '
                   '在小说阅读器读本章 去阅读 在公众号小说中沉浸阅读 今天要聊的工具。')

    def test_真串里的界面残留被清干净(self):
        from _utils import strip_wx_ads
        cleaned = strip_wx_ads(self.REAL_TAIL)
        self.assertEqual(cleaned, '正文第一句。')
        for junk in ('在小说阅读器读本章', '去阅读', '沉浸阅读', '______', '原创'):
            self.assertNotIn(junk, cleaned)

    def test_那串出现在段间时也要清掉_而正文留着(self):
        from _utils import strip_wx_ads
        self.assertEqual(strip_wx_ads(self.REAL_MIDDLE), '正文第一句。 今天要聊的工具。')

    def test_只动已知的几样_正文一个字不改(self):
        from _utils import strip_wx_ads
        prose = '这句话里有「沉浸」两个字，也有一个下划线的变量名 my_var 和原创性的讨论。'
        self.assertEqual(strip_wx_ads(prose), prose, '不是那几样固定的串就不许动')

    def test_空输入不炸(self):
        from _utils import strip_wx_ads
        for value in ('', None, '   '):
            self.assertEqual(strip_wx_ads(value), '')

    def test_卡里的摘要要过这一关(self):
        # 卡是「照抄摘要」的，所以抄之前必须擦——否则那串东西跟着卡进概念页
        article = dict(ARTICLE)
        article['body'] = ('## 📋 摘要\n\n' + self.REAL_TAIL + '\n\n## 💡 核心观点\n\n- 观点\n')
        _, body = an.build_card(article, [], '2026-09-26 15:00')
        self.assertIn('正文第一句。', body)
        self.assertNotIn('在小说阅读器读本章', body)
        self.assertNotIn('______', body)


class PromptTests(unittest.TestCase):
    def test_提示词说清了三件事(self):
        prompt = an.CONCEPT_PROMPT.format(title='甲', source='某号', topic='AI', body='正文')
        self.assertIn('编译者，不是作者', prompt)
        self.assertIn('不是分类词', prompt, '主题标签不许当概念——实测里它曾霸占引用榜首')
        self.assertIn('只写文章里有的', prompt)


if __name__ == '__main__':
    unittest.main()
