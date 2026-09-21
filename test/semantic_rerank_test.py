"""检索重排：编号对应关系、只增字段、以及失败时退回原序。

重排最危险的失效方式**不会报错**：只要问题编号和候选编号错位，最相关的那条
就会被排到最底下，看起来像一个"模型觉得不相关"的正常结果。所以这里的重点不是
分数高低，而是**这条链路本身有没有错位**，以及拿不到分数时会不会把"未知"
当成"不相关"。

不联网：客户端是桩。也不依赖 numpy/sqlcipher3 —— 这个模块的 import 已经被改成
懒加载，好让 CI（只装 zstandard pycryptodome）也能跑这份测试。
"""
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('semantic_rerank', SCRIPTS / 'semantic_search.py')
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


class StubClient:
    """按候选序号返回 noul，另附一个 best 单选。"""

    def __init__(self, scores=None, best=None, error=None):
        self.scores = scores or {}
        self.best = best
        self.error = error
        self.calls = []

    def decide(self, state, questions):
        self.calls.append({'state': state, 'questions': questions})
        if self.error is not None:
            raise self.error
        answers = {key: {'type': 'noul', 'noul': self.scores[key]}
                   for key in self.scores}
        if self.best is not None:
            answers['best'] = {'type': 'choice', 'choice': self.best}
        return answers, {'input_tokens': 0}


def items(*titles):
    return [{'title': t, 'source': 'src', 'text': t + ' 的正文', 'score': 0.1} for t in titles]


class QuestionShapeTests(unittest.TestCase):
    def test_one_question_per_candidate_and_the_numbering_is_explicit(self):
        """`state` 里的【候选k】标签是编号不跑偏的唯一依靠，必须存在。"""
        questions = ss.build_rerank_questions(4)
        self.assertEqual(sorted(questions), ['c0', 'c1', 'c2', 'c3'])
        for index in range(4):
            self.assertEqual(questions['c%d' % index]['type'], 'noul')
            self.assertIn('【候选%d】' % index, questions['c%d' % index]['instructions'])

    def test_the_request_labels_every_candidate(self):
        client = StubClient(scores={'c0': 0.5, 'c1': 0.5}, best='候选0')
        ss.rerank('问题', items('甲', '乙'), client)
        state = client.calls[0]['state']
        self.assertIn('【候选0】甲', state)
        self.assertIn('【候选1】乙', state)
        criteria = client.calls[0]['questions']['best']['criteria']
        self.assertEqual(sorted(criteria), ['候选0', '候选1'])


class OrderingTests(unittest.TestCase):
    def test_the_order_follows_the_scores_across_a_sizeable_pool(self):
        # 相关的那条放在中间，验证它会被顶上来——编号错位的话它反而会沉底。
        pool = items(*'ABCDE')
        client = StubClient(scores={'c0': .1, 'c1': .2, 'c2': .9, 'c3': .05, 'c4': .01},
                            best='候选2')
        out = ss.rerank('问题', pool, client)
        # .9 > .2 > .1 > .05 > .01：C 从中间被顶上来，D/E 沉底。
        self.assertEqual([x['title'] for x in out], list('CBADE'))
        self.assertEqual([x['rerankScore'] for x in out], [.9, .2, .1, .05, .01])

    def test_the_original_score_is_preserved(self):
        # 下游可能在展示 `score`。改它的含义属于改既有契约。
        pool = items('甲', '乙')
        for entry in pool:
            entry['score'] = 0.42
        out = ss.rerank('问题', pool, StubClient(scores={'c0': .1, 'c1': .9}, best='候选1'))
        self.assertEqual([x['score'] for x in out], [0.42, 0.42])

    def test_candidates_beyond_the_pool_keep_their_place_at_the_end(self):
        pool = items(*'ABCD')
        out = ss.rerank('问题', pool, StubClient(scores={'c0': .5, 'c1': .4}, best='候选0'),
                        pool_size=2)
        self.assertEqual([x['title'] for x in out], ['A', 'B', 'C', 'D'])
        self.assertIsNone(out[2].get('rerankScore'))

    def test_a_missing_score_means_unknown_not_irrelevant(self):
        """没拿到分数的排在最后，但**不能**当成 0 分——0 分是"明确不相关"。"""
        pool = items('甲', '乙', '丙')
        client = StubClient(scores={'c1': .9, 'c2': .2}, best='候选1')  # c0 缺席
        out = ss.rerank('问题', pool, client)
        self.assertEqual([x['title'] for x in out], ['乙', '丙', '甲'])
        self.assertNotIn('rerankScore', out[-1])
        self.assertEqual(out[-1]['rerankScore'] if 'rerankScore' in out[-1] else None, None)

    def test_all_scores_missing_leaves_the_order_alone(self):
        pool = items('甲', '乙', '丙')
        out = ss.rerank('问题', pool, StubClient(scores={}, best=None))
        self.assertEqual([x['title'] for x in out], ['甲', '乙', '丙'])


class FailSoftTests(unittest.TestCase):
    def test_no_client_is_exactly_the_old_behaviour(self):
        pool = items('甲', '乙')
        self.assertIs(ss.rerank('问题', pool, None), pool)

    def test_a_single_candidate_is_not_worth_a_call(self):
        pool = items('甲')
        client = StubClient(scores={'c0': .9})
        self.assertIs(ss.rerank('问题', pool, client), pool)
        self.assertEqual(client.calls, [])

    def test_a_failed_call_returns_the_original_order(self):
        pool = items('甲', '乙')
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            out = ss.rerank('问题', pool, StubClient(error=RuntimeError('boom')))
        self.assertEqual([x['title'] for x in out], ['甲', '乙'])
        self.assertTrue(all('rerankScore' not in x for x in out))
        self.assertIn('重排失败', buffer.getvalue())

    def test_a_disagreeing_self_check_is_reported_but_does_not_reorder(self):
        """逐条打分与单选对不上，通常意味着编号被搞混了——必须说出来。"""
        pool = items('甲', '乙', '丙')
        client = StubClient(scores={'c0': .1, 'c1': .2, 'c2': .9}, best='候选0')
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            out = ss.rerank('问题', pool, client)
        self.assertIn('自检不一致', buffer.getvalue())
        # 顺序仍按逐条打分——单选只用来报警，不参与排序（否则一次错答就全盘换序）。
        self.assertEqual(out[0]['title'], '丙')


if __name__ == '__main__':
    unittest.main()
