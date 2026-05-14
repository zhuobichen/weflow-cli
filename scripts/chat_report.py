#!/usr/bin/env python3
"""
微信聊天月报 — 汇总指定联系人聊天记录，DeepSeek AI 分析任务和回复。

用法:
  python scripts/chat_report.py --month 2026-04
  python scripts/chat_report.py --month 2026-04 --talker 张三 --talker 李老师
  python scripts/chat_report.py --month 2026-04 --from-whitelist
  python scripts/chat_report.py --month 2026-04 --talker 张三 --李老师 --家人群
  python scripts/chat_report.py --month 2026-04 --no-ai

输出: output/report-2026-04.md
"""

import sys, os, json, re, time, random, hashlib, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict, Counter

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print("请安装: pip install sqlcipher3"); sys.exit(1)

from _utils import load_config, decrypt_lock, get_db_config, call_deepseek

OUTPUT_ROOT = 'output'
AI_DELAY = (2, 4)  # AI 调用间隔 (min, max) 秒

# 任务关键词预筛 — 只有命中这些的联系人才调 AI
TASK_KEYWORDS = ['帮忙', '做一下', '问个', '帮我', '麻烦', '有空吗', '方便吗',
                 '能不能', '可以吗', '好不好', '行吗', '对吗', '怎么', '什么时候',
                 '发给我', '看一下', '改一下', '整理', '准备', '安排', '通知',
                 '?', '？']

# ====== Database ======

def open_db(db_path, key_hex, salt_hex):
    raw_key = f"x'{key_hex}{salt_hex}'"
    conn = sqlcipher.connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    c.execute("SELECT count(*) FROM sqlite_master")
    return conn


def get_sessions(conn):
    """Get active chat sessions from Name2Id (NT format)."""
    c = conn.cursor()
    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        sessions = [{'username': r[0]} for r in c.fetchall()]
        return sessions
    except:
        pass
    # Fallback: scan Msg_ tables
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
        for r in c.fetchall(): name_map[r[0]] = r[1]
        conn.close()
    except: pass
    return name_map


def get_messages(conn, talker, start_ts, end_ts, sender_map, name_map, own_wxid):
    """Get text messages for a talker within time range."""
    tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
    c = conn.cursor()
    try:
        c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
        if c.fetchone()[0] == 0: return []
    except: return []

    c.execute(f'SELECT local_id, local_type, create_time, real_sender_id, message_content FROM "{tbl}" WHERE create_time >= ? AND create_time <= ? ORDER BY create_time ASC', (start_ts, end_ts))
    rows = c.fetchall()

    msgs = []
    for row in rows:
        lt, ct, sid = row[1] or 0, row[2] or 0, row[3] or 0
        username = sender_map.get(sid, '')
        is_self = False
        if own_wxid:
            is_self = (username == own_wxid)
            if not is_self:
                p = own_wxid.rsplit('_', 1)
                if len(p) == 2 and len(p[1]) == 4 and p[1].isalnum():
                    is_self = (username == p[0])

        content = row[4] if isinstance(row[4], str) else (f'[{lt}类型]' if lt != 1 else '')
        sender = '我' if is_self else name_map.get(username, username)
        msgs.append({'time': ct, 'is_self': is_self, 'sender': sender, 'content': content, 'type': lt})
    return msgs


def has_tasks(msgs):
    """Quick keyword check: does this conversation likely contain tasks?"""
    text = ' '.join(m['content'][:100] for m in msgs if not m['is_self'] and m['type'] == 1)
    return any(kw in text for kw in TASK_KEYWORDS)


# ====== AI ======

TALKER_PROMPT = """分析微信聊天记录，识别任务和请求。

格式：[时间] 发送者: 内容
规则：只关注对方（非"我"）的任务/请求/问句，判断我是否回复、紧急度、完成情况。

返回JSON（不要其他内容）：
{"tasks": [{"content":"任务摘要","time":"消息时间","replied":true,"reply":"回复","urgency":"高/中/低","status":"完成/进行中/未回复"}], "summary": "总结"}

==MESSAGES==
"""

SUMMARY_PROMPT = """根据以下联系人分析结果生成月度总结。统计任务数、已/未回复，按紧急度列出未回复项，给出3条行动建议。

格式：
【任务统计】共 N 项，已回复 M，未回复 K
【紧急事项】
- 来源 | 任务 | 逾期
【建议】
1. ...
2. ...
3. ...

==TALKER_DATA==
"""


# ====== Main ======

def fmt_time(ts): return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')
def fmt_date(ts): return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime('%Y-%m-%d')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    p = argparse.ArgumentParser(description='微信聊天月报')
    p.add_argument('--month'); p.add_argument('--year'); p.add_argument('--start'); p.add_argument('--end')
    p.add_argument('--talker', action='append', help='指定联系人（可多选）')
    p.add_argument('--from-whitelist', action='store_true', help='使用白名单中的联系人')
    p.add_argument('--api-key'); p.add_argument('--no-ai', action='store_true')
    p.add_argument('--output', '-o', default=OUTPUT_ROOT)
    args = p.parse_args()

    # Time range
    tz = timezone(timedelta(hours=8))
    if args.month:
        y, m = map(int, args.month.split('-'))
        sd = datetime(y, m, 1, tzinfo=tz)
        ed = (datetime(y, m+1, 1, tzinfo=tz) if m < 12 else datetime(y+1, 1, 1, tzinfo=tz)) - timedelta(seconds=1)
        period = args.month
    elif args.year:
        y = int(args.year)
        sd = datetime(y, 1, 1, tzinfo=tz); ed = datetime(y, 12, 31, 23, 59, 59, tzinfo=tz); period = args.year
    elif args.start and args.end:
        sd = datetime.strptime(args.start, '%Y-%m-%d').replace(tzinfo=tz)
        ed = datetime.strptime(args.end, '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=tz)
        period = f"{args.start}~{args.end}"
    else:
        now = datetime.now(tz)
        fm = (now.year, now.month-1) if now.month > 1 else (now.year-1, 12)
        sd = datetime(fm[0], fm[1], 1, tzinfo=tz)
        ed = datetime(fm[0], fm[1]+1, 1, tzinfo=tz) - timedelta(seconds=1) if fm[1] < 12 else datetime(fm[0]+1, 1, 1, tzinfo=tz) - timedelta(seconds=1)
        period = f"{fm[0]}-{fm[1]:02d}"

    s_ts, e_ts = int(sd.timestamp()), int(ed.timestamp())

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not args.no_ai and not api_key:
        print('[ERROR] 需要 DeepSeek API key'); sys.exit(1)

    config = load_config()
    db = get_db_config(config)
    if not db['nt_db'] or not db['nt_key']:
        print('[ERROR] 未配置，先运行: weflow-cli init'); sys.exit(1)

    print(f'=== 微信聊天月报 {period} ===')
    print(f'时间: {fmt_date(s_ts)} ~ {fmt_date(e_ts)}')
    print(f'AI: {"关闭" if args.no_ai else "DeepSeek V4"}')

    # Resolve talker filter
    target_wxids = set()
    if args.talker:
        # Need to resolve nicknames to wxids
        conn = open_db(db['nt_db'], db['nt_key'], db['nt_salt'])
        name_map = get_name_map(db['contact_db'], db['contact_key'], db['contact_salt'])
        c = conn.cursor()
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        all_sessions = {r[0]: name_map.get(r[0], r[0]) for r in c.fetchall()}
        conn.close()
        for t in args.talker:
            t = t.strip()
            found = False
            for wxid, name in all_sessions.items():
                if t in (wxid, name) or t.lower() in name.lower():
                    target_wxids.add(wxid); found = True; break
            if not found:
                # Try as wxid directly
                target_wxids.add(t)
        if target_wxids:
            print(f'选定联系人: {len(target_wxids)} 人')
    if args.from_whitelist:
        wl = config.get('whitelist', [])
        if wl: target_wxids.update(wl); print(f'白名单: {len(wl)} 人')

    # Connect
    print('\n连接数据库...')
    conn = open_db(db['nt_db'], db['nt_key'], db['nt_salt'])
    name_map = get_name_map(db['contact_db'], db['contact_key'], db['contact_salt'])
    own_wxid = config.get('wxid', '')

    # Get sessions
    print('获取会话...')
    sessions = get_sessions(conn)
    if target_wxids:
        sessions = [s for s in sessions if s['username'] in target_wxids]

    print(f'活跃会话: {len(sessions)}')

    # Build sender map
    c = conn.cursor()
    c.execute("SELECT rowid, user_name FROM Name2Id")
    sender_map = {rowid: uname for rowid, uname in c.fetchall()}

    # Phase 1: Collect
    print(f'\n=== Phase 1: 采集消息 ===\n')
    data = []
    total_msgs = total_sent = total_recv = 0
    active_days = set(); hour_dist = [0]*24

    for i, s in enumerate(sessions):
        wxid = s['username']; name = name_map.get(wxid, wxid)
        msgs = get_messages(conn, wxid, s_ts, e_ts, sender_map, name_map, own_wxid)
        if not msgs: continue
        sent = sum(1 for m in msgs if m['is_self']); recv = len(msgs) - sent
        days = set(fmt_date(m['time']) for m in msgs)
        total_msgs += len(msgs); total_sent += sent; total_recv += recv
        active_days.update(days)
        for m in msgs: hour_dist[datetime.fromtimestamp(m['time'], tz=tz).hour] += 1

        data.append({'wxid': wxid, 'name': name, 'msgs': msgs, 'sent': sent, 'recv': recv, 'days': len(days)})
        if (i+1) % 20 == 0: print(f'  {i+1}/{len(sessions)}...')

    data.sort(key=lambda d: d['sent'] + d['recv'], reverse=True)
    print(f'\n联系人: {len(data)} | 消息: {total_msgs} | 我发: {total_sent} | 收到: {total_recv}')

    # Phase 2: AI
    all_tasks = []; monthly_summary = ''
    if not args.no_ai and api_key:
        print(f'\n=== Phase 2: AI 分析 ===\n')
        # Pre-filter: only analyze contacts with likely tasks
        ai_targets = [d for d in data if has_tasks(d['msgs'])]
        skipped = len(data) - len(ai_targets)
        if skipped: print(f'预筛跳过 {skipped} 人（无任务关键词）\n')

        for i, d in enumerate(ai_targets):
            lines = []
            for m in d['msgs']:
                if m['type'] != 1: continue
                lines.append(f"[{fmt_time(m['time'])}] {m['sender']}: {m['content'][:120]}")
            if not lines: continue

            prompt = TALKER_PROMPT.replace('==MESSAGES==', '\n'.join(lines[-30:]))
            # V4 reasoning model: ~1 token/chars (CN overhead), need 2x input + 500 output
            estimated_tokens = max(2000, len(prompt) * 2 + 500)
            try:
                resp = call_deepseek(prompt, api_key, max_tokens=estimated_tokens, timeout=120)
                # Extract JSON from response (may be in code block or raw)
                json_str = resp
                code_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', resp)
                if code_match: json_str = code_match.group(1)
                m = re.search(r'\{[\s\S]*\}', json_str)
                if m:
                    raw_json = m.group()
                    # Fix common DeepSeek JSON issues
                    raw_json = re.sub(r',\s*}', '}', raw_json)  # trailing comma
                    raw_json = re.sub(r',\s*]', ']', raw_json)  # trailing comma in array
                    analysis = json.loads(raw_json)
                    d['tasks'] = analysis.get('tasks', [])
                    d['summary'] = analysis.get('summary', '')
                    for t in d['tasks']: t['talker'] = d['name']
                    all_tasks.extend(d['tasks'])
                    print(f'  [{i+1}/{len(ai_targets)}] {d["name"][:20]:20s} → {len(d["tasks"])} 任务')
                else:
                    d['tasks'] = []; d['summary'] = ''
                    # Save raw response for debugging
                    with open(f'debug_ai_{i}.json', 'w', encoding='utf-8') as df:
                        df.write(resp[:2000])
                    print(f'  [{i+1}/{len(ai_targets)}] {d["name"][:20]:20s} → 解析失败 (已保存debug_ai_{i}.json)')
            except Exception as e:
                d['tasks'] = []; d['summary'] = ''
                print(f'  [{i+1}/{len(ai_targets)}] {d["name"][:20]:20s} → ERR: {e}')
            time.sleep(AI_DELAY[0] + random.random() * (AI_DELAY[1] - AI_DELAY[0]))

        # Monthly summary
        print(f'\n=== 月度总结 ===\n')
        parts = []
        for d in ai_targets:
            if d.get('tasks'):
                parts.append(f"{d['name']}: {json.dumps(d['tasks'], ensure_ascii=False)}")
        if parts:
            monthly_summary = call_deepseek(SUMMARY_PROMPT.replace('==TALKER_DATA==', '\n\n'.join(parts)), api_key, max_tokens=2000)
        print(f'  识别任务: {len(all_tasks)} | 未回复: {sum(1 for t in all_tasks if t.get("status")=="未回复")}')

    # Phase 3: Generate markdown
    print(f'\n=== Phase 3: 生成报告 ===\n')
    lines = [f'# 微信聊天月报 — {period}', '',
             f'> 统计范围: {fmt_date(s_ts)} ~ {fmt_date(e_ts)} | 生成: {datetime.now(tz).strftime("%Y-%m-%d")}',
             f'> AI: {"DeepSeek V4 Pro" if not args.no_ai else "关闭"}',
             f'> 联系人: {len(data)} | 消息: {total_msgs} | 活跃天数: {len(active_days)}',
             '', '---', '', '## 总览', '',
             '| 指标 | 数值 |', '|------|------|',
             f'| 活跃联系人 | {len(data)} |', f'| 消息总数 | {total_msgs:,} |',
             f'| 我发出 | {total_sent:,} ({total_sent/max(total_msgs,1)*100:.0f}%) |',
             f'| 收到 | {total_recv:,} ({total_recv/max(total_msgs,1)*100:.0f}%) |',
             f'| 活跃天数 | {len(active_days)} |', '']

    if not args.no_ai and all_tasks:
        unreplied = [t for t in all_tasks if t.get('status') == '未回复']
        replied = [t for t in all_tasks if t.get('status') != '未回复']
        lines += ['## AI 任务分析', '', '> DeepSeek V4 Pro', '']
        lines += [f'### 🔴 未回复 ({len(unreplied)} 项)', '']
        if unreplied:
            lines += ['| # | 来源 | 任务 | 时间 | 紧急度 |', '|---|------|------|------|--------|']
            for i, t in enumerate(unreplied):
                u = '🔴' if t.get('urgency') == '高' else ('⚠️' if t.get('urgency') == '中' else '🟢')
                lines.append(f'| {i+1} | {t.get("talker","")} | {t.get("content","")[:30]} | {t.get("time","")} | {u} {t.get("urgency","")} |')
        else: lines.append('✅ 无未回复任务')
        lines.append('')
        lines += [f'### 🟡 已回复 ({len(replied)} 项)', '']
        if replied:
            lines += ['| # | 来源 | 任务 | 时间 | 回复 | 状态 |', '|---|------|------|------|------|------|']
            for i, t in enumerate(replied):
                s = '✅' if t.get('status') == '完成' else '⏳'
                lines.append(f'| {i+1} | {t.get("talker","")} | {t.get("content","")[:25]} | {t.get("time","")} | {t.get("reply","")[:20]} | {s} {t.get("status","")} |')
        lines.append('')
        if monthly_summary:
            lines += ['### 📊 AI 月度总结', '']
            for line in monthly_summary.strip().split('\n'): lines.append(f'> {line}')
            lines.append('')

    # Per contact
    lines += ['## 按联系人', '']
    for d in data:
        total = d['sent'] + d['recv']
        lines += [f'### {d["name"]}（{total} 条）', '',
                   f'我发 {d["sent"]} | 对方发 {d["recv"]} | 活跃 {d["days"]} 天', '']
        if d.get('summary'):
            lines += [f'> {d["summary"]}', '']
        if d.get('tasks'):
            lines += ['**任务：**', '']
            for t in d['tasks']:
                s = '✅' if t.get('status') == '完成' else ('⏳' if t.get('status') == '进行中' else '❌')
                lines.append(f'- [{t.get("time","")}] {t.get("content","")[:60]} → {s} {t.get("status","")}')
            lines.append('')
        # Recent conversations
        text_msgs = [m for m in d['msgs'] if m['type'] == 1]
        if text_msgs:
            lines += ['**最近对话：**', '']
            for m in text_msgs[-5:]:
                lines.append(f'> **{fmt_time(m["time"])} {m["sender"]}:** {m["content"][:100]}')
            lines.append('')

    # Hour dist
    lines += ['## 活跃时段', '', '| 时段 | 消息 | 占比 |', '|------|------|------|']
    for label, s, e in [('00-06',0,6),('06-09',6,9),('09-12',9,12),('12-14',12,14),('14-18',14,18),('18-22',18,22),('22-24',22,24)]:
        n = sum(hour_dist[s:e])
        lines.append(f'| {label} | {n} | {n/max(total_msgs,1)*100:.1f}% |')
    lines.append('')

    # Top 10
    lines += ['## TOP 10', '', '| 排名 | 联系人 | 消息 | 占比 |', '|------|--------|------|------|']
    for i, d in enumerate(data[:10]):
        t = d['sent'] + d['recv']
        lines.append(f'| {i+1} | {d["name"]} | {t} | {t/max(total_msgs,1)*100:.1f}% |')
    lines += ['', '*由 weflow-cli report 自动生成*', '']

    # Write
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    fp = out / f'report-{period}.md'
    with open(fp, 'w', encoding='utf-8') as f: f.write('\n'.join(lines))
    print(f'✓ {fp}')
    print(f'  联系人: {len(data)} | 消息: {total_msgs}')
    if not args.no_ai: print(f'  任务: {len(all_tasks)} | 未回复: {sum(1 for t in all_tasks if t.get("status")=="未回复")}')


if __name__ == '__main__': main()
