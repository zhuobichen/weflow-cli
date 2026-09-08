"""Security regression for secrets passed through the daily pipeline."""

import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'pipeline.py'
spec = importlib.util.spec_from_file_location('pipeline_security', SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


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
        with patch.object(pipeline, 'run_step', capture_step), \
                patch.object(sys, 'argv', argv), \
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


if __name__ == '__main__':
    unittest.main()
