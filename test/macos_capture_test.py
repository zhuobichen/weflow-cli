"""macOS 捕获脚本的可离线验证部分。

真机验证需要一台 Mac（SIP 状态、真实客户端版本都影响结果），这里只钉住
**不依赖 macOS 也必然成立**的几件事，避免这段代码在无人看管下腐烂：

1. lldb 脚本同时覆盖 arm64（x1/x2）与 x86_64（rsi/rdx）两套寄存器；
2. 日志路径被真的写进了脚本（漏了它，捕获永远返回空）；
3. 库目录发现认得 `~/Library/Containers/com.tencent.xinWeChat/...` 两种布局；
4. 非 macOS 平台调用会明确拒绝，而不是静默返回空。
"""
import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'test'))

from _tmpenv import mkdtemp  # noqa: E402

spec = importlib.util.spec_from_file_location('macos_capture', ROOT / 'scripts' / 'macos_capture.py')
mac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mac)


class MacCaptureStaticTests(unittest.TestCase):
    def test_lldb_script_covers_both_architectures(self):
        self.assertIn("reg('x1')", mac.PY_BODY)
        self.assertIn("reg('rsi')", mac.PY_BODY)
        self.assertIn('arm64', mac.PY_BODY)

    def test_lldb_script_embeds_log_path(self):
        log = '/tmp/hits.log'
        built = mac.LLDB_TEMPLATE.replace(
            'script\nimport gdb\nPYTHON_BLOCK',
            'script\n' + mac.PY_BODY.replace('LOG', repr(log)),
        )
        self.assertIn(log, built)
        self.assertIn('CCKeyDerivationPBKDF', built)

    def test_db_candidates_finds_containers_layout(self):
        home = pathlib.Path(mkdtemp('wf-mac-home-'))
        try:
            base = (home / 'Library' / 'Containers' / 'com.tencent.xinWeChat'
                    / 'Data' / 'Documents' / 'xwechat_files')
            (base / 'wxid_example' / 'db_storage').mkdir(parents=True)
            with patch.object(pathlib.Path, 'home', return_value=home):
                found = mac.db_candidates()
            self.assertIn(base, found)
            self.assertIn(base / 'wxid_example' / 'db_storage', found)
        finally:
            import shutil
            shutil.rmtree(home, ignore_errors=True)

    def test_non_darwin_is_rejected(self):
        with patch.object(sys, 'argv', ['macos_capture.py']), \
                patch.object(sys, 'platform', 'win32'):
            with self.assertRaises(SystemExit) as ctx:
                mac.main()
        self.assertIn('macOS', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
