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
        arm = mac.build_lldb_script(*mac.REGISTERS['arm64'])
        intel = mac.build_lldb_script(*mac.REGISTERS['x86_64'])
        self.assertIn('register read x1 x2', arm)
        self.assertIn('register read rsi rdx', intel)
        self.assertIn('CCKeyDerivationPBKDF', arm)

    def test_lldb_script_autocontinues(self):
        """回调里没有 process continue，微信就会冻在断点上。"""
        script = mac.build_lldb_script('x1', 'x2')
        self.assertIn('-o \'process continue\'', script)   # 回调内自动放行
        self.assertTrue(script.rstrip().endswith('process continue'))  # 主 continue

    def test_no_gdb_dependency(self):
        """旧版在 lldb 里 import gdb 必炸——这条钉住回归（只看代码，不看文档）。"""
        import ast as _ast
        src = (ROOT / 'scripts' / 'macos_capture.py').read_text(encoding='utf-8')
        tree = _ast.parse(src)
        imported = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, _ast.ImportFrom):
                imported.add((node.module or '').split('.')[0])
        self.assertNotIn('gdb', imported)

    def test_watchers_cover_helper_process(self):
        """口令可能在 WeChatAppEx 里派生，两类进程都得扫。"""
        src = (ROOT / 'scripts' / 'macos_capture.py').read_text(encoding='utf-8')
        self.assertIn('WeChatAppEx', src)

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

    def test_db_candidates_keeps_every_known_layout(self):
        """四种布局都要认——少一条，那类用户就是"找不到库目录"（曾经被 PR 删掉两条）。"""
        home = pathlib.Path(mkdtemp('wf-mac-home-'))
        try:
            layouts = [
                home / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files',
                home / 'Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat',
                home / 'Library/Containers/com.tencent.WeChat/Data/Documents/xwechat_files',
                home / 'Library/Application Support/com.tencent.xinWeChat',
            ]
            for base in layouts:
                base.mkdir(parents=True, exist_ok=True)
            with patch.object(pathlib.Path, 'home', return_value=home):
                found = mac.db_candidates()
            for base in layouts:
                self.assertIn(base, found, f'这种布局没有被认出来：{base}')
        finally:
            import shutil
            shutil.rmtree(home, ignore_errors=True)

    def test_non_darwin_is_rejected(self):
        with patch.object(sys, 'argv', ['macos_capture.py']), \
                patch.object(sys, 'platform', 'win32'):
            with self.assertRaises(SystemExit) as ctx:
                mac.main()
        self.assertIn('macOS', str(ctx.exception))


class AttachmentPolicyTests(unittest.TestCase):
    """attach 的调度规则。每一条都对应一次真机事故。"""

    def test_new_pid_is_attached(self):
        self.assertEqual(mac.plan_attachments([100], {}, set()), [100])

    def test_pid_that_failed_attach_is_never_retried(self):
        """SIP 没关时若每 0.5s 重开一个 lldb，5 分钟就是六百次 + 六百个泄漏的 fd。"""
        self.assertEqual(mac.plan_attachments([100, 200], {}, {100}), [200])
        self.assertEqual(mac.plan_attachments([100], {100: _DeadProc()}, {100}), [])

    def test_pid_whose_lldb_died_is_reattached_once(self):
        """lldb 自己崩了不代表不能再来一次（但失败过的不在此列）。"""
        self.assertEqual(mac.plan_attachments([100], {100: _DeadProc()}, set()), [100])

    def test_live_pid_is_not_attached_twice(self):
        self.assertEqual(mac.plan_attachments([100], {100: _LiveProc()}, set()), [])


class _DeadProc:
    def poll(self):
        return 1


class _LiveProc:
    def poll(self):
        return None


class LengthDiagnosticTests(unittest.TestCase):
    """口令长度对不上时要说清楚，而不是笼统地"没有命中"。"""

    def test_reads_len_register_values(self):
        logdir = pathlib.Path(mkdtemp('wf-mac-len-'))
        try:
            (logdir / 'lldb_1.log').write_text(
                '     x1 = 0x00007ff7bf6ff000\n'
                '     x2 = 0x0000000000000020\n'
                '     x2 = 0x0000000000000010\n', encoding='utf-8')
            self.assertEqual(mac.collect_lengths(logdir, 'x2'), {0x20, 0x10})
        finally:
            import shutil
            shutil.rmtree(logdir, ignore_errors=True)

    def test_ignores_other_registers(self):
        logdir = pathlib.Path(mkdtemp('wf-mac-len2-'))
        try:
            (logdir / 'lldb_1.log').write_text(
                '     x1 = 0x0000000000000020\n', encoding='utf-8')
            self.assertEqual(mac.collect_lengths(logdir, 'x2'), set())
        finally:
            import shutil
            shutil.rmtree(logdir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
