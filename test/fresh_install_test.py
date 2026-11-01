"""全新机器（没有任何配置）上的首次体验。

2026-11-01 在干净 HOME 上实测：

- `weflow-cli daily` 直接抛 `FileNotFoundError: .../config.json` 的 **原始 traceback**；
- `load_config()` 修成"没有文件就返回空 dict"之后，`daily` 又回退到
  "缺少订阅号数据库密钥：请配置 passphrase"——对连 `init` 都没跑过的人是误导；
- 更关键的是：**外部订阅源**（Linux 上拿不到本地库密钥时唯一的日报来源）
  当时只在 `scripts/pipeline.py` 里有，主命令 `weflow-cli daily` 传
  `--articles-json` 会报 `unknown option`。

这里钉住三件事：缺配置说"先 init"、有外部源不拦、外部源真的能出文章。
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
sys.path.insert(0, str(ROOT / 'test'))

from _tmpenv import mkdtemp  # noqa: E402  临时目录不许落在仓库里


class FreshInstallTests(unittest.TestCase):
    def setUp(self):
        self.home = mkdtemp('wf-fresh-')

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _run(self, *extra):
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, PYTHONIOENCODING='utf-8')
        return subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'biz_daily.py'),
             '--date', '2026-11-01', '--dry-run', '--no-ai', *extra],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            env=env, timeout=180, cwd=str(ROOT))

    def test_no_config_says_run_init_not_passphrase(self):
        proc = self._run()
        out = (proc.stdout or '') + (proc.stderr or '')
        self.assertEqual(proc.returncode, 1, out[-400:])
        self.assertIn('还没有配置', out)
        self.assertIn('init', out, '必须指向 init，而不是一上来就让人配 passphrase')
        self.assertNotIn('Traceback', out, '不许把原始 traceback 丢给第一次用的用户')
        # 提示里点名的选项必须真实存在（我第一版写了个不存在的 --rss-feeds-file）
        self.assertNotIn('--rss-feeds-file', out)

    def test_external_articles_work_without_any_config(self):
        """正对照：给了外部源就必须能出文章——否则上一条"缺配置就说 init"毫无意义。"""
        path = os.path.join(self.home, 'articles.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump([{
                'title': '外部文章标题',
                'url': 'https://mp.weixin.qq.com/s/EXTERNAL',
                'account': 'gh_ext',
                'account_name': '外部号',
                'create_time': 1767261600,
            }], fh, ensure_ascii=False)

        proc = self._run('--articles-json', path)
        out = (proc.stdout or '') + (proc.stderr or '')
        self.assertEqual(proc.returncode, 0, out[-400:])
        self.assertIn('找到 1 篇文章', out)
        self.assertIn('外部文章标题', out)
        self.assertNotIn('还没有配置', out, '有外部源时不该被"先 init"拦住')


if __name__ == '__main__':
    unittest.main()
