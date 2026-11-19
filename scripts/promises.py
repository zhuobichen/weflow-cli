"""欠账清单：我说过要给谁做什么，后来做了没有。

这是微信永远回答不了的问题 —— 它知道"我明天发你"，但不知道"兑现"是什么。

**默认不调模型**：先用规则把候选捞出来（我发的 + 承诺句式），再在同一个会话之后的对话里
找"完成信号 / 对方催问 / 对方确认"三类证据。规则会误报，所以每条都带着原话和后续，
让读者自己判 —— 而不是给一个"AI 认为你欠了 7 件事"的黑箱。

用法：
    python scripts/promises.py --days 60
    python scripts/promises.py --days 90 --json
    python scripts/promises.py --days 60 --talker 老表
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from _utils import load_config, decrypt_lock  # noqa: E402
import nt_decrypt as nd  # noqa: E402

TZ = timezone(timedelta(hours=8))

# 承诺句式：我 + 将来/稍后 + 一个交付动作。宁可多收，后面还有证据过滤。
PROMISE_RE = re.compile(
    r'(我|这边)[^。！？\n]{0,12}'
    r'(明天|后天|下周|下周一|晚点|稍后|回头|等会|等会儿|下午|晚上|今天之内|这周)[^。！？\n]{0,12}'
    r'(发|给|寄|做|弄|整理|传|送|安排|联系|确认|回复|处理)')
DELIVER_RE = re.compile(r'(发了|发你了|发您了|已发|寄了|弄好了|做好了|整理好了|搞定了|已上传|传了|安排了|回复了|处理好了)')
CHASE_RE = re.compile(r'(发了吗|寄了吗|好了吗|弄好了吗|什么时候|还没|催一下|怎么还没|在吗.{0,6}(发|给))')
ACK_RE = re.compile(r'^(收到|好的|好滴|谢谢|多谢|ok|OK|嗯嗯|好嘞)')
_NOT_PROMISE = re.compile(r'(我明天要|我下周要|我想|我觉得|我昨天|我上次|我已经)')


def _clean(text):
    text = ' '.join(str(text or '').split())
    return re.sub(r'^[A-Za-z0-9_\-]+@?(chatroom|openim)?\s*:\s*', '', text)


def same_account(name, wxid):
    """配置里的 wxid 与 `Name2Id` 里的账号名**可能是同一个人的两种写法**。

    实测本机：配置是 `wxid_账号A_后缀`，而 `Name2Id` 里只有 `wxid_账号A`（没有后缀）——
    精确比较一个都匹配不上，于是"我发的消息"一条也捞不到（第一版就是这么得到 0 条承诺的）。
    """
    n, w = (name or '').strip(), (wxid or '').strip()
    if not n or not w:
        return False
    return n == w or n.startswith(w + '_') or w.startswith(n + '_')


def load_messages(conns, days, my_wxid):
    """{talker: [ (ts, is_me, sender, text) ]}，按时间升序。"""
    since = int((datetime.now(TZ) - timedelta(days=days)).timestamp())
    out = defaultdict(list)
    for conn in conns:
        c = conn.cursor()
        try:
            c.execute('SELECT rowid, user_name FROM Name2Id')
            id2name = {r[0]: r[1] for r in c.fetchall()}
        except Exception:
            continue
        try:
            c.execute('SELECT user_name FROM Name2Id WHERE is_session = 1')
            sessions = [r[0] for r in c.fetchall() if r[0]]
        except Exception:
            sessions = []
        for talker in sessions:
            tbl = 'Msg_%s' % __import__('hashlib').md5(talker.encode()).hexdigest()
            try:
                c.execute('SELECT COUNT(*) FROM sqlite_master WHERE name=?', (tbl,))
                if c.fetchone()[0] == 0:
                    continue
                c.execute('SELECT create_time, real_sender_id, local_type, message_content FROM "%s" '
                          'WHERE create_time >= ? ORDER BY create_time' % tbl, (since,))
                for ts, sender, ltype, content in c.fetchall():
                    if (int(ltype or 0) & 0xFFFFFFFF) != 1:
                        continue
                    text = _clean(nd._decode_content(content))
                    if not text:
                        continue
                    who = id2name.get(sender, '')
                    out[talker].append((int(ts), same_account(who, my_wxid), who, text))
            except Exception:
                continue
    return out


def classify(follow):
    """看承诺之后发生了什么：已兑现 / 被催 / 确认收到 / 看不清。

    **「确认」的判据是我声称已发之后对方才说收到。** 原来的写法只要后续出现
    「好的 / 收到」就判 `acked`，于是「我明天把排期表发到群里」+「好嘞」被标成
    "已确认"——那一声是在回应**承诺**，不是在确认**收到东西**。合成样例第一次
    跑就把这条误报打出来了；拿它做宣传，懂得看的用户一眼能挑出来。

    现在：先找我"已发/已弄好"的位置，只有**它之后**对方说收到才算确认；
    只有承诺+对方应一声时不判确认，落回"看不清"（由人来判断）。
    """
    deliver_at = None
    for i, f in enumerate(follow):
        if f[1] and DELIVER_RE.search(f[3]):
            deliver_at = i
            break
    if deliver_at is not None:
        if any(not f[1] and ACK_RE.search(f[3]) for f in follow[deliver_at + 1:]):
            return 'acked', '我说已发之后，对方确认收到'
        return 'delivered', '我后来提到"已发/已弄好"'
    chases = [f for f in follow if not f[1] and CHASE_RE.search(f[3])]
    if chases:
        return 'chased', '对方后来催过：' + chases[0][3][:40]
    if any(not f[1] and ACK_RE.search(f[3]) for f in follow):
        return 'unknown', '对方只应了一声，没有确认收到东西'
    return 'unknown', '没有后续证据'


def main():
    ap = argparse.ArgumentParser(description='我说过要给谁做什么，后来做了没有')
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--talker', default=os.environ.get('WEFLOW_TALKER', ''), help='只看某个会话（wxid 或昵称子串）')
    ap.add_argument('--limit', type=int, default=30, help='最多列几条（默认 30）')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    cfg = load_config()
    db = cfg.get('ntDbPath') or ''
    my_wxid = cfg.get('wxid') or ''
    if not db:
        raise SystemExit('配置里没有 ntDbPath')
    key = decrypt_lock(cfg.get('ntKey', '') or '')
    salt = decrypt_lock(cfg.get('ntSalt', '') or '')
    pw = decrypt_lock(cfg.get('favPassphrase', '') or cfg.get('decryptKey', '') or '')
    conns = nd.connect_message_shards(db, key, salt, pw)
    if not conns:
        raise SystemExit('打不开消息分片')

    try:
        by_talker = load_messages(conns, args.days, my_wxid)
        # 昵称在 **contact.db**，消息库里没有 `contact` 表 —— 从消息库找一定落空，
        # 输出里就只剩 wxid（和 on_this_day.py 同一个坑）。
        name_map = {}
        try:
            from network import load_graph
            people, _rooms, _members = load_graph(cfg)
            name_map = {p['username']: (p['remark'] or p['nick'] or p['username'])
                        for p in people.values()}
        except Exception as exc:
            print(f'[WARN] 读联系人失败（{type(exc).__name__}: {exc}），下面显示 id',
                  file=sys.stderr)
    finally:
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass

    items = []
    for talker, msgs in by_talker.items():
        shown = name_map.get(talker, talker)
        if args.talker and args.talker not in (talker, shown):
            continue
        for i, (ts, is_me, who, text) in enumerate(msgs):
            if not is_me or not PROMISE_RE.search(text) or _NOT_PROMISE.search(text):
                continue
            follow = msgs[i + 1:i + 20]
            state, why = classify(follow)
            items.append({
                'time': datetime.fromtimestamp(ts, tz=TZ).strftime('%Y-%m-%d %H:%M'),
                'talker': shown,
                'said': text[:100],
                'state': state,
                'evidence': why,
            })
    items.sort(key=lambda x: x['time'], reverse=True)
    items = items[:args.limit]

    if args.json:
        print(json.dumps({'success': True, 'days': args.days, 'items': items}, ensure_ascii=False, indent=2))
        return

    label = {'delivered': '已兑现', 'chased': '可能欠着', 'acked': '已确认', 'unknown': '看不清'}
    order = {'chased': 0, 'unknown': 1, 'delivered': 2, 'acked': 3}
    items.sort(key=lambda x: (order[x['state']], x['time']))
    print(f'最近 {args.days} 天里，"我"承诺过 {len(items)} 件事：\n')
    for it in items:
        print(f"[{label[it['state']]}] {it['time']}  {it['talker']}")
        print(f"    我说：{it['said']}")
        print(f"    后续：{it['evidence']}")
        print()


if __name__ == '__main__':
    main()
