"""`_custom_endpoint()` 的优先级：环境变量 > 配置 > 默认。

为什么值得单独钉住：它是"换个端点做一次对照"的唯一开关。写错的表现是**不报错**——
请求照样发出去，只是发去了错的地方（账单和结果都变），排查时根本想不到是这里。
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'test'))

from _tmpenv import mkdtemp  # noqa: E402


class CustomEndpointTests(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(mkdtemp('wf-endpoint-'))
        (self.home / '.weflow-cli').mkdir(parents=True, exist_ok=True)
        self._old_home = os.environ.get('HOME')
        self._old_base = os.environ.pop('WEFLOW_AI_BASE_URL', None)
        self._old_model = os.environ.pop('WEFLOW_AI_MODEL', None)
        os.environ['HOME'] = str(self.home)
        os.environ['USERPROFILE'] = str(self.home)

        # 重新加载，让 CONFIG_PATH 指向临时家目录
        if '_utils' in sys.modules:
            del sys.modules['_utils']
        import _utils
        self.u = _utils

    def tearDown(self):
        for key, val in (('WEFLOW_AI_BASE_URL', self._old_base), ('WEFLOW_AI_MODEL', self._old_model)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        if self._old_home is not None:
            os.environ['HOME'] = self._old_home
        if '_utils' in sys.modules:
            del sys.modules['_utils']

    def _write_config(self, **kw):
        cfg = pathlib.Path(self.u.CONFIG_PATH)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(kw, ensure_ascii=False), encoding='utf-8')

    def test_env_beats_config(self):
        self._write_config(aiBaseUrl='https://config.example/v1', aiModel='config-model')
        os.environ['WEFLOW_AI_BASE_URL'] = 'https://env.example/v1'
        os.environ['WEFLOW_AI_MODEL'] = 'Jev'
        self.assertEqual(self.u._custom_endpoint(), ('https://env.example/v1', 'Jev'))

    def test_env_base_without_model_falls_back_to_default_model(self):
        os.environ['WEFLOW_AI_BASE_URL'] = 'https://env.example/v1'
        self.assertEqual(self.u._custom_endpoint(), ('https://env.example/v1', 'deepseek-chat'))

    def test_config_used_when_env_absent(self):
        self._write_config(aiBaseUrl='https://config.example/v1/', aiModel='cfg-model')
        self.assertEqual(self.u._custom_endpoint(), ('https://config.example/v1', 'cfg-model'))

    def test_nothing_configured_returns_none(self):
        self.assertEqual(self.u._custom_endpoint(), (None, None))

    def test_call_deepseek_honours_the_custom_endpoint(self):
        """这条是本次修的 bug：`call_deepseek` 曾直接 new DeepSeekEngine，绕过 aiBaseUrl。"""
        os.environ['WEFLOW_AI_BASE_URL'] = 'https://env.example/v1'
        os.environ['WEFLOW_AI_MODEL'] = 'Jev'
        engine = self.u.create_engine('deepseek', 'dummy-key')
        self.assertEqual(engine.base_url, 'https://env.example/v1')
        self.assertEqual(engine.model, 'Jev')


if __name__ == '__main__':
    unittest.main()
