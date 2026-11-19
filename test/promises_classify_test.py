"""承诺判定的三分类，重点钉住「应一声 ≠ 收到货」这条。

**这条是被样例打出来的**：合成库第一次跑 `promises`，把
「我明天把排期表发到群里。」+「好嘞」判成了「已确认」。对方那句是在回应
**承诺**，不是在确认**收到东西**。拿这种判定去做对外样例，等于把误报摆在
首页上。

判据因此改成：确认必须出现在**我声称已发之后**。这里把五种组合都钉一遍，
并带一个正对照（确认确实跟在已发之后时，仍然要判成 confirmed）——
否则"永远不返回 acked"也能让负例全绿。
"""
import importlib.util
import pathlib
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('promises_under_test', SCRIPTS / 'promises.py')
promises = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promises)

ME, PEER = True, False


def msg(is_me, text):
    """(ts, is_me, who, text) —— 与 load_messages 产出的四元组同形。"""
    return (0, is_me, 'wxid_demo', text)


class ClassifyTest(unittest.TestCase):
    def test_ack_after_delivery_is_confirmed(self):
        """正对照：我声称已发、对方随后说收到 ⇒ 确认收到。"""
        state, why = promises.classify([
            msg(ME, '新版清单发你了。'),
            msg(PEER, '收到了'),
        ])
        self.assertEqual(state, 'acked', why)
        self.assertIn('确认收到', why)

    def test_ack_before_delivery_is_not_confirmed(self):
        """核心回归：只应了一声、我没有声称已发 ⇒ 不能算确认。"""
        state, why = promises.classify([
            msg(PEER, '好嘞'),
        ])
        self.assertEqual(state, 'unknown', why)
        self.assertIn('只应了一声', why)

    def test_ack_then_delivery_without_later_ack_counts_as_delivered(self):
        """对方先应一声，我后来才说已发、之后没人确认 ⇒ 已兑现（不是我确认收到）。"""
        state, why = promises.classify([
            msg(PEER, '好的'),
            msg(ME, '清单发你了。'),
        ])
        self.assertEqual(state, 'delivered', why)

    def test_delivery_then_ack_is_confirmed_not_merely_delivered(self):
        """两者都成立时，取更强的那个（确认收到），而不是停在已兑现。"""
        state, _why = promises.classify([
            msg(ME, '清单发你了。'),
            msg(PEER, '收到了，谢谢'),
        ])
        self.assertEqual(state, 'acked')

    def test_peer_chasing_is_reported_as_chased(self):
        state, why = promises.classify([
            msg(PEER, '会议纪要发了吗？'),
        ])
        self.assertEqual(state, 'chased', why)
        self.assertIn('催过', why)

    def test_no_followup_is_unknown_not_guessed(self):
        state, why = promises.classify([])
        self.assertEqual(state, 'unknown')
        self.assertIn('没有后续证据', why)

    def test_my_own_ack_is_not_a_peer_confirmation(self):
        """我自己说「好的」不算对方确认——否则自言自语就能把账销掉。"""
        state, why = promises.classify([
            msg(ME, '清单发你了。'),
            msg(ME, '收到'),
        ])
        self.assertEqual(state, 'delivered', why)


class PromisePatternTest(unittest.TestCase):
    def test_time_word_must_follow_i(self):
        """草稿里那句「周五前我把新版清单发你」不会被抓——时间词在「我」之前。

        这条不是要修规则，是要**记住规则的形状**：对外样例的措辞必须照它写，
        否则样例承诺的效果与实际输出对不上。
        """
        self.assertIsNone(promises.PROMISE_RE.search('周五前我把新版清单发你。'))
        self.assertIsNotNone(promises.PROMISE_RE.search('我明天把新版清单发你。'))

    def test_intent_sentences_are_excluded(self):
        self.assertIsNotNone(promises._NOT_PROMISE.search('我明天要开会'))


if __name__ == '__main__':
    unittest.main()
