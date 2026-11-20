#!/usr/bin/env python3
"""深度解析（classify_daily Step 3）的并发与单篇失败隔离。

这一步默认不做（`--deep` 才跑，见 D-158），但一旦跑起来就必须并发：
168 篇串行约 40 分钟（2026-11-19 实测），并发 6 路约 8 分钟——这不是"更快"，
是"这个功能还能不能在晚上跑"的分界。

两条契约：
1. **并发**：N 篇用 W 个 worker，总耗时明显小于串行（用假 call_ai 计时验证）；
2. **单篇失败不拖垮整步**：一篇抛异常只算那一篇失败，其余照常落盘。
"""
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'scripts'))

import classify_daily as cd  # noqa: E402

ARTICLE = """---
title: "{title}"
---

# {title}
来源：示例来源

## AI 摘要

旧摘要

## 正文

正文内容
"""


def make_articles(count: int) -> tuple[tempfile.TemporaryDirectory, list[pathlib.Path]]:
    d = tempfile.TemporaryDirectory(prefix='deep-')
    paths = []
    for i in range(count):
        p = pathlib.Path(d.name) / f'a{i}.md'
        p.write_text(ARTICLE.format(title=f'标题{i}'), encoding='utf-8')
        paths.append(p)
    return d, paths


class DeepSummaryParallelTests(unittest.TestCase):
    def setUp(self):
        self._orig = cd.call_ai
        self.calls = []
        self.lock = threading.Lock()

    def tearDown(self):
        cd.call_ai = self._orig

    def _fake(self, delay: float = 0.2, fail_on: str | None = None):
        def call(prompt, engine, key, max_tokens=2000):
            with self.lock:
                self.calls.append(prompt[:20])
            time.sleep(delay)
            if fail_on and fail_on in prompt:
                raise RuntimeError('模拟云端报错')
            return '并发验证用的解析正文'
        return call

    def test_writes_deep_section_and_marks_enhanced(self):
        cd.call_ai = self._fake(delay=0.01)
        d, paths = make_articles(1)
        try:
            self.assertTrue(cd._deep_summary_one(1, paths[0], 1, None, ''))
            body = paths[0].read_text(encoding='utf-8')
            self.assertIn('## 深度解析', body)
            self.assertIn('并发验证用的解析正文', body)
            self.assertIn('enhanced: true', body)
        finally:
            d.cleanup()

    def test_runs_concurrently(self):
        """4 篇 × 0.2s、2 个 worker：串行要 0.8s，并发应在 0.5s 内完成。"""
        cd.call_ai = self._fake(delay=0.2)
        d, paths = make_articles(4)
        try:
            t0 = time.time()
            with ThreadPoolExecutor(max_workers=2) as pool:
                out = list(pool.map(
                    lambda it: cd._deep_summary_one(it[0], it[1], 4, None, ''),
                    list(enumerate(paths, 1))))
            elapsed = time.time() - t0
            self.assertEqual(sum(out), 4)
            self.assertEqual(len(self.calls), 4)
            self.assertLess(elapsed, 0.7, f'看起来没并发：{elapsed:.2f}s')
        finally:
            d.cleanup()

    def test_one_failure_does_not_stop_the_others(self):
        cd.call_ai = self._fake(delay=0.01, fail_on='标题1')
        d, paths = make_articles(3)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                out = list(pool.map(
                    lambda it: cd._deep_summary_one(it[0], it[1], 3, None, ''),
                    list(enumerate(paths, 1))))
            self.assertEqual(sum(out), 2, '失败的那篇算 False，其余仍要成功')
            ok_files = [p for p in paths if '## 深度解析' in p.read_text(encoding='utf-8')]
            self.assertEqual(len(ok_files), 2)
        finally:
            d.cleanup()


if __name__ == '__main__':
    unittest.main()
