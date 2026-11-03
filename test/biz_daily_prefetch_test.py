"""`--prefetch-bodies`：并发预热正文缓存，让 Phase 1 走缓存命中、不再逐篇 sleep。

为什么钉这几条：
- **每篇只抓一次、只抓有 url 的** —— 预热是"给上游加请求"的动作，多抓一次就是白打上游一次；
- **单篇失败不拖整批** —— 预热是优化，它挂掉不该让这一天连 Phase 1 都进不去；
- **空结果算失败**（`('', False)`）—— 缓存只存成功（`fetch_article_cached` 的契约），
  把空串当成功会让我们以为"已经预热好了"，而 Phase 1 照样要再抓一遍。

`sqlcipher3` 是桩掉的（biz_daily 在模块级 import 它），做法同 `biz_daily_parallel_test.py`。
"""
import importlib.util
import io
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('biz_daily_prefetch', SCRIPTS / 'biz_daily.py')
biz = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(biz)


def art(url=None, title='标题'):
    a = {'title': title}
    if url:
        a['url'] = url
    return a


class PrefetchBodiesTests(unittest.TestCase):
    def run_prefetch(self, articles, fake, workers=3):
        calls = []

        def fetch(url, use_cache=True):
            calls.append(url)
            return fake(url)

        buffer = io.StringIO()
        with patch.object(biz, 'fetch_article_cached', fetch), redirect_stdout(buffer):
            result = biz.prefetch_bodies(articles, workers, log=print)
        return result, calls, buffer.getvalue()

    def test_每篇只抓一次且只抓有_url_的(self):
        dup = 'http://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=aaa'
        result, calls, _ = self.run_prefetch(
            [art(dup), art(dup), art(), art('http://mp.weixin.qq.com/s?__biz=B&mid=2&idx=1&sn=bbb')],
            lambda _u: ('正文' * 50, False))
        self.assertEqual(result, {'requested': 2, 'ok': 2})
        self.assertEqual(len(calls), 2, '同一个 url 出现两次只该抓一次')
        self.assertNotIn(None, calls)

    def test_单篇炸了不拖整批(self):
        def fake(url):
            if 'bad' in url:
                raise RuntimeError('boom')
            return ('正文' * 50, False)

        result, _calls, _ = self.run_prefetch(
            [art('http://x/s?__biz=good&mid=1&idx=1&sn=1'),
             art('http://x/s?__biz=bad&mid=2&idx=1&sn=2')], fake)
        self.assertEqual(result, {'requested': 2, 'ok': 1})

    def test_空正文不算成功(self):
        """缓存只存成功；把空串算成功会让我们以为预热好了，Phase 1 照样再抓一遍。"""
        for empty in (('', False), (None, False), ('   ', False)):
            with self.subTest(empty=empty):
                result, _calls, _ = self.run_prefetch(
                    [art('http://x/s?__biz=A&mid=1&idx=1&sn=1')], lambda _u, e=empty: e)
                self.assertEqual(result, {'requested': 1, 'ok': 0})

    def test_没有_url_时一个请求都不发(self):
        result, calls, _ = self.run_prefetch([art(), art(title='也没有')], lambda _u: ('x', False))
        self.assertEqual(result, {'requested': 0, 'ok': 0})
        self.assertEqual(calls, [])

    def test_打印进度但不在成功时才打(self):
        """进度按**完成数**打（不是成功数）：全失败时也得看得见"它在动"。"""
        articles = [art('http://x/s?__biz=A&mid=%d&idx=1&sn=%d' % (i, i)) for i in range(60)]
        _result, _calls, out = self.run_prefetch(articles, lambda _u: (None, False))
        self.assertIn('预热 60/60', out)
        self.assertIn('成功 0', out)

    def test_默认并发数是8(self):
        self.assertEqual(biz.PREFETCH_WORKERS, 8)


class PipelineForwardingTests(unittest.TestCase):
    """`--prefetch-bodies` 必须**真的转发**到 biz_daily —— 它是新加的一层旗标。

    为什么专门钉转发：这个仓库里"旗标声明了但没转发"是反复出现过的坑（声明在 CLI/pipeline，
    跑到 python 就没带过去，症状是"这个开关没反应"）。做法与 `pipeline_security_test.py`
    一致：把 `run_step` 换成捕获器，只看子进程参数，不真的跑任何一步。
    """

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        spec = importlib.util.spec_from_file_location('pipeline_prefetch', SCRIPTS / 'pipeline.py')
        self.pipeline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pipeline)
        import _utils
        self._utils = _utils

    def child_args(self, extra):
        captured = []

        def capture(name, args):
            captured.append((name, list(args)))
            return True

        argv = ['pipeline.py', '--no-ai', '--skip-classify', '--skip-html', '--skip-ai-report'] + extra
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'config.json'
            config_path.write_text('{}', encoding='utf-8')
            with patch.object(self.pipeline, 'run_step', capture), \
                    patch.object(sys, 'argv', argv), \
                    patch.object(self._utils, 'CONFIG_PATH', str(config_path)):
                self.pipeline.main()
        step1 = [args for name, args in captured if 'biz_daily' in name]
        self.assertEqual(len(step1), 1, captured)
        return step1[0]

    def test_不带数字时也转发_默认并发数留给_biz_daily(self):
        args = self.child_args(['--prefetch-bodies'])
        self.assertIn('--prefetch-bodies', args)
        self.assertNotIn('8', args, '默认值只在 biz_daily 侧写一份，pipeline 不该再造一个')

    def test_显式数字要原样传下去(self):
        args = self.child_args(['--prefetch-bodies', '3'])
        self.assertEqual(args[args.index('--prefetch-bodies') + 1], '3')

    def test_默认就转发_因为默认开启(self):
        """默认是**开启**的（450 篇 75 分钟 → 秒级），所以不带任何旗标也要转发。

        2026-11-03 改：默认值从 0 改成 8。旧断言"没给就不转发"钉的是"默认关闭"，
        那正是用户踩的坑——少带一个参数就掉进串行慢车道。
        """
        self.assertIn('--prefetch-bodies', self.child_args([]))

    def test_显式关闭时不转发(self):
        self.assertNotIn('--prefetch-bodies', self.child_args(['--no-prefetch-bodies']))

    def test_yesterday_折成具体的日期传下去(self):
        """计划任务用 `--yesterday`：它必须在下游变成**具体的日期**（biz_daily 只认 --date）。"""
        expected = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        args = self.child_args(['--yesterday'])
        self.assertEqual(args[args.index('--date') + 1], expected)

    def test_date_与_yesterday_一起给要报错(self):
        """两个都给时"哪一天"没有唯一答案 —— 宁可当场退出，也不要静默选一个。"""
        with self.assertRaises(SystemExit):
            self.child_args(['--date', '2026-10-01', '--yesterday'])


if __name__ == '__main__':
    unittest.main()
