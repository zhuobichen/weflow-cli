"""导出产物的归档打包：可选 ZIP，密码时用 WinZip AES-256。

**为什么单独一个脚本**：导出有三个前端（HTML / Markdown / 别的格式），
打包只该有一份实现；而且加密要能被单独测（见 `test/archive_zip_test.py`）。

AE-256 走 `pyzipper`（它复用已装的 pycryptodome）。没装时**明确报错**，
不静默降级成"看起来加密了其实没有"——那比不加密更糟。

用法：
    python scripts/archive_zip.py --src <目录或文件> --out <zip 路径> [--password <pw>]
输出（stdout，单行 JSON）：
    {"success": true, "path": "...", "entries": 42, "encrypted": true, "algorithm": "WZ_AES-256"}
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import zipfile

# AES 加密只有 pyzipper 提供；没有它就只能在有密码时停下来。
try:
    import pyzipper
except Exception:                                     # pragma: no cover
    pyzipper = None


def collect(src: pathlib.Path):
    """要打包的条目：目录递归（跳过 zip 自身与 .tmp），单文件就一条。"""
    if src.is_file():
        return [src]
    out = []
    for root, _dirs, files in os.walk(src):
        for name in files:
            p = pathlib.Path(root) / name
            if p.suffix.lower() in ('.tmp',) or p.name.startswith('.'):
                continue
            out.append(p)
    return sorted(out)


def build(src, out_zip: pathlib.Path, password: str = '') -> dict:
    """`src` 可以是一个路径，也可以是多个（导出文件 + 它的 _assets + 媒体报告）。

    多个来源时，arcname 取各自的**文件名/目录名**——因为它们可能来自不同父目录。
    """
    sources = [pathlib.Path(x) for x in (src if isinstance(src, (list, tuple)) else [src])]
    if not sources:
        raise SystemExit('没有给出要打包的来源')
    for one in sources:
        if not one.exists():
            raise SystemExit('源不存在: %s' % one)
    pairs = []
    for one in sources:
        root = one if one.is_dir() else one.parent
        for f in collect(one):
            pairs.append((f, str(f.relative_to(root))))
    if not pairs:
        raise SystemExit('没有可打包的文件: %s' % sources)

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if out_zip.exists():
        out_zip.unlink()

    if password:
        if pyzipper is None:
            raise SystemExit(
                '需要 pyzipper 才能做 AES-256 加密（pip install pyzipper）；'
                '不加 --password 可打包成普通 ZIP')
        with pyzipper.AESZipFile(str(out_zip), 'w',
                                 compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(password.encode('utf-8'))
            # setencryption 在部分版本里需要显式调用一次
            if hasattr(zf, 'setencryption'):
                zf.setencryption(pyzipper.WZ_AES, nbits=256)
            for path, arc in pairs:
                zf.write(str(path), arcname=arc)
        return {'success': True, 'path': str(out_zip), 'entries': len(pairs),
                'encrypted': True, 'algorithm': 'WZ_AES-256'}

    with zipfile.ZipFile(str(out_zip), 'w', zipfile.ZIP_DEFLATED) as zf:
        for path, arc in pairs:
            zf.write(str(path), arcname=arc)
    return {'success': True, 'path': str(out_zip), 'entries': len(pairs),
            'encrypted': False, 'algorithm': 'deflate'}


def main():
    ap = argparse.ArgumentParser(description='把导出目录打成 ZIP（可选 AES-256 加密）')
    ap.add_argument('--src', action='append', required=True,
                    help='要打包的目录或文件；可重复（导出文件 + _assets + 媒体报告）')
    ap.add_argument('--out', required=True, help='输出 zip 路径')
    ap.add_argument('--password', default='', help='给定时用 WinZip AES-256 加密')
    ap.add_argument('--json', action='store_true', help='只输出 JSON（默认也输出 JSON）')
    args = ap.parse_args()

    try:
        result = build([pathlib.Path(x) for x in args.src],
                       pathlib.Path(args.out), args.password)
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps({'success': False, 'code': 'ZIP_FAILED',
                          'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])},
                         ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
