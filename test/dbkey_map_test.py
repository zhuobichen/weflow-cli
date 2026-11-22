"""dbKeyMap 优先级：命中用 map、不命中退回 passphrase、都没有退回原值。

不需要微信：用临时文件造"库"（前 16 字节当盐），monkeypatch 掉 scan_key_map。
"""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('nt_common', SCRIPTS / 'nt_common.py')
nc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nc)

SALT = bytes.fromhex('2c44d71d9e62b15bfecbcc4b278b497c')
MAPPED_KEY = 'ab' * 32
PASSPHRASE = 'cd' * 32


class DerivePriorityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='keymap-'))
        self.db = self.tmp / 'message_0.db'
        self.db.write_bytes(SALT + b'\x00' * 4080)
        self._orig = nc.scan_key_map

    def tearDown(self):
        nc.scan_key_map = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_map_hit_wins_over_passphrase(self):
        nc.scan_key_map = lambda: {SALT.hex(): MAPPED_KEY}
        key, salt = nc.derive_database_key(str(self.db), 'fb', 'fbs', PASSPHRASE)
        self.assertEqual(key, MAPPED_KEY)
        self.assertEqual(salt, SALT.hex())

    def test_map_miss_falls_back_to_passphrase(self):
        nc.scan_key_map = lambda: {}
        key, salt = nc.derive_database_key(str(self.db), 'fb', 'fbs', PASSPHRASE)
        expected = hashlib.pbkdf2_hmac('sha512', bytes.fromhex(PASSPHRASE), SALT, 256000, 32).hex()
        self.assertEqual(key, expected)
        self.assertEqual(salt, SALT.hex())

    def test_no_map_no_passphrase_returns_config_values(self):
        nc.scan_key_map = lambda: {}
        key, salt = nc.derive_database_key(str(self.db), 'fb', 'fbs', '')
        self.assertEqual((key, salt), ('fb', 'fbs'))

    def test_map_hit_works_without_passphrase(self):
        """这正是接入的目的：全新机器取不到 passphrase 时，靠 map 也能读。"""
        nc.scan_key_map = lambda: {SALT.hex(): MAPPED_KEY}
        key, _ = nc.derive_database_key(str(self.db), 'fb', 'fbs', '')
        self.assertEqual(key, MAPPED_KEY)

    def test_missing_file_returns_config_values(self):
        nc.scan_key_map = lambda: {SALT.hex(): MAPPED_KEY}
        key, salt = nc.derive_database_key(str(self.tmp / 'nope.db'), 'fb', 'fbs', PASSPHRASE)
        self.assertEqual((key, salt), ('fb', 'fbs'))


if __name__ == '__main__':
    unittest.main()
