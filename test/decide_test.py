"""本机判断层的请求校验与回包。

这个命令的价值主张是"判断得起、而且答案不用解析"，所以它必须**在本机把形状不对的
请求挡住**：发出去换一个服务端的 422，只会得到一条说得出"哪个字段不合法"、
说不出"你本来想干什么"的错误。这些测试盯的就是那几类本来会变成 422 的输入。
"""
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('decide', SCRIPTS / 'decide.py')
decide = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decide)


def run_cli(payload, extra=None):
    """把请求喂给 main()，返回 (退出码, 解析后的输出)。"""
    argv = ['decide.py'] + (extra or [])
    buffer = io.StringIO()
    with patch.object(sys, 'argv', argv), \
            patch.object(sys, 'stdin', io.StringIO(json.dumps(payload, ensure_ascii=False))), \
            redirect_stdout(buffer):
        code = decide.main()
    return code, json.loads(buffer.getvalue())


class ValidationTests(unittest.TestCase):
    def test_a_well_formed_request_passes(self):
        _, _, error = decide.validate_request({
            'state': '一段文本',
            'questions': {'甲': {'type': 'noul'}},
        })
        self.assertIsNone(error)

    def test_every_bad_shape_is_rejected_with_a_specific_reason(self):
        cases = [
            ('not-an-object', []),
            ('missing state', {'questions': {'a': {'type': 'noul'}}}),
            ('blank state', {'state': '   ', 'questions': {'a': {'type': 'noul'}}}),
            ('wrong state type', {'state': 42, 'questions': {'a': {'type': 'noul'}}}),
            ('missing questions', {'state': 'x'}),
            ('empty questions', {'state': 'x', 'questions': {}}),
            ('bad type', {'state': 'x', 'questions': {'a': {'type': '猜'}}}),
            ('choice without criteria', {'state': 'x', 'questions': {'a': {'type': 'choice'}}}),
            ('choice with empty criteria',
             {'state': 'x', 'questions': {'a': {'type': 'choice', 'criteria': {}}}}),
            ('score with one level',
             {'state': 'x', 'questions': {'a': {'type': 'score', 'criteria': ['仅此一档']}}}),
            ('score with no criteria',
             {'state': 'x', 'questions': {'a': {'type': 'score'}}}),
        ]
        for label, payload in cases:
            with self.subTest(case=label):
                _, _, error = decide.validate_request(payload)
                self.assertIsNotNone(error, '本该被拒：%s' % label)

    def test_a_score_needs_two_levels_because_position_is_the_value(self):
        """只有一档时 score 恒等于 0，问出来的东西没有信息量。"""
        _, _, error = decide.validate_request(
            {'state': 'x', 'questions': {'a': {'type': 'score', 'criteria': ['一样']}}})
        self.assertIn('至少两个', error)

    def test_a_non_dict_state_is_allowed_to_be_an_object_or_array(self):
        for state in ({'标题': 'x'}, [1, 2, 3], 'plant'):
            _, _, error = decide.validate_request(
                {'state': state, 'questions': {'a': {'type': 'noul'}}})
            self.assertIsNone(error, 'state=%r 本该被接受' % (state,))


class CliBehaviourTests(unittest.TestCase):
    GOOD = {'state': '一段文本', 'questions': {'甲': {'type': 'noul'}}}

    def test_a_malformed_request_never_reaches_the_network(self):
        with patch.object(decide, 'create_client') as client:
            code, body = run_cli({'state': 'x', 'questions': {'a': {'type': '猜'}}})
        self.assertEqual(code, 2)
        self.assertEqual(body['code'], 'INVALID_REQUEST')
        client.assert_not_called()

    def test_dry_run_reports_the_shape_without_calling_the_model(self):
        with patch.object(decide, 'create_client') as client:
            code, body = run_cli(self.GOOD, ['--dry-run'])
        self.assertEqual(code, 0)
        self.assertTrue(body['dryRun'])
        self.assertEqual(body['questionCount'], 1)
        self.assertEqual(body['questions'], ['甲'])
        self.assertFalse(body['readsLocalData'])
        client.assert_not_called()

    def test_an_empty_stdin_is_reported_rather_than_crashing(self):
        buffer = io.StringIO()
        with patch.object(sys, 'argv', ['decide.py']), \
                patch.object(sys, 'stdin', io.StringIO('  ')), \
                redirect_stdout(buffer):
            code = decide.main()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(buffer.getvalue())['code'], 'EMPTY_REQUEST')

    def test_broken_json_is_reported_rather_than_crashing(self):
        buffer = io.StringIO()
        with patch.object(sys, 'argv', ['decide.py']), \
                patch.object(sys, 'stdin', io.StringIO('{不是 json')), \
                redirect_stdout(buffer):
            code = decide.main()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(buffer.getvalue())['code'], 'INVALID_JSON')

    def test_no_key_is_a_clear_refusal_not_a_traceback(self):
        with patch.object(decide, 'create_client', return_value=None):
            code, body = run_cli(self.GOOD)
        self.assertEqual(code, 2)
        self.assertEqual(body['code'], 'NO_KEY')

    def test_a_successful_call_returns_answers_and_the_cost(self):
        class Client:
            model = 'jev-latest'

            def decide(self, state, questions):
                return ({'甲': {'type': 'noul', 'noul': 0.9}},
                        {'input_tokens': 1_000_000, 'output_tokens': 5})

        with patch.object(decide, 'create_client', return_value=Client()):
            code, body = run_cli(self.GOOD)
        self.assertEqual(code, 0)
        self.assertEqual(body['answers']['甲']['noul'], 0.9)
        # 成本要回给调用方：一个 Agent 得能自己判断"再问一批"划不划算。
        self.assertAlmostEqual(body['costUsd'], 0.042, places=6)

    def test_a_failed_call_is_a_structured_error_not_a_traceback(self):
        from jev_client import JevError

        class Client:
            model = 'jev-latest'

            def decide(self, state, questions):
                raise JevError('HTTP 529: overloaded')

        with patch.object(decide, 'create_client', return_value=Client()):
            code, body = run_cli(self.GOOD)
        self.assertEqual(code, 1)
        self.assertEqual(body['code'], 'DECIDE_FAILED')
        self.assertIn('529', body['error'])


class BatchModeTests(unittest.TestCase):
    """批量模式：一个 glob × 一批问题，自动展开成 N×M 个问题。

    **这个测试类的存在本身是有原因的**：批量模式最初是没写测试就加进去的，
    结果它一跑就崩（一段删除补丁把 `main()` 弄乱了，批量分支落进了一段引用未定义
    变量的旧代码）。靠"跑一次看看"才发现——所以这里补上这条路径。
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(prefix='decide-batch-')
        self.tmp = self._tmp.name
        for name, body in (('a.py', 'x' * 4000), ('b.py', 'print(1)'),
                           ('c.md', '# note')):
            with open(Path(self.tmp) / name, 'w', encoding='utf-8') as handle:
                handle.write(body)
        self.addCleanup(self._tmp.cleanup)

    def run_main(self, extra, stub=None):
        buffer = io.StringIO()
        patcher = patch.object(decide, 'create_client',
                               return_value=stub) if stub is not None else None
        if patcher:
            patcher.start()
        try:
            with patch.object(sys, 'argv', ['decide.py'] + extra), \
                    redirect_stdout(buffer):
                code = decide.main()
        finally:
            if patcher:
                patcher.stop()
        return code, json.loads(buffer.getvalue())

    class Stub:
        model = 'jev-latest'

        def __init__(self):
            self.questions = None

        def decide(self, state, questions):
            self.questions = questions
            return ({name: {'type': 'noul', 'noul': 0.5} for name in questions},
                    {'input_tokens': 10, 'output_tokens': 1})

    def test_one_question_per_file_and_question_pair(self):
        state, questions, meta, error = decide.expand_batch(
            [str(Path(self.tmp) / '*.py')], ['甲吗', '乙吗'], 1500, 50)
        self.assertIsNone(error)
        self.assertEqual(len(questions), 4)          # 2 个文件 × 2 个问题
        self.assertEqual(sorted(questions), ['f0|q0', 'f0|q1', 'f1|q0', 'f1|q1'])
        self.assertEqual(meta['files'], ['a.py', 'b.py'])

    def test_the_answer_keys_map_back_to_names_without_parsing(self):
        # 问题名是序号，映射由 batch.files / batch.asks 给出——文件名里有 '|'、
        # 空格和中文标点，编进问题名会让调用方不得不去解析字符串。
        state, questions, meta, error = decide.expand_batch(
            [str(Path(self.tmp) / '*.py')], ['甲吗'], 1500, 50)
        self.assertEqual(meta['asks'], ['甲吗'])
        self.assertIn('a.py', questions['f0|q0']['instructions'])

    def test_the_state_says_how_much_of_each_file_it_carries(self):
        # 证据量决定上限（实测 14 行 → 77%，1500 字符 → 89%），所以它必须写在明面上。
        state, _q, meta, _e = decide.expand_batch(
            [str(Path(self.tmp) / 'a.py')], ['甲吗'], 100, 50)
        self.assertEqual(meta['maxChars'], 100)
        self.assertIn('前 100 个字符', state)
        self.assertNotIn('x' * 200, state)

    def test_the_file_order_is_deterministic(self):
        # 同一批文件两次跑必须展开成同样的顺序，否则问题名会和文件错位。
        first, _q1, meta1, _e1 = decide.expand_batch(
            [str(Path(self.tmp) / '*.py')], ['甲吗'], 1500, 50)
        second, _q2, meta2, _e2 = decide.expand_batch(
            [str(Path(self.tmp) / '*.py')], ['甲吗'], 1500, 50)
        self.assertEqual(meta1['files'], meta2['files'])

    def test_the_limit_caps_the_file_count(self):
        _state, _q, meta, _e = decide.expand_batch(
            [str(Path(self.tmp) / '*')], ['甲吗'], 1500, 2)
        self.assertEqual(len(meta['files']), 2)

    def test_no_match_is_an_error_not_an_empty_request(self):
        state, questions, meta, error = decide.expand_batch(
            [str(Path(self.tmp) / 'nothing-*.zig')], ['甲吗'], 1500, 50)
        self.assertIsNotNone(error)
        self.assertIn('没有匹配到任何文件', error)

    def test_dry_run_reports_that_it_read_local_files(self):
        # `--request` 那条路一个本地文件都不读，批量模式会读——两者不能共用一句声明。
        code, body = self.run_main(['--over', str(Path(self.tmp) / '*.py'),
                                    '--ask', '甲吗', '--dry-run'])
        self.assertEqual(code, 0)
        self.assertTrue(body['readsLocalData'])
        self.assertEqual(body['fileCount'], 2)
        self.assertEqual(body['questionCount'], 2)
        self.assertEqual(body['maxChars'], 1500)

    def test_batch_mode_actually_reaches_the_model_and_returns_the_mapping(self):
        # 回归：这条路径曾经一跑就崩（引用未定义的变量），靠真跑才发现。
        stub = self.Stub()
        code, body = self.run_main(['--over', str(Path(self.tmp) / '*.py'),
                                    '--ask', '甲吗'], stub=stub)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(body['batch']['files']), ['a.py', 'b.py'])
        self.assertEqual(sorted(body['answers']), ['f0|q0', 'f1|q0'])
        self.assertEqual(len(stub.questions), 2)

    def test_batch_mode_without_a_question_is_rejected(self):
        code, body = self.run_main(['--over', str(Path(self.tmp) / '*.py')])
        self.assertEqual(code, 2)
        self.assertEqual(body['code'], 'INVALID_REQUEST')


if __name__ == '__main__':
    unittest.main()
