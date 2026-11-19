#!/usr/bin/env python3
"""微信 4.x（V2）本地媒体导出：解密 `.dat` 图片、复制视频。

## 格式（实测校验，2026-11-20，客户端 4.1.15）

```
偏移  长度   内容
0     6     魔数 07 08 56 32 08 07   ("\\x07\\x08V2\\x08\\x07")
6     4     aes_size (LE u32)   —— 实测 1024
10    4     xor_size (LE u32)
14    1     标志位（实测 0x01）
15    N     AES-128-ECB 加密段，N = aes_size
15+N  16    分隔符，**跳过**（本机实测 5adac40b5420f4abecb01338b2678321；
            网上老资料说它是全局常量，实测会变，所以按长度跳过，不硬编码）
15+N+16 M   单字节 XOR 段，M = xor_size
```

`文件大小 == 15 + aes_size + 16 + xor_size` 精确成立（本机 3 个样本逐字节吻合）；
不成立就不是 V2，直接跳过而不是硬解。

## 密钥：离线推导，不需要扫内存

```
AES key = MD5(str(UIN) + wxid)[:16] 的 ASCII 字节
XOR key = UIN & 0xFF
```

**两个坑**（本机踩过，值得写下来）：

1. `wxid` 是账号 wxid **去掉目录后缀**。账号目录名是 `wxid_mgnjdl8034eh22_4c8e`，
   而公式里要用 `wxid_mgnjdl8034eh22`——带上 `_4c8e` 时 12 个 UIN 候选全部解不出来，
   去掉之后一次命中（6/6 样本）。
2. `UIN` 不写在配置里，它是 `%APPDATA%\\Tencent\\xwechat\\{net,net_1}\\kvcomm\\` 下
   那些 `key_*` / `*_input.statistic` **文件名里的数字**之一（同一目录里有好几个，
   多数是版本号/时间戳）。

所以这里不"猜"UIN：把候选全试一遍，用**缩略图（`_t`）解码后的魔数**当判据
（缩略图是明文 JPEG/PNG；原图可能是 WxAM，见下）。判据为 0 就明确报错，
不拿一堆解不开的文件充数。

## 原图是 WxAM，缩略图不是

解密后魔数为 `wxgf`（77 78 67 66）的是 **WxAM 压缩的原图**，还要第二层解码，
本脚本**不处理**——它会计入 `wxam_skipped` 并打印，不写成坏文件。
`_t` / `_h` 后缀的是缩略图/中图，解密后就是普通 JPEG/PNG（实测可被 PIL 完整打开）。

## 视频不用解密

`msg/video/<YYYY-MM>/*.mp4` 是**明文** mp4（头部 `00 00 00 xx ftyp`），直接复制/索引。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import struct
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# 输出里有 ✓ / ⚠ 这类字符：重定向到管道时 Windows 默认 GBK 会 UnicodeEncodeError。
# 桥接跑带 PYTHONIOENCODING 所以看不出来，`weflow-cli media-export > x.txt` 才会炸。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:  # noqa: BLE001 - 老解释器没有 reconfigure 就维持原样
    pass

from _utils import load_config, output_root  # noqa: E402

V2_MAGIC = b'\x07\x08V2\x08\x07'
HEADER = 15
SEP = 16

#: 解密后的魔数 → 扩展名。wxgf 单独列出：它是 WxAM 压缩，需要第二层解码。
IMAGE_MAGIC = (
    (b'\xff\xd8\xff', '.jpg'),
    (b'\x89PNG\r\n\x1a\n', '.png'),
    (b'GIF87a', '.gif'),
    (b'GIF89a', '.gif'),
    (b'RIFF', '.webp'),
)
WXAM_MAGIC = (b'wxgf', b'WxAM')

#: kvcomm 文件名里的数字候选长度。太短的是分片号，太长的是纳秒时间戳。
_NUM = re.compile(r'\d{6,10}')


def find_uin_candidates(kvcomm_dirs: list[pathlib.Path]) -> list[int]:
    """从 kvcomm 目录的文件名里收集 UIN 候选（不猜，全试）。"""
    seen: set[int] = set()
    for d in kvcomm_dirs:
        try:
            names = [p.name for p in d.iterdir()]
        except OSError:
            continue
        for name in names:
            for m in _NUM.findall(name):
                seen.add(int(m))
    return sorted(seen)


def wxid_variants(wxid: str) -> list[str]:
    """wxid 的候选写法：原样、以及去掉末尾 `_xxxx` 目录后缀。

    实测（本机）：公式里用的是**去掉后缀**的那个；带后缀一个都解不出来。
    """
    out = [wxid]
    m = re.match(r'^(wxid_[0-9a-zA-Z]+)_[0-9a-f]{4}$', wxid or '')
    if m:
        out.insert(0, m.group(1))
    return out


def derive_keys(uin: int, wxid: str) -> tuple[bytes, int]:
    """AES key（ASCII 的 md5 前 16 位）与单字节 XOR key。"""
    aes = hashlib.md5(f'{uin}{wxid}'.encode()).hexdigest()[:16].encode()
    return aes, uin & 0xFF


def parse_v2(data: bytes) -> tuple[int, int] | None:
    """返回 (aes_size, xor_size)；不是 V2 或长度不自洽就返回 None。"""
    if len(data) < HEADER or data[:6] != V2_MAGIC:
        return None
    aes_size, xor_size = struct.unpack('<II', data[6:14])
    if aes_size <= 0 or aes_size % 16 or xor_size < 0:
        return None
    if len(data) != HEADER + aes_size + SEP + xor_size:
        return None
    return aes_size, xor_size


def parse_v2_loose(data: bytes) -> tuple[int, int] | None:
    """只看头字段，不校验长度——用来分辨"不是 V2"和"是 V2 但布局不认识"。

    实测（2026-11-20）有一类 `_h.dat`：头里 `xor_size` 写 1 MB，文件却有 1.34 MB，
    多出的 29 万字节用哪种切法都拼不回完整图片（整段 XOR / 前半段 XOR + 尾段原样 /
    截断到 xor_size 三种都试过，PIL 全部报 broken data stream）。
    它是**布局差异**，不是"坏文件"——所以单独归类、明确报出，而不是混进失败率。
    """
    if len(data) < HEADER or data[:6] != V2_MAGIC:
        return None
    aes_size, xor_size = struct.unpack('<II', data[6:14])
    if aes_size <= 0 or aes_size % 16 or xor_size < 0:
        return None
    return aes_size, xor_size


def decrypt_v2(data: bytes, aes_key: bytes, xor_key: int) -> bytes:
    """两段拼接：AES 段 + 跳过分隔符后的 XOR 段。"""
    parsed = parse_v2(data)
    if parsed is None:
        return b''
    aes_size, xor_size = parsed
    from Crypto.Cipher import AES

    head = AES.new(aes_key, AES.MODE_ECB).decrypt(data[HEADER:HEADER + aes_size])
    start = HEADER + aes_size + SEP
    tail = bytes(b ^ xor_key for b in data[start:start + xor_size])
    return head + tail


def classify(plain: bytes) -> str:
    """按魔数给扩展名；`wxgf`/`WxAM` 返回空串（由调用方计入 xwam）。认不出也返回空串。"""
    for magic, ext in IMAGE_MAGIC:
        if plain.startswith(magic):
            return ext
    return ''


def is_wxam(plain: bytes) -> bool:
    return plain.startswith(WXAM_MAGIC)


def extract_hevc(plain: bytes) -> bytes:
    """从 WxAM（`wxgf`）容器里取出 Annex-B HEVC 比特流。

    实测（2026-11-20，本机 4.1.15）：文件头是 42 字节，紧随其后就是标准起始码
    `00 00 00 01`，再往后是普通 HEVC——`ffmpeg -f hevc -i` 能直接解出 1280×1858 的原图
    （同一张图的缩略图只有 145×210）。

    这里**不解析那 42 字节头**：它对我们的用途没有信息量，而"找第一个起始码"这个判断
    对单帧、多帧动画都成立。找不到起始码就返回空（由调用方计入 unsupported）。
    """
    for start in (b'\x00\x00\x00\x01', b'\x00\x00\x01'):
        idx = plain.find(start)
        if idx >= 0:
            return plain[idx:]
    return b''


def find_ffmpeg(explicit: str | None = None) -> str | None:
    """定位 ffmpeg：显式参数 > 环境变量 `WEFLOW_FFMPEG` > PATH。找不到返回 None。

    不写死任何本机路径——包是发给别人用的，他们装在哪我们不知道。
    """
    if explicit and pathlib.Path(explicit).is_file():
        return explicit
    env = os.path.dirname(os.path.abspath(sys.executable))
    cand = pathlib.Path(env) / 'ffmpeg.exe'          # 与 Python 同目录的常见部署方式
    if cand.is_file():
        return str(cand)
    from os import environ
    from shutil import which
    if environ.get('WEFLOW_FFMPEG') and pathlib.Path(environ['WEFLOW_FFMPEG']).is_file():
        return environ['WEFLOW_FFMPEG']
    return which('ffmpeg')


def decode_wxam(hevc: bytes, dest_stem: pathlib.Path, ffmpeg: str) -> dict:
    """把 HEVC 比特流转成图片：单帧出 `.png`，多帧出 `.gif`。

    WeChat 的动图表情就是多帧 HEVC。先按帧导出到临时目录数帧数，再决定产物形态——
    直接 `-frames:v 1` 会把动图悄悄截成第一帧，那种"成功"比失败更难发现。
    """
    import shutil as _shutil
    import subprocess
    import tempfile

    stem = str(dest_stem)
    with tempfile.TemporaryDirectory(prefix='wxam-') as tmp:
        src = pathlib.Path(tmp) / 'in.265'
        src.write_bytes(hevc)
        pattern = pathlib.Path(tmp) / 'f_%03d.png'
        proc = subprocess.run(
            [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'hevc', '-i', str(src),
             # 新 ffmpeg 把 `-vsync` 换成了 `-fps_mode`（本机 2026-10 版本已不认 -vsync）。
             # passthrough = 有几帧写几帧，动图不会被悄悄截成第一帧。
             '-fps_mode', 'passthrough', str(pattern)],
            capture_output=True)
        frames = sorted(pathlib.Path(tmp).glob('f_*.png'))
        if proc.returncode != 0 or not frames:
            return {'ok': False, 'error': (proc.stderr.decode('utf-8', 'replace') or '解不出帧')[:160]}
        if len(frames) == 1:
            dest = pathlib.Path(stem + '.png')
            _shutil.move(str(frames[0]), dest)
            return {'ok': True, 'path': str(dest), 'bytes': dest.stat().st_size, 'frames': 1}
        dest = pathlib.Path(stem + '.gif')
        gif = subprocess.run(
            [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'hevc', '-i', str(src),
             '-filter_complex', '[0:v]split[a][b];[a]palettegen[p];[b][p]paletteuse',
             '-loop', '0', str(dest)],
            capture_output=True)
        if gif.returncode != 0 or not dest.exists():
            return {'ok': False, 'error': (gif.stderr.decode('utf-8', 'replace') or 'GIF 打包失败')[:160]}
        return {'ok': True, 'path': str(dest), 'bytes': dest.stat().st_size, 'frames': len(frames)}


def calibrate(samples: list[pathlib.Path], cands: list[int], wxids: list[str]) -> tuple[int, str] | None:
    """用真实样本反推 (uin, wxid)：解出来是图片的才算数。

    判据用 `_t` 缩略图——原图可能是 WxAM，拿它当判据会把正确密钥判成错的。
    """
    thumbs = [p for p in samples if p.stem.endswith('_t')] or samples
    best: tuple[int, str, int] | None = None
    for uin in cands:
        for wxid in wxids:
            aes, xor = derive_keys(uin, wxid)
            hit = 0
            for p in thumbs[:6]:
                try:
                    plain = decrypt_v2(p.read_bytes(), aes, xor)
                except Exception:  # noqa: BLE001 - 坏文件不该中断标定
                    continue
                if classify(plain):
                    hit += 1
            if hit and (best is None or hit > best[2]):
                best = (uin, wxid, hit)
    return (best[0], best[1]) if best else None


def _kvcomm_dirs() -> list[pathlib.Path]:
    import os
    base = pathlib.Path(os.environ.get('APPDATA') or pathlib.Path.home()) / 'Tencent' / 'xwechat'
    return [base / 'net' / 'kvcomm', base / 'net_1' / 'kvcomm', base / 'ilink' / 'kvcomm']


def _months_between(since: str | None, until: str | None) -> set[str] | None:
    """把 --since/--until 变成 'YYYY-MM' 集合；没给就返回 None（不限）。"""
    if not since and not until:
        return None
    def ym(s: str) -> tuple[int, int]:
        y, m = s.split('-')[:2]
        return int(y), int(m)
    start = ym(since) if since else ym(until)  # type: ignore[arg-type]
    end = ym(until) if until else start
    out: set[str] = set()
    y, m = start
    while (y, m) <= end:
        out.add(f'{y:04d}-{m:02d}')
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description='导出微信 4.x 本地媒体（解密 .dat 图片 / 复制视频）')
    ap.add_argument('--since', help='起始月份 YYYY-MM（按目录名筛，默认全部）')
    ap.add_argument('--until', help='结束月份 YYYY-MM')
    ap.add_argument('--out', help='输出目录（默认 output/media）')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--limit', type=int, default=0, help='最多处理多少个 .dat（0=不限）')
    ap.add_argument('--no-video', action='store_true', help='只导出图片，不复制视频')
    ap.add_argument('--dry-run', action='store_true', help='只统计，不落盘')
    ap.add_argument('--ffmpeg', help='ffmpeg 路径（默认取环境变量 WEFLOW_FFMPEG 或 PATH 里的）')
    ap.add_argument('--no-wxam', action='store_true', help='不解码 WxAM 原图（只解明文缩略图）')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    config = load_config() or {}
    data_root = pathlib.Path(config.get('dbPath') or '')
    wxid = config.get('wxid') or ''
    if not data_root or not wxid:
        print('  [ERROR] 没配置 dbPath / wxid：先运行 weflow-cli init')
        return 1
    attach = data_root / wxid / 'msg' / 'attach'
    videos = data_root / wxid / 'msg' / 'video'
    if not attach.is_dir():
        print(f'  [ERROR] 找不到图片缓存目录: {attach}')
        return 1

    months = _months_between(args.since, args.until)
    dat_files = sorted(p for p in attach.glob('*/*/Img/*.dat')
                       if months is None or any(f'{m}' in str(p) for m in months))
    if args.limit:
        dat_files = dat_files[:args.limit]
    video_files = []
    if not args.no_video and videos.is_dir():
        video_files = sorted(p for p in videos.glob('*/*.mp4')
                             if months is None or any(m in p.parent.name for m in months))

    cands = find_uin_candidates(_kvcomm_dirs())
    calib = calibrate(dat_files, cands, wxid_variants(wxid)) if dat_files else None
    if dat_files and not calib:
        print('  [ERROR] 解不开任何样本：UIN 候选 %d 个、wxid 变体 %s 都试过了。'
              % (len(cands), wxid_variants(wxid)))
        print('          可能客户端换了密钥推导（格式仍是 V2）。不要硬试，先更新本脚本的标定。')
        return 1
    if calib:
        print(f'  密钥标定: UIN={calib[0]} wxid={calib[1]}')

    root = pathlib.Path(args.out) if args.out else pathlib.Path(output_root()) / 'media'
    ffmpeg = None if args.no_wxam else find_ffmpeg(args.ffmpeg)
    if not args.no_wxam:
        print(f"  WxAM 解码: {ffmpeg or '未找到 ffmpeg（原图将跳过，只出缩略图）'}")
    if args.dry_run:
        print(f'  .dat {len(dat_files)} 个 | mp4 {len(video_files)} 个')
        print(f'  输出: {root}')
        return 0

    for sub in ('images', 'videos'):
        (root / sub).mkdir(parents=True, exist_ok=True)

    stats = {'images': 0, 'videos': 0, 'skipped': 0, 'wxam_skipped': 0,
             'wxam_decoded': 0, 'unsupported': 0, 'bad': 0, 'bytes': 0}
    failures: list[dict] = []

    def one_dat(p: pathlib.Path) -> dict:
        aes, xor = derive_keys(*calib) if calib else (b'', 0)
        raw = p.read_bytes()
        try:
            plain = decrypt_v2(raw, aes, xor)
        except Exception as exc:  # noqa: BLE001
            return {'file': p.name, 'ok': False, 'reason': f'{type(exc).__name__}: {exc}'[:100]}
        if not plain:
            # 能认出 V2 头但长度不自洽 = 布局差异（见 parse_v2_loose 的注释），不是坏文件
            loose = parse_v2_loose(raw)
            reason = ('V2 布局差异（xor_size 与文件长度不符）' if loose
                      else '不是 V2（旧版 XOR 或其它格式）')
            return {'file': p.name, 'ok': True, 'unsupported': True, 'reason': reason, 'bytes': 0}
        ext = classify(plain)
        month = p.parent.parent.name
        dest_dir = root / 'images' / month
        if not ext:
            if is_wxam(plain):
                if not ffmpeg:
                    return {'file': p.name, 'ok': True, 'wxam': True, 'bytes': 0}
                hevc = extract_hevc(plain)
                if not hevc:
                    return {'file': p.name, 'ok': True, 'unsupported': True,
                            'reason': 'WxAM 里找不到 HEVC 起始码', 'bytes': 0}
                dest_dir.mkdir(parents=True, exist_ok=True)
                got = decode_wxam(hevc, dest_dir / p.stem, ffmpeg)
                if not got['ok']:
                    return {'file': p.name, 'ok': True, 'unsupported': True,
                            'reason': f"WxAM 解码失败: {got['error']}", 'bytes': 0}
                return {'file': p.name, 'ok': True, 'bytes': got['bytes'],
                        'path': got['path'], 'wxam_decoded': True, 'frames': got['frames']}
            return {'file': p.name, 'ok': True, 'unsupported': True,
                    'reason': f'未知魔数 {plain[:4].hex()}', 'bytes': 0}
        # 落盘：images/<YYYY-MM>/<stem>.<ext>。月份来自源目录名（attach/<hash>/<YYYY-MM>/Img）
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (p.stem + ext)
        if dest.exists() and dest.stat().st_size:
            return {'file': p.name, 'ok': True, 'skipped': True, 'bytes': 0}
        dest.write_bytes(plain)
        return {'file': p.name, 'ok': True, 'bytes': len(plain), 'path': str(dest)}

    print(f'  解密 {len(dat_files)} 个图片 → {root / "images"}')
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(one_dat, p): p for p in dat_files}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            if not r['ok']:
                stats['bad'] += 1
                failures.append(r)
            elif r.get('wxam'):
                stats['wxam_skipped'] += 1
            elif r.get('unsupported'):
                stats['unsupported'] += 1
            elif r.get('skipped'):
                stats['skipped'] += 1
            else:
                stats['images'] += 1
                stats['bytes'] += r.get('bytes', 0)
                if r.get('wxam_decoded'):
                    stats['wxam_decoded'] += 1
            if i % 200 == 0 or i == len(futs):
                print(f'    [{i}/{len(futs)}] 图片 {stats["images"]} · 跳过 {stats["skipped"]}'
                      f' · WxAM {stats["wxam_skipped"]} · 布局差异 {stats["unsupported"]}'
                      f' · 坏 {stats["bad"]}', flush=True)

    for v in video_files:
        dest_dir = root / 'videos' / v.parent.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / v.name
        if dest.exists() and dest.stat().st_size:
            stats['skipped'] += 1
            continue
        shutil.copy2(v, dest)
        stats['videos'] += 1
        stats['bytes'] += v.stat().st_size
    if video_files:
        print(f'  复制 {stats["videos"]} 个视频 → {root / "videos"}')

    summary = {
        'success': stats['bad'] == 0,
        'key': {'uin': calib[0], 'wxid': calib[1]} if calib else None,
        'dat': len(dat_files), 'video': len(video_files),
        **stats,
        'out': str(root),
        'failures': failures[:20],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"\n  ✓ 完成：图片 {stats['images']} · 视频 {stats['videos']}"
              f" · 跳过 {stats['skipped']} · {stats['bytes'] / 1024 / 1024:.1f} MB")
        if stats['wxam_skipped']:
            print(f"  [WARN] {stats['wxam_skipped']} 个是 WxAM 压缩原图，需要第二层解码，未导出")
        if stats['wxam_decoded']:
            print(f"  WxAM 原图解码成功 {stats['wxam_decoded']} 个（单帧出 png、多帧出 gif）")
        if stats['unsupported']:
            print(f"  [WARN] {stats['unsupported']} 个是 V2 布局差异（xor_size 与文件长度不符）或非 V2，未导出")
        for f in failures[:5]:
            print(f"    - {f['file']}: {f['reason']}")
        print(f'  位置: {root}')
    return 0 if stats['bad'] == 0 else 2


if __name__ == '__main__':
    sys.exit(main())
