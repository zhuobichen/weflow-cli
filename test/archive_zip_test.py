"""归档打包：普通 ZIP 与 AES-256 加密 ZIP 各验一遍。

加密那组要证明三件事：
1. 打包出来**确实加密**（未给密码读不出内容）
2. 给对密码能读回原文
3. 没装 pyzipper 时**明确失败**，而不是静默打成明文
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import tempfile
import unittest
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / 'scripts'
if str(SCRIPTS) not in str(__import__('sys').path):
    __import__('sys').path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('archive_zip', SCRIPTS / 'archive_zip.py')
az = importlib.util.module_from_spec(spec)
spec.loader.exec_module(az)


class ZipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='zip-test-'))
        self.src = self.tmp / 'export'
        (self.src / 'assets').mkdir(parents=True)
        (self.src / 'chat.html').write_text('<html>你好</html>', encoding='utf-8')
        (self.src / 'assets' / 'a.txt').write_text('媒体', encoding='utf-8')
        (self.src / 'skip.tmp').write_text('x', encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plain_zip_contains_files_and_skips_tmp(self):
        out = self.tmp / 'out.zip'
        r = az.build(self.src, out)
        self.assertTrue(r['success'])
        self.assertFalse(r['encrypted'])
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        self.assertIn('chat.html', names)
        self.assertIn('assets/a.txt', names)
        self.assertNotIn('skip.tmp', names, '临时文件不应进包')

    @unittest.skipIf(az.pyzipper is None, '本机没有 pyzipper')
    def test_aes_zip_requires_password(self):
        out = self.tmp / 'enc.zip'
        r = az.build(self.src, out, 'hunter2')
        self.assertTrue(r['encrypted'])
        self.assertEqual(r['algorithm'], 'WZ_AES-256')

        with zipfile.ZipFile(out) as zf:
            info = zf.getinfo('chat.html')
            self.assertEqual(info.flag_bits & 0x1, 1, '这条应当被标记为已加密')
            with self.assertRaises(RuntimeError):
                zf.read('chat.html')          # 没有密码读不出来

        with az.pyzipper.AESZipFile(out) as zf:
            zf.setpassword(b'hunter2')
            self.assertEqual(zf.read('chat.html').decode('utf-8'), '<html>你好</html>')

    @unittest.skipIf(az.pyzipper is None, '本机没有 pyzipper')
    def test_wrong_password_fails(self):
        out = self.tmp / 'enc2.zip'
        az.build(self.src, out, 'right')
        with az.pyzipper.AESZipFile(out) as zf:
            zf.setpassword(b'wrong')
            with self.assertRaises(Exception):
                zf.read('chat.html')

    def test_missing_pyzipper_fails_loudly(self):
        """把 pyzipper 置空，确认它**报错而不是打明文**。"""
        out = self.tmp / 'nope.zip'
        original = az.pyzipper
        az.pyzipper = None
        try:
            with self.assertRaises(SystemExit) as cm:
                az.build(self.src, out, 'pw')
            self.assertIn('pyzipper', str(cm.exception))
            self.assertFalse(out.exists(), '失败时不该留下一个假加密的包')
        finally:
            az.pyzipper = original

    def test_multiple_sources_keep_both(self):
        """导出产物是"文件 + 媒体报告"两份，必须一起进包，且 arcname 只留文件名。"""
        extra = self.tmp / 'other' / 'media.json'
        extra.parent.mkdir(parents=True)
        extra.write_text('{}', encoding='utf-8')
        out = self.tmp / 'multi.zip'
        r = az.build([self.src / 'chat.html', extra], out)
        self.assertEqual(r['entries'], 2)
        with zipfile.ZipFile(out) as zf:
            names = sorted(zf.namelist())
        self.assertEqual(names, ['chat.html', 'media.json'],
                         '不同父目录的两份产物，arcname 都应当只留文件名')

    def test_missing_one_source_raises(self):
        with self.assertRaises(SystemExit):
            az.build([self.src / 'chat.html', self.tmp / 'nope.json'], self.tmp / 'x.zip')

    def test_empty_source_raises(self):
        empty = self.tmp / 'empty'
        empty.mkdir()
        with self.assertRaises(SystemExit):
            az.build(empty, self.tmp / 'x.zip')


if __name__ == '__main__':
    unittest.main()
