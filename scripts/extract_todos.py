#!/usr/bin/env python3
"""
微信对话待办提取器 — 从聊天记录中用 AI 提取待办事项、承诺和截止日期。

用法:
  python scripts/extract_todos.py --talker "张三"
  python scripts/extract_todos.py --talker "张三" --days 30
  python scripts/extract_todos.py --talker "张三" --days 7 --output output/待办_张三.md

输出: output/待办_<联系人>.md
"""

import sys, os, json, re, hashlib, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print("请安装: pip install sqlcipher3"); sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import load_config, decrypt_lock, get_db_config, call_deepseek, extract_todos_from_messages

OUTPUT_ROOT = 'output'


# ====== Database (reuse from chat_report.py) ======

def open_db(db_path, key_hex, salt_hex):
    raw_key = f"x'{key_hex}{salt_hex}'"
    conn = sqlcipher.connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    c.execute("SELECT count(*) FROM sqlite_master")
    return conn


def get_sessions(conn):
    c = conn.cursor()
    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        return [{'username': r[0]} for r in c.fetchall()]
    except:
        pass
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
    return [{'username': t, 'table': t} for t in [r[0] for r in c.fetchall()]]


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


def get_messages(conn, talker, start_ts, end_ts, name_map):
    """Get text messages for a talker within time range."""
    tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
    c = conn.cursor()
    try:
        c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
        if c.fetchone()[0] == 0:
            return []
    except:
        return []

    rows = []
    try:
        c.execute(f'''
            SELECT create_time, real_sender_id, local_type, message_content
            FROM "{tbl}"
            WHERE create_time >= ? AND create_time < ?
            ORDER BY create_time
        ''', (start_ts, end_ts))
        for r in c.fetchall():
            ts, sender_id, msg_type, content = r
            # 只取文本消息 (type=1)
            # Ensure content is str (might be bytes in some DBs)
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='ignore')
            if msg_type == 1 and content and content.strip():
                # Resolve sender via Name2Id → username → name_map
                c2 = conn.cursor()
                c2.execute("SELECT user_name FROM Name2Id WHERE rowid = ?", (sender_id,))
                row = c2.fetchone()
                sender_raw = row[0] if row else str(sender_id)
                sender_name = name_map.get(sender_raw, sender_raw)
                dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
                rows.append({
                    'timestamp': dt.strftime('%m-%d %H:%M'),
                    'sender': sender_name,
                    'content': content.strip(),
                })
    except Exception as e:
        print(f'  [WARN] 读取消息失败: {e}')

    return rows


def find_talker(name_query, sessions, name_map):
    """Fuzzy match talker by name or wxid."""
    name_query = name_query.strip()
    # 精确匹配 wxid
    for s in sessions:
        if s['username'] == name_query:
            return s['username']
    # 模糊匹配昵称/备注
    for wxid, name in name_map.items():
        if name_query in name or name_query in wxid:
            return wxid
    # 模糊匹配 wxid
    for s in sessions:
        if name_query in s['username']:
            return s['username']
    return None


def format_output(todos, talker_name, days):
    """Format todos into markdown."""
    lines = [
        f'# 待办事项 — {talker_name}',
        f'> 最近 {days} 天聊天记录提取 | 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}',
        '',
    ]

    if not todos:
        lines.append('暂无待办事项 ✅')
        return '\n'.join(lines)

    # 按截止日期分组
    has_deadline = [t for t in todos if t['deadline'] != '无期限']
    no_deadline = [t for t in todos if t['deadline'] == '无期限']

    # 排序：有期限的按日期排
    has_deadline.sort(key=lambda x: x['deadline'])

    if has_deadline:
        lines.append('## ⏰ 有截止日期')
        lines.append('')
        for t in has_deadline:
            source = f'（来源：{t["source"]}）' if t['source'] else ''
            lines.append(f'- [ ] **【{t["deadline"]}】** {t["task"]} {source}')
        lines.append('')

    if no_deadline:
        lines.append('## 📌 无期限')
        lines.append('')
        for t in no_deadline:
            source = f'（来源：{t["source"]}）' if t['source'] else ''
            lines.append(f'- [ ] {t["task"]} {source}')
        lines.append('')

    # 统计
    lines.append('---')
    lines.append(f'共 {len(todos)} 项待办 | {len(has_deadline)} 项有截止日期 | {len(no_deadline)} 项无期限')

    return '\n'.join(lines)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='从聊天记录中提取待办事项')
    parser.add_argument('--talker', required=True, help='联系人名称或 wxid')
    parser.add_argument('--days', type=int, default=30, help='扫描最近多少天的消息（默认 30）')
    parser.add_argument('--api-key', help='DeepSeek API key')
    parser.add_argument('--output', help='输出文件路径（默认 output/待办_<联系人>.md）')
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print('[ERROR] 需要 DeepSeek API key（--api-key 或 DEEPSEEK_API_KEY 环境变量）')
        sys.exit(1)

    # 时间范围
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    start_ts = int((now - timedelta(days=args.days)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    end_ts = int(now.timestamp())

    print(f'=== 对话待办提取器 ===')
    print(f'联系人: {args.talker}')
    print(f'时间范围: 最近 {args.days} 天 ({now - timedelta(days=args.days):%Y-%m-%d} ~ {now:%Y-%m-%d})')

    # 加载数据库配置
    db_cfg = get_db_config()
    if not db_cfg['nt_db']:
        print('[ERROR] 未找到数据库路径，请先运行 weflow-cli init')
        sys.exit(1)

    # 打开数据库
    try:
        conn = open_db(db_cfg['nt_db'], db_cfg['nt_key'], db_cfg['nt_salt'])
    except Exception as e:
        print(f'[ERROR] 打开数据库失败: {e}')
        sys.exit(1)

    # 获取会话列表和名称映射
    sessions = get_sessions(conn)
    name_map = get_name_map(db_cfg['contact_db'], db_cfg['contact_key'], db_cfg['contact_salt'])
    print(f'会话数: {len(sessions)} | 联系人映射: {len(name_map)}')

    # 查找联系人
    talker_wxid = find_talker(args.talker, sessions, name_map)
    if not talker_wxid:
        print(f'[ERROR] 未找到联系人: {args.talker}')
        print('提示：可以使用 wxid 或昵称/备注名模糊匹配')
        # 列出部分联系人供参考
        print('\n部分联系人：')
        for wxid, name in list(name_map.items())[:20]:
            print(f'  {name} ({wxid})')
        conn.close()
        sys.exit(1)

    talker_name = name_map.get(talker_wxid, talker_wxid)
    print(f'匹配到: {talker_name} ({talker_wxid})')

    # 读取消息
    print(f'\n读取消息...')
    messages = get_messages(conn, talker_wxid, start_ts, end_ts, name_map)
    conn.close()
    print(f'共 {len(messages)} 条文本消息')

    if len(messages) == 0:
        print('没有消息，退出。')
        sys.exit(0)

    # AI 提取待办
    print(f'\nAI 提取待办事项...')
    todos = extract_todos_from_messages(messages, api_key)
    print(f'提取到 {len(todos)} 项待办')

    # 输出
    output_path = args.output or os.path.join(OUTPUT_ROOT, f'待办_{talker_name}.md')
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    content = format_output(todos, talker_name, args.days)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f'\n✓ 已保存到: {output_path}')
    print(content)


if __name__ == '__main__':
    main()
