"""库打不开 / 一个库都没找到时，报错必须指向**真因**。

背景（2026-11-01，Linux 实测）：

- 把 `biz_message_0.db` 改成不可读后，`key-status` 报 `verdict: mismatch`
  —— 库里明明有数据，报的却是"口令不对"；真因是文件打不开。
- 同一场景下 `biz_daily` 直接抛 Python traceback：
  `sqlcipher3.dbapi2.OperationalError: unable to open database file`，
  用户在 Linux 上只看到这一句，不知道该 chmod 还是 chown。

两个假错误都修了：`key-status` 现在区分 `no-databases` 与 `mismatch`；
`biz_daily` 打不开库时给明确提示并以 1 退出。
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

try:
    import sqlcipher3  # noqa: F401
    HAS_SQLCIPHER = True
except ImportError:
    HAS_SQLCIPHER = False


class UnopenableDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='wf-perm-')
        self.msg = os.path.join(self.home, 'xwechat_files', 'wxid_a',
                                'db_storage', 'message')
        os.makedirs(self.msg, exist_ok=True)
        os.makedirs(os.path.join(self.home, '.weflow-cli'), exist_ok=True)
        with open(os.path.join(self.home, '.weflow-cli', 'config.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump({'ntDbPath': os.path.join(self.msg, 'message_0.db'),
                       'bizKey': '11' * 32, 'bizSalt': '22' * 16}, fh)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _env(self):
        return dict(os.environ, HOME=self.home, USERPROFILE=self.home,
                    PYTHONIOENCODING='utf-8')

    def test_key_status_says_no_databases_not_mismatch(self):
        """目录里没有可读库时，是 no-databases，不是 mismatch。"""
        proc = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'nt_decrypt.py'),
             'key-status', '--root', os.path.join(self.home, 'xwechat_files'), '--json'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            env=self._env(), timeout=120)
        line = [l for l in (proc.stdout or '').splitlines() if l.strip().startswith('{')]
        self.assertTrue(line, f'没有 JSON 输出: {proc.stdout!r} {proc.stderr[:200]!r}')
        got = json.loads(line[-1])
        self.assertEqual(got['total'], 0)
        self.assertEqual(got['verdict'], 'no-databases',
                         '一个库都没找到时不能说成"密钥不匹配"')

    @unittest.skipUnless(HAS_SQLCIPHER, 'sqlcipher3 未安装')
    def test_biz_daily_reports_unopenable_database_without_traceback(self):
        """库**存在但打不开**时：明确报错 + 退出码 1，且不出 traceback。

        用"同名目录"制造打不开的库：既跨平台，又不需要 chmod
        （第一版用"文件不存在"，结果 `get_db_keys` 直接把 biz_db 置空，
        走的是"没有本地库"的另一条分支，压根没触发这里）。
        """
        os.makedirs(os.path.join(self.msg, 'biz_message_0.db'), exist_ok=True)
        proc = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'biz_daily.py'),
             '--date', '2026-01-01', '--dry-run', '--no-ai'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            env=self._env(), timeout=180, cwd=str(ROOT))
        out = (proc.stdout or '') + (proc.stderr or '')
        self.assertEqual(proc.returncode, 1, f'应以 1 退出，实际 {proc.returncode}: {out[-400:]}')
        self.assertIn('无法打开订阅号数据库', out, '必须说清"打不开库"')
        self.assertIn('权限', out, '必须给出可排查的方向')
        self.assertNotIn('Traceback', out, '不该把原始 traceback 丢给用户')


if __name__ == '__main__':
    unittest.main()
