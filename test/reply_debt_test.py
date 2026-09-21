"""欠账雷达的纯逻辑：消息怎么进 state、证据有多薄、自检能不能自证矛盾。

这个脚本输出的每个分数都会被当线索用，所以三件事必须钉住：
**非文本消息不能变成空行**（否则模型是在空白上给分）、**证据量要能看见**、
以及**自相矛盾的判断要被抓出来**（"在等我"但最后一条是我发的）。
"""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('reply_debt', SCRIPTS / 'reply_debt.py')
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)


def message(text='', is_send=False, local_type=1, ts=1_700_000_000, sender='张三'):
    return {'parsedContent': text, 'content': text, 'isSend': 1 if is_send else 0,
            'localType': local_type, 'createTime': ts, 'senderDisplay': sender}


class FormatLineTests(unittest.TestCase):
    def test_a_text_message_keeps_its_speaker_and_text(self):
        line = rd.format_line(message('明天发你', sender='李四'))
        self.assertIn('李四：明天发你', line)
        self.assertTrue(line.startswith('['))

    def test_my_own_message_says_me(self):
        self.assertIn('] 我：', rd.format_line(message('好', is_send=True)))

    def test_a_non_text_message_is_labelled_not_left_blank(self):
        """这是整份输出的前提：空行会让模型在空白上给分。"""
        for local_type, label in ((3, '图片'), (34, '语音'), (43, '视频')):
            line = rd.format_line(message('', local_type=local_type))
            self.assertIn('[%s]' % label, line, '类型 %d 没有标签' % local_type)
            self.assertFalse(line.rstrip().endswith('：'), '末尾是空内容')

    def test_an_unlabelled_type_still_says_it_was_not_text(self):
        line = rd.format_line(message('', local_type=9999))
        self.assertIn('非文本', line)
        self.assertIn('9999', line)

    def test_long_text_is_truncated(self):
        line = rd.format_line(message('字' * 500))
        self.assertLessEqual(len(line), 200)


class EvidenceTests(unittest.TestCase):
    def test_it_measures_the_other_side_not_mine(self):
        long_enough = '这是一句有内容的回复'
        messages = [message('我说了一句比较长的话', is_send=True),
                    message('嗯'),                       # 对方 1 字，算不上实质
                    message(long_enough)]
        proof = rd.evidence(messages)
        self.assertEqual(proof['theirCount'], 2)
        self.assertEqual(proof['theirSubstantive'], 1)
        # 用 len() 而不是手数字符：这条断言想说的是"量的是对方，不是我自己"。
        self.assertEqual(proof['theirLastChars'], len(long_enough))

    def test_an_empty_last_message_is_zero_chars(self):
        # 图片/语音在 message_content 里就是空的——证据为 0 必须如实反映出来。
        proof = rd.evidence([message('有内容的一句'), message('', local_type=3)])
        self.assertEqual(proof['theirLastChars'], 0)
        self.assertEqual(proof['theirSubstantive'], 1)

    def test_no_messages_from_them_is_not_a_crash(self):
        proof = rd.evidence([message('只有我说话', is_send=True)])
        self.assertEqual(proof['theirCount'], 0)
        self.assertEqual(proof['theirLastChars'], 0)


class SelfCheckTests(unittest.TestCase):
    def test_claiming_they_wait_while_i_spoke_last_is_flagged(self):
        verdict = {'waiting': 0.8}
        lines = ['[09-20 10:00] 对方：在吗', '[09-20 10:05] 我：在的']
        self.assertIn('我发的', rd.self_check(verdict, lines))

    def test_a_consistent_verdict_is_not_flagged(self):
        lines = ['[09-20 10:00] 我：稍等', '[09-20 10:05] 对方：好的我等']
        self.assertIsNone(rd.self_check({'waiting': 0.8}, lines))

    def test_a_low_probability_is_never_flagged(self):
        # 低于阈值本来就不进欠账列表，标它没有意义。
        lines = ['[09-20 10:05] 我：在的']
        self.assertIsNone(rd.self_check({'waiting': 0.4}, lines))

    def test_no_transcript_is_not_a_crash(self):
        self.assertIsNone(rd.self_check({'waiting': 0.9}, []))


class StateTests(unittest.TestCase):
    def test_the_state_names_the_conversation_and_marks_group_chats(self):
        state = rd.build_state('老表亲戚群', True, ['[09-20 10:00] 对方：来吃饭'])
        self.assertIn('老表亲戚群', state)
        self.assertIn('群聊', state)
        self.assertIn('来吃饭', state)

    def test_a_direct_chat_is_marked_as_such(self):
        self.assertIn('单聊', rd.build_state('张三', False, ['x']))


class DecideOneTests(unittest.TestCase):
    class Stub:
        def __init__(self, answers=None, error=None):
            self.answers = answers or {}
            self.error = error
            self.calls = []

        def decide(self, state, questions):
            self.calls.append({'state': state, 'questions': questions})
            if self.error:
                raise self.error
            return self.answers, {'input_tokens': 1}

    def answers(self, waiting=0.7, urgency=2.0, kind='工作'):
        return {'waiting': {'type': 'noul', 'noul': waiting},
                'commitment': {'type': 'noul', 'noul': 0.2},
                'money': {'type': 'noul', 'noul': 0.1},
                'urgency': {'type': 'score', 'score': urgency},
                'kind': {'type': 'choice', 'choice': kind}}

    def test_it_reads_every_field(self):
        row = rd.decide_one(self.Stub(self.answers()), '张三', False, ['line'])
        self.assertEqual(row['waiting'], 0.7)
        self.assertEqual(row['urgencyScore'], 2.0)
        self.assertEqual(row['kind'], '工作')
        self.assertEqual(row['commitment'], 0.2)

    def test_an_unknown_kind_becomes_none_rather_than_being_passed_through(self):
        # 类别会被下游用来做过滤，认识之外的值不该悄悄流进去。
        row = rd.decide_one(self.Stub(self.answers(kind='外星事务')), '张三', False, ['l'])
        self.assertIsNone(row['kind'])

    def test_a_failed_call_yields_none_not_a_default_verdict(self):
        row = rd.decide_one(self.Stub(error=RuntimeError('boom')), '张三', False, ['l'])
        self.assertIsNone(row)

    def test_the_questions_cover_all_the_judgements(self):
        stub = self.Stub(self.answers())
        rd.decide_one(stub, '张三', False, ['l'])
        self.assertEqual(sorted(stub.calls[0]['questions']),
                         ['commitment', 'kind', 'money', 'urgency', 'waiting'])


class CollectTests(unittest.TestCase):
    def test_it_filters_by_recency_sorts_and_limits(self):
        sessions = {'sessions': [
            {'username': 'a', 'lastTimestamp': 1000, 'displayName': '老会话'},
            {'username': 'b', 'lastTimestamp': 4_000_000_000, 'displayName': '新会话'},
            {'username': 'c', 'lastTimestamp': 3_999_999_000, 'displayName': '次新'},
        ]}
        with patch.object(rd.nt_decrypt, 'get_sessions', return_value=sessions):
            picked = rd.collect_conversations([], {}, '', days=30, limit=2)
        self.assertEqual([p['talker'] for p in picked], ['b', 'c'])

    def test_the_contact_book_name_wins_over_the_session_name(self):
        # get_sessions 不接受 name_map，它的 displayName 往往就是 wxid 本身。
        sessions = {'sessions': [
            {'username': 'wxid_x', 'lastTimestamp': 4_000_000_000, 'displayName': 'wxid_x'}]}
        with patch.object(rd.nt_decrypt, 'get_sessions', return_value=sessions):
            picked = rd.collect_conversations([], {'wxid_x': '李四'}, '', days=30, limit=5)
        self.assertEqual(picked[0]['name'], '李四')


class RenderedPageTests(unittest.TestCase):
    """可分享页面的**唯一**安全要求：它不能把聊天内容带出去。

    它只该输出概率、天数、类别与证据量。所以最要紧的一条测试是：给行数据塞一个
    多出来的字段（比如消息正文），渲染结果里**不许**出现它。
    """

    def row(self, **extra):
        base = {'name': '张三', 'days': 1.5, 'kind': '工作', 'waiting': 0.8,
                'urgencyScore': 2.0, 'evidence': {'theirLastChars': 40}}
        base.update(extra)
        return base

    def meta(self):
        return {'days': 30, 'minProb': 0.5, 'generatedAt': '2026-09-21 20:00'}

    def test_chat_text_never_reaches_the_page(self):
        secret = '今晚七点老地方见，别迟到'
        page = rd.render_html([self.row(text=secret)], [], 0, self.meta())
        self.assertNotIn(secret, page)
        self.assertNotIn('今晚', page)

    def test_a_display_name_cannot_inject_markup(self):
        # 联系人显示名是外部数据，里面完全可能有尖括号。
        page = rd.render_html([self.row(name='<script>alert(1)</script>')], [], 0, self.meta())
        self.assertNotIn('<script>alert', page)
        self.assertIn('&lt;script&gt;', page)

    def test_the_headline_carries_the_numbers_worth_quoting(self):
        rows = [self.row(days=3.2), self.row(name='李四', days=0.5)]
        page = rd.render_html(rows, [self.row(name='王五')], 4, self.meta())
        self.assertIn('2', page)      # 欠账数
        self.assertIn('3.2', page)    # 最久等了几天
        self.assertIn('另有 4 个', page)

    def test_thin_evidence_is_marked_visually(self):
        # 证据薄的那行要看得出来，否则页面会把一个 2 字末条得出的分数当成结论。
        page = rd.render_html([self.row(evidence={'theirLastChars': 2})], [], 0, self.meta())
        self.assertIn('class="num thin"', page)

    def test_an_empty_result_is_stated_rather_than_rendered_as_an_empty_table(self):
        page = rd.render_html([], [], 0, self.meta())
        self.assertIn('没有判定为欠账的会话', page)
        self.assertNotIn('<table>', page)

    def test_the_page_says_what_it_is_not(self):
        page = rd.render_html([self.row()], [], 0, self.meta())
        self.assertIn('没有金标准校准过', page)
        self.assertIn('本页不含任何聊天内容', page)

    def test_it_is_a_self_contained_document(self):
        # 可分享 = 一个文件就能打开。不引外部样式、脚本或字体。
        page = rd.render_html([self.row()], [], 0, self.meta())
        self.assertTrue(page.startswith('<!doctype html>'))
        self.assertNotIn('http://', page)
        self.assertNotIn('https://', page)
        self.assertNotIn('<script', page)


if __name__ == '__main__':
    unittest.main()
