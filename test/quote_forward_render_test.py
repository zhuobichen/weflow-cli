"""引用 / 合并转发渲染的测试（合成 XML）。

**真实样本没有**：本机消息库里引用 0 命中、recorditem 0 命中。
所以这里能证明的是"给定已知形态能正确渲染、解析不出来会安全退回"，证不了真实数据表现。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('export_chat_html', SCRIPTS / 'export_chat_html.py')
ech = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ech)

QUOTE = """<msg><appmsg>
  <title>好的，我明天发你</title>
  <type>57</type>
  <refermsg><displayname>联系人A</displayname><content>会议纪要发了吗？</content></refermsg>
</appmsg></msg>"""

FORWARD = """<msg><appmsg><title>群聊的聊天记录</title><type>19</type>
<recorditem><dataitem datatype="1"><title>联系人A：第一句</title></dataitem>
<dataitem datatype="1"><title>我：第二句</title></dataitem></recorditem>
</appmsg></msg>"""


class QuoteTest(unittest.TestCase):
    def test_renders_quoted_text_and_my_reply(self):
        html = ech.render_quote(QUOTE)
        self.assertIsNotNone(html)
        self.assertIn('会议纪要发了吗', html)
        self.assertIn('好的，我明天发你', html)
        self.assertIn('联系人A', html)
        self.assertIn('msg-quote', html)

    def test_placeholder_displayname_is_hidden(self):
        """真实样本里 displayname 可能是 "."（2108 条引用里大量如此）。

        直接显示会变成"引用（.）：…"，比不显示更糟——这条来自真实数据，不是想象。
        """
        xml = ('<msg><appmsg><title>收到</title><refermsg><displayname>.</displayname>'
               '<content>原话在这</content></refermsg></appmsg></msg>')
        html = ech.render_quote(xml) or ''
        self.assertIn('原话在这', html)
        self.assertNotIn('（.）', html)

    def test_empty_returns_none(self):
        self.assertIsNone(ech.render_quote(''))

    def test_garbage_returns_none(self):
        self.assertIsNone(ech.render_quote('not xml at all'))

    def test_escapes_injected_tags(self):
        evil = ('<msg><appmsg><title>&lt;script&gt;alert(1)&lt;/script&gt;</title>'
                '<refermsg><content>&lt;b&gt;x&lt;/b&gt;</content></refermsg></appmsg></msg>')
        html = ech.render_quote(evil) or ''
        self.assertNotIn('<script>', html)


class ForwardTest(unittest.TestCase):
    def test_counts_and_lists_items(self):
        html = ech.render_merged_forward(FORWARD)
        self.assertIsNotNone(html)
        self.assertIn('合并转发（2 条）', html)
        self.assertIn('第一句', html)
        self.assertIn('第二句', html)
        self.assertIn('<details', html)

    def test_no_recorditem_returns_none(self):
        self.assertIsNone(ech.render_merged_forward('<msg><appmsg><title>x</title></appmsg></msg>'))

    def test_recorditem_without_titles_returns_none(self):
        html = ech.render_merged_forward('<msg><recorditem></recorditem></msg>')
        self.assertIsNone(html)


if __name__ == '__main__':
    unittest.main()
