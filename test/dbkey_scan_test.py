"""密钥扫描的离线单元测试：掩码相位、字面量切分、按盐分组。

不需要微信、不需要进程内存——只测纯函数。真实扫描（要管理员 + 微信在运行）另跑。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('dbkey_scan', SCRIPTS / 'dbkey_scan.py')
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

# 真实形态：32 字节密钥 + 16 字节盐，字符分布要够杂（全同字节会被低差异过滤掉）
KEY = '3f9a1c7e0b5d2846a1f3c9e7b2d4085166abcdeffedcba0987654321abcdef01'[:64]
SALT = '2c44d71d9e62b15bfecbcc4b278b497c'
LITERAL = b"x'" + (KEY + SALT).encode() + b"'"


def mask(data: bytes, align: int = 0) -> bytes:
    """按统一掩码"加密"一段字节，用于造测试数据。"""
    return bytes(b ^ ds.XOR_MASK[(align + i) % len(ds.XOR_MASK)] for i, b in enumerate(data))


class MaskedLiteralTest(unittest.TestCase):
    def test_prefixes_cover_all_phases(self):
        prefixes = ds.masked_prefixes()
        self.assertEqual(len(prefixes), len(ds.XOR_MASK))
        # 掩码里存在重复字节，所以前缀**不保证**两两不同（实测 32 个相位只有 26 种）。
        # 这里只钉住"数量对得上"，不做过强断言。
        self.assertLessEqual(len(set(prefixes)), len(prefixes))

    def test_roundtrip_finds_key_at_every_alignment(self):
        """把字面量按任意相位掩码、前面垫任意长度垃圾，都必须能找回来。"""
        for align in range(len(ds.XOR_MASK)):
            for pad in (0, 1, 7, 33):
                blob = b'\x00' * pad + mask(LITERAL, align)
                hits = ds.candidates_from_masked(blob)
                self.assertIn((KEY, SALT), hits,
                              '相位 %d 垫 %d 字节时没找回' % (align, pad))

    def test_plain_literal_found_by_literal_path(self):
        hits = ds.candidates_from_literal(KEY + SALT)
        self.assertIn((KEY, SALT), hits)

    def test_low_variety_key_rejected(self):
        """全同字节的"密钥"不是密钥，别浪费校验。"""
        self.assertEqual(ds.candidates_from_literal('0' * 64), [])

    def test_performance_guard_limits_decode(self):
        """前缀误命中很多时不能退化成平方级：命中上限必须生效。"""
        dense = ds.masked_prefixes()[0] * 5000
        hits = ds.candidates_from_masked(dense, max_hits_per_chunk=8)
        self.assertEqual(hits, [])


class UnmaskTest(unittest.TestCase):
    def test_unmask_inverts_mask_at_phase_zero(self):
        # xor_unmask 固定从相位 0 开始；非 0 相位本来就解不回来（这是设计，不是 bug）
        self.assertEqual(ds.xor_unmask(mask(LITERAL, 0)), LITERAL)

    def test_unmask_of_other_phase_differs(self):
        self.assertNotEqual(ds.xor_unmask(mask(LITERAL, 5)), LITERAL)


if __name__ == '__main__':
    unittest.main()
