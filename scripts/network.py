"""人脉图谱：谁和你在同一个群、谁适合做引荐人。

数据全在 `contact.db`（联系人 + 群 + 群成员），不碰消息库，也就没有"读聊天内容"这件事：
     contact(id, username, nick_name, remark)      4192 人
     chat_room(id, username, owner)                80 个群
     chatroom_member(room_id, member_id)           4305 条关系（member_id 是 contact.id）

用法：
    python scripts/network.py --groups              群概览（成员数，按大小排）
    python scripts/network.py --with 张三           我和他共同在哪些群
    python scripts/network.py --intro 张三           谁最适合把我介绍给他
    python scripts/network.py --intro 张三 --json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from _utils import load_config, decrypt_lock  # noqa: E402
from nt_common import derive_database_key  # noqa: E402
from export_chat_html import connect  # noqa: E402


class ContactDbUnavailable(RuntimeError):
    """联系人库不可用。

    **不要用 SystemExit 表达这个错误**：`promises` / `today` / `replay` 等命令都靠
    `except Exception` 把联系人名降级成 wxid 继续跑（联系人只是锦上添花，消息库才是主体）。
    SystemExit 继承 BaseException，`except Exception` 接不住——缺 contactDbPath 时
    整个命令直接退出，用户看到的是"先跑 init"，而其实该命令本可以正常出结果。
    """


def _open_contact(cfg):
    path = cfg.get('contactDbPath') or ''
    if not path:
        raise ContactDbUnavailable('配置里没有 contactDbPath，先跑 weflow-cli init')
    key = decrypt_lock(cfg.get('contactKey', '') or cfg.get('ntKey', '') or '')
    salt = decrypt_lock(cfg.get('contactSalt', '') or cfg.get('ntSalt', '') or '')
    pw = decrypt_lock(cfg.get('favPassphrase', '') or cfg.get('decryptKey', '') or '')
    k, s = derive_database_key(path, key, salt, pw)
    return connect(path, k, s)


def load_graph(cfg):
    """(people, rooms, members) —— people: id->名字; rooms: id->群名; members: room->{id}"""
    conn, cur = _open_contact(cfg)
    try:
        people = {}
        for pid, username, nick, remark in cur.execute(
                'SELECT id, username, COALESCE(nick_name,""), COALESCE(remark,"") FROM contact'):
            name = remark or nick or username
            people[int(pid)] = {'name': name, 'username': username, 'nick': nick, 'remark': remark}

        rooms = {}
        # 群昵称在 `contact` 里（`@chatroom` 那条的 remark/nick_name），`chat_room.username`
        # 只是 id —— 不查这一步，输出里 80 个群全叫 `47966561144@chatroom`。
        by_username = {p['username']: p for p in people.values()}
        for rid, username, owner in cur.execute('SELECT id, username, COALESCE(owner,"") FROM chat_room'):
            info = by_username.get(username) or {}
            name = info.get('remark') or info.get('nick') or username
            rooms[int(rid)] = {'name': name, 'username': username, 'owner': owner}

        members = defaultdict(set)
        for rid, mid in cur.execute('SELECT room_id, member_id FROM chatroom_member'):
            members[int(rid)].add(int(mid))
        return people, rooms, members
    finally:
        conn.close()


def resolve(people, needle):
    """按 wxid / 昵称 / 备注找一个人；返回 (id, 记录) 或 None。

    **排除群**：`contact` 表里 `@chatroom` 也在，用 `--with 老表` 会先命中「老表亲戚群」
    这个群而不是人（第一版就这么错），所以匹配人时跳过它们。
    """
    needle = (needle or '').strip()
    if not needle:
        return None
    exact, fuzzy = [], []
    for pid, p in people.items():
        if p['username'].endswith('@chatroom'):
            continue
        fields = (p['name'], p['username'], p['nick'], p['remark'])
        if needle == p['username']:
            return pid, p
        if any(needle == f for f in fields if f):
            exact.append((pid, p))
        elif any(needle in f for f in fields if f):
            fuzzy.append((pid, p))
    pool = exact or fuzzy
    return pool[0] if pool else None


def find_me(people, members, cfg):
    """自己的 contact.id。

    先按配置里的 wxid 找；**找不到就用"出现在最多群里的人"** —— 你在自己所有群里，
    而 `contact` 表不一定收录自己（实测这里就没有），少了这个兜底，
    「共同群」这类功能会直接退化成一列群名。
    """
    wxid = cfg.get('wxid') or ''
    if wxid:
        for pid, p in people.items():
            if p['username'] == wxid:
                return pid
    counts = defaultdict(int)
    for ids in members.values():
        for pid in ids:
            counts[pid] += 1
    if counts:
        pid, n = max(counts.items(), key=lambda kv: kv[1])
        if n >= 3:
            return pid
    return None


def rooms_of(members, pid):
    return {rid for rid, ids in members.items() if pid in ids}


def cmd_groups(people, rooms, members):
    rows = []
    for rid, info in rooms.items():
        ids = members.get(rid, set())
        rows.append({'room': info['name'], 'members': len(ids)})
    rows.sort(key=lambda r: -r['members'])
    print(f'共 {len(rows)} 个群\n')
    for r in rows[:30]:
        print(f"  {r['members']:>4} 人  {r['room']}")
    if len(rows) > 30:
        print(f'  …还有 {len(rows)-30} 个')


def cmd_with(people, rooms, members, needle, cfg):
    me_id = find_me(people, members, cfg)
    me = (me_id, people.get(me_id, {})) if me_id else None
    target = resolve(people, needle)
    if not target:
        raise SystemExit(f'没找到「{needle}」')
    tid, tinfo = target
    t_rooms = rooms_of(members, tid)
    print(f"目标：{tinfo['name']}（{tinfo['username']}）在 {len(t_rooms)} 个群")
    if not me:
        print('（配置里没有自己的 wxid，跳过共同群计算）')
        for rid in sorted(t_rooms):
            print(f"  · {rooms[rid]['name']}")
        return
    my_rooms = rooms_of(members, me[0])
    common = t_rooms & my_rooms
    print(f"你们共同在 {len(common)} 个群：")
    for rid in sorted(common):
        print(f"  · {rooms[rid]['name']}（{len(members.get(rid, []))} 人）")
    if not common:
        print('  （没有共同群）')


def cmd_intro(people, rooms, members, needle, cfg, top=8):
    me_id = find_me(people, members, cfg)
    me = (me_id, people.get(me_id, {})) if me_id else None
    target = resolve(people, needle)
    if not target:
        raise SystemExit(f'没找到「{needle}」')
    tid, tinfo = target
    if not me:
        raise SystemExit('配置里没有自己的 wxid，算不了引荐路径')
    my_rooms = rooms_of(members, me[0])
    t_rooms = rooms_of(members, tid)

    # 候选人 = 同时在我和目标的群里的人；分数 = 交集群数，其次是他和目标最近的共同群大小
    scores = defaultdict(lambda: {'rooms': set()})
    for rid in my_rooms & t_rooms:
        for pid in members.get(rid, set()):
            if pid in (me[0], tid):
                continue
            scores[pid]['rooms'].add(rid)
    if not scores:
        print(f'你和「{tinfo["name"]}」不在同一个群，也没有共同的联系人可引荐')
        return
    ranked = sorted(scores.items(), key=lambda kv: -len(kv[1]['rooms']))
    print(f"想认识「{tinfo['name']}」，这些人同时和你们在一个群：\n")
    for pid, info in ranked[:top]:
        p = people.get(pid, {})
        shared = '、'.join(rooms[r]['name'] for r in list(info['rooms'])[:3])
        print(f"  {p.get('name') or p.get('username')}  共同群 {len(info['rooms'])} 个：{shared}")


def main():
    ap = argparse.ArgumentParser(description='人脉图谱：共同群与引荐路径')
    ap.add_argument('--groups', action='store_true', help='群概览')
    ap.add_argument('--with', dest='with_who', default='', help='我和他共同在哪些群')
    ap.add_argument('--intro', default='', help='谁能把我介绍给他')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    cfg = load_config()
    try:
        people, rooms, members = load_graph(cfg)
    except ContactDbUnavailable as exc:
        raise SystemExit(str(exc))
    if args.groups:
        cmd_groups(people, rooms, members)
    elif args.with_who:
        cmd_with(people, rooms, members, args.with_who, cfg)
    elif args.intro:
        cmd_intro(people, rooms, members, args.intro, cfg)
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
