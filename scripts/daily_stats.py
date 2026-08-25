#!/usr/bin/env python3
"""公众号推送频率与日报处理频率统计。"""

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import decrypt_lock, load_config
from biz_daily import extract_article_info, get_db_keys

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print('请安装 sqlcipher3: pip install sqlcipher3', file=sys.stderr)
    sys.exit(1)


def load_processed_counts(start_date: str, end_date: str) -> dict[str, int]:
    root = Path(__file__).resolve().parent.parent / 'output' / 'biz-daily'
    counts: dict[str, int] = defaultdict(int)
    current = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    while current <= end:
        path = root / current.isoformat() / '.articles.json'
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                for article in payload.get('articles', []):
                    source = str(article.get('source') or '').strip()
                    if source:
                        counts[source] += 1
            except (OSError, ValueError, TypeError):
                pass
        current += timedelta(days=1)
    return dict(counts)


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='统计公众号推送与日报处理频率')
    parser.add_argument('--days', type=int, default=30, help='统计最近多少天，默认 30')
    parser.add_argument('--limit', type=int, default=30, help='最多显示多少个公众号，默认 30')
    args = parser.parse_args()
    if args.days < 1 or args.days > 3650:
        parser.error('--days 必须在 1 到 3650 之间')
    if args.limit < 1:
        parser.error('--limit 必须大于 0')

    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).date()
    start_date = today - timedelta(days=args.days - 1)
    start_ts = int(datetime.combine(start_date, datetime.min.time(), tzinfo=tz).timestamp())
    end_ts = int(datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=tz).timestamp())

    config = load_config()
    keys = get_db_keys(config)
    conn = sqlcipher.connect(keys['biz_db'])
    conn.execute(f'PRAGMA key = "x\'{keys["biz_key"]}{keys["biz_salt"]}\'";')
    cursor = conn.cursor()

    name_map: dict[str, str] = {}
    if keys['contact_key'] and os.path.exists(keys['contact_db']):
        try:
            contact = sqlcipher.connect(keys['contact_db'])
            contact.execute(f'PRAGMA key = "x\'{keys["contact_key"]}{keys["contact_salt"]}\'";')
            name_map = dict(contact.execute(
                "SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username) "
                "FROM contact WHERE username LIKE 'gh_%'"
            ).fetchall())
            contact.close()
        except Exception:
            pass

    rows: list[dict] = []
    users = [row[0] for row in cursor.execute(
        "SELECT user_name FROM Name2Id WHERE user_name LIKE 'gh_%'"
    ).fetchall()]
    for user in users:
        table = 'Msg_' + hashlib.md5(user.encode()).hexdigest()
        try:
            records = cursor.execute(
                f'SELECT create_time, message_content FROM "{table}" '
                'WHERE create_time >= ? AND create_time < ? ORDER BY create_time',
                (start_ts, end_ts),
            ).fetchall()
        except Exception:
            continue
        article_count = sum(
            1 for _, content in records
            if content and extract_article_info(content).get('title')
        )
        if article_count:
            rows.append({'name': str(name_map.get(user, user)), 'id': user, 'pushed': article_count})
    conn.close()

    processed = load_processed_counts(start_date.isoformat(), today.isoformat())
    for row in rows:
        row['processed'] = processed.get(row['name'], 0)
    rows.sort(key=lambda row: (-row['processed'], -row['pushed'], row['name']))

    print(f'公众号频率统计（{start_date.isoformat()} 至 {today.isoformat()}）')
    print('说明：推送数是数据库中的文章数；处理数是已生成日报的文章数，不等同于打开次数。')
    print(f'共统计 {len(rows)} 个有推送记录的公众号\n')
    print(f'{"排名":<5}{"公众号":<28}{"推送数":>8}{"处理数":>8}{"ID"}')
    print('-' * 72)
    for index, row in enumerate(rows[:args.limit], 1):
        print(f'{index:<5}{row["name"][:26]:<28}{row["pushed"]:>8}{row["processed"]:>8}  {row["id"]}')


if __name__ == '__main__':
    main()
