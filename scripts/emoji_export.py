"""导出表情：**收藏表情做成真图片**，专辑做成清单（名称/数量/说明/图标）。

为什么是这个范围（实测，2026-11-22 本机 4.2.0.53）：
- 缓存目录（`cache/YYYY-MM/Emoticon` 等）里的文件按 md5 命名，`wechat_emoticon.load_sticker`
  能可靠解出图片：收藏 92 条里 **90 条能在缓存里对上**
- 专辑本体在 `business/emoticon/PersistStore`，整包加密方式与缓存**不同**：
  整体解密、逐条切片、逐条 `aes_key` 三种解法都得到乱码（已试）
- 专辑本体 102 条里只有 3 条也出现在缓存里 → 不能拿缓存冒充专辑

所以：**收藏导出真文件；专辑导出清单与说明，并明确标注本体未导出**。
不产出 `.bin` 垃圾，也不假装成功。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import nt_common
import nt_decrypt as nd
import wechat_emoticon as we
from _utils import load_config, decrypt_lock

_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name, fallback='untitled', limit=60):
    cleaned = _BAD_NAME.sub('_', str(name or '')).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    if not cleaned or cleaned in ('.', '..'):
        cleaned = fallback
    return cleaned[:limit]


def find_account_root(cfg):
    db = cfg.get('dbPath') or ''
    if not db:
        return None
    root = pathlib.Path(db).parent
    accts = [p for p in (root / 'xwechat_files').glob('wxid_*') if p.is_dir()]
    return accts[0] if accts else None


def open_emoticon_db(acct, cfg):
    db = acct / 'db_storage' / 'emoticon' / 'emoticon.db'
    if not db.exists():
        raise SystemExit('找不到 emoticon.db（微信还没缓存过表情？）')
    key = decrypt_lock(cfg.get('decryptKey', '') or '')
    salt = decrypt_lock(cfg.get('ntSalt', '') or '')
    pw = decrypt_lock(cfg.get('favPassphrase', '') or cfg.get('decryptKey', '') or '')
    k, s = nt_common.derive_database_key(str(db), key, salt, pw)
    return nd.connect_nt_db(str(db), k, s)


def resolve_key(acct, cfg=None):
    """(wxid, seed, key)。顺序：配置里的 emoticonSeed → 内存扫描 → 日志启发式。

    **顺序很重要**：
    - 配置里的 `emoticonSeed` 是用户/维护者确认过的，省掉每次扫内存（扫描要几分钟）
    - 内存扫描（`find_seed`）拿到的 seed 是拿样本实测验证过的
    - 日志启发式（`seed_from_logs`）曾给出一个能"跑通"但解不出图的假种子，
      所以它只作最后兜底
    """
    wxid = we.account_wxid(acct.name)
    sample = we.any_sticker_file(we.sticker_cache_dirs(str(acct)))
    seed = None
    if cfg and cfg.get('emoticonSeed'):
        try:
            seed = decrypt_lock(str(cfg['emoticonSeed']))
        except Exception:
            seed = str(cfg['emoticonSeed'])
    if not sample:
        return wxid, seed, (we.derive_key(seed, wxid) if seed else None)
    if not seed:
        try:
            seed = we.find_seed(wxid, str(sample))
        except Exception:
            seed = None
    if not seed:
        try:
            seed = we.resolve_seed(wxid, str(sample))
        except Exception:
            seed = None
    return wxid, seed, (we.derive_key(seed, wxid) if seed else None)


def _cache_index(acct):
    """md5 -> [候选文件路径]。

    同一 md5 可能有多份：`.thumb` 预览、`Thumb/` 下的缩略、`Persist/` 下的本体。
    它们的加密方式不一定相同，所以**别取第一个就放弃**——按"非 .thumb 优先"排序后逐个试。
    """
    index = {}
    for d in we.sticker_cache_dirs(str(acct)):
        for p in pathlib.Path(d).rglob('*'):
            if not p.is_file():
                continue
            md5 = p.name.split('.')[0]
            index.setdefault(md5, []).append(p)
    for md5, paths in index.items():
        paths.sort(key=lambda x: ('.thumb' in x.name, len(str(x))))
    return index


def decode_file(path, key):
    """把一个缓存文件解成 (bytes, mime)。

    与 `wechat_emoticon.load_sticker` 同样的两步：先看原文是不是图（含 wxgf 容器），
    认不出才解密。区别是这里**只要一个路径**，不要求文件名恰好是 md5 或位于特定目录——
    缓存里同一个表情可能叫 `<md5>`、`<md5>.thumb`，也可能在子目录里。
    """
    try:
        raw = pathlib.Path(path).read_bytes()
    except OSError:
        return b'', ''
    mime, _ = we.sniff(raw)
    if mime == 'image/hevc':
        data = we.decode_wxgf(raw)
        return data, (we.sniff(data)[0] or '')
    if mime:
        return raw, mime
    if not key:
        return b'', ''
    dec = we.decrypt(raw, key)
    mime, _ = we.sniff(dec)
    if mime == 'image/hevc':
        dec = we.decode_wxgf(dec)
        mime = we.sniff(dec)[0] or ''
    return dec, mime


def export_favourites(cur, acct, wxid, key, out_dir):
    """收藏表情：按 kFavEmoticonOrderTable 的顺序，从缓存解出真图。"""
    index = _cache_index(acct)
    caps = {}
    try:
        cur.execute('SELECT md5, caption FROM kNonStoreEmoticonTable')
        caps = {str(r[0]): (r[1] or '') for r in cur.fetchall()}
    except Exception:
        pass

    cur.execute('SELECT md5 FROM kFavEmoticonOrderTable')
    order = [str(r[0]) for r in cur.fetchall()]

    target = out_dir / '_收藏'
    entries, notes = [], []
    for idx, md5 in enumerate(order, 1):
        cands = index.get(md5) or []
        if not cands:
            notes.append('收藏 %s：缓存里没有这个文件（微信里点开过一次才会缓存）' % md5[:8])
            continue
        if not key:
            notes.append('收藏：没有解密密钥（seed 未找到），整类跳过')
            return [], notes
        data, mime = b'', ''
        for cand in cands:
            data, mime = decode_file(cand, key)
            if data and mime:
                break
        if not data or not mime:
            notes.append('收藏 %s：%d 个候选文件都解不出图片' % (md5[:8], len(cands)))
            continue
        ext = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif',
               'image/webp': '.webp'}.get(mime, '.img')
        target.mkdir(parents=True, exist_ok=True)
        name = '%03d_%s%s' % (idx, md5[:8], ext)
        (target / name).write_bytes(data)
        entries.append({'md5': md5, 'file': '_收藏/' + name, 'size': len(data),
                        'caption': caps.get(md5, '')})
    return entries, notes


def _store_index(acct):
    """整包文件按大小索引（排掉 .icon）。"""
    index = {}
    base = acct / 'business' / 'emoticon' / 'PersistStore'
    if not base.exists():
        return index
    for f in base.rglob('*'):
        if f.is_file() and not f.name.endswith('.icon'):
            index.setdefault(f.stat().st_size, f)
    return index


def _find_pack_file(index, total, end):
    """按库里总长找整包：允许 PKCS7 填充造成的 0..16 字节差。"""
    base = int(total or 0)
    for size in [base] + list(range(base + 1, base + 17)) + [end]:
        if size in index:
            return index[size]
    return None


def _caption(cur, package_id, md5):
    """表情说明文字（优先中文）。"""
    try:
        cur.execute('SELECT language_, caption_ FROM kStoreEmoticonCaptionsTable '
                    'WHERE package_id_=? AND md5_=?', (package_id, md5))
        rows = cur.fetchall()
    except Exception:
        return ''
    for lang, cap in rows:
        if str(lang).lower().startswith('zh') and cap:
            return cap
    return rows[0][1] if rows else ''


def export_packs(cur, acct, wxid, key, out_dir):
    """按专辑导出表情本体：整包解密 → 按库偏移切片 → 逐条落盘。"""
    index = _store_index(acct)
    cur.execute('SELECT package_id_, package_name_, introduction_, author_, install_time_ '
                'FROM kStoreEmoticonPackageTable ORDER BY sort_order_')
    packs, notes = [], []
    for pid, name, intro, author, installed in cur.fetchall():
        disp = safe_name(name, fallback='专辑_%s' % str(pid)[:8])
        cur2 = cur.connection.cursor()
        cur2.execute('SELECT count(*), sum(emoticon_size_), max(emoticon_offset_ + emoticon_size_) '
                     'FROM kStoreEmoticonFilesTable WHERE package_id_=?', (pid,))
        n, total, end_off = cur2.fetchone()
        entry = {'packageId': str(pid), 'name': str(name or ''), 'author': str(author or ''),
                 'introduction': str(intro or ''), 'count': int(n or 0),
                 'installedAt': int(installed or 0), 'dir': disp,
                 'files': [], 'status': 'ok'}
        pack = _find_pack_file(index, total, end_off)
        if not key:
            entry['status'] = 'unavailable'
            entry['reason'] = '没有解密密钥（内存里没扫到 seed）'
            notes.append('%s：%s' % (disp, entry['reason']))
            packs.append(entry)
            continue
        if not pack:
            entry['status'] = 'unavailable'
            entry['reason'] = '本地没有整包文件（4.x 把专辑合并存放，可能尚未下载）'
            notes.append('%s：%s' % (disp, entry['reason']))
            packs.append(entry)
            continue

        plain = we.decrypt(pack.read_bytes(), key)
        if not plain:
            entry['status'] = 'unavailable'
            entry['reason'] = '整包解密失败'
            notes.append('%s：%s' % (disp, entry['reason']))
            packs.append(entry)
            continue

        target = out_dir / disp
        target.mkdir(parents=True, exist_ok=True)
        cur2.execute('SELECT md5_, emoticon_size_, emoticon_offset_ FROM kStoreEmoticonFilesTable '
                     'WHERE package_id_=? ORDER BY sort_order_', (pid,))
        bad = 0
        for idx, (md5, size, off) in enumerate(cur2.fetchall(), 1):
            blob = plain[int(off):int(off) + int(size)]
            if not blob:
                bad += 1
                continue
            _mime, ext = we.sniff(blob)
            if ext == '.hevc':
                try:
                    decoded = we.decode_wxgf(blob)
                except Exception:
                    decoded = b''
                if decoded:
                    _dm, dext = we.sniff(decoded)
                    if dext:
                        blob, ext = decoded, dext
            if not ext:
                bad += 1
                continue
            fname = '%03d_%s%s' % (idx, str(md5)[:8], ext)
            (target / fname).write_bytes(blob)
            entry['files'].append({'md5': str(md5), 'file': '%s/%s' % (disp, fname),
                                   'size': len(blob), 'caption': _caption(cur, pid, md5)})
        if bad:
            notes.append('%s：%d 条切片认不出格式，已跳过（不写 .bin 垃圾）' % (disp, bad))
        if not entry['files']:
            entry['status'] = 'unavailable'
            entry['reason'] = '切片全部无法识别'
        packs.append(entry)
    return packs, notes


INDEX_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>表情归档</title>
<style>
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f7f7f8;margin:0;padding:24px;color:#222}
h1{font-size:20px;margin:0 0 6px}
.meta{color:#777;font-size:13px;margin-bottom:18px}
section{background:#fff;border-radius:12px;padding:16px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
h2{font-size:16px;margin:0 0 4px}
.count{color:#888;font-size:12px;margin-bottom:10px}
.grid{display:flex;flex-wrap:wrap;gap:10px}
figure{margin:0;width:96px;text-align:center}
figure img{width:96px;height:96px;object-fit:contain;background:#fafafa;border-radius:8px}
figcaption{font-size:11px;color:#888;margin-top:4px;word-break:break-all}
.note{background:#fff7e6;border:1px solid #ffe0a3;color:#7a5b00;padding:10px 12px;border-radius:8px;font-size:13px;margin-bottom:18px}
table{border-collapse:collapse;font-size:13px;width:100%}
td,th{border-bottom:1px solid #eee;padding:6px 8px;text-align:left}
</style></head><body>
"""


def _esc(text):
    return (str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def write_index(out_dir, favourites, packs, notes, meta):
    """生成可离线打开的 index.html。"""
    with_files = [x for x in packs if x.get('files')]
    without = [x for x in packs if not x.get('files')]
    total = len(favourites) + sum(len(x['files']) for x in with_files)

    parts = [INDEX_HEAD, '<h1>表情归档</h1>',
             '<div class="meta">%s ｜ 表情 %d 个 ｜ 收藏 %d 个 ｜ 专辑 %d 个 ｜ 本机导出</div>'
             % (_esc(meta.get('generatedAt', '')), total, len(favourites), len(packs))]
    if notes:
        parts.append('<div class="note"><b>%d 条说明</b><br>%s</div>'
                     % (len(notes), '<br>'.join(_esc(n) for n in notes[:12])))

    if favourites:
        parts.append('<section><h2>收藏表情</h2><div class="count">%d 个</div><div class="grid">'
                     % len(favourites))
        for f in favourites:
            parts.append(_figure(f['file'], f.get('caption')))
        parts.append('</div></section>')

    for pk in with_files:
        parts.append('<section><h2>%s</h2><div class="count">%d 个%s</div><div class="grid">'
                     % (_esc(pk.get('name') or pk.get('dir')),
                        len(pk['files']),
                        (' ｜ ' + _esc(pk.get('author'))) if pk.get('author') else ''))
        for f in pk['files']:
            parts.append(_figure(f['file'], f.get('caption')))
        parts.append('</div></section>')

    if without:
        parts.append('<section><h2>未导出的专辑</h2><div class="count">%d 个</div>' % len(without))
        parts.append('<table><tr><th>专辑</th><th>表情数</th><th>原因</th></tr>')
        for pk in without:
            parts.append('<tr><td>%s</td><td>%d</td><td>%s</td></tr>'
                         % (_esc(pk.get('name')), pk.get('count') or 0, _esc(pk.get('reason'))))
        parts.append('</table></section>')
    parts.append('</body></html>')
    (out_dir / 'index.html').write_text('\n'.join(parts), encoding='utf-8')


def _figure(path, caption):
    return ('<figure><img loading="lazy" src="%s" alt=""><figcaption>%s</figcaption></figure>'
            % (_esc(path), _esc(caption)))


def export(out_dir):
    cfg = load_config()
    acct = find_account_root(cfg)
    if not acct:
        raise SystemExit('没有找到微信账号目录（先跑 weflow-cli init）')
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    wxid, seed, key = resolve_key(acct, cfg)
    conn, cur = open_emoticon_db(acct, cfg)
    try:
        favourites, fnotes = export_favourites(cur, acct, wxid, key, out_dir)
        packs, pnotes = export_packs(cur, acct, wxid, key, out_dir)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    notes = fnotes + pnotes
    meta = {
        'generatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'account': (wxid[:6] + '…') if wxid else '',
        'favourites': len(favourites), 'packs': len(packs),
        'storeBodiesExported': any(x.get('files') for x in packs),
        'seedResolved': bool(seed),
    }
    write_index(out_dir, favourites, packs, notes, meta)
    manifest = dict(meta, notes=notes, favouriteList=favourites, packList=packs)
    (out_dir / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    return manifest


def main():
    ap = argparse.ArgumentParser(description='导出收藏表情与专辑清单')
    ap.add_argument('--out', required=True, help='输出目录')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    try:
        manifest = export(args.out)
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps({'success': False, 'code': 'EMOJI_EXPORT_FAILED',
                          'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])},
                         ensure_ascii=False))
        sys.exit(1)
    print(json.dumps({'success': True, 'path': str(pathlib.Path(args.out)),
                      'favourites': manifest['favourites'], 'packs': manifest['packs'],
                      'notes': len(manifest['notes'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
