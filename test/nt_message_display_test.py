"""非文本消息**不能丢**：读取这条路上每条消息都要有可读的显示形态。

背景（实测）：一条 44 条消息的真实对话里，助手只看到"空内容"的三条，分别是**一条撤回提示**
和两个表情。原因是 `_message_dict` 里 `parsedContent` 只在 `local_type == 1` 时才有值，
而非文本消息的内容是 BLOB（zstd 压缩），会在"不是 str 就置空"那一步丢掉。下游于是把
"有消息但读不出"与"没有消息"混为一谈——助手如实回答"我没解析出内容"。

这些测试盯三件事：
1. 常见非文本类型都有标签；能从 XML 里取到东西的（文件/引用/撤回/转账）要带上那部分；
2. **任何类型都不返回空串**——不认识的类型也要写清"未识别的消息类型 N"；
3. 文本那条路原样不动（`content`/`rawContent` 保持"就是库里的样子"）。

合成数据，不接数据库。
"""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('nt_decrypt_display', SCRIPTS / 'nt_decrypt.py')
nt = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=None)}):
    spec.loader.exec_module(nt)

try:
    import zstandard
    _HAVE_ZSTD = True
except ImportError:      # 环境里没有 zstandard 时压缩用例跳过，其余照测
    _HAVE_ZSTD = False


def zstd(payload: str) -> bytes:
    return zstandard.ZstdCompressor().compress(payload.encode('utf-8'))


def row(local_type, content):
    return {'local_id': 1, 'server_id': 2, 'local_type': local_type,
            'real_sender_id': 7, 'create_time': 1758000000, 'message_content': content}


class NonTextDisplayTests(unittest.TestCase):
    def test_common_types_get_a_label(self):
        for local_type, expected in [(3, '[图片]'), (34, '[语音]'), (43, '[视频]'),
                                     (47, '[表情]'), (48, '[位置]'), (42, '[名片]')]:
            self.assertEqual(nt.non_text_display(local_type, ''), expected)

    def test_an_emoji_message_is_a_label_not_a_blank(self):
        # 表情的内容是 zstd 压缩的 XML，里面**没有名字**（只有 md5 与 productid），
        # 所以只能标到 `[表情]`：知道"他发了个表情"已经比"空"多得多。
        payload = '<msg><emoji md5="dd6f13ec" productid="com.tencent.xin.emoticon"/></msg>'
        self.assertEqual(nt.non_text_display(47, payload), '[表情]')
        if _HAVE_ZSTD:
            self.assertEqual(nt.non_text_display(47, zstd(payload)), '[表情]')

    def test_a_revoked_message_shows_what_was_revoked(self):
        payload = ('<sysmsg type="revokemsg"><revokemsg>'
                   '<content>"某人" 撤回了一条消息</content></revokemsg></sysmsg>')
        raw = zstd(payload) if _HAVE_ZSTD else payload
        self.assertEqual(nt.non_text_display(10000, raw), '"某人" 撤回了一条消息')

    def test_a_file_message_carries_its_name(self):
        payload = '<msg><appmsg><title>Base.csv</title></appmsg></msg>'
        self.assertEqual(nt.non_text_display(6 * 2 ** 32 + 49, payload), '[文件] Base.csv')

    def test_a_quote_carries_the_reply_and_what_it_quotes(self):
        # 实测的真实形状：`appmsg/title` 是**回复正文**，被引原文在 `refermsg/content`。
        # 这两段此前只留了前一段——37 条真实引用消息里，被引原文中位 36 字全被丢掉，
        # 于是模型看到"是呀，够得意个"这样一句不知道在回什么的话。
        payload = ('<msg><appmsg><title>是呀，够得意个[呲牙]</title>'
                   '<refermsg><type>1</type><content>难怪见退群了</content></refermsg>'
                   '</appmsg></msg>')
        self.assertEqual(nt.non_text_display(57 * 2 ** 32 + 49, payload),
                         '[引用] 是呀，够得意个[呲牙] ｜ 引：难怪见退群了')

    def test_a_quote_without_a_refermsg_is_just_the_reply(self):
        # 老格式（3.x 的 Type=49）与任何取不到 refermsg 的载荷：只给回复，不留下一个
        # 空落落的"引："。
        payload = '<msg><appmsg><title>师兄这个课比较好嘛还是？</title></appmsg></msg>'
        self.assertEqual(nt.non_text_display(57 * 2 ** 32 + 49, payload),
                         '[引用] 师兄这个课比较好嘛还是？')

    def test_a_quote_with_an_empty_refermsg_does_not_dangle(self):
        payload = ('<msg><appmsg><title>好的</title>'
                   '<refermsg><type>3</type><content></content></refermsg></appmsg></msg>')
        self.assertEqual(nt.non_text_display(57 * 2 ** 32 + 49, payload), '[引用] 好的')

    def test_a_long_quoted_text_is_clipped_and_marked(self):
        # 被引原文最长的 12733 字（引用了一整篇公众号文章）。截断必须留省略号：
        # 不标记的截断读起来像说完的话，模型会把半句当整句。
        quoted = '引' * 300
        payload = ('<msg><appmsg><title>看这个</title>'
                   '<refermsg><type>1</type><content>%s</content></refermsg></appmsg></msg>' % quoted)
        out = nt.non_text_display(57 * 2 ** 32 + 49, payload)
        self.assertEqual(out, '[引用] 看这个 ｜ 引：' + '引' * 120 + '…')

    def test_a_quote_with_no_reply_text_that_quotes_anything_but_a_placeholder(self):
        # `<content>null</content>` 是占位值，不是原文——按"没有原文"处理，不许当成引文。
        payload = ('<msg><appmsg><title>嗯</title>'
                   '<refermsg><type>1</type><content>null</content></refermsg></appmsg></msg>')
        self.assertEqual(nt.non_text_display(57 * 2 ** 32 + 49, payload), '[引用] 嗯')

    def test_a_link_falls_back_to_des_when_there_is_no_title(self):
        payload = '<msg><appmsg><title></title><des>一段描述</des></appmsg></msg>'
        self.assertEqual(nt.non_text_display(5 * 2 ** 32 + 49, payload), '[链接] 一段描述')

    def test_cdata_title_is_read(self):
        payload = '<msg><appmsg><title><![CDATA[微信转账]]></title><des><![CDATA[收到转账114.80元]]></des></appmsg></msg>'
        self.assertEqual(nt.non_text_display(2000 * 2 ** 32 + 49, payload), '[转账] 微信转账')

    def test_an_unknown_app_type_says_so_instead_of_guessing_link(self):
        # 标签是给下游读的：支撑不起的结论不写（猜成"链接"会让下游当成链接去处理）。
        payload = '<msg><appmsg><title>某个东西</title></appmsg></msg>'
        self.assertEqual(nt.non_text_display(88 * 2 ** 32 + 49, payload), '[应用消息] 某个东西')
        self.assertEqual(nt.non_text_display(88 * 2 ** 32 + 49, ''), '[应用消息]')

    def test_a_self_closing_tag_is_not_mistaken_for_an_open_tag(self):
        # 回归：`<des />` 曾被当成开标签，正则一路吃到后面某个 `</des>`，把整段
        # XML 当文本返回（自验时真的吐出过 `<des />` 加一串标签）。开标签里不许出现 `/`。
        # 文本返回（自验时真的吐出过 `<des />
        self.assertEqual(nt._xml_text('<msg><appmsg><title /><des /></appmsg></msg>', 'title'), '')
        payload = '<msg><appmsg><title /><des /><type>8</type></appmsg></msg>'
        self.assertEqual(nt.non_text_display(8 * 2 ** 32 + 49, payload), '[应用消息]')

    def test_a_nested_containers_placeholder_title_is_not_a_title(self):
        # 实测：payload 里 <emotionpageshared><title>null</title> 会被当成标题，
        # 于是助手读到"[应用消息] null"。占位值一律按"没有标题"处理。
        payload = ('<msg><appmsg><title /><des />'
                   '<emotionpageshared><tid>0</tid><title>null</title></emotionpageshared>'
                   '</appmsg></msg>')
        self.assertEqual(nt.non_text_display(8 * 2 ** 32 + 49, payload), '[应用消息]')

    def test_a_tag_with_attributes_is_still_read(self):
        self.assertEqual(nt._xml_text('<title lang="zh">带属性的</title>', 'title'), '带属性的')

    def test_xml_entities_are_decoded_for_display(self):
        # 显示形态是给人（和模型）读的：`a&amp;b` 该读成 `a&b`。TS 侧的 `appMsgFormat.tagText`
        # 同样解实体——两边不一致的话，同一条消息在 3.x 与 4.x 下读起来会不一样。
        payload = '<msg><appmsg><title>a&amp;b</title></appmsg></msg>'
        self.assertEqual(nt.non_text_display(6 * 2 ** 32 + 49, payload), '[文件] a&b')

    def test_an_escaped_revoke_notice_reads_as_a_sentence(self):
        payload = ('<sysmsg type="revokemsg"><revokemsg>'
                   '<content>&quot;某人&quot; 撤回了一条消息</content></revokemsg></sysmsg>')
        self.assertEqual(nt.non_text_display(10000, payload), '"某人" 撤回了一条消息')

    def test_an_unknown_type_still_says_something(self):
        self.assertEqual(nt.non_text_display(987654321, ''), '[未识别的消息类型 987654321]')

    def test_a_broken_payload_does_not_become_an_empty_string(self):
        # 解压失败 / 内容为空 / 内容不是 XML —— 兜底必须仍然是"有东西"。
        broken = b'\x28\xb5\x2f\xfd' + b'\x00' * 20      # zstd 魔数 + 垃圾
        for local_type in (47, 3, 10000, 6 * 2 ** 32 + 49, 424242):
            with self.subTest(local_type=local_type):
                self.assertNotEqual(nt.non_text_display(local_type, broken), '')

    def test_no_non_text_type_returns_an_empty_string(self):
        """**这条是这次修复的全部要点**：非文本消息不许在读取这一步消失。"""
        types_to_try = [0, 3, 34, 42, 43, 47, 48, 10000, 10002]
        types_to_try += [t * 2 ** 32 + 49 for t in (4, 5, 6, 19, 33, 57, 63, 2000, 2001, 88)]
        for local_type in types_to_try:
            with self.subTest(local_type=local_type):
                self.assertTrue(nt.non_text_display(local_type, ''),
                                'local_type=%d 返回了空串' % local_type)


class MessageDictTests(unittest.TestCase):
    def test_text_is_unchanged(self):
        got = nt._message_dict(row(1, '普通一句话'), {}, {}, 'wxid_me')
        self.assertEqual(got['content'], '普通一句话')
        self.assertEqual(got['rawContent'], '普通一句话')
        self.assertEqual(got['parsedContent'], '普通一句话')

    def test_a_non_text_message_gets_a_display_form(self):
        got = nt._message_dict(row(3, b'\x00\x01\x02'), {}, {}, 'wxid_me')
        self.assertEqual(got['parsedContent'], '[图片]')
        # `content`/`rawContent` 的契约不变：就是库里的样子（BLOB 不是 str，因此为空）
        self.assertEqual(got['content'], '')

    def test_a_compressed_emoji_is_labelled_not_dropped(self):
        if not _HAVE_ZSTD:
            self.skipTest('没有 zstandard')
        got = nt._message_dict(row(47, zstd('<msg><emoji md5="abc"/></msg>')), {}, {}, 'wxid_me')
        self.assertEqual(got['parsedContent'], '[表情]')

    def test_a_revoke_notice_reaches_the_reader(self):
        if not _HAVE_ZSTD:
            self.skipTest('没有 zstandard')
        payload = '<sysmsg type="revokemsg"><content>"某人" 撤回了一条消息</content></sysmsg>'
        got = nt._message_dict(row(10000, zstd(payload)), {}, {}, 'wxid_me')
        self.assertIn('撤回了一条消息', got['parsedContent'])

    def test_local_type_survives_so_consumers_can_branch_on_it(self):
        got = nt._message_dict(row(47, b''), {}, {}, 'wxid_me')
        self.assertEqual(got['localType'], 47)


if __name__ == '__main__':
    unittest.main()
