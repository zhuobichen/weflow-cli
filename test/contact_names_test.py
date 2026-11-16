"""load_contact_names 的回归测试：游标必须自己取，不能从连接函数的第二个返回值接。

这是 352a3d3「只读副本改造」引入的 bug：`open_database_with_cache` 的第二个
返回值是"是否命中缓存"（bool），`connect_encrypted_readonly` 的第二个返回值
才是 cursor。旧代码两个分支都不给 `c` 赋值，`c.execute(...)` 抛 NameError，
被 `except Exception: pass` 吞掉——静默返回空表，53,057 个联系人全变裸 wxid。

静默失败的特性决定了测试写法：**光断言"返回值非空"不够**，空库也会返回空表。
所以每个用例都带正对照——先用裸 sqlite3 读同一个库，证明数据确实在那里，
再断言 load_contact_names 读出同样的东西。正对照失败时用例作废，而不是报
"函数坏了"。这样修复前必红、修复后必绿，且红得不含糊。
"""
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'

# 用 stdlib sqlite3 顶替 sqlcipher3：合成库是明文 SQLite，PRAGMA key 被忽略。
# 与 nt_decrypt_shards_test.py 同一套加载方式。
sys.path.insert(0, str(SCRIPTS))
import nt_common  # noqa: F401  先加载，避免 patch.dict 退出时把它从 sys.modules 删掉

spec = importlib.util.spec_from_file_location('nt_decrypt_contacts', SCRIPTS / 'nt_decrypt.py')
nt = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(nt)
# require_sqlcipher() 把模块缓存到全局；只 patch sys.modules 覆盖不到后续调用。
nt.sqlcipher = sqlite3

KEY_HEX = 'a' * 64
SALT_HEX = 'b' * 32

# 列序与建表一致：(username, alias, remark, nick_name)。
# SELECT 的 COALESCE 优先级：remark > nick_name > alias > username。
ROWS = [
    ('contact_remark', 'aliasA', '小季家农产品', '昵称A'),   # 备注优先
    ('contact_nick', 'aliasB', '', 'Venus'),                  # 备注空 -> 昵称
    ('contact_alias', '服务通知', '', ''),                     # 备注/昵称都空 -> alias
    ('contact_plain', '', '', ''),                            # 全空 -> 退回 wxid
    ('', '忽略', '无用户名', '忽略'),                       # username 空 -> 跳过
]


class ContactNamesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, 'contact.db')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute('CREATE TABLE contact (username TEXT, alias TEXT, '
                         'remark TEXT, nick_name TEXT)')
            conn.executemany('INSERT INTO contact VALUES (?,?,?,?)', ROWS)
            conn.commit()
        finally:
            conn.close()

    def positive_control(self):
        """正对照：用裸 SQL 证明库里确实有这些名字。

        返回值即"正确答案"；若这里读不到，下面的用例作废而不是误判函数。
        """
        conn = sqlite3.connect(self.db)
        try:
            raw = conn.execute(
                'SELECT username, COALESCE(NULLIF(remark,\'\'), NULLIF(nick_name,\'\'), '
                'NULLIF(alias,\'\'), username) FROM contact').fetchall()
        finally:
            conn.close()
        expected = {u: d for u, d in raw if u}
        self.assertEqual(set(expected),
                         {'contact_remark', 'contact_nick', 'contact_alias', 'contact_plain'},
                         '正对照失效：合成库没读到预期数据，本用例作废')
        return expected

    def test_names_load_with_cache_disabled(self):
        """默认分支：`connect_encrypted_readonly` 给 (conn, cursor)，第二个值不是游标。"""
        expected = self.positive_control()
        with patch('database_cache.DatabaseCache.is_enabled', return_value=False):
            got = nt.load_contact_names(self.db, KEY_HEX, SALT_HEX)
        self.assertEqual(got, expected)

    def test_names_load_with_cache_enabled(self):
        """缓存分支：`open_database_with_cache` 给 (conn, bool)，第二个值是 bool 不是游标。

        这正是 bug 的原发场景——从解包里接第二个值当游标，接的是个 True/False，
        或干脆（如旧代码）没赋值 -> NameError。patch 掉缓存导出只需它返回
        (真实连接, bool)，把测试焦点留在 load_contact_names 自身对游标的处理上。
        """
        expected = self.positive_control()
        conn = sqlite3.connect(self.db)
        with patch('database_cache.DatabaseCache.is_enabled', return_value=True), \
             patch.object(nt, 'open_database_with_cache', return_value=(conn, False)):
            got = nt.load_contact_names(self.db, KEY_HEX, SALT_HEX)
        self.assertEqual(got, expected)

    def test_display_name_priority(self):
        """remark > nick_name > alias > username，逐格钉死（含两个"相等"之外的对角线）。"""
        self.positive_control()
        with patch('database_cache.DatabaseCache.is_enabled', return_value=False):
            got = nt.load_contact_names(self.db, KEY_HEX, SALT_HEX)
        self.assertEqual(got['contact_remark'], '小季家农产品')
        self.assertEqual(got['contact_nick'], 'Venus')
        self.assertEqual(got['contact_alias'], '服务通知')
        self.assertEqual(got['contact_plain'], 'contact_plain')
        self.assertNotIn('', got)

    def test_missing_inputs_return_empty(self):
        """缺参数/库不存在时返回空表是既定契约，不是 bug——别把这个 also 测成 bug。"""
        self.assertEqual(nt.load_contact_names('', KEY_HEX, SALT_HEX), {})
        self.assertEqual(nt.load_contact_names(self.db, '', SALT_HEX), {})
        self.assertEqual(nt.load_contact_names(self.db, KEY_HEX, ''), {})
        self.assertEqual(nt.load_contact_names(os.path.join(self.tmp.name, 'nope.db'),
                                               KEY_HEX, SALT_HEX), {})


if __name__ == '__main__':
    unittest.main()
