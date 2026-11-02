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


    def _wal_db(self, with_shm=True):
        """造一个**热 WAL** 库：写连接保持打开，最新几行只在 -wal 里。"""
        db = self.dir / 'wal.db'
        writer = sqlcipher3.connect(str(db))
        writer.execute(f"PRAGMA key = \"x'{KEY}{SALT}'\"")
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE t (x INTEGER)')
        writer.execute('INSERT INTO t VALUES (1)')
        writer.commit()
        writer.execute('INSERT INTO t VALUES (2)')
        writer.commit()
        if not with_shm:
            # 复制 db+wal 到另一目录（丢掉 -shm），模拟客户端异常退出后留下的现场
            import shutil as sh
            alt = self.dir / 'noshm'
            alt.mkdir(exist_ok=True)
            sh.copy2(db, alt / 'wal.db')
            sh.copy2(str(db) + '-wal', str(alt / 'wal.db') + '-wal')
            return alt / 'wal.db', alt, writer
        return db, self.dir, writer

    def test_hot_wal_rows_are_read(self):
        """WAL 里未 checkpoint 的行必须读到——否则日报会漏掉当天的文章。

        这条是回归测试：我第一版用 `immutable=1`，它在有 WAL 时连表结构都看不见
        （`no such table`），而且不会报"我读不到 WAL"，只会安静地少数据。
        """
        from nt_decrypt import connect_nt_db
        db, home, writer = self._wal_db()
        try:
            conn, cur = connect_nt_db(str(db), KEY, SALT)
            try:
                self.assertEqual(cur.execute('SELECT count(*) FROM t').fetchone()[0], 2)
            finally:
                conn.close()
        finally:
            writer.close()

    def test_hot_wal_leaves_no_files_beside_the_original(self):
        """有活动 WAL 时，`mode=ro` 会在原库旁建 `-shm`——那正是客户端判"损坏"的原因。

        所以这条路必须读**副本**：目录里不许出现任何新东西。
        """
        from nt_decrypt import connect_nt_db
        db, home, writer = self._wal_db()
        before = sorted(p.name for p in home.iterdir())
        try:
            conn, cur = connect_nt_db(str(db), KEY, SALT)
            cur.execute('SELECT count(*) FROM t').fetchone()
            conn.close()
        finally:
            writer.close()
        after = sorted(p.name for p in home.iterdir())
        self.assertEqual([f for f in after if f not in before], [],
                         f'原目录多出了 {after} 里的新文件')

    def test_wal_without_shm_still_reads(self):
        """只剩 db+wal（客户端异常退出）也要读得到，且不许补建 -shm。"""
        from nt_decrypt import connect_nt_db
        db, home, writer = self._wal_db(with_shm=False)
        before = sorted(p.name for p in home.iterdir())
        try:
            conn, cur = connect_nt_db(str(db), KEY, SALT)
            try:
                self.assertEqual(cur.execute('SELECT count(*) FROM t').fetchone()[0], 2)
            finally:
                conn.close()
        finally:
            writer.close()
        after = sorted(p.name for p in home.iterdir())
        self.assertEqual([f for f in after if f not in before], [])


if __name__ == '__main__':
    unittest.main()
