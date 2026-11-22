"""表情导出的单元测试：切分、容差匹配、落盘、索引页。

集成部分（真实微信数据、需要管理员扫 seed）默认跳过，用 `WEFLOW_TEST_EMOJI=1` 打开。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('emoji_export', SCRIPTS / 'emoji_export.py')
ee = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ee)

# 最小合法 GIF（1x1）与 PNG（1x1）——用来当"容器里的两帧"
GIF_1PX = bytes.fromhex(
    '47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b')
PNG_1PX = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489'
    '0000000a49444154789c6360000002000154a24f5e0000000049454e44ae426082')


class SafeNameTest(unittest.TestCase):
    def test_strips_windows_illegal_chars(self):
        self.assertEqual(ee.safe_name('a/b:c*d?"e<f>g|h'), 'a_b_c_d__e_f_g_h')

    def test_blank_falls_back(self):
        self.assertEqual(ee.safe_name('   ', fallback='专辑_1'), '专辑_1')

    def test_truncates_long_names(self):
        self.assertLessEqual(len(ee.safe_name('长' * 200)), 60)


class FindPackFileTest(unittest.TestCase):
    """库里总长与磁盘文件差 ≤16 字节（PKCS7 填充），必须能容忍。"""

    def test_exact_size_matches(self):
        index = {1000: 'exact.bin'}
        self.assertEqual(ee._find_pack_file(index, 1000, 1000), 'exact.bin')

    def test_padding_difference_matches(self):
        index = {1008: 'padded.bin'}
        self.assertEqual(ee._find_pack_file(index, 1000, 1000), 'padded.bin')

    def test_too_far_does_not_match(self):
        index = {1100: 'other.bin'}
        self.assertIsNone(ee._find_pack_file(index, 1000, 1000))


class SliceExportTest(unittest.TestCase):
    """整包 = 两张图首尾相连；按偏移切片后必须逐条落盘且格式正确。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='emoji-test-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_slices_written_with_correct_extensions(self):
        container = GIF_1PX + PNG_1PX
        off2 = len(GIF_1PX)
        rows = [('aaaa1111', len(GIF_1PX), 0), ('bbbb2222', len(PNG_1PX), off2)]
        target = self.tmp / '专辑'
        target.mkdir()
        wrote = []
        for idx, (md5, size, off) in enumerate(rows, 1):
            blob = container[off:off + size]
            _m, ext = ee.we.sniff(blob)
            self.assertIn(ext, ('.gif', '.png'))
            name = '%03d_%s%s' % (idx, md5[:8], ext)
            (target / name).write_bytes(blob)
            wrote.append(name)
        self.assertEqual(sorted(p.suffix for p in target.iterdir()), ['.gif', '.png'])
        # 切出来的必须和原始字节完全一致（不能有跨条污染）
        self.assertEqual((target / wrote[0]).read_bytes(), GIF_1PX)
        self.assertEqual((target / wrote[1]).read_bytes(), PNG_1PX)


class DecodeFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='emoji-dec-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plaintext_gif_passes_through(self):
        f = self.tmp / 'plain'
        f.write_bytes(GIF_1PX)
        data, mime = ee.decode_file(f, None)
        self.assertEqual(mime, 'image/gif')
        self.assertEqual(data, GIF_1PX)

    def test_unknown_bytes_without_key_return_empty(self):
        f = self.tmp / 'junk'
        f.write_bytes(b'\x00' * 64)
        data, mime = ee.decode_file(f, None)
        self.assertEqual((data, mime), (b'', ''))


class IndexHtmlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='emoji-idx-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_lists_packs_and_escapes(self):
        packs = [{'name': '<坏名>', 'count': 1, 'status': 'ok',
                  'files': [{'file': 'p/001.gif', 'caption': '<b>'}]}]
        favs = [{'file': '_收藏/001.gif', 'caption': '哈哈'}]
        ee.write_index(self.tmp, favs, packs, ['一条说明'], {'generatedAt': '2026-01-01 00:00'})
        html = (self.tmp / 'index.html').read_text(encoding='utf-8')
        self.assertIn('&lt;坏名&gt;', html)          # 名称必须转义
        self.assertNotIn('<坏名>', html)
        self.assertIn('_收藏/001.gif', html)
        self.assertIn('p/001.gif', html)
        self.assertIn('一条说明', html)


@unittest.skipUnless(__import__('os').environ.get('WEFLOW_TEST_EMOJI') == '1',
                     '需要真实微信数据与管理员终端（WEFLOW_TEST_EMOJI=1 打开）')
class IntegrationTest(unittest.TestCase):
    def test_real_export_produces_files(self):
        out = pathlib.Path(tempfile.mkdtemp(prefix='emoji-real-')) / 'export'
        try:
            manifest = ee.export(str(out))
            self.assertGreater(manifest['favourites'] + sum(
                len(p['files']) for p in manifest['packList']), 0)
            self.assertTrue((out / 'index.html').exists())
        finally:
            shutil.rmtree(out.parent, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
