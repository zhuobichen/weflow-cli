"""`chat_notes.py`：把会话变成知识卡（纯函数，不联网、不读库）。

这份测试的重点不是"生成的卡好不好看"，而是**它对下游的契约**：卡要被
`compile_wiki.scan_articles` 认出来，而且 `[[…]]` 必须只出现在我们指定的那一节。
两个失效方式都是静默的：

- 卡里的 wikilink 解析不出来 → 概念页少一批，而产物看起来是成功的；
- 模型在摘要里随手写一个 `[[…]]` → **凭空多出一个概念**（下游收正文里所有 wikilink，
  它不会问这是谁写的）；
- 文件名没做安全处理 → 会话名里带 `/` 时，卡写到别的目录去了。
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


cn = load('chat_notes')
cw = load('compile_wiki')


NOTE = {
    'summary': '在谈那份文件什么时候发。',
    'timeline': [{'when': '09-20', 'what': '说要把文件发我'}],
    'topics': [{'name': '文件交付', 'desc': '一直在拖这件事'}],
    'people': [{'name': '老王', 'desc': '09-20 说要把文件发我'}],
    'owed': '我欠他一份交付时间。',
}


class FileNameTests(unittest.TestCase):
    def test_会话名里的路径字符不会写到别的目录去(self):
        path = cn.note_path('out', 'A/B:C*D?E"F<G>H|I')
        self.assertEqual(path.parent, Path('out'), '斜杠不许被当成目录分隔')
        self.assertNotIn('/', path.name)
        self.assertTrue(path.name.endswith('.md'))

    def test_空名字与超长名字都有兜底(self):
        self.assertEqual(cn.note_path('out', '   ').name, '未命名会话.md')
        self.assertEqual(cn.note_path('out', '..').name, '未命名会话.md')
        self.assertLessEqual(len(cn.note_path('out', '长' * 300).name), 84)


class ParseTests(unittest.TestCase):
    def test_正常_JSON(self):
        note = cn.parse_note(json.dumps(NOTE, ensure_ascii=False))
        self.assertEqual(note['summary'], NOTE['summary'])
        self.assertEqual(note['people'][0]['name'], '老王')

    def test_容忍代码块与前言(self):
        raw = '好的，这是结果：\n```json\n' + json.dumps(NOTE, ensure_ascii=False) + '\n```\n'
        self.assertIsNotNone(cn.parse_note(raw))

    def test_解不出来就返回_None_不猜(self):
        for raw in ['', '   ', '抱歉，我无法完成', '[]', '{"timeline": []}', None, '{坏 JSON']:
            self.assertIsNone(cn.parse_note(raw), repr(raw)[:40])

    def test_缺描述的话题被丢掉_列表有上限(self):
        raw = json.dumps({'summary': '有摘要', 'topics': [{'name': '甲'}] * 9 + [{'name': '乙', 'desc': '在说乙'}],
                          'people': 'not-a-list'}, ensure_ascii=False)
        note = cn.parse_note(raw)
        self.assertEqual([t['name'] for t in note['topics']], ['乙'], '没有描述的条目不要')
        self.assertEqual(note['people'], [], '不是列表就当空')

    def test_摘要里的_wikilink_被拆掉_不许凭空造概念(self):
        # 这是那份纪律里最容易破的一处：摘要/时间线/欠着什么都是模型的原话
        raw = json.dumps({'summary': '关于 [[某个主题]] 的讨论',
                          'timeline': [{'when': '09-20', 'what': '提到 [[另一个]]'}],
                          'topics': [{'name': '[[带括号的话题]]', 'desc': 'x'}],
                          'owed': '欠 [[某人]] 一个答复'}, ensure_ascii=False)
        note = cn.parse_note(raw)
        self.assertEqual(note['summary'], '关于 某个主题 的讨论')
        self.assertEqual(note['timeline'][0]['what'], '提到 另一个')
        self.assertEqual(note['owed'], '欠 某人 一个答复')
        self.assertEqual(note['topics'][0]['name'], '带括号的话题', '名字里的方括号也去掉')


class RenderTests(unittest.TestCase):
    def note(self):
        return cn.parse_note(json.dumps(NOTE, ensure_ascii=False))

    def test_wikilink_只出现在主题与人物那一节(self):
        _, body = cn.render_note(self.note(), '老王', 30, '2026-09-26 10:00', 42)
        head, _, tail = body.partition('## 主题与人物')
        self.assertNotIn('[[', head, '摘要与时间线里不许有 wikilink')
        self.assertIn('[[文件交付]] — 一直在拖这件事', tail)
        self.assertIn('[[老王]] — 09-20 说要把文件发我', tail)

    def test_下游的消费者认得出这张卡_并读到描述(self):
        # **跨脚本契约**：产出方写的 markdown 必须被 `compile_wiki.scan_articles` 认出来
        with tempfile.TemporaryDirectory() as tmp:
            frontmatter, body = cn.render_note(self.note(), '老王', 30, '2026-09-26 10:00', 42)
            path = cn.note_path(tmp, '老王')
            path.parent.mkdir(parents=True, exist_ok=True)
            from _utils import write_with_frontmatter
            write_with_frontmatter(str(path), frontmatter, body)
            notes = cw.scan_articles(tmp)

        self.assertEqual(len(notes), 1, '下游要能扫到这张卡')
        note = notes[0]
        self.assertEqual(note['title'], '老王')
        self.assertEqual(note['source'], '老王', 'frontmatter 的 source 是下游要用的键')
        self.assertEqual(note['tags'], ['聊天', '知识卡'])
        self.assertIn('在谈那份文件什么时候发', note['summary'], '## AI 摘要 那一段要能被抽出来')
        links = {w[0]: w[1] for w in note['wikilinks']}
        self.assertEqual(links.get('文件交付'), '一直在拖这件事')
        self.assertEqual(links.get('老王'), '09-20 说要把文件发我')
        # 参考行优先用 desc：人物页的时间线就是靠它
        refs = cw.aggregate_concepts(notes)['老王']
        self.assertIn('09-20 说要把文件发我', cw.build_ref_lines(refs)[0])

    def test_没有时间线与欠账时不写空标题(self):
        note = cn.parse_note(json.dumps({'summary': '只说了两句话'}, ensure_ascii=False))
        _, body = cn.render_note(note, '甲', 7, '2026-09-26 10:00', 3)
        self.assertNotIn('## 时间线', body)
        self.assertNotIn('## 欠着什么', body)
        self.assertNotIn('## 主题与人物', body)


class WindowTests(unittest.TestCase):
    def test_只留窗口内且方向明确的消息(self):
        messages = [
            {'createTime': 200, 'isSend': False},
            {'createTime': 100, 'isSend': True},
            {'createTime': 100, 'isSend': None},   # 方向不明：丢掉（标签会标错）
            {'createTime': 50, 'isSend': True},    # 窗口外
        ]
        kept = cn.within_window(messages, 100)
        self.assertEqual([m['createTime'] for m in kept], [200, 100])


class ThinConversationTests(unittest.TestCase):
    def test_太薄的会话不产卡(self):
        # 几句话的对话写出来的"知识卡"只会是"只说了两句话"，而它照样占一次调用、
        # 并往概念页里灌一条没有信息量的来源
        self.assertFalse(cn.worth_a_card(2, 300), '消息太少')
        self.assertFalse(cn.worth_a_card(30, 20), '字数太少')
        self.assertTrue(cn.worth_a_card(3, 60), '刚好够')
        self.assertTrue(cn.worth_a_card(10, 500))

    def test_阈值是常量_不许藏在调用处(self):
        self.assertEqual(cn.MIN_MESSAGES_FOR_CARD, 3)
        self.assertEqual(cn.MIN_CHARS_FOR_CARD, 60)


class PromptTests(unittest.TestCase):
    def test_提示词写死了两条底线(self):
        prompt = cn.build_note_prompt('老王', ['[09-20 10:00] 对方：文件呢'], 30)
        self.assertIn('只写材料里有的', prompt)
        self.assertIn('时间不许推断', prompt)
        self.assertIn('不要用代码块围栏', prompt)
        self.assertIn('文件呢', prompt)

    def test_提示词里写了编译者那一句(self):
        # 借自 WeKnora 的 wiki 提示词：模型一旦当自己是作者，就会把模糊的地方"补圆"
        prompt = cn.build_note_prompt('甲', [], 7)
        self.assertIn('编译者，不是作者', prompt)
        self.assertIn('照实并列', prompt)


class FetchAllTests(unittest.TestCase):
    """**把一个会话取完**，而不是只取最近 120 条。

    这条是真踩出来的：窗口内 138 个会话共 8 万条，而它只送了 9,010 条（约 19%），
    因为每个会话无论多大都只看最新 120 条。用户的要求是"同一个群聊必须在同一个上下文"
    ——切块会让模型看不到前因后果，所以取完是硬要求，而不是优化。
    """

    def setUp(self):
        self.original = cn.nt_decrypt.get_messages
        self.calls = []

    def tearDown(self):
        cn.nt_decrypt.get_messages = self.original

    def fake(self, total, page=2000):
        def get_messages(conns, talker, limit, offset=0, **kwargs):
            self.calls.append((limit, offset))
            start = offset
            end = min(offset + limit, total)
            return {'messages': [{'i': i} for i in range(start, end)]}
        return get_messages

    def test_分页取到取完为止(self):
        cn.nt_decrypt.get_messages = self.fake(total=5000)
        msgs, truncated = cn.fetch_all_messages(None, 't', {}, 'me')
        self.assertEqual(len(msgs), 5000)
        self.assertFalse(truncated)
        self.assertEqual([c[1] for c in self.calls], [0, 2000, 4000], 'offset 要一页页往后推')

    def test_最后一页不满就停(self):
        cn.nt_decrypt.get_messages = self.fake(total=2500)
        msgs, _ = cn.fetch_all_messages(None, 't', {}, 'me')
        self.assertEqual(len(msgs), 2500)
        self.assertEqual(len(self.calls), 2, '第二页只有 500 条 → 不该再翻第三页')

    def test_空会话返回空(self):
        cn.nt_decrypt.get_messages = self.fake(total=0)
        self.assertEqual(cn.fetch_all_messages(None, 't', {}, 'me'), ([], False))

    def test_撞到上限要报出来_不许静默截断(self):
        original_cap = cn.MAX_MESSAGES_PER_CONVERSATION
        cn.MAX_MESSAGES_PER_CONVERSATION = 3000
        try:
            cn.nt_decrypt.get_messages = self.fake(total=99999)
            msgs, truncated = cn.fetch_all_messages(None, 't', {}, 'me')
        finally:
            cn.MAX_MESSAGES_PER_CONVERSATION = original_cap
        self.assertTrue(truncated, '撞了上限就得说，不然读的是多少没人知道')
        self.assertGreaterEqual(len(msgs), 3000)


if __name__ == '__main__':
    unittest.main()
