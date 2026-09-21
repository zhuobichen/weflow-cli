"""Security regression for secrets passed through the daily pipeline."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT = SCRIPTS_DIR / 'pipeline.py'
spec = importlib.util.spec_from_file_location('pipeline_security', SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)

import _utils  # noqa: E402  (importable once scripts/ is on sys.path)


class PipelineSecretTests(unittest.TestCase):
    def test_api_key_is_inherited_through_environment_not_child_arguments(self):
        secret = 'synthetic-api-key-for-test'
        child_arguments = []

        def capture_step(_name, args):
            child_arguments.extend(args)
            return True

        argv = [
            'pipeline.py', '--api-key', secret, '--skip-classify', '--skip-wiki',
            '--skip-vault', '--skip-html', '--skip-ai-report',
        ]
        # main() reads the config through _utils.load_config(), which resolves
        # CONFIG_PATH at call time - so pointing it at a throwaway file keeps
        # this test off the developer's real ~/.weflow-cli/config.json and
        # working on a machine that has no config at all.
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'config.json'
            config_path.write_text(json.dumps({}), encoding='utf-8')
            with patch.object(pipeline, 'run_step', capture_step), \
                    patch.object(sys, 'argv', argv), \
                    patch.object(_utils, 'CONFIG_PATH', str(config_path)), \
                    patch.dict(os.environ, {}, clear=False):
                old_key = os.environ.pop('DEEPSEEK_API_KEY', None)
                try:
                    pipeline.main()
                    self.assertEqual(os.environ.get('DEEPSEEK_API_KEY'), secret)
                    self.assertNotIn(secret, child_arguments)
                    self.assertNotIn('--api-key', child_arguments)
                finally:
                    if old_key is None:
                        os.environ.pop('DEEPSEEK_API_KEY', None)
                    else:
                        os.environ['DEEPSEEK_API_KEY'] = old_key


class TypesafeKeyTests(unittest.TestCase):
    """Jev 的 key 走的是同一条规矩：只从配置来，不解密失败就回空串。"""

    def test_a_missing_field_yields_an_empty_string(self):
        self.assertEqual(_utils.get_typesafe_key({}), '')

    def test_a_plaintext_value_passes_through(self):
        self.assertEqual(_utils.get_typesafe_key({'typesafeApiKey': 'plain'}), 'plain')

    def test_an_undecryptable_value_degrades_instead_of_raising(self):
        # 跨机器拷 config.json 就会遇到：密文解不开。那必须表现为"没配 key"，
        # 让分类退回 LLM 路径，而不是把整天的日报打挂。
        self.assertEqual(_utils.get_typesafe_key({'typesafeApiKey': 'lock:not-base64!!'}), '')

    def test_a_missing_config_file_degrades_too(self):
        with patch.object(_utils, 'CONFIG_PATH', '/nonexistent/weflow/config.json'):
            self.assertEqual(_utils.get_typesafe_key(), '')

    def test_the_real_key_is_never_the_same_as_a_neighbouring_field(self):
        # 只读自己那一项，别把 deepseek 的 key 当 Jev 的用。
        config = {'deepseekApiKey': 'ds-key', 'typesafeApiKey': 'ts-key'}
        self.assertEqual(_utils.get_typesafe_key(config), 'ts-key')


if __name__ == '__main__':
    unittest.main()
