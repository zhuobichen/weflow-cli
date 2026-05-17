#!/usr/bin/env python3
"""
微信个人信息消费报告 — 统计消息量、聊天对象、公众号阅读偏好、活跃时段。

用法:
  python scripts/chat_stats.py --period week
  python scripts/chat_stats.py --period month
  python scripts/chat_stats.py --period month --output output/报告.md

输出: output/微信报告_<周期>.md
"""

import sys, os, json, re, hashlib, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter, defaultdict

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print("请安装: pip install sqlcipher3"); sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import load_config, decrypt_lock, parse_frontmatter

OUTPUT_ROOT = 'output'


# ====== Database ======

def open_db(db_path, key_hex, salt_hex):
    raw_key = f"x'{key_hex}{salt_hex}'"
    conn = sqlcipher.connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    c.execute("SELECT count(*) FROM sqlite_master")
    return conn


def get_name_map(contact_db, contact_key, contact_salt):
    name_map = {}
    if not contact_db or not contact_key or not os.path.exists(contact_db):
        return name_map
    try:
        conn = open_db(contact_db, contact_key, contact_salt)
        c = conn.cursor()
        c.execute("SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username) FROM contact")
        for r in c.fetchall():
            name_map[r[0]] = r[1]
        conn.close()
    except:
        pass
    return name_map


def get_own_wxid(nt_db, nt_key, nt_salt):
    """Get own wxid from UserInfo table."""
    try:
        conn = open_db(nt_db, nt_key, nt_salt)
        c = conn.cursor()
        c.execute("SELECT user_name FROM UserInfo LIMIT 1")
        row = c.fetchone()
        conn.close()
        return row[0] if row else ''
    except:
        return ''


def get_biz_keys(config):
    """Get biz database keys."""
    nt_db = config.get('ntDbPath', '')
    msg_dir = os.path.dirname(nt_db.replace('\\', '/'))
    wxid_dir = os.path.dirname(os.path.dirname(msg_dir))
    contact_db = os.path.join(wxid_dir, 'db_storage', 'contact', 'contact.db')

    contact_key_enc = config.get('contactKey', '')
    contact_salt = config.get('contactSalt', '')
    contact_key = decrypt_lock(contact_key_enc) if contact_key_enc else ''

    biz_db = os.path.join(msg_dir, 'biz_message_0.db')
    biz_key_enc = config.get('bizKey', '')
    biz_salt = config.get('bizSalt', '')
    if biz_key_enc and biz_salt:
        biz_key = decrypt_lock(biz_key_enc)
    else:
        biz_key = '1c0451540217eb0373eb9242f61c173f8f7b3a4f29e922a9623ed59a3f90630e'
        biz_salt = '0478298c58563d07e3fa53b45f13d593'

    return {
        'biz_db': biz_db, 'biz_key': biz_key, 'biz_salt': biz_salt,
        'contact_db': contact_db, 'contact_key': contact_key, 'contact_salt': contact_salt,
    }


# ====== Stats Collection ======

def collect_chat_stats(conn, start_ts, end_ts, name_map, own_wxid):
    """Collect chat message statistics."""
    c = conn.cursor()

    # Get all sessions
    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        sessions = [r[0] for r in c.fetchall()]
    except:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
        sessions = [r[0] for r in c.fetchall()]

    total_sent = 0
    total_recv = 0
    talker_stats = Counter()  # talker -> total messages
    hour_dist = Counter()  # hour -> message count
    day_dist = Counter()  # weekday -> message count

    for talker in sessions:
        tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        try:
            c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
            if c.fetchone()[0] == 0:
                continue

            c.execute(f'''
                SELECT create_time, real_sender_id
                FROM "{tbl}"
                WHERE create_time >= ? AND create_time < ?
            ''', (start_ts, end_ts))

            count = 0
            for ts, sender in c.fetchall():
                count += 1
                dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
                hour_dist[dt.hour] += 1
                day_dist[dt.weekday()] += 1

                is_self = False
                if own_wxid:
                    username = sender if sender else talker
                    is_self = (username == own_wxid)
                    if not is_self:
                        p = own_wxid.rsplit('_', 1)
                        if len(p) == 2 and len(p[1]) == 4 and p[1].isalnum():
                            is_self = (username == p[0])

                if is_self:
                    total_sent += 1
                else:
                    total_recv += 1

            if count > 0:
                name = name_map.get(talker, talker)
                talker_stats[name] += count
        except:
            pass

    return {
        'total_sent': total_sent,
        'total_recv': total_recv,
        'talker_stats': talker_stats,
        'hour_dist': hour_dist,
        'day_dist': day_dist,
    }


def collect_biz_stats(biz_db, biz_key, biz_salt, start_ts, end_ts, name_map):
    """Collect public account article statistics."""
    try:
        conn = open_db(biz_db, biz_key, biz_salt)
    except:
        return {'total_articles': 0, 'account_stats': Counter(), 'topic_stats': Counter()}

    c = conn.cursor()
    c.execute("SELECT user_name FROM Name2Id WHERE user_name LIKE 'gh_%'")
    biz_users = [r[0] for r in c.fetchall()]

    total_articles = 0
    account_stats = Counter()
    hour_dist = Counter()

    for user in biz_users:
        tbl = 'Msg_' + hashlib.md5(user.encode()).hexdigest()
        try:
            c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
            if c.fetchone()[0] == 0:
                continue

            c.execute(f'''
                SELECT create_time FROM "{tbl}"
                WHERE create_time >= ? AND create_time < ?
            ''', (start_ts, end_ts))

            count = 0
            for (ts,) in c.fetchall():
                count += 1
                dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
                hour_dist[dt.hour] += 1

            if count > 0:
                name = name_map.get(user, user)
                account_stats[name] += count
                total_articles += count
        except:
            pass

    conn.close()

    # Read topic stats from existing daily reports
    topic_stats = Counter()
    daily_dir = Path(OUTPUT_ROOT) / 'biz-daily'
    if daily_dir.exists():
        for d in sorted(daily_dir.iterdir()):
            if not d.is_dir():
                continue
            try:
                dt = datetime.strptime(d.name, '%Y-%m-%d')
                dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                if dt.timestamp() < start_ts or dt.timestamp() >= end_ts:
                    continue
            except:
                continue

            readme = d / 'README.md'
            if readme.exists():
                content = readme.read_text(encoding='utf-8')
                # Extract topic counts from README
                for topic in ['AI', '学术', '新闻', '文学', '投资']:
                    match = re.search(rf'{topic}/:\s*(\d+)', content)
                    if match:
                        topic_stats[topic] += int(match.group(1))

    return {
        'total_articles': total_articles,
        'account_stats': account_stats,
        'topic_stats': topic_stats,
    }


# ====== Report Generation ======

WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


def format_report(chat_stats, biz_stats, period_label, start_date, end_date):
    """Generate markdown report."""
    lines = [
        f'# 📊 微信个人信息消费报告',
        f'> {period_label} | {start_date} ~ {end_date}',
        '',
    ]

    # === Overview ===
    total_msgs = chat_stats['total_sent'] + chat_stats['total_recv']
    lines.append('## 📈 数据概览')
    lines.append('')
    lines.append(f'| 指标 | 数量 |')
    lines.append(f'|------|------|')
    lines.append(f'| 收到消息 | {chat_stats["total_recv"]:,} 条 |')
    lines.append(f'| 发送消息 | {chat_stats["total_sent"]:,} 条 |')
    lines.append(f'| 总消息量 | {total_msgs:,} 条 |')
    lines.append(f'| 公众号文章 | {biz_stats["total_articles"]:,} 篇 |')
    lines.append(f'| 活跃联系人 | {len(chat_stats["talker_stats"])} 个 |')
    lines.append(f'| 活跃公众号 | {len(biz_stats["account_stats"])} 个 |')
    lines.append('')

    # === Top Talkers ===
    lines.append('## 💬 Top 10 聊天对象')
    lines.append('')
    lines.append('| 排名 | 联系人 | 消息数 | 占比 |')
    lines.append('|------|--------|--------|------|')
    top_talkers = chat_stats['talker_stats'].most_common(10)
    for i, (name, count) in enumerate(top_talkers, 1):
        pct = f"{count / total_msgs * 100:.1f}%" if total_msgs > 0 else '0%'
        lines.append(f'| {i} | {name} | {count:,} | {pct} |')
    lines.append('')

    # === Reading Preferences ===
    if biz_stats['topic_stats']:
        lines.append('## 📰 阅读偏好')
        lines.append('')
        total_topics = sum(biz_stats['topic_stats'].values())
        lines.append('| 主题 | 篇数 | 占比 |')
        lines.append('|------|------|------|')
        topic_emoji = {'AI': '🤖', '学术': '🔬', '新闻': '📰', '文学': '📖', '投资': '💰'}
        for topic, count in biz_stats['topic_stats'].most_common():
            pct = f"{count / total_topics * 100:.0f}%" if total_topics > 0 else '0%'
            emoji = topic_emoji.get(topic, '📌')
            lines.append(f'| {emoji} {topic} | {count} | {pct} |')
        lines.append('')

    # === Top Public Accounts ===
    if biz_stats['account_stats']:
        lines.append('## 📱 Top 10 公众号')
        lines.append('')
        lines.append('| 排名 | 公众号 | 文章数 |')
        lines.append('|------|--------|--------|')
        for i, (name, count) in enumerate(biz_stats['account_stats'].most_common(10), 1):
            lines.append(f'| {i} | {name} | {count} |')
        lines.append('')

    # === Activity Hours ===
    lines.append('## ⏰ 活跃时段分布')
    lines.append('')
    lines.append('| 时段 | 消息数 | 热度 |')
    lines.append('|------|--------|------|')
    max_hour = max(chat_stats['hour_dist'].values()) if chat_stats['hour_dist'] else 1
    for h in range(24):
        count = chat_stats['hour_dist'].get(h, 0)
        bar_len = int(count / max_hour * 10) if max_hour > 0 else 0
        bar = '█' * bar_len + '░' * (10 - bar_len)
        label = f'{h:02d}:00-{h:02d}:59'
        lines.append(f'| {label} | {count:,} | {bar} |')
    lines.append('')

    # Most/least active hours
    if chat_stats['hour_dist']:
        most_active_h = chat_stats['hour_dist'].most_common(1)[0]
        least_active_h = min(chat_stats['hour_dist'].items(), key=lambda x: x[1])
        lines.append(f'- 🔥 最活跃：**{most_active_h[0]:02d}:00-{most_active_h[0]:02d}:59**（{most_active_h[1]:,} 条）')
        lines.append(f'- 😴 最安静：**{least_active_h[0]:02d}:00-{least_active_h[0]:02d}:59**（{least_active_h[1]:,} 条）')
    lines.append('')

    # === Weekday Distribution ===
    lines.append('## 📅 星期分布')
    lines.append('')
    lines.append('| 星期 | 消息数 | 热度 |')
    lines.append('|------|--------|------|')
    max_day = max(chat_stats['day_dist'].values()) if chat_stats['day_dist'] else 1
    for d in range(7):
        count = chat_stats['day_dist'].get(d, 0)
        bar_len = int(count / max_day * 10) if max_day > 0 else 0
        bar = '█' * bar_len + '░' * (10 - bar_len)
        lines.append(f'| {WEEKDAY_NAMES[d]} | {count:,} | {bar} |')
    lines.append('')

    # === Fun Facts ===
    lines.append('## 💡 趣味数据')
    lines.append('')
    from datetime import datetime
    d1 = datetime.strptime(start_date, '%Y-%m-%d') if isinstance(start_date, str) else start_date
    d2 = datetime.strptime(end_date, '%Y-%m-%d') if isinstance(end_date, str) else end_date
    avg_per_day = total_msgs / max(1, (d2 - d1).days)
    lines.append(f'- 📊 日均消息量：**{avg_per_day:.0f} 条**')
    if chat_stats['total_sent'] > 0 and chat_stats['total_recv'] > 0:
        ratio = chat_stats['total_sent'] / chat_stats['total_recv']
        if ratio > 1.2:
            lines.append(f'- 🗣️ 你是个**话痨**，发送/接收比 = {ratio:.2f}')
        elif ratio < 0.8:
            lines.append(f'- 👂 你是个**倾听者**，发送/接收比 = {ratio:.2f}')
        else:
            lines.append(f'- 🤝 沟通均衡，发送/接收比 = {ratio:.2f}')
    if biz_stats['topic_stats']:
        top_topic = biz_stats['topic_stats'].most_common(1)[0]
        lines.append(f'- ❤️ 最爱看：**{top_topic[0]}**类文章（{top_topic[1]} 篇）')
    if top_talkers:
        lines.append(f'- 🏆 最常聊天：**{top_talkers[0][0]}**（{top_talkers[0][1]:,} 条）')
    lines.append('')

    return '\n'.join(lines)


# ====== Main ======

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='微信个人信息消费报告')
    parser.add_argument('--period', choices=['week', 'month'], default='week', help='报告周期（默认 week）')
    parser.add_argument('--output', help='输出文件路径')
    args = parser.parse_args()

    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)

    if args.period == 'week':
        # 本周（周一到今天）
        days_since_monday = now.weekday()
        start_date = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        period_label = f'周报 — {start_date.strftime("%Y-%m-%d")} ~ {now.strftime("%Y-%m-%d")}'
    else:
        # 本月
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_label = f'月报 — {now.strftime("%Y-%m")}'

    start_ts = int(start_date.timestamp())
    end_ts = int(now.timestamp())

    print(f'=== 微信个人信息消费报告 ===')
    print(f'周期: {period_label}')

    # Load config
    config = load_config()
    nt_db = config.get('ntDbPath', '')
    if not nt_db:
        print('[ERROR] 未找到数据库路径，请先运行 weflow-cli init')
        sys.exit(1)

    nt_key = decrypt_lock(config.get('ntKey', ''))
    nt_salt = config.get('ntSalt', '')

    # Get name map and own wxid
    keys = get_biz_keys(config)
    name_map = get_name_map(keys['contact_db'], keys['contact_key'], keys['contact_salt'])
    own_wxid = get_own_wxid(nt_db, nt_key, nt_salt)
    print(f'联系人映射: {len(name_map)} | 自己: {own_wxid[:20]}...')

    # Collect chat stats
    print(f'\n统计聊天消息...')
    try:
        conn = open_db(nt_db, nt_key, nt_salt)
    except Exception as e:
        print(f'[ERROR] 打开数据库失败: {e}')
        sys.exit(1)

    chat_stats = collect_chat_stats(conn, start_ts, end_ts, name_map, own_wxid)
    conn.close()
    print(f'  消息: 发送 {chat_stats["total_sent"]:,} / 接收 {chat_stats["total_recv"]:,}')
    print(f'  联系人: {len(chat_stats["talker_stats"])} 个')

    # Collect biz stats
    print(f'\n统计公众号文章...')
    biz_stats = collect_biz_stats(
        keys['biz_db'], keys['biz_key'], keys['biz_salt'],
        start_ts, end_ts, name_map
    )
    print(f'  文章: {biz_stats["total_articles"]:,} 篇')
    print(f'  公众号: {len(biz_stats["account_stats"])} 个')

    # Generate report
    print(f'\n生成报告...')
    report = format_report(
        chat_stats, biz_stats, period_label,
        start_date.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d')
    )

    # Save
    output_path = args.output or os.path.join(OUTPUT_ROOT, f'微信报告_{args.period}.md')
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f'\n✓ 已保存到: {output_path}')
    print(report[:500] + '...')


if __name__ == '__main__':
    main()
