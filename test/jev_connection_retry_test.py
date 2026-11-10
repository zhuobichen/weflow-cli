"""连接层异常必须进重试路径（`RemoteDisconnected` 曾经绕过全部重试）。

背景（2026-11-09 日报实测）：485 篇里有 1 篇打
`Jev 分类失败（RemoteDisconnected），退回 LLM 解析`。

根因不是网络，是**异常类没被接住**：
  · `http.client.RemoteDisconnected` 既不是 `urllib.error.URLError`，也不是 `HTTPError`；
  · `_post_once` 原来只 except 那两类 → 异常原样穿出；
  · `_post` 的重试循环只捕获 `JevError` → 这类失败**跳过全部 3 次重试**，
    直接让调用方降级到 LLM。

几秒钟的网络抖动就吃掉一次重试机会——这与"重试三次、退避 0.8s"的设计意图相反。
"""
import http.client
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import jev_client as jc


class ConnectionErrorRetryTests(unittest.TestCase):
    def _client(self):
        return jc.JevClient('fake-key', timeout=5)

    def test_remote_disconnected_is_retryable(self):
        """`RemoteDisconnected` 要变成 retryable 的 JevError，而不是原样抛出。"""
        client = self._client()
        with mock.patch.object(jc.urllib.request, 'urlopen',
                               side_effect=http.client.RemoteDisconnected('remote closed')):
            with self.assertRaises(jc.JevError) as ctx:
                client._post_once('https://example.invalid/x')
        self.assertTrue(getattr(ctx.exception, 'retryable', False),
                        '不标 retryable 的话 _post 会直接放弃')

    def test_connection_reset_is_retryable(self):
        client = self._client()
        with mock.patch.object(jc.urllib.request, 'urlopen',
                               side_effect=ConnectionResetError('reset by peer')):
            with self.assertRaises(jc.JevError) as ctx:
                client._post_once('https://example.invalid/x')
        self.assertTrue(getattr(ctx.exception, 'retryable', False))

    def test_timeout_is_retryable(self):
        client = self._client()
        with mock.patch.object(jc.urllib.request, 'urlopen', side_effect=TimeoutError('timed out')):
            with self.assertRaises(jc.JevError) as ctx:
                client._post_once('https://example.invalid/x')
        self.assertTrue(getattr(ctx.exception, 'retryable', False))

    def test_post_actually_retries_then_succeeds(self):
        """第一次 RemoteDisconnected、第二次成功 —— 调用方不该看到失败。"""
        client = self._client()
        calls = {'n': 0}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true}'

        def flaky(*a, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                raise http.client.RemoteDisconnected('remote closed')
            return FakeResp()

        with mock.patch.object(jc.urllib.request, 'urlopen', side_effect=flaky):
            body = client._post('https://example.invalid/x', {'a': 1})
        self.assertEqual(body, {'ok': True})
        self.assertEqual(calls['n'], 2, '应该重试一次后成功')

    def test_still_gives_up_after_all_attempts(self):
        client = self._client()
        with mock.patch.object(jc.urllib.request, 'urlopen',
                               side_effect=http.client.RemoteDisconnected('x')) as m:
            with self.assertRaises(jc.JevError):
                client._post('https://example.invalid/x', {'a': 1})
        self.assertEqual(m.call_count, 3, '重试次数不该变：3 次')


if __name__ == '__main__':
    unittest.main()
