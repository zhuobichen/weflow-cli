#!/usr/bin/env python3
"""
微信对话待办提取 & 任务追踪 — AI 识别承诺 + JSON 持久化。

用法:
  python scripts/extract_todos.py extract [--days 7]
  python scripts/extract_todos.py list [--status pending|done] [--urgency high|mid|low]
  python scripts/extract_todos.py done <id>
  python scripts/extract_todos.py undone <id>
  python scripts/extract_todos.py rm <id>
  python scripts/extract_todos.py remind

数据: ~/.weflow-cli/todos.json
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
from _utils import load_config, decrypt_lock, call_deepseek

TODOS_FILE = os.path.join(os.path.expanduser('~'), '.weflow-cli', 'todos.json')
TZ = timezone(timedelta(hours=8))

TODO_EXTRACT_PROMPT = """你是一个任务提取助手。分析微信聊天记录，提取其中出现的承诺、待办、约定事项。

要求：
1. 提取任何人的承诺或待办（如"我明天做""我处理""尽快发"等）
2. 每项返回 JSON: {{"task": "事项描述", "urgency": "高/中/低", "deadline": "截止描述或未提及"}}
3. 不要提取闲聊、问候等非任务内容
4. 如果没有待办，返回空数组 []

聊天记录：
{messages}

请只返回 JSON 数组，不要其他内容。"""


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


def collect_recent_messages(conn, start_ts, end_ts, name_map, limit_per_session=50):
    """收集近期待办相关消息：扫描所有会话，过滤含承诺/任务关键词的消息。"""
    c = conn.cursor()
    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        sessions = [r[0] for r in c.fetchall()]
    except:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
        sessions = [r[0].replace('Msg_', '') for r in c.fetchall()]

    # 关键词预筛选：承诺、任务、约定类词汇
    keywords = ['明天', '下次', '等会', '一会', '尽快', '稍后', '待会', '改天',
                '我弄', '我做', '我来', '我写', '我发', '我处理', '我整', '我搞',
                '我看看', '我去', '我找', '我帮忙', '我帮', '好的我', '行我',
                '没问题', 'ok', 'OK', '可以我', '收到我']

    all_msgs = []
    seen = set()

    for talker in sessions[:80]:
        tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        try:
            c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
            if c.fetchone()[0] == 0:
                continue

            c.execute(f'''
                SELECT create_time, real_sender_id, message_content
                FROM "{tbl}"
                WHERE create_time >= ? AND create_time < ?
                  AND message_content IS NOT NULL
                ORDER BY create_time DESC
                LIMIT ?
            ''', (start_ts, end_ts, limit_per_session))

            for ts, sender_id, content in c.fetchall():
                if not content or not isinstance(content, str) or len(content) < 5:
                    continue
                # 关键词过滤
                content_lower = content.lower()
                if not any(kw.lower() in content_lower for kw in keywords):
                    continue
                # 去重
                msg_hash = hashlib.md5(f"{talker}:{ts}:{content[:50]}".encode()).hexdigest()
                if msg_hash in seen:
                    continue
                seen.add(msg_hash)

                # 获取发送者名称
                try:
                    c2 = conn.cursor()
                    c2.execute("SELECT user_name FROM Name2Id WHERE rowid = ?", (sender_id,))
                    row = c2.fetchone()
                    sender_raw = row[0] if row else str(sender_id)
                except:
                    sender_raw = str(sender_id)
                sender_name = name_map.get(sender_raw, sender_raw)
                talker_name = name_map.get(talker, talker)

                dt = datetime.fromtimestamp(ts, tz=TZ)
                all_msgs.append({
                    'time': dt.strftime('%m-%d %H:%M'),
                    'talker': talker_name,
                    'sender': sender_name,
                    'content': content[:300],
                })
        except:
            pass

    all_msgs.sort(key=lambda x: x['time'])
    return all_msgs


# ====== Todo CRUD ======

def load_todos() -> list[dict]:
    if os.path.exists(TODOS_FILE):
        try:
            return json.loads(open(TODOS_FILE, encoding='utf-8').read())
        except:
            return []
    return []


def save_todos(todos: list[dict]):
    os.makedirs(os.path.dirname(TODOS_FILE), exist_ok=True)
    with open(TODOS_FILE, 'w', encoding='utf-8') as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)


def extract_todos(api_key: str, days: int = 7):
    """扫描最近的聊天记录，AI 提取待办。"""
    config = load_config()
    nt_db = config.get('ntDbPath', '')
    if not nt_db:
        return {"error": "未初始化，请先运行 weflow-cli init"}

    nt_key = decrypt_lock(config.get('ntKey', ''))
    nt_salt = config.get('ntSalt', '')
    contact_key = decrypt_lock(config.get('contactKey', ''))
    contact_salt = config.get('contactSalt', '')
    msg_dir = os.path.dirname(nt_db.replace('\\', '/'))
    wxid_dir = os.path.dirname(os.path.dirname(msg_dir))
    contact_db = os.path.join(wxid_dir, 'db_storage', 'contact', 'contact.db')

    name_map = get_name_map(contact_db, contact_key, contact_salt)
    now = datetime.now(TZ)
    start_ts = int((now - timedelta(days=days)).timestamp())
    end_ts = int(now.timestamp())

    conn = open_db(nt_db, nt_key, nt_salt)
    messages = collect_recent_messages(conn, start_ts, end_ts, name_map)
    conn.close()

    if not messages:
        return {"new": 0, "total": len(load_todos()), "message": "近 {days} 天没有发现相关消息"}

    # 构建 prompt
    msg_text = '\n'.join(
        f"[{m['time']}] {m['talker']} - {m['sender']}: {m['content']}"
        for m in messages[-150:]  # 最多 150 条
    )
    prompt = TODO_EXTRACT_PROMPT.format(messages=msg_text)

    # AI 提取
    try:
        response = call_deepseek(prompt, api_key, max_tokens=2000, timeout=90)
        # 清理可能的 markdown 代码块
        response = response.strip()
        if response.startswith('```'):
            response = response.split('\n', 1)[1]
            if response.endswith('```'):
                response = response[:-3]
        todos_raw = json.loads(response)
    except Exception as e:
        return {"error": f"AI 提取失败: {e}"}

    if not isinstance(todos_raw, list):
        return {"error": "AI 返回格式错误"}

    # 合并到现有待办（去重：相同 task 文本）
    existing = load_todos()
    existing_tasks = {t['task'] for t in existing}
    new_count = 0

    for item in todos_raw:
        if not isinstance(item, dict) or not item.get('task'):
            continue
        if item['task'] in existing_tasks:
            continue
        todo = {
            'id': f"todo_{now.strftime('%Y%m%d_%H%M%S')}_{new_count + 1}",
            'task': item['task'],
            'urgency': item.get('urgency', '中'),
            'deadline': item.get('deadline', '未提及'),
            'status': 'pending',
            'created_at': now.strftime('%Y-%m-%d %H:%M'),
            'context': '',
        }
        existing.append(todo)
        new_count += 1

    save_todos(existing)
    return {"new": new_count, "total": len(existing)}


def list_todos(status: str = None, urgency: str = None):
    todos = load_todos()
    if status:
        todos = [t for t in todos if t['status'] == status]
    if urgency:
        todos = [t for t in todos if t['urgency'] == urgency]

    # 排序：pending 在前，高紧急度在前
    order = {'pending': 0, 'done': 1}
    urg_order = {'高': 0, '中': 1, '低': 2}
    todos.sort(key=lambda t: (order.get(t['status'], 2), urg_order.get(t['urgency'], 3)))

    return todos


def toggle_todo(todo_id: str, status: str):
    todos = load_todos()
    for t in todos:
        if t['id'] == todo_id or t['id'].startswith(todo_id):
            t['status'] = status
            save_todos(todos)
            return t
    return None


def delete_todo(todo_id: str):
    todos = load_todos()
    for i, t in enumerate(todos):
        if t['id'] == todo_id or t['id'].startswith(todo_id):
            deleted = todos.pop(i)
            save_todos(todos)
            return deleted
    return None


def remind():
    """显示需要关注的待办。"""
    todos = load_todos()
    pending = [t for t in todos if t['status'] == 'pending']
    high = [t for t in pending if t['urgency'] == '高']
    mid = [t for t in pending if t['urgency'] == '中']
    low = [t for t in pending if t['urgency'] == '低']

    return {
        'total_pending': len(pending),
        'high': high,
        'mid': mid,
        'low': low,
    }


# ====== Main ======

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='待办提取与任务追踪')
    subparsers = parser.add_subparsers(dest='command')

    # extract
    p = subparsers.add_parser('extract', help='从聊天记录中 AI 提取待办')
    p.add_argument('--days', type=int, default=7, help='扫描最近多少天（默认 7）')
    p.add_argument('--api-key', help='DeepSeek API key（优先从 config 读取）')

    # list
    p = subparsers.add_parser('list', help='列出待办')
    p.add_argument('--status', choices=['pending', 'done'], help='按状态筛选')
    p.add_argument('--urgency', choices=['高', '中', '低'], help='按紧急度筛选')
    p.add_argument('--json', action='store_true', help='JSON 输出')

    # done
    p = subparsers.add_parser('done', help='标记待办为已完成')
    p.add_argument('id', help='待办 ID（或前缀）')

    # undone
    p = subparsers.add_parser('undone', help='取消已完成标记')
    p.add_argument('id', help='待办 ID（或前缀）')

    # rm
    p = subparsers.add_parser('rm', help='删除待办')
    p.add_argument('id', help='待办 ID（或前缀）')

    # remind
    subparsers.add_parser('remind', help='查看待办提醒')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = load_config()
    api_key = getattr(args, 'api_key', None) or os.environ.get('DEEPSEEK_API_KEY', '') or config.get('deepseekApiKey', '')

    if args.command == 'extract':
        if not api_key:
            print('[ERROR] 缺少 DeepSeek API key')
            sys.exit(1)
        print(f'扫描最近 {args.days} 天的聊天记录...')
        result = extract_todos(api_key, days=args.days)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == 'list':
        result = list_todos(args.status, args.urgency)
        if getattr(args, 'json', False):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif not result:
            print('✅ 暂无待办')
        else:
            status_labels = {'pending': '⬜', 'done': '✅'}
            urg_labels = {'高': '🔴', '中': '🟡', '低': '🟢'}
            for t in result:
                s = status_labels.get(t['status'], '❓')
                u = urg_labels.get(t['urgency'], '⚪')
                print(f"  {s} {u} [{t['id']}] {t['task']}")
                if t.get('deadline') and t['deadline'] != '未提及':
                    print(f"     ⏰ {t['deadline']}")
            print(f"\n共 {len(result)} 项")

    elif args.command == 'done':
        t = toggle_todo(args.id, 'done')
        if t:
            print(f'✅ 已标记完成: {t["task"]}')
        else:
            print(f'未找到待办: {args.id}')

    elif args.command == 'undone':
        t = toggle_todo(args.id, 'pending')
        if t:
            print(f'🔄 已恢复: {t["task"]}')
        else:
            print(f'未找到待办: {args.id}')

    elif args.command == 'rm':
        t = delete_todo(args.id)
        if t:
            print(f'🗑️ 已删除: {t["task"]}')
        else:
            print(f'未找到待办: {args.id}')

    elif args.command == 'remind':
        result = remind()
        print(f'\n📋 待办提醒 — {datetime.now(TZ).strftime("%Y-%m-%d %H:%M")}')
        print(f'   共 {result["total_pending"]} 项未完成\n')
        if result['high']:
            print('🔴 高紧急度:')
            for t in result['high']:
                print(f'   - {t["task"]}')
                if t['deadline'] != '未提及':
                    print(f'     ⏰ {t["deadline"]}')
            print()
        if result['mid']:
            print('🟡 中紧急度:')
            for t in result['mid']:
                print(f'   - {t["task"]}')
            print()
        if result['low']:
            print('🟢 低紧急度:')
            for t in result['low']:
                print(f'   - {t["task"]}')
            print()
        if result['total_pending'] == 0:
            print('✅ 暂无待办，干得好！')


if __name__ == '__main__':
    main()
