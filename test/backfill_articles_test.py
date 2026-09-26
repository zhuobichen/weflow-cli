"""`backfill_articles.py`：历史文章回填。**纯函数与落盘格式**，不查库、不联网。

这一支里唯一真正重要的是**对下游的契约**：回填写出来的 md 必须能被
`create_reading_notes` 读出 frontmatter、被 `compile_wiki` 摘出摘要。两条路读的键
不一样（前者读 title/source/url/topic/date，后者找 `## AI 摘要` 那一段），而它们读错
时**都不报错**——笔记会生成、概念页也会有，只是的字段是空的。所以这里把两边的读法
都钉住，而不是只断言"文件存在"。
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


bf = load('backfill_articles')
notes = load('create_reading_notes')
cw = load('compile_wiki')


def article(**over):
    base = {
        'account': 'gh_test',
        'account_name': '测试号',
        'topic': 'AI',
        'title': '某篇讲 MCP 的文章',
        'digest': '这篇文章讲了 MCP 协议怎么把工具暴露给模型。',
        'url': 'http://mp.weixin.qq.com/s?__biz=AAA&mid=1&idx=1&sn=bbb',
        'cover': '',
        'local_text': '',
        'time': '08:15',
        'timestamp': 1,
        # 必须**长过 MIN_BODY_CHARS**：夹具太短会被闸门正确地跳过，于是断言"写入了"
        # 会失败，而失败原因看着像功能坏了。这条踩过一次——第一次改的时候我按"看着够长"
        # 估计，实际只有 79 字，还是被拦。
        'fetched_md': ('正文第一段讲的是这套工具怎么把时间线开放给 Agent。' * 6)
                      + '\n\n![图](http://x/y.png)\n',
    }
    base.update(over)
    return base


class BodyTests(unittest.TestCase):
    def test_去图去链再数字数(self):
        self.assertEqual(bf.body_without_media('a\n\n![图](http://x/y.png)\nb'), 'ab')
        self.assertEqual(bf.body_without_media('[点这](http://x/y) 正文'), '正文')

    def test_空输入不炸(self):
        for value in ('', None, '   \n\n  '):
            self.assertEqual(bf.body_without_media(value), '')

    def test_阈值与_biz_daily_是同一个数(self):
        """两条路对"哪些文章算有正文"必须给同一个答案。

        `biz_daily` 那边是**内联写死**的 `if len(body_text) < 100`（没有常量可 import），
        所以这里只能钉住这个数本身，并在改动时提醒自己去改另一边。
        """
        self.assertEqual(bf.MIN_BODY_CHARS, 100,
                         'biz_daily.py 里那个内联的 100 要跟着一起改')


class WriteTests(unittest.TestCase):
    def test_太短的正文被跳过并计入(self):
        with tempfile.TemporaryDirectory() as tmp:
            short = article(title='图片型文章', fetched_md='![图](http://x/y.png)')
            result = bf.write_day([short, article()], '2026-03-05', tmp)
            self.assertEqual(result['written'], 1)
            self.assertEqual(result['skipped'], 1)

    def test_没有正文的也算跳过_不算写入(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = bf.write_day([article(fetched_md='', local_text='')], '2026-03-05', tmp)
            self.assertEqual((result['written'], result['skipped']), (0, 1))

    def test_下游读得回_frontmatter(self):
        """**这条是这一支的核心**：写出来的 md 必须被 `create_reading_notes` 认。"""
        with tempfile.TemporaryDirectory() as tmp:
            bf.write_day([article()], '2026-03-05', tmp)
            files = list(Path(tmp).rglob('*.md'))
            files = [p for p in files if p.name != 'README.md']
            self.assertEqual(len(files), 1)
            fm, body = notes.parse_frontmatter(files[0].read_text(encoding='utf-8'))
            self.assertEqual(fm.get('title'), '某篇讲 MCP 的文章')
            self.assertEqual(fm.get('source'), '测试号')
            self.assertEqual(fm.get('topic'), 'AI')
            self.assertEqual(fm.get('date'), '2026-03-05')
            self.assertIn('mp.weixin.qq.com', str(fm.get('url')), '原文链接要落盘，笔记里要能点回去')

    def test_下游摘得出摘要(self):
        """`compile_wiki` 从 `## AI 摘要` 那一段摘——回填的摘要用的是文章自带 digest。"""
        with tempfile.TemporaryDirectory() as tmp:
            bf.write_day([article()], '2026-03-05', tmp)
            path = [p for p in Path(tmp).rglob('*.md') if p.name != 'README.md'][0]
            fm, body = notes.parse_frontmatter(path.read_text(encoding='utf-8'))
            summary = cw._extract_summary(body)
            self.assertIn('MCP 协议', summary, '摘要取的是 digest，不是正文开头')
            # 正文本身还在（知识库要的就是它）
            self.assertIn('正文第一段', body)

    def test_回填标记写进了_frontmatter_和_json(self):
        # 下游要能分辨"这一篇是历史回填的"——那张 md 没经过当天的日报流程
        with tempfile.TemporaryDirectory() as tmp:
            bf.write_day([article()], '2026-03-05', tmp)
            path = [p for p in Path(tmp).rglob('*.md') if p.name != 'README.md'][0]
            fm, _ = notes.parse_frontmatter(path.read_text(encoding='utf-8'))
            self.assertEqual(str(fm.get('backfilled')).lower(), 'true')
            payload = json.loads((Path(tmp) / '2026-03-05' / '.articles.json').read_text(encoding='utf-8'))
            self.assertTrue(payload['backfilled'])
            self.assertEqual(payload['articles'][0]['title'], '某篇讲 MCP 的文章')
            self.assertEqual(payload['articles'][0]['source'], '测试号')

    def test_主题不在分类法里时兜底并计数(self):
        with tempfile.TemporaryDirectory() as tmp:
            odd = article(topic='不存在的主题')
            result = bf.write_day([odd], '2026-03-05', tmp)
            self.assertEqual(result['written'], 1)
            self.assertEqual(result['fallbackTopics'], 1)


class TopicTests(unittest.TestCase):
    """主题必须**在收集时**就定下来，不能留给下游兜底。

    这条是真踩出来的：第一版 `collect_articles` 根本没设 `topic`，于是 `_group_by_topic`
    把整天 106 篇全部折成 `DEFAULT_TOPIC`，100% 落进 `学术/`。最坏的地方是它**不报错**——
    文件照写、笔记照生成，只有一个不起眼的"主题兜底 113 篇"混在输出里。
    """

    def test_没给主题的按关键词猜_而不是全塞兜底(self):
        item = article()
        del item['topic']
        item['title'] = '某大模型的 Agent 实践笔记'
        bf.apply_topics([item])
        self.assertEqual(item['topic'], 'AI')
        self.assertNotEqual(item['topic'], bf.DEFAULT_TOPIC)

    def test_已经定过的主题不被改写(self):
        item = article(topic='文学')
        bf.apply_topics([item])
        self.assertEqual(item['topic'], '文学')

    def test_越界的主题会被重猜(self):
        item = article(topic='不存在')
        item['title'] = '一篇讲融资与财报的文章'
        bf.apply_topics([item])
        self.assertEqual(item['topic'], '投资')

    def test_猜出来的主题落进目录名(self):
        item = article()
        del item['topic']
        item['title'] = '某大模型的 Agent 实践笔记'
        bf.apply_topics([item])
        with tempfile.TemporaryDirectory() as tmp:
            bf.write_day([item], '2026-03-05', tmp)
            dirs = sorted(p.name for p in Path(tmp, '2026-03-05').iterdir() if p.is_dir())
        self.assertEqual(dirs, ['AI'])


class IncrementalTests(unittest.TestCase):
    def test_已回填的天被跳过(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(bf.day_done(tmp, '2026-03-05'), '还没回填过 → 要做')
            bf.write_day([article()], '2026-03-05', tmp)
            self.assertTrue(bf.day_done(tmp, '2026-03-05'), '写过就跳过，免得重跑再抓一遍')

    def test_写了但一篇都没有的天不算完成(self):
        """当天全是图片型文章时 `.articles.json` 存在但列表为空——那不该被当成"做过了"。"""
        with tempfile.TemporaryDirectory() as tmp:
            bf.write_day([article(fetched_md='短')], '2026-03-05', tmp)
            self.assertFalse(bf.day_done(tmp, '2026-03-05'))


if __name__ == '__main__':
    unittest.main()
