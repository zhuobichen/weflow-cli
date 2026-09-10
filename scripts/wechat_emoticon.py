#!/usr/bin/env python3
"""Decrypt WeChat custom stickers (表情包) from the local cache.

WeChat 4.x keeps stickers in `cache/YYYY-MM/Emoticon/<md5[:2]>/<md5>` and
`business/emoticon/{Persist,Thumb,...}`. Each file is AES-128-CBC with the key
also used as the IV, where

    key = md5(f"{seed}{wxid}EMOTICON")[:16]

`seed` is a per-account constant held in WeChat's process memory (see
find_seed). Once known it is stable, so it only has to be scanned for once.

Most cached stickers are WeChat's own `wxgf` container - a small header
followed by a raw H.265 stream - which needs ffmpeg to turn into an image.
Decoded results are cached on disk because that is the expensive step.

Reference: CN-Grace/Wechat-Emoticon-Parser (v4.0-plus branch).
"""
import hashlib
import os
import subprocess
import tempfile

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    _HAVE_CRYPTO = True
except ImportError:
    _HAVE_CRYPTO = False

try:
    import zstandard
    _HAVE_ZSTD = True
except ImportError:
    _HAVE_ZSTD = False

IMAGE_MAGICS = (
    (b'\x89PNG', 'image/png', '.png'),
    (b'\xff\xd8\xff', 'image/jpeg', '.jpg'),
    (b'GIF8', 'image/gif', '.gif'),
    (b'RIFF', 'image/webp', '.webp'),
    (b'wxgf', 'image/hevc', '.hevc'),
)


def derive_key(seed, wxid):
    """Cache key for an account. Returns 16 bytes."""
    return hashlib.md5(f'{seed}{wxid}EMOTICON'.encode()).digest()[:16]


def account_wxid(account_dir_name):
    """`wxid_mgnjdl8034eh22_4c8e` -> `wxid_mgnjdl8034eh22`."""
    parts = account_dir_name.rsplit('_', 1)
    return parts[0] if len(parts) == 2 and len(parts[1]) == 4 else account_dir_name


def decrypt(raw, key):
    """AES-128-CBC(key=IV) with PKCS7. Returns plaintext (best effort)."""
    if not _HAVE_CRYPTO or not raw or len(raw) < 32:
        return b''
    body = raw[:len(raw) // 16 * 16]
    dec = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    out = dec.update(body) + dec.finalize()
    try:
        unp = padding.PKCS7(128).unpadder()
        return unp.update(out) + unp.finalize()
    except Exception:
        return out


def sniff(data):
    """(mime, extension) for decrypted bytes, or (None, None)."""
    for magic, mime, ext in IMAGE_MAGICS:
        if data.startswith(magic):
            return mime, ext
    return None, None


def find_seed_candidates():
    """Decimal strings scraped from WeChat's process memory (seed candidates)."""
    try:
        import ctypes
        from ctypes import (byref, c_size_t, c_void_p, create_string_buffer,
                            sizeof, wintypes)
        import pymem
        import re as _re
    except ImportError:
        return []

    class MBI(ctypes.Structure):
        _fields_ = [
            ('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
            ('AllocationProtect', wintypes.DWORD), ('PartitionId', wintypes.WORD),
            ('RegionSize', ctypes.c_size_t), ('State', wintypes.DWORD),
            ('Protect', wintypes.DWORD), ('Type', wintypes.DWORD),
        ]

    k32 = ctypes.windll.kernel32
    rpm = k32.ReadProcessMemory
    rpm.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    vqe = k32.VirtualQueryEx
    vqe.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
    vqe.restype = ctypes.c_size_t

    pid = None
    for p in pymem.process.list_processes():
        try:
            name = p.szExeFile
            if isinstance(name, bytes):
                name = name.decode('utf-8', 'ignore')
            if name.lower() == 'weixin.exe':
                pid = p.th32ProcessID
                break
        except Exception:
            pass
    if not pid:
        return []

    h = k32.OpenProcess(0x0010 | 0x0400, False, pid)
    if not h:
        return []

    pat = _re.compile(rb'\d{5,20}')
    seen = set()
    addr = 0x10000
    try:
        while addr < 0x7FFFFFFFFFFF:
            mbi = MBI()
            if vqe(h, c_void_p(addr), byref(mbi), sizeof(mbi)) == 0:
                break
            ba, rs = mbi.BaseAddress or 0, mbi.RegionSize or 0
            if rs == 0:
                break
            if (mbi.State == 0x1000 and 256 < rs < 200 * 1024 * 1024
                    and mbi.Protect not in (0, 0x01, 0x100)):
                pos, end = ba, ba + rs
                while pos < end:
                    n = min(65536, end - pos)
                    buf = create_string_buffer(n)
                    br = c_size_t(0)
                    if rpm(h, c_void_p(pos), buf, n, byref(br)) and br.value:
                        for m in pat.finditer(buf.raw[:br.value]):
                            s = m.group(0).decode()
                            if s not in seen:
                                seen.add(s)
                                yield s
                    pos += 65536
            addr = ba + rs
    finally:
        k32.CloseHandle(h)


def find_seed(wxid, sample_file, candidates=None):
    """The seed that decrypts `sample_file`, or None.

    Proven per candidate by decrypting the sample and looking for a known
    image magic - so a false positive is essentially impossible.
    """
    raw = open(sample_file, 'rb').read(64) if sample_file else b''
    if not raw:
        return None
    for seed in (candidates or find_seed_candidates()):
        if sniff(decrypt(raw, derive_key(seed, wxid)))[0]:
            return seed
    return None


def _ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


_FFMPEG = None
_FFMPEG_CHECKED = False


def decode_wxgf(data):
    """First frame of a `wxgf` payload as PNG bytes, or b''.

    The container is 'wxgf' + a small header, then a raw H.265 stream.
    """
    global _FFMPEG, _FFMPEG_CHECKED
    if not _FFMPEG_CHECKED:
        _FFMPEG = _ffmpeg_exe()
        _FFMPEG_CHECKED = True
    if not _FFMPEG:
        return b''
    tmp_in = tmp_out = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.hevc', delete=False) as fh:
            fh.write(data[4:])
            tmp_in = fh.name
        tmp_out = tmp_in + '.png'
        subprocess.run(
            [_FFMPEG, '-y', '-loglevel', 'error', '-f', 'hevc', '-i', tmp_in,
             '-frames:v', '1', tmp_out],
            capture_output=True, timeout=30,
        )
        if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
            with open(tmp_out, 'rb') as fh:
                return fh.read()
    except Exception:
        pass
    finally:
        for p in (tmp_in, tmp_out):
            try:
                if p:
                    os.remove(p)
            except OSError:
                pass
    return b''


def load_sticker(cache_dirs, md5_hex, key, decode_cache_dir=''):
    """(image bytes, mime) for a sticker by md5, or (b'', '').

    Decoding wxgf needs ffmpeg and is slow, so results are cached under
    `decode_cache_dir` keyed by md5.
    """
    if not md5_hex or not key:
        return b'', ''
    if decode_cache_dir:
        cached = os.path.join(decode_cache_dir, md5_hex)
        if os.path.isfile(cached):
            try:
                with open(cached, 'rb') as fh:
                    data = fh.read()
                mime, _ = sniff(data)
                if mime:
                    return data, mime
            except OSError:
                pass

    src = ''
    for base in cache_dirs:
        candidate = os.path.join(base, md5_hex[:2], md5_hex)
        if os.path.isfile(candidate):
            src = candidate
            break
    if not src:
        return b'', ''

    try:
        with open(src, 'rb') as fh:
            raw = fh.read()
    except OSError:
        return b'', ''
    data = decrypt(raw, key)
    mime, _ = sniff(data)
    if mime == 'image/hevc':
        data = decode_wxgf(data)
        mime = 'image/png' if data else ''
    if not mime:
        return b'', ''

    if decode_cache_dir and data:
        try:
            os.makedirs(decode_cache_dir, exist_ok=True)
            with open(os.path.join(decode_cache_dir, md5_hex), 'wb') as fh:
                fh.write(data)
        except OSError:
            pass
    return data, mime


def sticker_cache_dirs(account_root):
    """Where WeChat stores cached stickers for this account."""
    dirs = []
    cache_root = os.path.join(account_root, 'cache')
    if os.path.isdir(cache_root):
        for month in sorted(os.listdir(cache_root)):
            d = os.path.join(cache_root, month, 'Emoticon')
            if os.path.isdir(d):
                dirs.append(d)
    for sub in ('Persist', 'Thumb', 'Temp'):
        d = os.path.join(account_root, 'business', 'emoticon', sub)
        if os.path.isdir(d):
            dirs.append(d)
    return dirs


def any_sticker_file(dirs):
    """Some cached sticker, used as the verification sample."""
    for base in dirs:
        for sub in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            path = os.path.join(base, sub)
            if os.path.isdir(path):
                for name in sorted(os.listdir(path)):
                    f = os.path.join(path, name)
                    if os.path.isfile(f) and os.path.getsize(f) > 32:
                        return f
    return ''


def _main():
    import argparse
    ap = argparse.ArgumentParser(description='WeChat sticker cache helper')
    ap.add_argument('--find-seed', metavar='ACCOUNT_ROOT',
                    help='Scan WeChat memory for the sticker seed of this account')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    if not args.find_seed:
        ap.print_help()
        return

    account_root = args.find_seed
    wxid = account_wxid(os.path.basename(os.path.normpath(account_root)))
    dirs = sticker_cache_dirs(account_root)
    sample = any_sticker_file(dirs)
    if not sample:
        print('未找到可用的表情缓存文件，请先在微信里查看几个表情包')
        return

    seed = find_seed(wxid, sample)
    if not seed:
        print('未找到 seed。请确认微信正在运行，且曾在微信中查看过表情包。')
        return

    import json as _json
    if args.json:
        print(_json.dumps({'success': True, 'seed': seed, 'wxid': wxid, 'sample': sample}))
    else:
        print(f'✓ seed = {seed}')
        print(f'  账号: {wxid}')
        print(f'  样本: {sample}')


if __name__ == '__main__':
    _main()
