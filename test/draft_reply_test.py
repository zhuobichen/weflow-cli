"""起草回复：判断七问的形状、闸门的三条分支、候选的解析与排序。

全部离线：Jev 客户端与 `call_deepseek` 都打桩，不 spawn、不联网、不花钱。

这份测试盯的是**这份实现自己会悄悄坏掉的地方**：

1. **题名集合**——七问少一道或多一道，判断就变了意思，而输出看起来还是正常的；
2. **闸门**——它拒了不该拒的（把最常见的工作场景挡在门外），或者放过了该拒的
   （涉钱那一条最要紧：一条替用户表态的草稿，比没有草稿危险）；
3. **候选解析**——模型偶尔不带 JSON、或者套了代码块，解析不出来时**宁可少给几条**
   也不能拿占位句凑数（凑出来的"（稍等，我看下）"会被当成一条真候选）。
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('draft_reply', SCRIPTS / 'draft_reply.py')
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


LINES = [
    '[09-24 09:10] 对方：那个文件你什么时候发我',
    '[09-24 09:12] 我：今天下午',
    '[09-24 18:40] 对方：下午过去了',
]


class StubClient:
    """只实现 `decide`：记下调用、按预设返回。不联网。"""

    def __init__(self, answers=None):
        self.calls = []
        self._answers = answers or {}

    def decide(self, state, questions):
        self.calls.append({'state': state, 'questions': questions})
        # 排序题按第二个请求进来时，给一个确定的名次
        if 'best' in questions:
            return ({'best': {'type': 'choice', 'choice': 'candidate_2', 'confidence': 0.7,
                              'probabilities': {'candidate_1': 0.2, 'candidate_2': 0.6, 'candidate_3': 0.2}}},
                    {'model': 'stub'})
        return self._answers, {'model': 'stub', 'input_tokens': 100}


def answers(**overrides):
    base = {'intent': {'type': 'choice', 'choice': 'request_action'},
            'need': {'type': 'choice', 'choice': 'action'},
            'action': {'type': 'choice', 'choice': 'give_commitment'},
            'should_reply': {'type': 'noul', 'noul': 0.82},
            'risk': {'type': 'score', 'score': 3, 'legend': {}},
            'money': {'type': 'noul', 'noul': 0.05},
            'commitment': {'type': 'noul', 'noul': 0.2}}
    base.update(overrides)
    return base


class QuestionSetTests(unittest.TestCase):
    def test_seven_questions_are_asked(self):
        """少一道多一道都会改变判断的意思，而输出看不出来。"""
        self.assertEqual(sorted(dr.build_questions()),
                         ['action', 'commitment', 'intent', 'money', 'need', 'risk', 'should_reply'])

    def test_choice_questions_have_non_empty_options(self):
        for name, question in dr.build_questions().items():
            if question['type'] != 'choice':
                continue
            criteria = question['criteria']
            self.assertGreaterEqual(len(criteria), 2, name)
            for key, when in criteria.items():
                self.assertTrue(key and isinstance(key, str), name)
                self.assertGreater(len(when), 10, '%s.%s 说得太少，等于没有判据' % (name, key))

    def test_score_bins_are_concrete_scenes_not_abstract_words(self):
        """每一档要写**具体情景**（官方要求）。抽象词（低/中/高）会让这一档没有信息量。"""
        bins = dr.build_questions()['risk']['criteria']
        self.assertGreaterEqual(len(bins), 2, '只有一档的话 score 恒等于 0，问不出东西')
        for index, scene in enumerate(bins):
            self.assertGreater(len(scene), 30, '第 %d 档太短，像是抽象词' % index)
            self.assertTrue(scene.rstrip().endswith('.'), '第 %d 档不是一句完整的情景' % index)
            for abstract in ('低', '中', '高', 'low', 'medium', 'high'):
                self.assertNotEqual(scene.strip().lower(), abstract.lower())

    def test_action_question_does_not_decide_timing(self):
        """两题打架是参考实现实测踩过的坑：`action` 不许替 `should_reply` 决定"要不要现在回"。"""
        instructions = dr.build_questions()['action']['instructions']
        self.assertIn('Do not decide whether to send a message immediately', instructions)

    def test_should_reply_carries_the_do_not_guess_rule(self):
        instructions = dr.build_questions()['should_reply']['instructions']
        self.assertIn('you would be guessing', instructions)
        self.assertIn('Answer FALSE', instructions)

    def test_money_and_commitment_keep_our_existing_chinese_wording(self):
        """这两问逐字复用 reply_debt 的措辞（它们已被那边的测试钉住），别在新脚本里改写一份。"""
        from reply_debt import build_questions as debt_questions
        ours = dr.build_questions()
        theirs = debt_questions()
        for key in ('money', 'commitment'):
            self.assertEqual(ours[key]['instructions'], theirs[key]['instructions'], key)


class GateTests(unittest.TestCase):
    def test_money_refuses_even_when_risk_is_low(self):
        reason, advice = dr.evaluate_gate(dr.to_judgment(answers(money={'type': 'noul', 'noul': 0.91})))
        self.assertIn('金钱', reason)
        self.assertTrue(advice, '拒绝时必须给出下一步该干什么')

    def test_high_risk_refuses_and_explains_the_level(self):
        refused = dr.evaluate_gate(dr.to_judgment(answers(risk={'type': 'score', 'score': 8})))
        self.assertIsNotNone(refused)
        self.assertIn('8/9', refused[0])
        self.assertIn('很危险', refused[0])

    def test_low_risk_and_no_money_drafts(self):
        self.assertIsNone(dr.evaluate_gate(dr.to_judgment(answers())))

    def test_commitment_alone_does_not_refuse_by_default(self):
        """承诺未兑现恰恰是最该帮写的场景（"我明天一定发你"是正当回复），一刀切拒掉会把
        最常见的场景挡在门外。所以默认只在提示词里约束，`--gate-commitment` 才硬拒。"""
        with_commitment = dr.to_judgment(answers(commitment={'type': 'noul', 'noul': 0.88}))
        self.assertIsNone(dr.evaluate_gate(with_commitment))
        self.assertIsNotNone(dr.evaluate_gate(with_commitment, gate_commitment=True))

    def test_refusal_advice_follows_the_best_action(self):
        """高风险那条分支才会去看 `action`——拒绝时给的不是空话，而是"下一步该干什么"。
        （涉钱那条有自己的固定建议：先想清楚能给什么，跟动作类型无关。）"""
        judgment = dr.to_judgment(answers(action={'type': 'choice', 'choice': 'check_history'},
                                         risk={'type': 'score', 'score': 8}))
        _, advice = dr.evaluate_gate(judgment)
        self.assertTrue(any('翻' in item for item in advice), advice)

    def test_noul_is_read_as_a_probability_not_a_boolean(self):
        """把 noul 当布尔读会让"每个问题都读成否"——0.31 不是 True。"""
        self.assertIsNone(dr.evaluate_gate(dr.to_judgment(answers(money={'type': 'noul', 'noul': 0.31}))))


class CandidateTests(unittest.TestCase):
    def test_bare_json_array(self):
        self.assertEqual(dr.parse_candidates('["一", "二", "三"]', 3), ['一', '二', '三'])

    def test_fenced_json(self):
        self.assertEqual(dr.parse_candidates('```json\n["甲", "乙"]\n```', 2), ['甲', '乙'])

    def test_line_fallback_when_the_model_forgets_json(self):
        self.assertEqual(dr.parse_candidates('1. 好的\n2. 我看看\n', 2), ['好的', '我看看'])

    def test_never_pads_with_a_placeholder(self):
        """凑数比少给更糟：补出来的"（稍等，我看下）"会被当成一条真候选端到端给人看。"""
        self.assertEqual(dr.parse_candidates('["只有一条"]', 3), ['只有一条'])

    def test_trims_to_the_requested_count(self):
        self.assertEqual(len(dr.parse_candidates(json.dumps(['一', '二', '三', '四']), 2)), 2)

    def test_garbage_returns_empty_not_a_crash(self):
        self.assertEqual(dr.parse_candidates('', 3), [])
        self.assertEqual(dr.parse_candidates(None, 3), [])


class PromptTests(unittest.TestCase):
    def test_judgment_is_injected_into_the_draft_prompt(self):
        """这是与参考实现的主要差异：它的起草提示词里**没有**判断结果。"""
        prompt = dr.build_draft_prompt('老王', LINES, dr.to_judgment(answers()), 3)
        self.assertIn('request_action', prompt)
        self.assertIn('give_commitment', prompt)
        self.assertIn('3 条必须策略不同', prompt)

    def test_my_own_words_are_offered_as_a_style_sample(self):
        """我们读得到库、它读不到：用用户自己的历史发言当语气样本。"""
        prompt = dr.build_draft_prompt('老王', LINES, dr.to_judgment(answers()), 3)
        self.assertIn('参考下面几行我平时说话的口气', prompt)
        self.assertIn('今天下午', prompt)

    def test_commitment_forbids_new_promises(self):
        prompt = dr.build_draft_prompt('老王', LINES,
                                       dr.to_judgment(answers(commitment={'type': 'noul', 'noul': 0.9})), 3)
        self.assertIn('不要许新的承诺', prompt)

    def test_emotional_context_asks_to_acknowledge_first(self):
        prompt = dr.build_draft_prompt('老王', LINES,
                                       dr.to_judgment(answers(risk={'type': 'score', 'score': 5})), 3)
        self.assertIn('先接住情绪', prompt)


class RunTests(unittest.TestCase):
    """跑通整条：判断 → 闸门 → 起草 → 排序。三个依赖都打桩。"""

    def _run(self, stub, deepseek_text='["一", "二", "三"]', **kwargs):
        with patch.object(dr, 'create_client', return_value=stub), \
             patch.object(dr, 'get_api_key', return_value='fake-key'), \
             patch.object(dr, 'call_deepseek', return_value=deepseek_text):
            return dr.run({'name': '老王', 'lines': LINES}, 3, {}, **kwargs)

    def test_drafts_come_back_ranked(self):
        stub = StubClient(answers())
        result = self._run(stub)
        self.assertTrue(result['success'])
        self.assertEqual(result['gate'], 'draft')
        self.assertEqual([d['text'] for d in result['drafts']], ['二', '一', '三'], '排序题说二是最好的')
        self.assertEqual(len(stub.calls), 2, '判断一次、排序一次')

    def test_refused_path_never_calls_the_generator(self):
        """被闸门拦下时**不许**再去生成——那既花了钱，又让人有现成的话可发。"""
        stub = StubClient(answers(money={'type': 'noul', 'noul': 0.93}))
        with patch.object(dr, 'call_deepseek') as generator:
            result = self._run(stub)
            generator.assert_not_called()
        self.assertEqual(result['gate'], 'refused')
        self.assertEqual(result['drafts'], [])
        self.assertTrue(result['advice'])

    def test_survives_a_failed_judgment_without_guessing(self):
        class Failing(StubClient):
            def decide(self, state, questions):
                raise RuntimeError('boom')
        result = self._run(Failing())
        self.assertFalse(result['success'])
        self.assertIn('不猜', result['error'])

    def test_survives_a_failed_ranking_but_says_so(self):
        stub = StubClient(answers())
        original = stub.decide

        def flaky(state, questions):
            if 'best' in questions:
                raise RuntimeError('boom')
            return original(state, questions)
        stub.decide = flaky
        result = self._run(stub)
        self.assertEqual(result['gate'], 'draft')
        self.assertFalse(result['ranked'], '排不出来就要如实说，不能假装排过')
        self.assertEqual([d['text'] for d in result['drafts']], ['一', '二', '三'])


if __name__ == '__main__':
    unittest.main()
