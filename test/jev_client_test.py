"""Jev 决策客户端：契约解析、分值映射、以及失败时到底抛还是吞。

这个模块的价值全在"不猜"两个字上，所以测试也主要盯两件事：
**契约**（响应长什么样就解析成什么样，不符就报错）和**失败取向**
（客户端 fail-loud、绝不把失败伪装成一个看起来正常的答案）。

HTTP 一律打桩，不打桩的只有 URL 常量 —— 这个测试文件不该联网。
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
import unittest
import urllib.error
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('jev_client', SCRIPTS / 'jev_client.py')
jev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev)

TOPICS = ['AI', '学术', '新闻', '文学', '投资', '政治']


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(topic='学术', confidence=0.93, score=1.8, noul=0.9):
    return {
        'model': 'jev-1.13.0',
        'answers': {
            'topic': {'type': 'choice', 'choice': topic, 'confidence': confidence,
                      'probabilities': {t: (1.0 if t == topic else 0.0) for t in TOPICS}},
            'relevance': {'type': 'score', 'score': score, 'confidence': 0.8,
                          'legend': {str(i): lv for i, lv in enumerate(jev.RELEVANCE_LEVELS)},
                          'probabilities': {'0': 0.0, '1': 0.2, '2': 0.8}},
            'worth_including': {'type': 'noul', 'noul': noul},
        },
        'usage': {'input_tokens': 1200, 'output_tokens': 60},
    }


class QuestionShapeTests(unittest.TestCase):
    def test_every_topic_becomes_a_choice_option(self):
        """criteria 就是选项集合：漏一个，模型就永远选不到它。"""
        questions = jev.build_questions(TOPICS)
        self.assertEqual(sorted(questions['topic']['criteria']), sorted(TOPICS))
        self.assertEqual(questions['topic']['type'], 'choice')

    def test_the_include_question_is_asked_and_nothing_dead_is(self):
        """问完没人读的问题就是 `COVER_STATE` 那种"算了就扔"——不加。"""
        questions = jev.build_questions(TOPICS)
        self.assertEqual(questions['worth_including']['type'], 'noul')
        self.assertIn('具体内容', questions['worth_including']['instructions'])
        self.assertEqual(sorted(questions), ['relevance', 'topic', 'worth_including'])

    def test_an_unknown_topic_still_gets_a_criterion(self):
        # 判据表里没有的主题不能变成空描述，否则那个选项等于不存在。
        questions = jev.build_questions(['AI', '未分类'])
        self.assertEqual(questions['topic']['criteria']['未分类'], '未分类')

    def test_the_score_scale_is_ordered_and_zero_indexed(self):
        """顺序即语义：第一个元素是 0 分。写反了不会报错，只会静默反向。"""
        criteria = jev.build_questions(TOPICS)['relevance']['criteria']
        self.assertEqual(criteria, jev.RELEVANCE_LEVELS)
        # 判据文本要带着说明（模型靠它判档），但必须仍以三个裸字开头。
        self.assertTrue(criteria[0].startswith('低'))
        self.assertTrue(criteria[-1].startswith('高'))

    def test_the_judging_labels_and_the_stored_names_stay_aligned(self):
        """落盘的是裸的「高/中/低」，判据是带说明的。两张表按下标对应。"""
        self.assertEqual(len(jev.RELEVANCE_LEVELS), len(jev.RELEVANCE_NAMES))
        for level, name in zip(jev.RELEVANCE_LEVELS, jev.RELEVANCE_NAMES):
            self.assertTrue(level.startswith(name), f'{level} 不以 {name} 开头')
        # 下标 i 的分值必须映射回第 i 个名字。
        self.assertEqual(jev.score_to_relevance(0.0), jev.RELEVANCE_NAMES[0])
        self.assertEqual(jev.score_to_relevance(2.0), jev.RELEVANCE_NAMES[2])


class ScoreMappingTests(unittest.TestCase):
    def test_the_three_levels_map_to_the_names_downstream_compares(self):
        # 下游是字面量比较（generate_ai_report.py:123 等），必须正好这三个字。
        # 切点定在 0.5 与 1.5：九篇实测里 score 落在 [0,2]，所以三档等宽。
        self.assertEqual(jev.score_to_relevance(0.0), '低')
        self.assertEqual(jev.score_to_relevance(0.4), '低')
        self.assertEqual(jev.score_to_relevance(0.5), '中')
        self.assertEqual(jev.score_to_relevance(1.4), '中')
        self.assertEqual(jev.score_to_relevance(1.5), '高')
        self.assertEqual(jev.score_to_relevance(2.0), '高')

    def test_boundaries_do_not_flip_with_bankers_rounding(self):
        # round(0.5) 在 Python 里是 0、round(1.5) 是 2——正是这种跳档要用
        # int(x + 0.5) 避开，让两个切点都往上取。
        self.assertEqual(jev.score_to_relevance(0.5), '中')
        self.assertEqual(jev.score_to_relevance(1.5), '高')

    def test_out_of_range_is_clamped_rather_than_crashing(self):
        self.assertEqual(jev.score_to_relevance(-3), '低')
        self.assertEqual(jev.score_to_relevance(99), '高')

    def test_a_missing_score_is_an_error_not_a_default(self):
        with self.assertRaises(jev.JevError):
            jev.score_to_relevance(None)


class DecideArticleTests(unittest.TestCase):
    def _client(self):
        return jev.JevClient('key-not-printed')

    def test_it_parses_choice_score_and_noul(self):
        with patch.object(jev.urllib.request, 'urlopen', return_value=FakeResponse(answer())):
            result = self._client().decide_article('某标题', '某正文', TOPICS)
        self.assertEqual(result['topic'], '学术')
        self.assertEqual(result['topicConfidence'], 0.93)
        self.assertEqual(result['relevance'], '高')
        self.assertEqual(result['relevanceScore'], 1.8)
        self.assertEqual(result['includeScore'], 0.9)

    def test_the_state_carries_no_answer_smelling_fields(self):
        """把 frontmatter 一起发过去，模型照抄就能"一致"——那是个假结论。"""
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['body'] = json.loads(request.data.decode('utf-8'))
            return FakeResponse(answer())

        with patch.object(jev.urllib.request, 'urlopen', fake_urlopen):
            self._client().decide_article('标题', '正文内容', TOPICS)
        state = captured['body']['state']
        self.assertNotIn('topic:', state)
        self.assertNotIn('relevance:', state)
        self.assertEqual(captured['body']['model'], jev.DEFAULT_MODEL)
        self.assertEqual(captured['body']['questions']['topic']['criteria'].keys(),
                         set(TOPICS) if isinstance(
                             captured['body']['questions']['topic']['criteria'], dict)
                         else set(captured['body']['questions']['topic']['criteria']))

    def test_a_topic_outside_the_vocabulary_raises(self):
        # 往 frontmatter 写一个陌生主题会污染下游分组，宁可报错让调用方回退。
        with patch.object(jev.urllib.request, 'urlopen',
                          return_value=FakeResponse(answer(topic='财经'))):
            with self.assertRaises(jev.JevError):
                self._client().decide_article('标题', '正文', TOPICS)

    def test_a_response_without_answers_raises(self):
        with patch.object(jev.urllib.request, 'urlopen',
                          return_value=FakeResponse({'model': 'jev-1.13.0'})):
            with self.assertRaises(jev.JevError):
                self._client().decide_article('标题', '正文', TOPICS)


class FailureModeTests(unittest.TestCase):
    def setUp(self):
        # 重试带退避（最坏 2.4s）。单元测试不该真的等——但也不能因此不测重试，
        # 所以只把 sleep 桩掉，重试次数与判定逻辑照跑。
        patcher = patch.object(jev.time, 'sleep')
        patcher.start()
        self.addCleanup(patcher.stop)

    def _http_error(self, code, body):
        return urllib.error.HTTPError(jev.ENDPOINT, code, 'err', {},
                                     io.BytesIO(body.encode('utf-8')))

    def test_a_rejected_key_says_so_and_never_leaks_the_key(self):
        secret = 'key-not-printed'
        with patch.object(jev.urllib.request, 'urlopen',
                          side_effect=self._http_error(403, '{"detail":"nope"}')):
            with self.assertRaises(jev.JevError) as caught:
                jev.JevClient(secret).decide('s', {})
        message = str(caught.exception)
        self.assertIn('鉴权', message)
        self.assertNotIn(secret, message)

    def test_an_error_body_is_truncated(self):
        with patch.object(jev.urllib.request, 'urlopen',
                          side_effect=self._http_error(500, 'x' * 5000)):
            with self.assertRaises(jev.JevError) as caught:
                jev.JevClient('k').decide('s', {})
        self.assertLess(len(str(caught.exception)), 700)

    def test_an_unreachable_host_raises_rather_than_returning_nothing(self):
        with patch.object(jev.urllib.request, 'urlopen',
                          side_effect=urllib.error.URLError('no route')):
            with self.assertRaises(jev.JevError):
                jev.JevClient('k').decide('s', {})

    def test_a_transient_failure_is_retried(self):
        """实测遇到过 529 system_overloaded —— 官方参考实现默认对它重试 2 次。"""
        calls = []

        def flaky(request, timeout=None):
            calls.append(request)
            if len(calls) == 1:
                raise self._http_error(529, '{"detail":{"error_type":"system_overloaded"}}')
            return FakeResponse(answer())

        with patch.object(jev.urllib.request, 'urlopen', flaky):
            answers, _usage = jev.JevClient('k').decide('s', {})
        self.assertEqual(len(calls), 2)
        self.assertIn('topic', answers)

    def test_a_rejected_key_is_not_retried(self):
        # 鉴权失败重试多少次都一样，白等 2.4 秒。
        calls = []

        def rejected(request, timeout=None):
            calls.append(request)
            raise self._http_error(403, '{"detail":"no"}')

        with patch.object(jev.urllib.request, 'urlopen', rejected):
            with self.assertRaises(jev.JevError):
                jev.JevClient('k').decide('s', {})
        self.assertEqual(len(calls), 1)

    def test_a_client_without_a_key_cannot_be_built(self):
        with self.assertRaises(jev.JevError):
            jev.JevClient('')


class ResolveKeyTests(unittest.TestCase):
    def test_explicit_beats_env_beats_config(self):
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'from-env'}):
            self.assertEqual(jev.resolve_key('explicit'), 'explicit')
            self.assertEqual(jev.resolve_key(), 'from-env')

    def test_nothing_configured_yields_no_key_rather_than_raising(self):
        with patch.dict(os.environ, {}, clear=True):
            stub = types.SimpleNamespace(get_typesafe_key=lambda config=None: '')
            with patch.dict(sys.modules, {'_utils': stub}):
                self.assertEqual(jev.resolve_key(), '')

    def test_a_config_that_cannot_be_decrypted_degrades_to_no_key(self):
        """跨机器拷 config.json 时密文解不开。那该退老路，不该把日报打挂。"""
        def explode(config=None):
            raise ValueError('cannot decrypt')

        stub = types.SimpleNamespace(get_typesafe_key=explode)
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(sys.modules, {'_utils': stub}):
                self.assertEqual(jev.resolve_key(), '')

    def test_the_config_key_is_used_when_there_is_no_env(self):
        stub = types.SimpleNamespace(get_typesafe_key=lambda config=None: 'from-config')
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(sys.modules, {'_utils': stub}):
                self.assertEqual(jev.resolve_key(), 'from-config')

    def test_create_client_returns_none_without_a_key(self):
        with patch.dict(os.environ, {}, clear=True):
            stub = types.SimpleNamespace(get_typesafe_key=lambda config=None: '')
            with patch.dict(sys.modules, {'_utils': stub}):
                self.assertIsNone(jev.create_client())

    def test_create_client_builds_one_when_a_key_exists(self):
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'from-env'}):
            self.assertIsInstance(jev.create_client(), jev.JevClient)


class ChoiceContractTests(unittest.TestCase):
    """`choice` 的四条硬断言：契约漂了就**当场抛**，不放行。

    这四条是从 browser-use × TypeSafe 的官方示范仓库（`validate_choice`）抄来的，
    并按实测响应校准过（`jev-1.13.0` 的真实返回四条全过）。

    为什么值一个专门的测试类：契约漂移的失效方式不是报错，而是**选错了但看起来
    正常**。本仓库已经踩过一次同型的坑——把 `noul`（概率浮点）当布尔读，于是每个
    问题都读成"否"，而输出把责任推给了模型。
    """

    def _q(self):
        return {'type': 'choice', 'criteria': {'甲': '选这个', '乙': '或这个'}}

    def test_a_well_formed_answer_passes(self):
        jev._check_choice('k', self._q(),
                          {'choice': '甲', 'probabilities': {'甲': 1.0, '乙': 0.0}})
        jev._check_choice('k', self._q(),
                          {'choice': '甲', 'probabilities': {'甲': 0.6, '乙': 0.4}})

    def test_missing_probabilities_raises(self):
        with self.assertRaises(jev.JevError):
            jev._check_choice('k', self._q(), {'choice': '甲'})

    def test_a_key_set_that_differs_from_criteria_raises(self):
        # 多出来的键意味着 criteria 已经漂了：那个选项根本不是我们给的。
        with self.assertRaises(jev.JevError):
            jev._check_choice('k', self._q(),
                              {'choice': '甲', 'probabilities': {'甲': 0.5, '丙': 0.5}})
        with self.assertRaises(jev.JevError):
            jev._check_choice('k', self._q(),
                              {'choice': '甲', 'probabilities': {'甲': 1.0}})

    def test_probabilities_outside_zero_to_one_raise(self):
        with self.assertRaises(jev.JevError):
            jev._check_choice('k', self._q(),
                              {'choice': '甲', 'probabilities': {'甲': 1.4, '乙': -0.4}})

    def test_probabilities_that_do_not_sum_to_one_raise(self):
        # `answer(topic='财经')` 那种"主题不在词表里"的构造会走到这里：全 0。
        with self.assertRaises(jev.JevError):
            jev._check_choice('k', self._q(),
                              {'choice': '甲', 'probabilities': {'甲': 0.0, '乙': 0.0}})

    def test_a_choice_that_is_not_the_argmax_raises(self):
        """**最要紧的一条**：它不是错答案，它是"看起来完全正常"的错答案。"""
        with self.assertRaises(jev.JevError) as ctx:
            jev._check_choice('k', self._q(),
                              {'choice': '乙', 'probabilities': {'甲': 0.9, '乙': 0.1}})
        self.assertIn('argmax', str(ctx.exception))

    def test_a_choice_outside_the_criteria_raises(self):
        with self.assertRaises(jev.JevError):
            jev._check_choice('k', self._q(),
                              {'choice': '丙', 'probabilities': {'甲': 1.0, '乙': 0.0}})

    def test_other_question_types_are_left_alone(self):
        # noul / score 没有 criteria 与 probabilities 的对应关系，不该被这套检查误伤。
        jev._check_choice('k', {'type': 'noul'}, {'noul': 0.9})
        jev._check_choice('k', {'type': 'score'}, {'score': 1.7})

    def test_the_validation_is_what_decide_actually_runs(self):
        """别把校验写成没人调用的函数——这里走一遍 `decide` 的完整路径。"""
        bad = answer()
        bad['answers']['topic'] = {'type': 'choice', 'choice': 'AI',
                                   'probabilities': {t: 0.0 for t in TOPICS}}
        with patch.object(jev.urllib.request, 'urlopen',
                          return_value=FakeResponse(bad)):
            with self.assertRaises(jev.JevError):
                jev.JevClient('k').decide('s', jev.build_questions(TOPICS))


class StructuredInstructionTests(unittest.TestCase):
    """`instructions` 可以是**结构化对象**，不只是字符串，而且要原样送出去。

    （实测：`{"goal": ..., "rules": [...]}` 被服务端接受。）本仓库的提示词仍是字符串
    ——把日报的判据改成对象形式会改变模型行为，而那套切点是按字符串版校准过的，
    所以这里只钉"能力在、且不会被拍平"，不用它。
    """

    def test_a_dict_instruction_survives_to_the_wire(self):
        questions = {'q': {'type': 'noul',
                           'instructions': {'goal': '判断某事', 'rules': ['规则一', '规则二']}}}
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['body'] = json.loads(request.data.decode('utf-8'))
            return FakeResponse({'answers': {'q': {'type': 'noul', 'noul': 0.9}},
                                 'usage': {}})

        with patch.object(jev.urllib.request, 'urlopen', fake_urlopen):
            jev.JevClient('k').decide('s', questions)
        sent = captured['body']['questions']['q']['instructions']
        self.assertEqual(sent, {'goal': '判断某事', 'rules': ['规则一', '规则二']})


class ServedModelTests(unittest.TestCase):
    """请求的是**别名**，响应说的是**实际服务的版本**。落盘后者。"""

    def test_the_served_model_lands_in_usage_and_on_the_client(self):
        client = jev.JevClient('k')
        with patch.object(jev.urllib.request, 'urlopen',
                          return_value=FakeResponse(answer())):
            _, usage = client.decide('s', jev.build_questions(TOPICS))
        self.assertEqual(usage['model'], 'jev-1.13.0')
        self.assertEqual(client.last_model, 'jev-1.13.0')
        # 别名与实际版本**不该**相等——相等就说明这个字段没在报真话。
        self.assertNotEqual(usage['model'], client.model)

    def test_a_response_without_a_model_field_leaves_it_unset(self):
        payload = answer()
        payload.pop('model')
        client = jev.JevClient('k')
        with patch.object(jev.urllib.request, 'urlopen',
                          return_value=FakeResponse(payload)):
            _, usage = client.decide('s', jev.build_questions(TOPICS))
        self.assertNotIn('model', usage)
        self.assertIsNone(client.last_model)


if __name__ == '__main__':
    unittest.main()
