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


if __name__ == '__main__':
    unittest.main()
