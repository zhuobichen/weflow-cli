"""朋友圈归档导出：文字忠实、媒体列清楚、不猜图。

范围与理由（2026-11-22 本机 4.2.0.53 实测）：
- `SnsTimeLine` 710 条动态，正文在 content XML（`<contentDesc>`），媒体在 `<mediaList>`
  的 `<url>` / `<thumb>` 里，**没有本地 md5 字段**
- 本地缓存 `cache/<月>/Sns/Img/...` 的文件名是 32 位十六进制，但 `md5(url)`、
  `md5(去 query 的 url)`、`md5(路径部分)` 三种最自然的推导**命中率都是 0/141**
- 所以这一版**不把本地缓存图硬塞进动态**（错配的图比没有图更糟），
  只把媒体 URL 原样写进 `posts.json` 与页面，并在 manifest 里写明原因

产出：`posts.json`（结构化）、`index.html`（可离线读的时间线）、`manifest.json`（统计与说明）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import nt_common
import nt_decrypt as nd
from _utils import load_config, decrypt_lock

_TAG = re.compile(r'<(\w+)>(.*?)</\1>', re.S)


def find_account_root(cfg):
    db = cfg.get('dbPath') or ''
    if not db:
        return None
    root = pathlib.Path(db).parent
    accts = [p for p in (root / 'xwechat_files').glob('wxid_*') if p.is_dir()]
    return accts[0] if accts else None


def open_sns_db(acct, cfg):
    db = acct / 'db_storage' / 'sns' / 'sns.db'
    if not db.exists():
        raise SystemExit('找不到 sns.db（微信还没缓存过朋友圈？）')
    key = decrypt_lock(cfg.get('decryptKey', '') or '')
    salt = decrypt_lock(cfg.get('ntSalt', '') or '')
    pw = decrypt_lock(cfg.get('favPassphrase', '') or cfg.get('decryptKey', '') or '')
    k, s = nt_common.derive_database_key(str(db), key, salt, pw)
    return nd.connect_nt_db(str(db), k, s)


def _esc(text):
    return (str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _text_of(root, name, default=''):
    node = root.find('.//%s' % name)
    return (node.text or default).strip() if node is not None else default


def parse_post(content):
    """把一条动态的 XML 解成 {time, author, text, media:[{url, thumb}]}。

    用 ElementTree 而不是正则：`<(\w+)>(.*?)</\1>` 会从外层 `<TimelineObject>` 一路
    吃到它自己的闭合标签，结果整条内容被当成一个字段（实测：只有一个匹配）。
    """
    if not content:
        return None
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # 个别动态的 content 不是严格 XML；退回按标签单取
        raw_time = (re.search(r'<createTime>(\d+)</createTime>', content) or [None, ''])[1]
        if not raw_time:
            return None
        return {
            'time': int(raw_time),
            'timeText': datetime.datetime.fromtimestamp(int(raw_time)).strftime('%Y-%m-%d %H:%M'),
            'author': (re.search(r'<username>(.*?)</username>', content, re.S) or [None, ''])[1].strip(),
            'text': (re.search(r'<contentDesc>(.*?)</contentDesc>', content, re.S) or [None, ''])[1].strip(),
            'media': [],
        }

    raw_time = _text_of(root, 'createTime')
    if not raw_time.isdigit():
        return None
    ts = int(raw_time)
    media = []
    for item in root.iter('media'):
        url = _text_of(item, 'url')
        thumb = _text_of(item, 'thumb')
        if url or thumb:
            media.append({'url': url, 'thumb': thumb})
    return {
        'time': ts,
        'timeText': datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M'),
        'author': _text_of(root, 'username'),
        'text': _text_of(root, 'contentDesc'),
        'media': media,
    }


def load_posts(cur, limit=0, user='', since=0, until=0):
    cur.execute('SELECT content FROM SnsTimeLine')
    posts = []
    for (content,) in cur.fetchall():
        p = parse_post(content)
        if not p or not p['time']:
            continue
        if user and user not in p['author']:
            continue
        if since and p['time'] < since:
            continue
        if until and p['time'] > until:
            continue
        posts.append(p)
    posts.sort(key=lambda x: x['time'], reverse=True)
    return posts[:limit] if limit else posts


INDEX_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>朋友圈归档</title>
<style>
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f2f2f5;margin:0;padding:20px;color:#222}
h1{font-size:20px;margin:0 0 4px}
.meta{color:#777;font-size:13px;margin-bottom:16px}
.post{background:#fff;border-radius:12px;padding:14px 16px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.who{font-size:13px;color:#576b95;margin-bottom:6px}
.when{color:#999;font-size:12px;margin-left:8px}
.text{white-space:pre-wrap;line-height:1.6;font-size:14px}
.media{margin-top:8px;font-size:12px;color:#999}
.media a{color:#576b95;word-break:break-all}
.note{background:#fff7e6;border:1px solid #ffe0a3;color:#7a5b00;padding:10px 12px;border-radius:8px;font-size:13px;margin-bottom:16px}
</style></head><body>
"""


def write_index(out_dir, posts, notes, meta):
    parts = [INDEX_HEAD, '<h1>朋友圈归档</h1>',
             '<div class="meta">%s ｜ %d 条动态 ｜ 本机导出</div>'
             % (_esc(meta.get('generatedAt', '')), len(posts))]
    if notes:
        parts.append('<div class="note">%s</div>' % '<br>'.join(_esc(n) for n in notes))
    for p in posts:
        parts.append('<div class="post">')
        parts.append('<div class="who">%s<span class="when">%s</span></div>'
                     % (_esc(p['author']), _esc(p['timeText'])))
        if p['text']:
            parts.append('<div class="text">%s</div>' % _esc(p['text']))
        if p['media']:
            links = []
            for m in p['media']:
                target = m.get('url') or m.get('thumb')
                links.append('<a href="%s" rel="noreferrer">图片 %d</a>' % (_esc(target), len(links) + 1))
            parts.append('<div class="media">媒体 %d 个（远程地址，可能过期）：%s</div>'
                         % (len(p['media']), ' · '.join(links)))
        parts.append('</div>')
    parts.append('</body></html>')
    (out_dir / 'index.html').write_text('\n'.join(parts), encoding='utf-8')


def export(out_dir, limit=0, user='', since=0, until=0):
    cfg = load_config()
    acct = find_account_root(cfg)
    if not acct:
        raise SystemExit('没有找到微信账号目录（先跑 weflow-cli init）')
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn, cur = open_sns_db(acct, cfg)
    try:
        posts = load_posts(cur, limit, user, since, until)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    media_total = sum(len(p['media']) for p in posts)
    notes = []
    if media_total:
        notes.append('媒体 %d 个只记录远程地址：本地缓存文件名与 url 的 md5 对不上'
                     '（实测 0/141），这一版不把缓存图硬塞进动态，避免错配。' % media_total)

    meta = {
        'generatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'account': (acct.name[:10] + '…'),
        'posts': len(posts), 'media': media_total, 'mediaFiles': 0,
    }
    write_index(out_dir, posts, notes, meta)
    (out_dir / 'posts.json').write_text(
        json.dumps({'meta': meta, 'notes': notes, 'posts': posts},
                   ensure_ascii=False, indent=1), encoding='utf-8')
    (out_dir / 'manifest.json').write_text(
        json.dumps(dict(meta, notes=notes), ensure_ascii=False, indent=1), encoding='utf-8')
    return dict(meta, notes=notes)


def main():
    ap = argparse.ArgumentParser(description='导出朋友圈归档（文字 + 媒体地址清单）')
    ap.add_argument('--out', required=True)
    ap.add_argument('-n', '--limit', type=int, default=0, help='最多多少条（0=全部）')
    ap.add_argument('-u', '--user', default='', help='只看某个 wxid（子串匹配）')
    ap.add_argument('--since', default='', help='起始日期 YYYY-MM-DD')
    ap.add_argument('--until', default='', help='结束日期 YYYY-MM-DD')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    def to_ts(text):
        if not text:
            return 0
        return int(datetime.datetime.strptime(text, '%Y-%m-%d').timestamp())

    try:
        since_ts = to_ts(args.since)
        # 只有给了 --until 才把它推到当天 23:59:59；不给就是"不设上限"，
        # 否则 0 + 86399 会把所有动态都过滤掉
        until_ts = to_ts(args.until) + 86399 if args.until else 0
        meta = export(args.out, args.limit, args.user, since_ts, until_ts)
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps({'success': False, 'code': 'SNS_EXPORT_FAILED',
                          'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])},
                         ensure_ascii=False))
        sys.exit(1)
    print(json.dumps({'success': True, 'path': str(pathlib.Path(args.out)),
                      'posts': meta['posts'], 'media': meta['media'],
                      'notes': len(meta['notes'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
