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
        # **USERPROFILE 也得记着**：Windows 上 `os.path.expanduser('~')` 读的是它，
        # 只还原 HOME 会让后面比较 `~` 的测试（source_prior_test）看到临时目录。
        self._old_profile = os.environ.get('USERPROFILE')
        self._old_base = os.environ.pop('WEFLOW_AI_BASE_URL', None)
        self._old_model = os.environ.pop('WEFLOW_AI_MODEL', None)
        os.environ['HOME'] = str(self.home)
        os.environ['USERPROFILE'] = str(self.home)

        # 重新加载，让 CONFIG_PATH 指向临时家目录
        self._orig_utils = sys.modules.get('_utils')   # 跑完要放回去，见 tearDown
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
        if self._old_profile is not None:
            os.environ['USERPROFILE'] = self._old_profile
        # **把原来的模块放回去，而不是只把它从 sys.modules 里删掉。**
        # 只删的后果：此后任何 `import _utils` 都会**新建一个模块对象**，而别处
        # （例如 `jev.create_client` 里的懒加载）拿到的是这个新对象；
        # 用 `patch.multiple(_utils, ...)` 打桩的测试打的却仍是旧对象，桩就打空了。
        # 实测（2026-11-13）：全量 discover 时 `jev_openrouter_test` 27 项失败，
        # 单独跑该文件却全过——就是这里留下的分叉。按文件名排序这两个文件挨得很近，
        # 于是"跑全量才红"。
        sys.modules.pop('_utils', None)
        if self._orig_utils is not None:
            sys.modules['_utils'] = self._orig_utils

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
