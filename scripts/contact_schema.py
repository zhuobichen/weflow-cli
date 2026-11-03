# -*- coding: utf-8 -*-
"""解 `contact.db` / `chat_room.ext_buffer` 里**已经验证过**的那几个字段（只读）。

为什么只解"验证过的"：顶层 `#3`/`#4` 的语义在本机实测里被三次证伪——不是
`chatroom_member.member_id` 的最大/最小值、不是 `chat_room.id`、也不在消息库
`message_*.db` 的任何 id 列里。语义没定就不给它编名字：本脚本把它们原样放进
`unrecognized`，谁也别照着一个猜出来的名字写逻辑。结构笔记见
`docs/CONTACT_DB_SCHEMA.md`，取舍见 D-067 / D-068。

已验证、因而输出的字段：

    RoomData
      #1 (重复) 成员 —— 每个成员：1=userName  2=displayName  3=status  4=inviter
      #5 (重复) = 成员里 **status 位 11 置位**的那批 id（本机 77/77 与该集合完全相等；
                 它**不是**"额外的参与者"——早前那么叫是错的，见 DECISIONS D-070）

联系人模式（`--contacts`）：读 `contact.extra_buffer` —— 同样只输出**已验证**的字段：

    #5  (ld)     国家/地区代码（ISO 3166-1 alpha-2）—— 本机 25/25 落在码表、众数 CN 占 94%
    #13 (varint) = 该行 `biz_info.type`（账号服务类型）—— 本机 687/687 逐行相等
    #41 (varint) = 该行资料的"最近更新时间"（epoch 秒；0 表示显式未设置，按缺省处理）

**故意不输出** `#4`/`#9`：它们已定性为"该账号自身资料文本的汇集"（含主体名/菜单按钮名），
是**内容不是字段名**，打进 CLI 输出等于把账号资料漏出去 —— 它们只以长度出现在 `unrecognized` 里。
其余字段未定名，一律原样进 `unrecognized`（键就是字段号）。取舍见 D-068 / D-082 / D-084 / D-088。

用法：
    python scripts/contact_schema.py --json
    python scripts/contact_schema.py --limit 5            # 只看前 5 个群
    python scripts/contact_schema.py --room <群 username 或 id>
    python scripts/contact_schema.py --contacts --limit 5 # 改看联系人（#5 地区码 / #13 服务类型 / #41 更新时间）
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 手跑时 stdout 是本机编码（Windows 是 GBK）：中文会乱码、非 GBK 字符直接崩掉整次输出
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def varint(buf, i):
    val = 0
    shift = 0
    while True:
        if i >= len(buf):
            raise ValueError('varint 越界')
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, i
        shift += 7
        if shift > 63:
            raise ValueError('varint 过长')


def walk(buf):
    """走一遍 wire format，返回 [(字段号, wire type, 值)]。

    **不认识的 wire type 一律抛错**：宁可整条报"解不动"，也不把错位的字节当成字段值
    ——那正是"看着像"的来源。
    """
    i = 0
    out = []
    while i < len(buf):
        tag, i = varint(buf, i)
        fno, wt = tag >> 3, tag & 7
        if fno == 0 or wt in (3, 4):
            raise ValueError('字段号/wire type 异常（fno=%d wt=%d）' % (fno, wt))
        if wt == 0:
            v, i = varint(buf, i)
            out.append((fno, wt, v))
        elif wt == 2:
            ln, i = varint(buf, i)
            if i + ln > len(buf):
                raise ValueError('长度越界')
            out.append((fno, wt, buf[i:i + ln]))
            i += ln
        elif wt == 5:
            if i + 4 > len(buf):
                raise ValueError('fixed32 越界')
            out.append((fno, wt, buf[i:i + 4]))
            i += 4
        elif wt == 1:
            if i + 8 > len(buf):
                raise ValueError('fixed64 越界')
            out.append((fno, wt, buf[i:i + 8]))
            i += 8
        else:
            raise ValueError('未知 wire type %d' % wt)
    return out


def _text(raw):
    if raw is None:
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None  # 不是 UTF-8 就别硬当字符串用（返回 None，调用方按未知处理）


def decode_room(blob):
    """把一个 ext_buffer 解成 {members, statusBit11Ids, unrecognized}；解不动就抛错。"""
    members = []
    extra_ids = []
    unrecognized = {}
    for fno, wt, value in walk(blob):
        if fno == 1 and wt == 2:
            inner = walk(value)
            # **键固定**：缺 #2 的成员也要有 displayName 这个键（值为 None），
            # 否则 JSON 里时有时无，下游每次都得先问"键在不在"——那种形状迟早被漏判成"没名字"。
            member = {'userName': None, 'displayName': None, 'status': None, 'inviter': None}
            for m_fno, m_wt, m_val in inner:
                if m_fno in (1, 2, 4) and m_wt == 2:
                    key = {1: 'userName', 2: 'displayName', 4: 'inviter'}[m_fno]
                    member[key] = _text(m_val)
                elif m_fno == 3 and m_wt == 0:
                    member['status'] = m_val
                else:
                    member.setdefault('unrecognized', {})['%d' % m_fno] = (
                        len(m_val) if m_wt == 2 else m_val)
            members.append(member)
        elif fno == 5 and wt == 2:
            extra_ids.append(_text(value))
        else:
            # 不猜：原样放进来，键就是字段号
            unrecognized['%d' % fno] = (len(value) if wt == 2 else value)
    return {'members': members, 'statusBit11Ids': extra_ids, 'unrecognized': unrecognized}


# `contact.extra_buffer` 的 `#5` 只在它是"恰好两个大写 ASCII 字母"时才当地区码输出。
REGION_RE = re.compile(r'^[A-Z]{2}$')


def decode_contact(blob):
    """解 `contact.extra_buffer` 里**已经验证过**的那几个字段；解不动就抛错。

    只解"验证过的"（D-068 / D-082 / D-084），三个：

      #5  (ld) 国家/地区代码（ISO 3166-1 alpha-2）—— 本机 25/25 落在码表、众数 CN 占 94%
      #13 (varint) = 该行 `biz_info.type`（账号服务类型）—— 本机 687/687 逐行相等
      #41 (varint) epoch 秒 = 该行资料的"最近更新时间"—— 0 表示显式未设置，按缺省处理

    **故意不输出的**（说清楚，免得后人以为是漏了）：

      - `#4` / `#9`：已定性为"该账号自身资料文本的汇集"（含主体名/菜单按钮名），它是**内容不是字段名** ——
        塞进 CLI 输出等于把账号资料漏出去。它们在 `unrecognized` 里**只留长度**。
      - 顶层 `#1`/`#2` 那一族（`contact.extra_buffer` 里 44 行的 OpenIM 小形状）：只报 `kind='openim'`，
        其余按键原样放进 `unrecognized`。那两键在文档里是 openim 库的外键，对"读联系人"没用。
      - 其余 30 来个字段：**语义未定，一律原样进 `unrecognized`**，键就是字段号（同 `decode_room`）。
    """
    fields = {}
    for fno, wt, value in walk(blob):
        fields.setdefault(fno, (wt, value))
    if not fields:
        return {'kind': None, 'region': None, 'bizType': None, 'updatedAt': None,
                'unrecognized': {}}
    raw = {'%d' % fno: (len(v) if wt == 2 else v) for fno, (wt, v) in fields.items()}
    # 小形状 = **字段号全 <= 9**（大 proto 一定有 #2..#38，不可能全 <= 9）。
    # ⚠️ 别再要求"必须有 #1"：那族里有一行没有 #1，加上这个条件会把它错判成联系人
    #    （全量核对时抓到：kind 分布变成 944/43，文档是 940/44 + 2 个残行）。
    if max(fields) <= 9:
        return {'kind': 'openim', 'region': None, 'bizType': None, 'updatedAt': None,
                'unrecognized': raw}

    out = {'kind': 'contact', 'region': None, 'bizType': None, 'updatedAt': None,
           'unrecognized': raw}
    wt5, v5 = fields.get(5, (None, None))
    if wt5 == 2 and isinstance(v5, bytes):
        text = _text(v5)
        if text and REGION_RE.match(text):
            out['region'] = text
    wt13, v13 = fields.get(13, (None, None))
    if wt13 == 0:
        out['bizType'] = v13
    wt41, v41 = fields.get(41, (None, None))
    if wt41 == 0 and v41:  # 0 = 显式"未设置"，不是 1970 年
        out['updatedAt'] = v41
    return out


def load_rows(args):
    from _utils import get_db_config  # noqa: E402
    from nt_decrypt import connect_encrypted_readonly  # noqa: E402

    cfg = get_db_config()
    db = args.db or cfg['contact_db']
    key = args.key or cfg['contact_key']
    salt = args.salt or cfg['contact_salt']
    if not db or not os.path.isfile(db):
        raise SystemExit('找不到 contact 库：%s' % (db or '(未配置)'))
    if not key or not salt:
        raise SystemExit('缺少库密钥（先跑 weflow-cli init，或显式给 --key/--salt）')
    # 只读打开：写模式会在微信的库旁留下 -wal/-shm，客户端会判定"数据库损坏"
    conn, cur = connect_encrypted_readonly(db, "x'%s%s'" % (key, salt))
    sql = ('SELECT id, username, owner, ext_buffer FROM chat_room '
           'WHERE ext_buffer IS NOT NULL AND length(ext_buffer) > 0')
    params = []
    if args.room:
        sql += ' AND (username = ? OR id = ?)'
        params = [args.room, args.room]
    sql += ' ORDER BY id LIMIT ?'
    params.append(args.limit)
    rows = cur.execute(sql, params).fetchall()
    conn.close()
    return rows


def load_contacts(args):
    from _utils import get_db_config  # noqa: E402
    from nt_decrypt import connect_encrypted_readonly  # noqa: E402

    cfg = get_db_config()
    db = args.db or cfg['contact_db']
    key = args.key or cfg['contact_key']
    salt = args.salt or cfg['contact_salt']
    if not db or not os.path.isfile(db):
        raise SystemExit('找不到 contact 库：%s' % (db or '(未配置)'))
    if not key or not salt:
        raise SystemExit('缺少库密钥（先跑 weflow-cli init，或显式给 --key/--salt）')
    # 只读打开：写模式会在微信的库旁留下 -wal/-shm，客户端会判定"数据库损坏"
    conn, cur = connect_encrypted_readonly(db, "x'%s%s'" % (key, salt))
    sql = ('SELECT id, username, local_type, extra_buffer FROM contact '
           'WHERE extra_buffer IS NOT NULL AND length(extra_buffer) > 0')
    params = []
    if args.room:
        sql += ' AND (username = ? OR id = ?)'
        params = [args.room, args.room]
    sql += ' ORDER BY id LIMIT ?'
    params.append(args.limit)
    rows = cur.execute(sql, params).fetchall()
    conn.close()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='读 contact.db 的 ext_buffer（只读，不联网、不调模型）：默认读**群**，--contacts 改读**联系人**')
    ap.add_argument('--limit', type=int, default=20, help='最多看几个（群 / 联系人，默认 20）')
    ap.add_argument('--room', help='只看某个（群 / 联系人 的 username 或 id）')
    ap.add_argument('--contacts', action='store_true',
                    help='改读联系人的 extra_buffer（#5 地区码 / #13 服务类型 / #41 资料更新时间），而不是群')
    ap.add_argument('--db', help='contact.db 路径（默认取配置）')
    ap.add_argument('--key', help='库密钥（默认取配置）')
    ap.add_argument('--salt', help='库盐（默认取配置）')
    ap.add_argument('--json', action='store_true', help='输出机器可读结果')
    args = ap.parse_args(argv)

    try:
        rows = load_contacts(args) if args.contacts else load_rows(args)
    except SystemExit:
        raise
    except Exception as exc:
        if args.json:
            print(json.dumps({'success': False, 'error': str(exc)[:200]}, ensure_ascii=False))
        else:
            print('读库失败：%s' % str(exc)[:200], file=sys.stderr)
        return 1

    if args.contacts:
        contacts = []
        failures = []
        for rid, username, local_type, blob in rows:
            try:
                decoded = decode_contact(bytes(blob))
            except Exception as exc:
                # 同群那条：解不动就说解不动，不返回一条"什么都没有"的联系人
                failures.append({'id': rid, 'username': username, 'error': str(exc)[:80]})
                continue
            contacts.append({'id': rid, 'username': username, 'localType': local_type,
                             'kind': decoded['kind'], 'region': decoded['region'],
                             'bizType': decoded['bizType'], 'updatedAt': decoded['updatedAt'],
                             'unrecognized': decoded['unrecognized']})
        if args.json:
            print(json.dumps({'success': True, 'action': 'contact-schema',
                              'contacts': contacts, 'failures': failures,
                              'readsLocalData': True, 'invokesAI': False,
                              'sendsNothing': True}, ensure_ascii=False))
        else:
            print('看了 %d 个联系人；解不动 %d 个' % (len(contacts), len(failures)))
            print('（只输出已验证字段：#5 地区码 / #13 服务类型 / #41 资料更新时间；'
                  '#4/#9 是账号资料文本，属内容，不打印，只在"未识别"里留长度）')
            for c in contacts:
                when = time.strftime('%Y-%m-%d', time.gmtime(c['updatedAt'])) if c['updatedAt'] else '-'
                print('  %-28s local_type=%-3s kind=%-8s 地区=%-3s 服务类型=%-4s 更新=%-11s 未识别 %d 个'
                      % (c['username'], c['localType'], c['kind'], c['region'] or '-',
                         c['bizType'] if c['bizType'] is not None else '-', when,
                         len(c['unrecognized'])))
            for f in failures:
                print('解不动: id=%s %s' % (f['id'], f['error']), file=sys.stderr)
        return 0

    rooms = []
    failures = []
    for rid, username, owner, blob in rows:
        try:
            decoded = decode_room(bytes(blob))
        except Exception as exc:
            # **解不动就说解不动**，不返回空成员表冒充"这个群没人"
            failures.append({'id': rid, 'username': username, 'error': str(exc)[:80]})
            continue
        rooms.append({'id': rid, 'username': username, 'owner': owner,
                      'memberCount': len(decoded['members']),
                      'members': decoded['members'],
                      'statusBit11Ids': decoded['statusBit11Ids'],
                      'unrecognized': decoded['unrecognized']})

    if args.json:
        print(json.dumps({'success': True, 'action': 'contact-schema', 'rooms': rooms,
                          'failures': failures, 'readsLocalData': True,
                          'invokesAI': False, 'sendsNothing': True}, ensure_ascii=False))
    else:
        print('看了 %d 个群；解不动 %d 个' % (len(rooms), len(failures)))
        for room in rooms:
            print('\n群 %s（id=%s）成员 %d 个；未识别字段 %s' % (
                room['username'], room['id'], room['memberCount'],
                ','.join(sorted(room['unrecognized'])) or '无'))
            for m in room['members'][:5]:
                print('   %-28s %-12s status=%-8s inviter=%s' % (
                    m.get('userName'), m.get('displayName'), m.get('status'), m.get('inviter')))
            if room['statusBit11Ids']:
                print('   #5（status 位 11 的成员）: %s'
                      % ', '.join(x or '?' for x in room['statusBit11Ids'][:5]))
        for f in failures:
            print('解不动: id=%s %s' % (f['id'], f['error']), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
