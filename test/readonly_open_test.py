"""读微信库必须**只读**：不许在库旁边留下 -wal / -shm。

2026-11-02 在 Linux 上踩到的真实事故：

- 我们的 `connect_nt_db` 用 `sqlcipher.connect(path)`（默认**读写**）打开微信的真实库；
- 即使 `PRAGMA key` 是错的、第一次查询就失败，SQLite 也会先创建 `-wal` / `-shm`；
- 微信客户端看到这些多出来的文件，直接弹「微信数据库出现损坏」，而客户端当时甚至是登出状态。

取证：登出状态下，`db_storage/*/` 里多个 `-shm` 的 mtime 被刷新到我们刚跑 `key-status` 的那一分钟。

修法：`mode=ro&immutable=1` 的 SQLite URI——只读、不加锁、连共享内存都不建。
代价是读不到尚未 checkpoint 的 WAL（微信会周期性 checkpoint，可接受）。
"""
import os
import pathlib
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'test'))

from _tmpenv import mkdtemp  # noqa: E402

try:
    import sqlcipher3
    HAS_SQLCIPHER = True
except ImportError:
    HAS_SQLCIPHER = False

KEY, SALT = '11' * 32, '22' * 16


@unittest.skipUnless(HAS_SQLCIPHER, 'sqlcipher3 未安装（CI 装 sqlcipher3-binary）')
class ReadOnlyOpenTests(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(mkdtemp('wf-ro-'))
        self.db = self.dir / 't.db'
        conn = sqlcipher3.connect(str(self.db))
        conn.execute(f"PRAGMA key = \"x'{KEY}{SALT}'\"")
        conn.execute('CREATE TABLE t (x INTEGER)')
        conn.execute('INSERT INTO t VALUES (1)')
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def files(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_reads_without_creating_side_files(self):
        from nt_decrypt import connect_nt_db
        before = self.files()
        conn, cur = connect_nt_db(str(self.db), KEY, SALT)
        try:
            self.assertEqual(cur.execute('SELECT count(*) FROM t').fetchone()[0], 1)
        finally:
            conn.close()
        self.assertEqual(self.files(), before,
                         '只读打开不得产生 -wal / -shm（微信会因此判定"数据库损坏"）')

    def test_writes_are_rejected(self):
        from nt_decrypt import connect_nt_db
        conn, cur = connect_nt_db(str(self.db), KEY, SALT)
        try:
            with self.assertRaises(Exception):
                cur.execute('INSERT INTO t VALUES (2)')
        finally:
            conn.close()

    def test_uri_is_read_only_and_immutable(self):
        from nt_decrypt import readonly_uri
        uri = readonly_uri(self.db)
        self.assertIn('mode=ro', uri)
        self.assertIn('immutable=1', uri)
        self.assertTrue(uri.startswith('file:'))


if __name__ == '__main__':
    unittest.main()
