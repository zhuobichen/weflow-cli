# -*- coding: utf-8 -*-
"""media_index.py：hardlink 行 → 本地路径的模板。

钉死**图片与文件/视频的 dir1/dir2 顺序相反**这件事——2026-10-26 第一版按统一顺序拼，
4647 条图片全指到 `attach/<月份>/<会话>/` 下一个也不存在，而文件/视频那条是对的，
所以只看"某一类能打开"会以为映射没问题。
"""
import importlib.util
import pathlib
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('media_index', SCRIPTS / 'media_index.py')
mi = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=__import__('sqlite3'))}):
    spec.loader.exec_module(mi)


class PathTemplateTests(unittest.TestCase):
    def setUp(self):
        # 用**当前平台**的路径：被测的 account_root()/resolve() 走 pathlib，
        # 在 POSIX 上解析 Windows 字面量会把整串当成一个文件名（CI 上就是这样红的）。
        self.acct = pathlib.Path(tempfile.gettempdir()) / 'fake' / 'wxid_x'
        self.msg_db = self.acct / 'db_storage' / 'message' / 'message_0.db'
        self.cfg = {'ntDbPath': str(self.msg_db)}

    def rel(self, kind, d1, d2, name):
        tmpl = mi.TABLES[kind][1]
        return tmpl.format(d1=d1, d2=d2, name=name)

    def test_image_puts_session_before_month(self):
        """图片是 attach/<会话>/<月份>/Img/ ——不是 attach/<月份>/<会话>。"""
        rel = self.rel('image', '5fb9e1e9', '2025-09', 'abc.dat')
        self.assertEqual(rel, 'attach/5fb9e1e9/2025-09/Img/abc.dat')

    def test_file_and_video_use_month_only(self):
        self.assertEqual(self.rel('file', '2025-09', '', 'a.xlsx'), 'file/2025-09/a.xlsx')
        self.assertEqual(self.rel('video', '2025-09', '', 'a.mp4'), 'video/2025-09/a.mp4')

    def test_resolve_joins_account_root_and_msg(self):
        row = {'kind': 'file', 'dir1': '2025-09', 'dir2': '', 'file': 'a.xlsx'}
        p = mi.resolve(self.cfg, row)
        self.assertEqual(
            str(p),
            str(self.acct / 'msg' / 'file' / '2025-09' / 'a.xlsx'))

    def test_hardlink_path_sits_next_to_message_shards(self):
        p = mi.hardlink_path(self.cfg)
        self.assertEqual(str(p), str(self.acct / 'db_storage' / 'hardlink' / 'hardlink.db'))

    def test_account_root_is_three_levels_up(self):
        self.assertEqual(str(mi.account_root(self.cfg)), str(self.acct))


if __name__ == '__main__':
    unittest.main()
