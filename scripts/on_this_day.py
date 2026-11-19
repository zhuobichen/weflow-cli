"""「那年今日」：把几年前的今天你在聊什么捞出来。

为什么只有本地全量数据能做到：微信的搜索只覆盖当前会话、而且历史一长就翻不动；
这里直接按 `create_time` 扫所有分片里的每一张会话表，所以"五年前的今天"是能查的。

用法：
    python scripts/on_this_day.py                     # 1 / 3 / 5 年前的今天
    python scripts/on_this_day.py --years 2,4,6
    python scripts/on_this_day.py --talker 老表亲戚群   # 只看某个会话
    python scripts/on_this_day.py --json
"""
from __future__ import annotations

import argparse
import re
import json
import os
import pathlib
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from _utils import load_config, decrypt_lock  # noqa: E402
import nt_decrypt as nd  # noqa: E402

TZ = timezone(timedelta(hours=8))
DEFAULT_YEARS = (1, 3, 5)


def _keys(cfg):
    key = decrypt_lock(cfg.get('ntKey', '') or '')
    salt = decrypt_lock(cfg.get('ntSalt', '') or '')
    pw = decrypt_lock(cfg.get('favPassphrase', '') or cfg.get('decryptKey', '') or '')
    return key, salt, pw


def _text_of(content, local_type=0):
    """消息正文 → 一行字。

    **不能只看 `message_content` 是不是以 `<` 开头**：appmsg 那一族（链接/文件/小程序）
    的内容是 zstd 压缩的二进制，原样打出来就是 `(/`$` 这种碎片（第一版就是这么翻车的）。
    这里直接复用 `nt_decrypt` 里那套已经处理过压缩、类型标签与引用消息的显示层。
    """
    lt = int(local_type or 0)
    base = lt & 0xFFFFFFFF
    if base == 1:                      # 纯文本
        text = nd._decode_content(content)
        text = ' '.join(str(text).split())[:120]
        # 群消息的 `message_content` 自带 `wxid_xxx: ` 前缀，而发言人已经在左边显示了一次
        return re.sub(r'^[A-Za-z0-9_\-]+@?(chatroom|openim)?\s*:\s*', '', text)[:120]
    try:
        out = nd.non_text_display(lt, content)[:120]
        return re.sub(r'^[A-Za-z0-9_\-]+:\s*', '', out)[:120]
    except Exception:
        return ''


def day_bounds(dt):
    start = datetime(dt.year, dt.month, dt.day, tzinfo=TZ)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def collect_for_day(conns, name_map, target_day, talker_filter=''):
    """{talker: [(ts, sender, text), ...]} for one calendar day."""
    start, end = day_bounds(target_day)
    out = defaultdict(list)
    for conn in conns:
        c = conn.cursor()
        try:
            c.execute('SELECT user_name FROM Name2Id WHERE is_session = 1')
            sessions = [r[0] for r in c.fetchall() if r[0]]
        except Exception:
            continue
        # `real_sender_id` 是 **Name2Id 的 rowid**，不是 wxid —— 少这一步映射，
        # 输出里发言人就全是 `65`、`4`、`8` 这样的数字（第一版就是这样）。
        try:
            c.execute('SELECT rowid, user_name FROM Name2Id')
            sender_map = {r[0]: r[1] for r in c.fetchall()}
        except Exception:
            sender_map = {}
        for talker in sessions:
            if talker_filter and talker_filter not in (talker, name_map.get(talker, talker)):
                continue
            tbl = f'Msg_{__import__("hashlib").md5(talker.encode()).hexdigest()}'
            try:
                c.execute('SELECT COUNT(*) FROM sqlite_master WHERE name=?', (tbl,))
                if c.fetchone()[0] == 0:
                    continue
                c.execute(f'SELECT create_time, real_sender_id, local_type, message_content '
                          'FROM "{0}" WHERE create_time >= ? AND create_time < ? ORDER BY create_time'.format(tbl),
                          (start, end))
                for ts, sender, ltype, content in c.fetchall():
                    text = _text_of(content, ltype)
                    if text:
                        who = sender_map.get(sender, '')
                        out[name_map.get(talker, talker)].append(
                            (int(ts), name_map.get(who, who), text))
            except Exception:
                continue
    return out


def main():
    ap = argparse.ArgumentParser(description='那几年的今天你在聊什么')
    ap.add_argument('--years', default=','.join(str(y) for y in DEFAULT_YEARS),
                    help='往前看几年，逗号分隔（默认 1,3,5）')
    ap.add_argument('--talker', default=os.environ.get('WEFLOW_TALKER', ''), help='只看某个会话（wxid 或昵称）')
    ap.add_argument('--limit', type=int, default=6, help='每天最多列几条（默认 6）')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    cfg = load_config()
    db = cfg.get('ntDbPath') or ''
    if not db:
        raise SystemExit('配置里没有 ntDbPath，先跑 weflow-cli init')
    key, salt, pw = _keys(cfg)
    conns = nd.connect_message_shards(db, key, salt, pw)
    if not conns:
        raise SystemExit('打不开任何消息分片')

    try:
        # 联系人在 **contact.db**，不在消息库里 —— 从消息库找 `contact` 表一定落空，
        # 输出里就只剩 `47966561144@chatroom` 和 `wxid_xxx`（第一版就是这样）。
        name_map = {}
        try:
            from network import load_graph
            people, _rooms, _members = load_graph(cfg)
            name_map = {p['username']: (p['remark'] or p['nick'] or p['username'])
                        for p in people.values()}
        except Exception as exc:
            print(f'[WARN] 读联系人失败（{type(exc).__name__}: {exc}），下面显示 id',
                  file=sys.stderr)

        today = datetime.now(TZ)
        years = [int(y) for y in args.years.split(',') if y.strip().isdigit()]
        result = []
        for y in years:
            try:
                day = today.replace(year=today.year - y)
            except ValueError:      # 2 月 29 日
                day = today.replace(year=today.year - y, day=28)
            by_talker = collect_for_day(conns, name_map, day, args.talker)
            total = sum(len(v) for v in by_talker.values())
            top = Counter({k: len(v) for k, v in by_talker.items()}).most_common(3)
            picks = []
            for talker, msgs in sorted(by_talker.items(), key=lambda kv: -len(kv[1]))[:3]:
                for ts, sender, text in msgs[:max(1, args.limit // 3)]:
                    picks.append({'talker': talker, 'time': datetime.fromtimestamp(ts, tz=TZ).strftime('%H:%M'),
                                  'sender': sender, 'text': text})
            result.append({'date': day.strftime('%Y-%m-%d'), 'yearsAgo': y,
                           'total': total, 'threads': top, 'samples': picks[:args.limit]})
    finally:
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass

    if args.json:
        print(json.dumps({'success': True, 'days': result}, ensure_ascii=False, indent=2))
        return

    for item in result:
        head = f"{item['date']}（{item['yearsAgo']} 年前的今天）"
        if not item['total']:
            print(f'\n{head}\n  那天没有聊天记录')
            continue
        threads = '、'.join(f'{n}({c})' for n, c in item['threads'])
        print(f"\n{head}  {item['total']} 条  {threads}")
        for s in item['samples']:
            print(f"  {s['time']}  {s['talker']} · {s['sender'] or '?'}：{s['text']}")


if __name__ == '__main__':
    main()
