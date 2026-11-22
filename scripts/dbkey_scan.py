"""扫微信进程内存找数据库密钥（不依赖 Hook，也不需要"打开数据库"事件）。

## 为什么要它

现有取 key 走 Hook：把 DLL 注入 Weixin.exe，等一次数据库打开事件。微信 4.x 用 WCDB，
连接常驻，点开聊天**不会**再 open 一次——所以只能靠重启/登录触发，用户侧体验是"卡住"
（2026-11-22 实测：hook 装得上，180 秒 1451 次轮询没有事件）。

微信 4.1+ 在进程里保留一个 WCDB 配置对象，里面是用固定掩码异或过的
`x'<64位hex密钥><32位hex盐>'` 字面量。把它读出来、异或还原、切出 (key, salt)，
再用数据库第一页的 HMAC-SHA512 校验——校验是毫秒级，不需要打开库。

## 两条路径

1. **cipher-blob**：找 `com.Tencent.WCDB.Config.Cipher` 字符串，在它附近找一段
   能用掩码还原出 `x'…'` 的字节块（4.1+ 的形态）
2. **literal**：全内存直接找 `x'…'`（4.0.x 会留明文；4.1+ 通常找不到）

校验一律用 `nt_decrypt.verify_raw_key_native`：首页 HMAC 对上才算数，
**不猜、不写配置**。找到就打印（默认打码），需要落盘由调用方决定。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import pathlib
import re
import sys
import time

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import nt_decrypt as nd

# 4.1.11+ 内存布局里那段 blob 的异或掩码（40 字节；来自公开实现，我们只按它还原字节）
CIPHER_NAME = b'com.Tencent.WCDB.Config.Cipher'
XOR_MASK = bytes.fromhex(
    'd2c7442458020000004889442450488b'
    '450048844c2448488944254048584c24'
)
LITERAL_RE = re.compile(rb"[xX]'([0-9a-fA-F]{64,192})'")
MAX_ADDR = 0x0000_7FFF_FFFF_FFFF
PAGE = 4096
CHUNK = 4 * 1024 * 1024
OVERLAP = 4096          # 跨块边界的字面量
NEAR_WINDOW = 256 * 1024
MASKED_PREFIXES = None  # 首次使用时算（依赖 XOR_MASK）  # 在 Cipher 字符串附近找 blob 的半径

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}


class MBI(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
        ('AllocationProtect', wt.DWORD), ('PartitionId', wt.WORD),
        ('RegionSize', ctypes.c_size_t), ('State', wt.DWORD),
        ('Protect', wt.DWORD), ('Type', wt.DWORD),
    ]


def _setup_api():
    k = kernel32
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.OpenProcess.restype = wt.HANDLE
    k.ReadProcessMemory.argtypes = [wt.HANDLE, wt.LPCVOID, wt.LPVOID,
                                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k.ReadProcessMemory.restype = wt.BOOL
    k.VirtualQueryEx.argtypes = [wt.HANDLE, wt.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
    k.VirtualQueryEx.restype = ctypes.c_size_t
    k.CloseHandle.argtypes = [wt.HANDLE]


def find_weixin_pids():
    """按工作集从大到小返回 Weixin.exe 的 pid（主进程通常最大）。"""
    import subprocess
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Weixin.exe', '/FO', 'CSV'],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) >= 5 and parts[0].lower() == 'weixin.exe':
            try:
                pids.append((int(parts[1]), int(parts[4].replace(',', '').replace(' K', '').replace(' ', '') or 0)))
            except ValueError:
                continue
    return [p for p, _ in sorted(pids, key=lambda x: x[1], reverse=True)]


def xor_unmask(blob: bytes, phase: int = 0) -> bytes:
    """按相位解掩码。phase 必须等于该字面量在缓冲区里的起始相位。"""
    n = len(XOR_MASK)
    return bytes(b ^ XOR_MASK[(phase + i) % n] for i, b in enumerate(blob))


def candidates_from_literal(run: str):
    """一段 64~192 hex 里滑窗切 (key_hex, salt_hex)。"""
    out = []
    starts = [0]
    if len(run) > 96:
        starts.extend(range(0, len(run) - 63, 32))
        starts.append(len(run) - 64)
    for st in dict.fromkeys(starts):
        if st < 0 or st + 64 > len(run):
            continue
        key_hex = run[st:st + 64].lower()
        try:
            raw = bytes.fromhex(key_hex)
        except ValueError:
            continue
        if len(set(raw)) < 15:
            continue
        salt_hex = run[st + 64:st + 96].lower() if st + 96 <= len(run) else ''
        out.append((key_hex, salt_hex))
    return out


def candidates_from_blob(blob: bytes):
    """把一段字节按 40 种对齐各解一次，找还原后的 x'…' 字面量。

    为什么要对齐：掩码是 `mask[i % 40]`，只对"blob 起点"成立。窗口起点只要偏 1 字节，
    后面全部解错——第一版就是这么漏掉的（0 候选）。
    """
    if not blob:
        return []
    hits = []
    seen_keys = set()
    for align in range(len(XOR_MASK)):
        decoded = xor_unmask(blob[align:])
        for m in LITERAL_RE.finditer(decoded):
            for key_hex, salt_hex in candidates_from_literal(m.group(1).decode('ascii')):
                if (key_hex, salt_hex) in seen_keys:
                    continue
                seen_keys.add((key_hex, salt_hex))
                hits.append((key_hex, salt_hex))
    return hits


def masked_prefixes():
    """40 种相位下，`x'` 被掩码后的两字节形态。"""
    out = []
    for align in range(len(XOR_MASK)):
        raw = b"x'"
        out.append(bytes(b ^ XOR_MASK[(align + i) % len(XOR_MASK)] for i, b in enumerate(raw)))
    return out


def _ensure_prefixes():
    """惰性初始化：常量依赖 XOR_MASK，且单测会直接调 candidates_from_masked。"""
    global MASKED_PREFIXES
    if MASKED_PREFIXES is None:
        MASKED_PREFIXES = masked_prefixes()
    return MASKED_PREFIXES


def candidates_from_masked(data: bytes, max_hits_per_chunk: int = 64):
    """在任意相位上找被掩码的 x'…' 字面量。"""
    hits = []
    budget = max_hits_per_chunk
    for phase, prefix in enumerate(_ensure_prefixes()):
        if budget <= 0:
            break
        pos = data.find(prefix)
        while pos >= 0 and budget > 0:
            budget -= 1
            # 只解命中处的前 1KB：2 字节前缀误命中很多，解整块 4MB 会退化成平方级。
            # 相位必须用 phase：掩码从缓冲区起点算，字面量不一定落在 0 相位上。
            decoded = xor_unmask(data[pos:pos + 1024], phase)
            m = LITERAL_RE.match(decoded)
            if m:
                hits.extend(candidates_from_literal(m.group(1).decode('ascii')))
            pos = data.find(prefix, pos + 1)
    return hits


def read_mem(handle, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got))
    return buf.raw[:got.value] if ok else b''


def regions(handle):
    """可读的已提交区域。"""
    addr = 0x10000
    mbi = MBI()
    while addr < MAX_ADDR:
        if not kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        if size == 0:
            break
        if mbi.State == MEM_COMMIT and (mbi.Protect & 0xFF) in READABLE:
            yield base, size
        addr = base + size


def scan(pid, db_paths, time_budget=120.0, progress=None):
    """返回 {ok, key, salt, verifiedAgainst, path, scannedMb, elapsedMs}。"""
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return {'ok': False, 'error': 'OpenProcess 失败（需要管理员）'}
    _ensure_prefixes()
    started = time.time()
    scanned = 0
    seen = set()
    near_hits = []
    diag = {'cipherStrings': 0, 'literalHits': 0, 'blobCandidates': 0}

    found = {}          # (key_hex, salt_hex) -> how

    def check(key_hex, salt_hex, how):
        """只收集候选（校验放到扫完之后按盐分组做，避免每个候选都去开库）。"""
        if (key_hex, salt_hex) in seen:
            return None
        seen.add((key_hex, salt_hex))
        found[(key_hex, salt_hex)] = how
        return None

    try:
        for base, size in regions(handle):
            if time.time() - started > time_budget:
                break
            off = 0
            while off < size:
                if time.time() - started > time_budget:
                    break
                want = min(CHUNK + OVERLAP, size - off)
                if want <= 0:
                    break
                data = read_mem(handle, base + off, want)
                scanned += len(data)
                if data and time.time() - started < time_budget:
                    # 路径 2：全内存直接找 x'…'（4.0.x 形态）
                    for m in LITERAL_RE.finditer(data):
                        diag['literalHits'] += 1
                        for key_hex, salt_hex in candidates_from_literal(m.group(1).decode('ascii')):
                            hit = check(key_hex, salt_hex, 'literal')
                            if hit:
                                return hit
                    # 路径 3：不找对象，直接找被掩码的 x' 前缀（最稳，不依赖布局）
                    for key_hex, salt_hex in candidates_from_masked(data):
                        diag['maskedCandidates'] = diag.get('maskedCandidates', 0) + 1
                        hit = check(key_hex, salt_hex, 'masked-literal')
                        if hit:
                            return hit
                    # 路径 1：先记下 Cipher 字符串位置，附近再找 blob
                    at = data.find(CIPHER_NAME)
                    if at >= 0:
                        diag['cipherStrings'] += 1
                        near_hits.append((base + off + at, data, at))
                        # 附近 1KB 内直接试（同一块里最常见）
                        for cand in candidates_from_blob(data[max(0, at - 4096): at + 4096]):
                            diag['blobCandidates'] += 1
                            hit = check(cand[0], cand[1], 'cipher-blob')
                            if hit:
                                return hit
                    if progress and scanned % (64 * 1024 * 1024) < CHUNK:
                        progress(scanned)
                off += CHUNK
        # 附近窗口的补充扫描
        for addr, data, at in near_hits[:8]:
            window = read_mem(handle, max(0, addr - NEAR_WINDOW), NEAR_WINDOW * 2)
            for cand in candidates_from_blob(window):
                diag['blobCandidates'] += 1
                hit = check(cand[0], cand[1], 'cipher-blob')
                if hit:
                    return hit
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass

    # 按盐分组校验：一个 (key, salt) 只对 salt 相同的库有意义
    by_salt = {}
    for db in db_paths:
        try:
            with open(db, 'rb') as fh:
                page = fh.read(16)
        except OSError:
            continue
        if len(page) == 16:
            by_salt.setdefault(page.hex(), []).append(db)

    verified = {}
    for (key_hex, salt_hex), how in found.items():
        for db in by_salt.get(salt_hex, [])[:4]:
            try:
                if nd.verify_raw_key_native(key_hex, salt_hex, str(db)):
                    verified[salt_hex] = {'key': key_hex, 'how': how,
                                          'db': pathlib.Path(db).name}
                    break
            except Exception:
                continue

    if verified:
        first = next(iter(verified.items()))
        return {'ok': True, 'salt': first[0], 'key': first[1]['key'],
                'verifiedAgainst': first[1]['db'], 'path': first[1]['how'],
                'verifiedCount': len(verified), 'saltTotal': len(by_salt),
                'keys': {k: v['key'] for k, v in verified.items()},
                'scannedMb': round(scanned / 1048576.0, 1),
                'elapsedMs': int((time.time() - started) * 1000),
                'candidates': len(seen), 'diag': diag}

    return {'ok': False, 'error': '没有候选通过首页 HMAC 校验',
            'scannedMb': round(scanned / 1048576.0, 1),
            'elapsedMs': int((time.time() - started) * 1000),
            'candidates': len(seen), 'saltTotal': len(by_salt), 'diag': diag}


def main():
    ap = argparse.ArgumentParser(description='扫微信进程内存提取数据库密钥（不依赖 Hook）')
    ap.add_argument('--db-storage', default='', help='账号的 db_storage 目录（默认从配置推）')
    ap.add_argument('--time-budget', type=float, default=120.0, help='扫描时间上限（秒）')
    ap.add_argument('--show-key', action='store_true', help='完整打印密钥（默认打码）')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    if args.db_storage:
        storage = pathlib.Path(args.db_storage)
    else:
        from _utils import load_config
        cfg = load_config()
        root = pathlib.Path(cfg.get('dbPath') or '').parent
        acct = next((p for p in (root / 'xwechat_files').glob('wxid_*') if p.is_dir()), None)
        storage = acct / 'db_storage' if acct else None
    if not storage or not storage.exists():
        print(json.dumps({'success': False, 'error': '找不到 db_storage'}, ensure_ascii=False))
        sys.exit(1)

    dbs = sorted(storage.rglob('*.db'))[:40]
    if not dbs:
        print(json.dumps({'success': False, 'error': 'db_storage 里没有 .db'}, ensure_ascii=False))
        sys.exit(1)

    pids = find_weixin_pids()
    if not pids:
        print(json.dumps({'success': False, 'error': '没有 Weixin.exe 在运行'}, ensure_ascii=False))
        sys.exit(1)

    result = {'success': False, 'error': '扫描未命中'}
    best = None
    for pid in pids[:4]:
        r = scan(pid, dbs, args.time_budget)
        if r.get('ok'):
            result = dict(r, success=True, pid=pid)
            best = None
            break
        r = dict(r, success=False, pid=pid)
        if best is None or (r.get('scannedMb') or 0) > (best.get('scannedMb') or 0):
            best = r
        if 'OpenProcess' in str(r.get('error', '')):
            break
    if best is not None and not result.get('ok'):
        result = best

    if result.get('ok'):
        key = result['key']
        result['keyMasked'] = key[:6] + '…' + key[-4:]
        if not args.show_key:
            result.pop('key', None)
        print(json.dumps(result, ensure_ascii=False))
        return
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1)


if __name__ == '__main__':
    main()
