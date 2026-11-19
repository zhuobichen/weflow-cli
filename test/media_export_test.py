#!/usr/bin/env python3
"""微信 4.x V2 `.dat` 解密的回归测试（全合成数据，不读真实微信目录）。

判据都是硬的：合成一张"明文图片"→ 按 V2 规则加密 → 解密回来必须逐字节相同。
这样测的是**格式与密钥推导本身**，而不是"某个真实文件恰好能打开"。

真实环境里还有两类解不开的，这里也各自钉一条（它们不是 bug，是已知边界）：
- 原图是 WxAM（`wxgf` 魔数），要第二层解码；
- 少数 `_h` 文件头里的 `xor_size` 与文件长度不符（布局差异）。
"""
import hashlib
import pathlib
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'scripts'))

import media_export as m  # noqa: E402

try:
    from Crypto.Cipher import AES
    HAVE_AES = True
except ImportError:  # pragma: no cover - 依赖缺失时跳过，不假绿
    HAVE_AES = False

JPEG = b'\xff\xd8\xff\xe0' + b'PSEUDO-IMAGE-BODY' * 4


def build_v2(plain: bytes, aes_key: bytes, xor_key: int, aes_size: int = 32) -> bytes:
    """把明文按 V2 规则加密成 .dat 字节（测试用，与被测解密互为逆行）。"""
    aes_part = plain[:aes_size]
    if len(aes_part) < aes_size:
        aes_part = aes_part + b'\x00' * (aes_size - len(aes_part))
    rest = plain[aes_size:]
    enc = AES.new(aes_key, AES.MODE_ECB).encrypt(aes_part)
    sep = bytes.fromhex('5adac40b5420f4abecb01338b2678321')
    body = bytes(b ^ xor_key for b in rest)
    return (b'\x07\x08V2\x08\x07' + struct.pack('<II', aes_size, len(rest)) + b'\x01'
            + enc + sep + body)


class HeaderTests(unittest.TestCase):
    def test_accepts_wellformed_v2(self):
        data = build_v2(JPEG, b'0123456789abcdef', 0x2e)
        self.assertEqual(m.parse_v2(data), (32, len(JPEG) - 32))

    def test_rejects_wrong_magic(self):
        self.assertIsNone(m.parse_v2(b'\x00\x01\x02' + b'\x00' * 40))

    def test_rejects_size_mismatch(self):
        data = bytearray(build_v2(JPEG, b'0123456789abcdef', 0x2e))
        data[6:10] = struct.pack('<I', 32)
        self.assertIsNone(m.parse_v2(bytes(data) + b'extra'))

    def test_loose_parser_still_recognises_mismatch(self):
        """长度不自洽时严格解析为 None，但宽松解析能认出"是 V2"。"""
        data = bytearray(build_v2(JPEG, b'0123456789abcdef', 0x2e)) + b'trailing'
        self.assertIsNone(m.parse_v2(bytes(data)))
        self.assertIsNotNone(m.parse_v2_loose(bytes(data)))


class KeyDerivationTests(unittest.TestCase):
    def test_keys_follow_documented_formula(self):
        uin, wxid = 433895278, 'wxid_example'
        aes, xor = m.derive_keys(uin, wxid)
        expect = hashlib.md5(f'{uin}{wxid}'.encode()).hexdigest()[:16].encode()
        self.assertEqual(aes, expect)
        self.assertEqual(len(aes), 16)
        self.assertEqual(xor, uin & 0xFF)

    def test_wxid_variants_drop_directory_suffix(self):
        """账号目录名带 `_4c8e` 这种后缀，公式里要用去掉它的那个（实测差异就在这）。"""
        self.assertEqual(m.wxid_variants('wxid_abc123_4c8e')[0], 'wxid_abc123')
        self.assertIn('wxid_abc123_4c8e', m.wxid_variants('wxid_abc123_4c8e'))
        self.assertEqual(m.wxid_variants('wxid_plainonly'), ['wxid_plainonly'])

    def test_uin_candidates_come_from_kvcomm_filenames(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / 'key_433895278_4065599540_1_1795101345_13117_3600_input.statistic').write_text('')
            (p / '0_4065599540_1_1795102584_30185_3600_input.statistic').write_text('')
            cands = m.find_uin_candidates([p])
        self.assertIn(433895278, cands)
        self.assertIn(4065599540, cands)


class DecryptTests(unittest.TestCase):
    def setUp(self):
        if not HAVE_AES:
            self.skipTest('pycryptodome 未安装')

    def test_roundtrip_returns_original_bytes(self):
        aes_key, xor_key = m.derive_keys(433895278, 'wxid_example')
        data = build_v2(JPEG, aes_key, xor_key)
        self.assertEqual(m.decrypt_v2(data, aes_key, xor_key), JPEG)

    def test_separator_is_skipped_not_decrypted(self):
        """16 字节分隔符不参与解密；硬编码它会随客户端版本漂（本机实测已与旧资料不同）。"""
        aes_key, xor_key = m.derive_keys(1, 'wxid_x')
        data = build_v2(JPEG, aes_key, xor_key)
        tampered = bytearray(data)
        sep_at = 15 + 32
        tampered[sep_at:sep_at + 16] = bytes(16)      # 换掉分隔符内容
        self.assertEqual(m.decrypt_v2(bytes(tampered), aes_key, xor_key), JPEG)

    def test_rejects_non_v2(self):
        self.assertEqual(m.decrypt_v2(b'\x00' * 64, b'0123456789abcdef', 0), b'')


class ClassifyTests(unittest.TestCase):
    def test_image_magics(self):
        self.assertEqual(m.classify(b'\xff\xd8\xff\xe0rest'), '.jpg')
        self.assertEqual(m.classify(b'\x89PNG\r\n\x1a\nrest'), '.png')
        self.assertEqual(m.classify(b'GIF89a...'), '.gif')
        self.assertEqual(m.classify(b'RIFF\x00\x00\x00\x00WEBPVP8 '), '.webp')

    def test_wxam_is_not_an_image(self):
        """原图是 WxAM（wxgf），本脚本不解第二层——必须认清并单独归类，不能当 jpg 写出去。"""
        self.assertEqual(m.classify(b'wxgf\x13\x00...'), '')
        self.assertTrue(m.is_wxam(b'wxgf\x13\x00...'))

    def test_garbage_is_not_an_image(self):
        self.assertEqual(m.classify(b'\x00\x01\x02\x03'), '')


class WxamTests(unittest.TestCase):
    """WxAM（`wxgf`）原图：本质是 Annex-B HEVC，交给 ffmpeg 解。"""

    def test_extracts_hevc_from_first_start_code(self):
        plain = b'wxgf' + b'\x13\x00\x02\x05' + b'\x00' * 34 + b'\x00\x00\x00\x01\x40\x01' + b'BODY'
        hevc = m.extract_hevc(plain)
        self.assertTrue(hevc.startswith(b'\x00\x00\x00\x01'))
        self.assertIn(b'BODY', hevc)

    def test_falls_back_to_three_byte_start_code(self):
        # 头里不能再有连着的零，否则 3 字节起始码会被前面的零拼成 4 字节形态
        plain = b'wxgf\x13\x00\x02' + b'\x00\x00\x01\x40\x01' + b'BODY'
        self.assertTrue(m.extract_hevc(plain).startswith(b'\x00\x00\x01'))

    def test_returns_empty_when_no_start_code(self):
        self.assertEqual(m.extract_hevc(b'wxgf' + b'\x00' * 40), b'')

    def test_ffmpeg_lookup_prefers_explicit_path(self):
        with tempfile.TemporaryDirectory() as d:
            fake = pathlib.Path(d) / 'ffmpeg.exe'
            fake.write_bytes(b'')
            self.assertEqual(m.find_ffmpeg(str(fake)), str(fake))


class WxamDecodeIntegrationTests(unittest.TestCase):
    """真调 ffmpeg：没有 ffmpeg 就跳过（不假绿），有则必须解出可打开的图片。"""

    def test_decodes_a_synthetic_hevc(self):
        ffmpeg = m.find_ffmpeg()
        if not ffmpeg:
            self.skipTest('本机没有 ffmpeg')
        # 用 ffmpeg 自己造一段最小 HEVC，再让被测代码把它解回图片
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d) / 'seed.png'
            from PIL import Image  # 没装 Pillow 就跳过，别用假数据凑
            Image.new('RGB', (64, 48), (10, 20, 30)).save(src)
            hevc = pathlib.Path(d) / 'seed.265'
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(src),
                            '-c:v', 'libx265', '-x265-params', 'log-level=error', str(hevc)],
                           capture_output=True, check=False)
            if not hevc.exists() or not hevc.stat().st_size:
                self.skipTest('本机 ffmpeg 不带 libx265，无法造样本')
            got = m.decode_wxam(hevc.read_bytes(), pathlib.Path(d) / 'out', ffmpeg)
            self.assertTrue(got['ok'], got.get('error'))
            with Image.open(got['path']) as im:
                im.load()
                self.assertEqual(im.size, (64, 48))


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        if not HAVE_AES:
            self.skipTest('pycryptodome 未安装')

    def test_picks_the_uin_that_actually_decrypts(self):
        real_uin, wxid = 433895278, 'wxid_example'
        aes_key, xor_key = m.derive_keys(real_uin, wxid)
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'aaaa_t.dat'
            p.write_bytes(build_v2(JPEG, aes_key, xor_key))
            got = m.calibrate([p], [111111111, real_uin], [wxid])
        self.assertEqual(got, (real_uin, wxid))

    def test_returns_none_when_nothing_decrypts(self):
        """标定不出来就要返回 None（调用方据此报错退出），不能随便挑一个凑数。"""
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'bbbb_t.dat'
            p.write_bytes(b'\x07\x08V2\x08\x07' + struct.pack('<II', 32, 0) + b'\x01' + b'\x00' * 32 + b'\x00' * 16)
            got = m.calibrate([p], [111111111], ['wxid_example'])
        self.assertIsNone(got)


if __name__ == '__main__':
    unittest.main()
