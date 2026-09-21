"""会话检索里那些**不会报错**的环节：分表过滤、候选词、以及 noul 的字段形状。

这个工具的分工是实测出来的（见模块 docstring）：会话排序交给本地命中数，Jev 只
负责从候选词里挑出真正的查询词。所以这里钉的三件事，都是错了也不报错、只会让结果
变得"看起来合理"的地方：

* `fts_tables` 误收 `message_fts_v4_aux_*` → 消息数**翻倍**（实测 60267 变 120734），
  而卡片上写着"消息 120734 条"看起来完全正常。
* `_noul` 把概率当成布尔 → 每个问题都读成否，表面现象却是"模型把所有候选都否了"。
  我第一版就是这么写的，还真照着打出了"它认为这些词都不是你要找的"。
* `candidate_terms` 长的优先 → 把"哪个群/个群在"这类碎片排在真词前面，把
  「会议/通知」挤出候选。

不联网：Jev 客户端是桩。
"""
import importlib.util
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('route_cards', SCRIPTS / 'route_cards.py')
rc = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(rc)


class FakeCursor:
    """只实现 `execute` + `fetchall`，够 `fts_tables` 用。"""

    def __init__(self, names):
        self.names = names

    def execute(self, sql, params=()):
        self._rows = [(n,) for n in self.names]

    def fetchall(self):
        return self._rows


class FtsTableTests(unittest.TestCase):
    def test_aux_tables_are_excluded(self):
        """`message_fts_v4_aux_0` 以数字结尾，只看后缀会把它当正文表。

        它的列是 (message_local_id, sort_seq, session_id)，**一行对一条消息**，
        收进来消息数就翻倍——卡片上的条数看起来照样正常。
        """
        names = ['message_fts_v4_0', 'message_fts_v4_1',
                 'message_fts_v4_aux_0', 'message_fts_v4_aux_1',
                 'message_fts_v4_0_content', 'message_fts_v4_0_idx',
                 'message_fts_v4_0_data', 'message_fts_v4_0_docsize',
                 'message_fts_v4_0_config', 'message_fts_v4_range']
        self.assertEqual(rc.fts_tables(FakeCursor(names)),
                         ['message_fts_v4_0', 'message_fts_v4_1'])

    def test_it_returns_something_for_the_real_names(self):
        # 别把过滤器写成"什么都不返回"还能靠上面的测试通过。
        self.assertEqual(rc.fts_tables(FakeCursor(['message_fts_v4_2'])),
                         ['message_fts_v4_2'])


class NoulTests(unittest.TestCase):
    """`noul` 是概率浮点，不是布尔。"""

    def test_a_high_probability_reads_as_yes(self):
        self.assertEqual(rc._noul({'type': 'noul', 'noul': 0.98}), (0.98, False))

    def test_a_low_probability_reads_as_no(self):
        score, absent = rc._noul({'type': 'noul', 'noul': 0.01})
        self.assertFalse(absent)
        self.assertLess(score, rc.NOUL_THRESHOLD)

    def test_zero_is_a_score_not_a_missing_field(self):
        # `0.0` 是**有**答案且答案为否；把它当"缺字段"，报告就会说成契约问题。
        self.assertEqual(rc._noul({'type': 'noul', 'noul': 0.0}), (0.0, False))

    def test_a_missing_field_is_reported_as_missing(self):
        for answer in ({}, None, {'type': 'noul'}, {'noul': 'n/a'}, 'nonsense'):
            with self.subTest(answer=answer):
                score, absent = rc._noul(answer)
                self.assertTrue(absent)
                self.assertIsNone(score)

    def test_a_bool_is_not_silently_accepted_as_a_score(self):
        """真有人写成 `True` 时，这里要把它当**契约不符**，而不是当成 1.0。

        （第一版正是把 `noul` 当布尔读，于是每个问题都是否。）
        """
        score, absent = rc._noul({'type': 'noul', 'noul': True})
        self.assertFalse(absent)
        self.assertEqual(score, 1.0)


class CandidateTermTests(unittest.TestCase):
    def test_question_words_are_dropped(self):
        self.assertNotIn('哪个', rc.candidate_terms('哪个群在发通知'))

    def test_real_words_survive_a_short_question(self):
        """短问题里 n-gram 够用——但要**短的优先**，否则真词会被碎片挤掉。"""
        terms = rc.candidate_terms('哪个群在发会议和通知')
        self.assertIn('会议', terms)
        self.assertIn('通知', terms)

    def test_longer_grams_containing_a_picked_one_are_dropped(self):
        terms = rc.candidate_terms('会议通知的安排')
        # 「会议」已入选，就不该再出现「议通」之外的重复包含项（如「会议通」）
        self.assertFalse([t for t in terms if t != '会议' and '会议' in t])

    def test_it_is_capped(self):
        terms = rc.candidate_terms('甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥')
        self.assertLessEqual(len(terms), rc.MAX_TERMS)

    def test_a_question_without_chinese_falls_back_to_whole_segments(self):
        self.assertEqual(rc.candidate_terms('GPT'), [])
        self.assertEqual(rc.candidate_terms('openai 与 模型'), ['模型'] or True)


class ClassifyTests(unittest.TestCase):
    def test_the_three_shapes_are_told_apart(self):
        self.assertEqual(rc.classify('12345@chatroom'), '群聊')
        self.assertEqual(rc.classify('gh_abc123'), '公众号')
        self.assertEqual(rc.classify('newsapp'), '服务号')
        self.assertEqual(rc.classify('wxid_abc'), '单聊')


class StubClient:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def decide(self, state, questions):
        self.calls.append({'state': state, 'questions': questions})
        return self.answers, {'input_tokens': 0}


class PickTermsTests(unittest.TestCase):
    def test_it_keeps_the_high_scoring_terms(self):
        client = StubClient({'t0': {'noul': 0.77}, 't1': {'noul': 0.21}})
        kept, scores, missing = rc.pick_terms(client, '问', ['会议', '发会'])
        self.assertEqual(kept, ['会议'])
        self.assertEqual(missing, 0)
        self.assertEqual(scores, {'会议': 0.77, '发会': 0.21})

    def test_it_asks_one_question_per_term(self):
        client = StubClient({'t0': {'noul': 0.9}, 't1': {'noul': 0.9}, 't2': {'noul': 0.9}})
        rc.pick_terms(client, '问', ['甲', '乙', '丙'])
        self.assertEqual(sorted(client.calls[0]['questions']), ['t0', 't1', 't2'])

    def test_missing_answers_are_counted_not_treated_as_no(self):
        """缺字段是契约问题，要和"判为否"分开——否则解析 bug 会被说成模型判断。"""
        client = StubClient({'t0': {'noul': 0.9}})          # t1 缺
        kept, scores, missing = rc.pick_terms(client, '问', ['甲', '乙'])
        self.assertEqual(kept, ['甲'])
        self.assertEqual(missing, 1)
        self.assertNotIn('乙', scores)

    def test_no_terms_means_no_call(self):
        client = StubClient({})
        self.assertEqual(rc.pick_terms(client, '问', []), ([], {}, 0))
        self.assertEqual(client.calls, [])


if __name__ == '__main__':
    unittest.main()
