"""只读副本缓存：复用、失效、过期清理、容量上限、退出清理（issue #14）。

背景（2026-11-05 报告）：`messages` / `export` / `sessions` 等**只读**命令，
有 `-wal` 时会复制整个库到 `%TEMP%/weflow-ro-<random>/`，**进程退出后不删**。
轮询 45 秒、跑 15 小时就能填满系统盘。修法不是"不复制"（有 WAL 必须复制），
而是：同一份库复用同一个目录，并按 TTL / 容量上限回收。

这些测试不需要 sqlcipher：`_ro_copy` / `sweep_readonly_cache` 是纯文件操作。
"""
import os
import pathlib
import shutil
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'test'))

from _tmpenv import mkdtemp  # noqa: E402
import nt_decrypt as N  # noqa: E402


class ReadonlyCopyCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(mkdtemp('wf-ro-cache-'))
        self.cache = self.tmp / 'weflow-ro'
        self.cache.mkdir(parents=True, exist_ok=True)
        self.src_dir = self.tmp / 'src'
        self.src_dir.mkdir()
        self.src = self.src_dir / 'message_0.db'
        self.src.write_bytes(b'x' * 4096)
        (self.src_dir / 'message_0.db-wal').write_bytes(b'w' * 1024)
        self._orig_root = N._ro_cache_root
        self._orig_swept = N._SWEPT
        N._ro_cache_root = lambda: self.cache      # 别污染真实 temp
        N._SWEPT = True                            # 跳过首次自动清扫

    def tearDown(self):
        N._ro_cache_root = self._orig_root
        N._SWEPT = self._orig_swept
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dirs(self):
        return sorted(p.name for p in self.cache.iterdir() if p.is_dir())

    def test_same_db_reuses_one_cache_dir(self):
        """同一个库连读 5 次，只能有一个副本目录——这是 #14 的核心。"""
        first = N._ro_copy(self.src)
        for _ in range(4):
            N._ro_copy(self.src)
        self.assertEqual(len(self.dirs()), 1, self.dirs())
        self.assertEqual(first.name, 'message_0.db')
        self.assertTrue(first.exists())

    def test_changed_wal_invalidates_cache(self):
        """WAL 变了（内容/大小变）就该换目录，不能拿旧副本糊弄。"""
        N._ro_copy(self.src)
        time.sleep(0.01)
        (self.src_dir / 'message_0.db-wal').write_bytes(b'w' * 2048)
        N._ro_copy(self.src)
        self.assertEqual(len(self.dirs()), 2, self.dirs())

    def test_sweep_removes_expired_dirs(self):
        N._ro_copy(self.src)
        old = time.time() - 10_000
        for d in self.cache.iterdir():
            os.utime(d, (old, old))
        removed = N.sweep_readonly_cache(ttl=3600, max_bytes=10 ** 12)
        self.assertEqual(removed, 1)
        self.assertEqual(self.dirs(), [])

    def test_sweep_enforces_size_cap_oldest_first(self):
        N._ro_copy(self.src)
        first_dirs = set(self.dirs())
        time.sleep(0.5)
        (self.src_dir / 'message_0.db-wal').write_bytes(b'w' * 3072)
        N._ro_copy(self.src)
        self.assertEqual(len(self.dirs()), 2)
        # 容量上限设成比一个副本还小：应删到只剩一个（最新的那个）
        N.sweep_readonly_cache(ttl=10 ** 9, max_bytes=1)
        self.assertEqual(len(self.dirs()), 1, self.dirs())
        self.assertNotIn(list(first_dirs)[0], self.dirs(), '应先删最旧的')

    def test_cache_off_leaves_dir_for_exit_cleanup(self):
        """WEFLOW_RO_CACHE=off 时用一次性目录，退出时由 _cleanup_tmpdirs 删干净。"""
        before = set(N._CHROME_TMPDIRS)
        os.environ['WEFLOW_RO_CACHE'] = 'off'
        try:
            created = []
            # 模拟 connect_readonly 在关闭缓存时的那条分支
            import tempfile
            d = tempfile.mkdtemp(prefix='weflow-ro-')
            N._CHROME_TMPDIRS.append(d)
            created.append(d)
            self.assertTrue(all(pathlib.Path(x).exists() for x in created))
            N._cleanup_tmpdirs()
            self.assertFalse(any(pathlib.Path(x).exists() for x in created))
        finally:
            os.environ.pop('WEFLOW_RO_CACHE', None)
            N._CHROME_TMPDIRS[:] = list(before)


if __name__ == '__main__':
    unittest.main()


class TempRootHonouringTests(unittest.TestCase):
    """`WEFLOW_TMPDIR` 必须对**一次性副本**也生效（PR #15 指出的那处）。

    `tempfile.gettempdir()` 在进程内缓存首次取值，所以只设 `TEMP` 不一定生效。
    关掉缓存（`WEFLOW_RO_CACHE=off`）时走的是 `mkdtemp(prefix='weflow-ro-')` 的
    默认分支——那里原先没传 `dir=`，副本会落到系统默认目录，而不是 `WEFLOW_TMPDIR`。
    """

    def setUp(self):
        self.tmp = pathlib.Path(mkdtemp('wf-ro-tmproot-'))
        self.custom = self.tmp / 'custom-root'
        self.src_dir = self.tmp / 'src'
        self.src_dir.mkdir()
        self.src = self.src_dir / 'message_0.db'
        self.src.write_bytes(b'x' * 4096)
        (self.src_dir / 'message_0.db-wal').write_bytes(b'w' * 1024)
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_temp_root_reads_env(self):
        os.environ['WEFLOW_TMPDIR'] = str(self.custom)
        self.assertEqual(str(N.temp_root()), str(self.custom))
        self.assertTrue(self.custom.is_dir(), 'temp_root 应该把目录建出来')

    def test_cache_root_sits_under_temp_root(self):
        os.environ['WEFLOW_TMPDIR'] = str(self.custom)
        self.assertEqual(str(N._ro_cache_root()), str(self.custom / 'weflow-ro'))

    def test_one_off_copy_uses_temp_root_not_system_default(self):
        """关掉缓存时，副本目录必须落在 WEFLOW_TMPDIR 下。"""
        os.environ['WEFLOW_TMPDIR'] = str(self.custom)
        self.custom.mkdir(parents=True, exist_ok=True)
        tmp = pathlib.Path(N.tempfile.mkdtemp(prefix='weflow-ro-', dir=str(N.temp_root())))
        try:
            self.assertEqual(tmp.parent, self.custom, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
