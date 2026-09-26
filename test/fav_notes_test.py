"""`fav_notes.py`：收藏 → 知识卡（纯函数，不联网、不读库）。

重点与另外两条线一致：**产出必须被下游 `compile_wiki.scan_articles` 读得回来**。
这条线还有两处自己的事要钉：

- **摘要这次是模型写的**（收藏记录里没有摘要可抄），卡里要标出来（`summary_by: model`）——
  不标的话，它看起来跟文章线那种"抄来的原文摘要"一模一样；
- **`saved` 不是 `published`**：收藏时间与发布时间是两回事，别把前者叫成后者。
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


fn = load('fav_notes')
cw = load('compile_wiki')

ITEM = {'title': '一本把数学讲到 Agent 的 AI 全栈书', 'source_name': '搞AI的陈老斯',
        'link': 'https://mp.weixin.qq.com/s/example', 'update_time': 1790322913}
NOTE = {'summary': '这篇文章介绍了那本书的脉络。',
        'concepts': [{'name': '全栈 AI 学习路径', 'desc': '从数学基础讲到 Agent'}]}


class ParseTests(unittest.TestCase):
    def test_正常_JSON(self):
        raw = json.dumps({'summary': 's', 'concepts': [{'name': '甲', 'desc': '在说甲'}]},
                         ensure_ascii=False)
        got = fn.parse_note(raw)
        self.assertEqual(got['concepts'], [{'name': '甲', 'desc': '在说甲'}])

    def test_容忍代码块(self):
        raw = '```json\n' + json.dumps({'summary': 's', 'concepts': []}, ensure_ascii=False) + '\n```'
        self.assertIsNotNone(fn.parse_note(raw))

    def test_没有摘要就算解不出来(self):
        # 这条线的摘要**是模型写的**，它缺席就没有卡的意义
        for raw in [json.dumps({'concepts': []}), json.dumps({'summary': ' ', 'concepts': []})]:
            self.assertIsNone(fn.parse_note(raw))

    def test_解不出来就_None_不猜(self):
        for raw in ['', '  ', '抱歉', '[]', '{"concepts": {}}', None, '{坏']:
            self.assertIsNone(fn.parse_note(raw), repr(raw)[:20])

    def test_方括号与空描述被清理(self):
        raw = json.dumps({'summary': 's', 'concepts': [{'name': '[[甲]]', 'desc': 'd'},
                                                       {'name': '乙'}]}, ensure_ascii=False)
        self.assertEqual(fn.parse_note(raw)['concepts'], [{'name': '甲', 'desc': 'd'}])


class TextSourceTests(unittest.TestCase):
    def test_收藏自带的文本优先_不联网(self):
        # 笔记/文本类收藏自带内容；有它就不该去抓链接（那条路要网络）
        item = dict(ITEM, desc='收藏自带的一段正文。' * 20)
        text, how = fn.favorite_text(item)
        self.assertEqual(how, 'local')
        self.assertIn('收藏自带的一段正文', text)

    def test_没有文本也没有链接时说得出原因(self):
        text, how = fn.favorite_text({'title': '甲'})
        self.assertEqual((text, how), ('', 'no-text'))

    def test_本地文本太短时不拿它凑数_继续去抓链接(self):
        # 几十个字的多半是收藏时的摘要片段，不够提炼概念
        calls = []

        def fake_fetch(link):
            calls.append(link)
            return '抓来的正文。' * 40, True        # 命中缓存

        text, how = fn.favorite_text(dict(ITEM, desc='很短'), fetch=fake_fetch)
        self.assertEqual(how, 'cache', '太短就该继续去抓，并如实说出正文是从哪来的')
        self.assertEqual(calls, [ITEM['link']])
        self.assertIn('抓来的正文', text)

    def test_抓取失败时说得出是哪一种失败(self):
        def boom(link):
            raise RuntimeError('boom')
        text, how = fn.favorite_text(ITEM, fetch=boom)
        self.assertEqual((text, how), ('', 'fetch-failed: RuntimeError'))

    def test_抓不到内容时不算有正文(self):
        text, how = fn.favorite_text(ITEM, fetch=lambda link: (None, False))
        self.assertEqual((text, how), ('', 'fetch-failed'))


class ClassifyTests(unittest.TestCase):
    """`--dry-run` 说得出"哪几条要联网抓"而**不去抓**——预览是只读的。

    我第一版让预览调用会联网的那个函数，于是跑 dry-run 时它真去抓了四篇文章。
    """

    def test_只看本地信息分类(self):
        self.assertEqual(fn.classify_source(dict(ITEM, desc='一段够长的自带正文。' * 20)), 'local')
        self.assertEqual(fn.classify_source(ITEM), 'fetch', '有链接 → 需要抓')
        self.assertEqual(fn.classify_source({'title': '甲'}), 'none', '既没文本也没链接')

    def test_分类不碰网络(self):
        # 分类函数不许有副作用：给一个不存在的域名，它也不该去解析
        self.assertEqual(fn.classify_source(dict(ITEM, link='https://不存在.invalid/x')), 'fetch')


class CardTests(unittest.TestCase):
    def test_摘要标出来源_收藏时间不叫_published(self):
        frontmatter, body = fn.build_card(ITEM, NOTE, 3000, '2026-09-25', '2026-09-26 16:00')
        self.assertEqual(frontmatter['summary_by'], 'model', '这条线的摘要不是抄的，要标出来')
        self.assertIn('saved', frontmatter)
        self.assertNotIn('published', frontmatter, '收藏时间不是发布时间')
        self.assertIn('这篇文章介绍了那本书的脉络', body)

    def test_下游消费者读得回概念与描述(self):
        frontmatter, body = fn.build_card(ITEM, NOTE, 3000, '2026-09-25', '2026-09-26 16:00')
        with tempfile.TemporaryDirectory() as tmp:
            from _utils import write_with_frontmatter
            write_with_frontmatter(str(Path(tmp) / '卡.md'), frontmatter, body)
            notes = cw.scan_articles(tmp)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['source'], '搞AI的陈老斯')
        self.assertIn('这篇文章介绍了那本书的脉络', notes[0]['summary'])
        self.assertEqual(dict(notes[0]['wikilinks']).get('全栈 AI 学习路径'), '从数学基础讲到 Agent')

    def test_wikilink_只出现在概念那一节(self):
        _, body = fn.build_card(ITEM, NOTE, 3000, '', '')
        head, _, tail = body.partition('## 概念')
        self.assertNotIn('[[', head)
        self.assertIn('[[全栈 AI 学习路径]]', tail)

    def test_没有概念时不写空标题(self):
        _, body = fn.build_card(ITEM, {'summary': '只有摘要', 'concepts': []}, 100, '', '')
        self.assertNotIn('## 概念', body)


class IncrementalTests(unittest.TestCase):
    def test_已有卡就跳过(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(fn.already_carded(ITEM, tmp))
            fn.note_path(tmp, ITEM['title']).write_text('x', encoding='utf-8')
            self.assertTrue(fn.already_carded(ITEM, tmp))

    def test_没有标题的不算已有(self):
        self.assertFalse(fn.already_carded({'title': ''}, tempfile.gettempdir()))

    def test_部分成功算成功(self):
        self.assertEqual(fn.exit_code(2), 0)
        self.assertEqual(fn.exit_code(0), 1, '一张都没写出来才算失败')


class PromptTests(unittest.TestCase):
    def test_提示词说清了概念要具体(self):
        prompt = fn.FAV_PROMPT.format(title='甲', source='某号', body='正文')
        self.assertIn('编译者，不是作者', prompt)
        self.assertIn('概念要具体', prompt)


if __name__ == '__main__':
    unittest.main()
