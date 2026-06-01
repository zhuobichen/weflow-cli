#!/usr/bin/env python3
"""
MCP Bridge — 为 MCP Server 提供微信数据库查询能力。

用法（由 MCP Server 通过 child_process 调用）:
  python scripts/mcp_bridge.py list_sessions [--limit 30]
  python scripts/mcp_bridge.py search_messages --keyword "遥感" --talker "张三" --days 30 --limit 20
  python scripts/mcp_bridge.py get_todos [--days 30]
  python scripts/mcp_bridge.py get_action_suggestions [--date 2026-05-15]
  python scripts/mcp_bridge.py get_chat_stats [--period week]

输出: JSON 格式
"""

import sys, os, json, hashlib, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print(json.dumps({"error": "请安装: pip install sqlcipher3"}))
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import load_config, decrypt_lock, parse_frontmatter

OUTPUT_ROOT = 'output'
TZ = timezone(timedelta(hours=8))


def json_output(data):
    """Output as JSON."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


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


def get_biz_keys(config):
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
        print('[ERROR] 缺少 biz_message_0.db 密钥，请运行: python scripts/nt_decrypt.py scan --json')
        sys.exit(1)
    return {
        'biz_db': biz_db, 'biz_key': biz_key, 'biz_salt': biz_salt,
        'contact_db': contact_db, 'contact_key': contact_key, 'contact_salt': contact_salt,
    }


def load_dbs():
    """Load all databases and return connections + metadata."""
    config = load_config()
    nt_db = config.get('ntDbPath', '')
    if not nt_db:
        return None, None, None, None, {"error": "未初始化，请先运行 weflow-cli init"}

    nt_key = decrypt_lock(config.get('ntKey', ''))
    nt_salt = config.get('ntSalt', '')
    keys = get_biz_keys(config)
    name_map = get_name_map(keys['contact_db'], keys['contact_key'], keys['contact_salt'])

    try:
        conn = open_db(nt_db, nt_key, nt_salt)
    except Exception as e:
        return None, None, None, None, {"error": f"数据库连接失败: {e}"}

    return conn, keys, name_map, nt_db, None


# ====== Commands ======

def cmd_list_sessions(args):
    """List recent chat sessions."""
    conn, keys, name_map, nt_db, err = load_dbs()
    if err:
        return err

    c = conn.cursor()
    limit = args.limit or 30
    now = datetime.now(TZ)
    start_ts = int((now - timedelta(days=90)).timestamp())
    end_ts = int(now.timestamp())

    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        sessions = [r[0] for r in c.fetchall()]
    except:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
        sessions = [r[0] for r in c.fetchall()]

    results = []
    for talker in sessions:
        tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        try:
            c.execute(f'SELECT COUNT(*), MAX(create_time) FROM "{tbl}" WHERE create_time >= ? AND create_time < ?', (start_ts, end_ts))
            count, last_ts = c.fetchone()
            if count > 0:
                name = name_map.get(talker, talker)
                last_time = datetime.fromtimestamp(last_ts, tz=TZ).strftime('%m-%d %H:%M')
                results.append({
                    "name": name,
                    "wxid": talker,
                    "count": count,
                    "last_active": last_time,
                })
        except:
            pass

    conn.close()
    results.sort(key=lambda x: x['count'], reverse=True)
    return {"sessions": results[:limit], "total": len(results)}


def cmd_search_messages(args):
    """Search messages by keyword."""
    conn, keys, name_map, nt_db, err = load_dbs()
    if err:
        return err

    keyword = args.keyword or ''
    talker_query = args.talker or ''
    days = args.days or 30
    limit = args.limit or 20

    now = datetime.now(TZ)
    start_ts = int((now - timedelta(days=days)).timestamp())
    end_ts = int(now.timestamp())

    c = conn.cursor()
    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        sessions = [r[0] for r in c.fetchall()]
    except:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
        sessions = [r[0] for r in c.fetchall()]

    # Filter by talker if specified
    if talker_query:
        filtered = []
        for s in sessions:
            name = name_map.get(s, s)
            if talker_query in name or talker_query in s:
                filtered.append(s)
        sessions = filtered
        if not sessions:
            conn.close()
            return {"results": [], "message": f"未找到联系人: {talker_query}"}

    results = []
    for talker in sessions[:50]:  # Limit scan scope
        tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        try:
            c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
            if c.fetchone()[0] == 0:
                continue

            c.execute(f'''
                SELECT create_time, real_sender_id, message_content
                FROM "{tbl}"
                WHERE create_time >= ? AND create_time < ?
                ORDER BY create_time DESC
            ''', (start_ts, end_ts))

            for ts, sender, content in c.fetchall():
                if not content or not isinstance(content, str):
                    continue
                if keyword and keyword.lower() not in content.lower():
                    continue
                sender_name = name_map.get(sender, sender) if sender else name_map.get(talker, talker)
                dt = datetime.fromtimestamp(ts, tz=TZ)
                results.append({
                    "talker": name_map.get(talker, talker),
                    "sender": sender_name,
                    "time": dt.strftime('%Y-%m-%d %H:%M'),
                    "content": content[:500],
                })
                if len(results) >= limit:
                    break
        except:
            pass
        if len(results) >= limit:
            break

    conn.close()
    results.sort(key=lambda x: x['time'], reverse=True)
    return {"results": results, "total": len(results), "keyword": keyword, "days": days}


def cmd_get_todos(args):
    """Get existing todo files."""
    days = args.days or 30
    todo_dir = Path(OUTPUT_ROOT)
    results = []

    # Find all 待办_*.md files
    for f in sorted(todo_dir.glob('待办_*.md'), reverse=True):
        content = f.read_text(encoding='utf-8')
        # Extract todo items
        items = []
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('- [ ]') or line.startswith('- [x]'):
                items.append(line)
        if items:
            results.append({
                "file": f.name,
                "items": items,
                "count": len(items),
            })

    if not results:
        return {"todos": [], "message": "暂无待办事项文件，请先运行 extract_todos.py"}

    return {"todos": results}


def cmd_get_action_suggestions(args):
    """Get action suggestion files."""
    date = args.date or ''
    daily_dir = Path(OUTPUT_ROOT) / 'biz-daily'

    if not daily_dir.exists():
        return {"suggestions": [], "message": "日报目录不存在"}

    if date:
        # Specific date
        action_file = daily_dir / date / '行动建议.md'
        if action_file.exists():
            content = action_file.read_text(encoding='utf-8')
            return {"date": date, "content": content}
        return {"suggestions": [], "message": f"未找到 {date} 的行动建议"}
    else:
        # Latest
        dates = sorted([d.name for d in daily_dir.iterdir() if d.is_dir()], reverse=True)
        for d in dates[:7]:  # Check last 7 days
            action_file = daily_dir / d / '行动建议.md'
            if action_file.exists():
                content = action_file.read_text(encoding='utf-8')
                return {"date": d, "content": content}
        return {"suggestions": [], "message": "最近 7 天无行动建议"}


def cmd_get_chat_stats(args):
    """Get chat statistics."""
    conn, keys, name_map, nt_db, err = load_dbs()
    if err:
        return err

    period = args.period or 'week'
    now = datetime.now(TZ)

    if period == 'week':
        days_since_monday = now.weekday()
        start_date = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    start_ts = int(start_date.timestamp())
    end_ts = int(now.timestamp())

    c = conn.cursor()
    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        sessions = [r[0] for r in c.fetchall()]
    except:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
        sessions = [r[0] for r in c.fetchall()]

    total_sent = 0
    total_recv = 0
    talker_counts = {}
    hour_dist = [0] * 24

    for talker in sessions:
        tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        try:
            c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
            if c.fetchone()[0] == 0:
                continue
            c.execute(f'SELECT create_time FROM "{tbl}" WHERE create_time >= ? AND create_time < ?', (start_ts, end_ts))
            count = 0
            for (ts,) in c.fetchall():
                count += 1
                dt = datetime.fromtimestamp(ts, tz=TZ)
                hour_dist[dt.hour] += 1
            if count > 0:
                name = name_map.get(talker, talker)
                talker_counts[name] = talker_counts.get(name, 0) + count
                total_recv += count  # Approximate
        except:
            pass

    conn.close()

    # Top talkers
    top_talkers = sorted(talker_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Most active hour
    most_active_hour = max(range(24), key=lambda h: hour_dist[h])

    return {
        "period": period,
        "start_date": start_date.strftime('%Y-%m-%d'),
        "end_date": now.strftime('%Y-%m-%d'),
        "total_messages": sum(talker_counts.values()),
        "active_contacts": len(talker_counts),
        "top_talkers": [{"name": n, "count": c} for n, c in top_talkers],
        "most_active_hour": f"{most_active_hour:02d}:00",
        "hour_distribution": {f"{h:02d}:00": hour_dist[h] for h in range(24)},
    }


# ====== Main ======

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    # list_sessions
    p = subparsers.add_parser('list_sessions')
    p.add_argument('--limit', type=int, default=30)

    # search_messages
    p = subparsers.add_parser('search_messages')
    p.add_argument('--keyword', default='')
    p.add_argument('--talker', default='')
    p.add_argument('--days', type=int, default=30)
    p.add_argument('--limit', type=int, default=20)

    # get_todos
    p = subparsers.add_parser('get_todos')
    p.add_argument('--days', type=int, default=30)

    # get_action_suggestions
    p = subparsers.add_parser('get_action_suggestions')
    p.add_argument('--date', default='')

    # get_chat_stats
    p = subparsers.add_parser('get_chat_stats')
    p.add_argument('--period', choices=['week', 'month'], default='week')

    args = parser.parse_args()

    handlers = {
        'list_sessions': cmd_list_sessions,
        'search_messages': cmd_search_messages,
        'get_todos': cmd_get_todos,
        'get_action_suggestions': cmd_get_action_suggestions,
        'get_chat_stats': cmd_get_chat_stats,
    }

    handler = handlers.get(args.command)
    if not handler:
        json_output({"error": f"未知命令: {args.command}", "available": list(handlers.keys())})
        sys.exit(1)

    result = handler(args)
    json_output(result)


if __name__ == '__main__':
    main()
