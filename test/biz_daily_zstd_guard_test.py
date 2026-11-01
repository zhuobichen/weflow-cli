"""缺 zstandard 时必须**响亮失败**，不能安静地报"找到 0 篇文章"。

背景（2026-11-01，CI 实测）：订阅号消息体是 zstd 压缩的，`extract_article_info()`
里 `import zstandard` 失败会抛 ImportError，而取数循环是 `except: continue` ——
于是"库里有 1 行、查询返回 1 行"最后变成"找到 0 篇文章"。
这个假象让 CI 上那条端到端判据灰了很久，最后是靠把依赖卸掉才复现出来的。
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

from biz_daily_readpath_test import build_db, GH, URL  # noqa: E402

# `build_db` 用的 create_time 是固定的 1793500000；日期必须从它算出来，
# 不能硬写 2026-01-01（第一版就是硬写的，正对照当场红）。
from datetime import datetime, timedelta, timezone  # noqa: E402

ARTICLE_TS = 1793500000
ARTICLE_DATE = datetime.fromtimestamp(ARTICLE_TS, tz=timezone(timedelta(hours=8))).strftime('%Y-%m-%d')


@unittest.skipUnless(HAS_SQLCIPHER, 'sqlcipher3 未安装（CI 装 sqlcipher3-binary）')
class ZstdGuardTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='wf-zstd-')
        msg = os.path.join(self.home, 'synth', 'wxid_demo', 'db_storage', 'message')
        os.makedirs(msg, exist_ok=True)
        key, salt = '11' * 32, '22' * 16
        db = os.path.join(msg, 'biz_message_0.db')
        build_db(db, key, salt)
        os.makedirs(os.path.join(self.home, '.weflow-cli'), exist_ok=True)
        with open(os.path.join(self.home, '.weflow-cli', 'config.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump({'ntDbPath': os.path.join(msg, 'message_0.db'),
                       'bizKey': key, 'bizSalt': salt}, fh, ensure_ascii=False)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _run(self, stub_zstd: bool):
        env = dict(os.environ)
        env.update({'HOME': self.home, 'USERPROFILE': self.home, 'PYTHONIOENCODING': 'utf-8'})
        stub_dir = None
        if stub_zstd:
            # 用一个**会抛 ImportError 的桩**顶掉真模块：PYTHONPATH 里排在前面，
            # site-packages 在后面，所以桩一定先被命中。
            stub_dir = tempfile.mkdtemp(prefix='wf-nozstd-')
            with open(os.path.join(stub_dir, 'zstandard.py'), 'w', encoding='utf-8') as fh:
                fh.write("raise ImportError('zstandard 被测试桩屏蔽')\n")
            env['PYTHONPATH'] = stub_dir + os.pathsep + env.get('PYTHONPATH', '')
        try:
            return subprocess.run(
                [sys.executable, str(ROOT / 'scripts' / 'biz_daily.py'),
                 '--date', ARTICLE_DATE, '--dry-run', '--no-ai'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                env=env, timeout=180, cwd=str(ROOT))
        finally:
            if stub_dir:
                shutil.rmtree(stub_dir, ignore_errors=True)

    def test_missing_zstandard_fails_loudly(self):
        proc = self._run(stub_zstd=True)
        self.assertNotEqual(proc.returncode, 0, '缺依赖时不能返回 0（那会变成"今天没文章"）')
        out = (proc.stdout or '') + (proc.stderr or '')
        self.assertIn('zstandard', out, '报错里必须点名缺的是 zstandard')
        self.assertIn('pip install', out, '必须给出可执行的安装指令')

    def test_with_zstandard_it_reads_the_article(self):
        """正对照：装了 zstandard 时必须读出文章——否则上一条通过说明不了问题。"""
        try:
            import zstandard  # noqa: F401
        except ImportError:
            self.skipTest('本机也没装 zstandard，跳过正对照')
        proc = self._run(stub_zstd=False)
        self.assertEqual(proc.returncode, 0, (proc.stdout or '')[-400:] + (proc.stderr or '')[-400:])
        self.assertIn('找到 1 篇文章', proc.stdout or '')


if __name__ == '__main__':
    unittest.main()
