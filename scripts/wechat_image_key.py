#!/usr/bin/env python3
"""Recover the AES key for WeChat 4.x V2 `.dat` chat images.

The `.dat` container is

    [6B sig 07 08 56 32 08 07][4B aes_size][4B xor_size][1B pad]
        + AES-128-ECB(ciphertext) | plaintext | XOR tail

Unlike the sticker cache, this key is **not** derived from an account seed -
WeChat loads it into memory only while images are being viewed, so it has to be
scraped from the process. Candidates are ASCII alphanumeric strings (also tried
as hex), each proven by decrypting a real file and finding a strong image magic.

Reference: ZedeX/weixin-decrypte-script (find_image_key.py).
"""
import ctypes
import glob
import os
import re
import struct
from ctypes import (byref, c_size_t, c_void_p, create_string_buffer, sizeof,
                    wintypes)

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# 3+ byte magics only: a 2-byte magic (e.g. BMP's 'BM') is matched by chance
# because every file in the store shares the same first ciphertext block.
STRONG_MAGICS = (b'\xff\xd8\xff', b'\x89PNG', b'GIF8', b'RIFF')


def parse_head(path, keep=64):
    """(aes ciphertext head, xor_size) for a V2 file, or (b'', 0)."""
    with open(path, 'rb') as fh:
        raw = fh.read(15 + keep)
    if len(raw) < 15 or raw[:6] != b'\x07\x08\x56\x32\x08\x07':
        return b'', 0
    aes_size, xor_size = struct.unpack('<II', raw[6:14])
    body = raw[15:15 + min(aes_size, keep)]
    return body[:len(body) // 16 * 16], xor_size


def key_works(key, bodies):
    """True if this key decrypts any sample into a recognisable image."""
    if len(key) != 16:
        return False
    for body in bodies:
        for mode in (modes.ECB(), modes.CBC(key)):
            try:
                dec = Cipher(algorithms.AES(key), mode).decryptor()
                out = dec.update(body) + dec.finalize()
            except Exception:
                continue
            if any(out.startswith(m) for m in STRONG_MAGICS):
                return True
    return False


def sample_bodies(account_root, limit=6):
    pat = os.path.join(account_root, 'msg', 'attach', '*', '*', 'Img', '*_t.dat')
    bodies = []
    for f in sorted(glob.glob(pat))[:limit]:
        body, _ = parse_head(f)
        if body:
            bodies.append(body)
    return bodies


def scan_keys(bodies, on_progress=None, should_stop=None):
    """Yield candidate keys scraped from WeChat's memory that decrypt a sample."""
    try:
        import pymem
    except ImportError:
        return

    PROCESS_VM_READ, PROCESS_QUERY_INFORMATION = 0x0010, 0x0400
    MEM_COMMIT, PAGE_NOACCESS, PAGE_GUARD = 0x1000, 0x01, 0x100

    class MBI(ctypes.Structure):
        _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                    ('AllocationProtect', wintypes.DWORD), ('PartitionId', wintypes.WORD),
                    ('RegionSize', ctypes.c_size_t), ('State', wintypes.DWORD),
                    ('Protect', wintypes.DWORD), ('Type', wintypes.DWORD)]

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
        return

    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return

    # 16 位与 32 位字母数字串（32 位的按 hex 再试一次）
    pat16 = re.compile(rb'[A-Za-z0-9]{16}')
    pat32 = re.compile(rb'[A-Za-z0-9]{32}')
    seen = set()
    tried = 0
    addr = 0x10000
    try:
        while addr < 0x7FFFFFFFFFFF:
            if should_stop and should_stop():
                break
            mbi = MBI()
            if vqe(h, c_void_p(addr), byref(mbi), sizeof(mbi)) == 0:
                break
            ba, rs = mbi.BaseAddress or 0, mbi.RegionSize or 0
            if rs == 0:
                break
            if (mbi.State == MEM_COMMIT and 256 < rs < 200 * 1024 * 1024
                    and mbi.Protect not in (0, PAGE_NOACCESS, PAGE_GUARD)):
                pos, end = ba, ba + rs
                while pos < end:
                    if should_stop and should_stop():
                        break
                    n = min(65536, end - pos)
                    buf = create_string_buffer(n)
                    br = c_size_t(0)
                    if rpm(h, c_void_p(pos), buf, n, byref(br)) and br.value:
                        data = buf.raw[:br.value]
                        for m in pat32.finditer(data):
                            s = m.group(0).decode()
                            if s in seen:
                                continue
                            seen.add(s)
                            tried += 1
                            try:
                                if key_works(bytes.fromhex(s), bodies):
                                    yield 'hex:' + s
                            except ValueError:
                                pass
                        for m in pat16.finditer(data):
                            s = m.group(0).decode()
                            if s in seen:
                                continue
                            seen.add(s)
                            tried += 1
                            if key_works(s.encode(), bodies):
                                yield 'ascii:' + s
                    pos += 65536
                    if on_progress and tried % 20000 < 1000:
                        on_progress(tried)
            addr = ba + rs
    finally:
        k32.CloseHandle(h)


if __name__ == '__main__':
    import argparse
    import sys
    ap = argparse.ArgumentParser(description='Recover the WeChat .dat image key')
    ap.add_argument('--account', required=True, help='账号目录 (含 msg/ 与 db_storage/)')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    bodies = sample_bodies(args.account)
    if not bodies:
        print('未找到 V2 格式的图片缓存')
        sys.exit(1)

    found = None
    for cand in scan_keys(bodies, on_progress=lambda n: print(f'  ...已测试 {n} 个候选', file=sys.stderr)):
        found = cand
        break

    if not found:
        if args.json:
            print('{"success": false}')
        else:
            print('未找到密钥。请先在微信里点开查看几张图片（让它把密钥载入内存），再立刻重跑。')
        sys.exit(1)

    kind, value = found.split(':', 1)
    if args.json:
        import json
        print(json.dumps({'success': True, 'key': value, 'kind': kind}))
    else:
        print(f'✓ 密钥 = {value}  ({kind})')
