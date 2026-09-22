#!/usr/bin/env python3
"""会话卡片 + Jev 路由：把"搜我的聊天"变成"先选会话、再精确检索"。

**分工是实测出来的，不是设计出来的**。最初的设想是让 Jev 排序会话（先把每个会话
压成"卡片"再问它哪个相关）。**实测不成立**：

* noul 问"这个会话更值得打开吗" → 候选全落在 0.50~0.51，包括只有一条消息的
  领券群和命中 213 次的会务群挤在同一档；它还漏掉了最该打开的那个。
* 换成 score 问"有多相关"（0/1/2 档）→ 20 个会话全给 1.74~1.76，命中 247 次的
  与命中 8 次的一模一样。**它与命中数毫无关系**。
* 同一批数据里**纯本地按命中数排序**却排得很准。

原因不难理解：模型看不到会话内容，卡片上只有"类型/条数/时间/名称/命中数"，
它对"这个会话里有没有你要找的东西"无从判断，只能给个中庸分。

**所以排序由本地命中数决定**（那是客观信号），**Jev 只做一件它确实做得到的事**：
从问题里切出的候选词中**挑出真正的查询词**。第一版把"个群/发会/议和/哪个"这类
n-gram 碎片一起递过去，它稳定地留下「会议」、否掉其余七个——这是在**有限的选项里
做选择**，正是它的本职（它不会凭空生成词，也不该）。

**检索仍然在本地**。微信自带的 `message_fts.db` 里存着**明文**正文与整数
`session_id`，而那个 `session_id` 就是同库 `name2id` 表的 **rowid**。`LIKE` 扫
6 万行是秒级——`MATCH` 用不了，它的分词器 `MMFtsTokenizer` 不在这里的 SQLCipher 里。

**已知边界**：这条路**只匹配字面词**，不做同义改写。"聊过上线的事"若正文里写的
是"部署"，这里命中不到——那是向量那条路（`search`）该管的，两者互补而非替代。
命中数为 0 时它会直说，而不是假装找到。

**出网边界**：`ask` 只把**候选词与你的问题**发给 Jev（会话排序不走它，所以会话名、
条数、时间都不出网了——比第一版更小）。**消息正文从不外发**。没有 `--yes` 不发送；
`--keyword` 完全不问 Jev，全程不出网。
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import load_config, decrypt_lock  # noqa: E402
from nt_common import derive_database_key  # noqa: E402

CARDS_DIR = os.path.join(os.path.expanduser('~'), '.weflow-cli', 'cards')
CARDS_PATH = os.path.join(CARDS_DIR, 'sessions.json')
MAX_TERMS = 8            # 一次最多试几个查询词：每个词要在 4 张分表上各扫一遍
CJK = re.compile(r'[一-鿿]{2,}')
NOUL_THRESHOLD = 0.5


def _noul(answer):
    """取 noul 的分数。**它是概率浮点，不是布尔**——实测「天气晴朗吗」= 0.98、「在下雪吗」= 0.01。

    写成 `is True` 会让每个问题都读成否，而表面现象是"模型把所有候选都否掉了"：
    一次真实调用里 8 个查询词（含显然正确的「会议」）全被读成否，程序还照着打出了
    "它认为这些词都不是你要找的"。所以返回 `(分数, 是否缺字段)`，让调用方能把
    **"契约变了"和"真的是低分"分开说**，而不是让解析错误伪装成模型判断。
    """
    if not isinstance(answer, dict) or 'noul' not in answer:
        return None, True
    try:
        return float(answer['noul']), False
    except (TypeError, ValueError):
        return None, True


def _open_fts(config):
    from sqlcipher3 import dbapi2 as sqlcipher
    msg_dir = os.path.dirname(os.path.normpath(config.get('ntDbPath', '')))
    path = os.path.join(msg_dir, 'message_fts.db')
    if not os.path.isfile(path):
        raise SystemExit('找不到 message_fts.db：%s' % path)
    passphrase = decrypt_lock(config.get('favPassphrase', ''))
    key, salt = derive_database_key(path, '', '', passphrase)
    conn = sqlcipher.connect(path)
    conn.execute('PRAGMA key = "x\'%s%s\'";' % (key, salt))
    return conn, path


def fts_tables(cursor):
    """消息 FTS 的正文分表。

    **`_aux` 也要排除**：它以数字结尾（`message_fts_v4_aux_0`），只看后缀是不是
    数字会把它当正文表收进来——它没有 `create_time`，且一行对一条消息，收进来会让
    消息数**翻倍**（实测 60267 与 120734 的关系就是这么来的）。
    `_content`/`_idx`/`_data`/`_docsize`/`_config` 是 FTS5 的内部表。
    """
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'message_fts_v4_%'")
    out = []
    for (name,) in cursor.fetchall():
        if '_aux' in name or name.endswith(('_content', '_idx', '_data', '_docsize', '_config')):
            continue
        if name.rsplit('_', 1)[-1].isdigit():
            out.append(name)
    return sorted(out)


def session_usernames(cursor):
    """`session_id` -> username。实测 `session_id` 就是 `name2id` 的 rowid。"""
    cursor.execute('SELECT rowid, username FROM name2id')
    return {rowid: name for rowid, name in cursor.fetchall()}


def classify(username):
    if username.endswith('@chatroom'):
        return '群聊'
    if username.startswith('gh_'):
        return '公众号'
    if username.isdigit() or username in ('newsapp', 'weixin', 'floatbottle', 'filehelper'):
        return '服务号'
    return '单聊'


def display_names(config):
    """会话显示名（备注 > 昵称 > username）。取不到就**退回 username**——名字是装饰性的。

    contact 库的密钥同样由 `favPassphrase` 派生（与 fts 库、订阅号库一条路子），
    不要求 config 里另配 `contactKey`/`contactSalt`——那两个键在这台机器上是空的，
    按 `biz_daily` 的写法会静默拿不到名字。
    """
    try:
        from nt_decrypt import load_contact_names
        msg_dir = os.path.dirname(os.path.normpath(config.get('ntDbPath', '')))
        wxid_dir = os.path.dirname(os.path.dirname(msg_dir))
        contact_db = os.path.join(wxid_dir, 'db_storage', 'contact', 'contact.db')
        if not os.path.isfile(contact_db):
            return {}
        passphrase = decrypt_lock(config.get('favPassphrase', ''))
        # `contactKey`/`contactSalt` 在盘上是**密文**，必须先解密——直接当明文传进去，
        # 一旦有人配了这两个键就会静默拿到一个错密钥（这台机器上是空的，所以走
        # passphrase 那条路看不出来）。这条被 `test/encrypted_config_test.py` 抓到过。
        return_enc = config.get('contactKey', '')
        salt_enc = config.get('contactSalt', '')
        key, salt = derive_database_key(contact_db,
                                       decrypt_lock(return_enc) if return_enc else '',
                                       decrypt_lock(salt_enc) if salt_enc else '',
                                       passphrase)
        return (load_contact_names(contact_db, key, salt) or {}) if key else {}
    except Exception:
        return {}


def build_cards(config, progress=print):
    """只取结构：类型、条数、时间跨度、最近活跃。主题信息留到 ask 时按查询词现算。"""
    conn, path = _open_fts(config)
    c = conn.cursor()
    names = session_usernames(c)
    tables = fts_tables(c)

    agg = {}
    for table in tables:
        try:
            c.execute('SELECT session_id, count(*), min(create_time), max(create_time) '
                      'FROM "%s" GROUP BY session_id' % table)
        except Exception as exc:            # 分表结构变了也不该让整次构建失败
            progress('  [WARN] 读 %s 失败：%s' % (table, exc))
            continue
        for sid, n, lo, hi in c.fetchall():
            cur = agg.get(sid)
            if cur is None:
                agg[sid] = [n, lo, hi]
            else:
                cur[0] += n
                cur[1] = min(cur[1], lo)
                cur[2] = max(cur[2], hi)
    conn.close()

    now = time.time()
    display = display_names(config)
    cards = []
    for sid, (n, lo, hi) in agg.items():
        username = names.get(sid, '')
        cards.append({
            'id': sid,
            'kind': classify(username),
            'label': display.get(username) or username or ('session#%d' % sid),
            'messages': n,
            'span_days': int((hi - lo) / 86400) if hi > lo else 0,
            'last_days_ago': round((now - hi) / 86400, 1),
        })
    cards.sort(key=lambda card: card['last_days_ago'])
    return {'built_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'source': os.path.basename(path),
            'sessions': len(cards), 'messages': sum(c['messages'] for c in cards), 'cards': cards}


# 问句里的疑问词/客套话，不是搜索词。故意短——名单越长越像在给这台机器调参，
# 而且剩下的碎词还有 Jev 那一关可以否掉（见 `build_questions` 的 t* 问题）。
#
# **必须用词，不能用 `set('哪个哪些…')`**：那样得到的是**单字集合**，而候选词是
# 2–3 个字，`gram in _QUESTION_WORDS` 永远为假——过滤看着在那儿，其实一条也没滤掉。
_QUESTION_WORDS = frozenset('''哪个 哪些 什么 怎么 如何 为何 为什么 是不是 有没有 能不能
帮我 找一下 找找 最近 关于 相关 那种 这种 一下 告诉 知道 记得 想想 查查'''.split())


def candidate_terms(question):
    """从问题里取候选查询词。

    **短的优先**（2 字先于 3 字）：中文里真词以双字居多，而滑窗切出的 3 字更可能跨
    词边界（"哪个群/个群在"）。先选 2 字、再把被它包含的 3 字丢掉，于是"会议/通知"
    能留下，而"发会议/议通知"不会挤掉它们。剩下的碎词由 Jev 否掉——它做选择是本职。
    """
    segs = CJK.findall(question)
    grams = []
    for seg in segs:
        for n in (2, 3):
            grams.extend(seg[i:i + n] for i in range(len(seg) - n + 1))
    if not grams and segs:
        grams = segs
    out = []
    for gram in sorted(set(grams), key=lambda g: (len(g), g)):
        # 丢掉**包含**已选词的候选（已选「会议」就不再要「会议通」）。方向别写反：
        # 写成"被已选词包含"等于没滤，碎片照样占名额（这条我一开始就写反了）。
        # 丢掉没有损失——LIKE 是子串匹配，「会议」本来就命中「会议室」。
        if gram in _QUESTION_WORDS or any(chosen in gram for chosen in out):
            continue
        out.append(gram)
        if len(out) >= MAX_TERMS:
            break
    return out


def count_hits(config, terms, progress=None):
    """每个查询词在**每个会话**里命中多少条。这是让 Jev 能判断的唯一主题信号。"""
    conn, _ = _open_fts(config)
    c = conn.cursor()
    tables = fts_tables(c)
    hits = {term: {} for term in terms}
    for term in terms:
        for table in tables:
            try:
                c.execute('SELECT session_id, count(*) FROM "%s" WHERE acontent LIKE ? '
                          'GROUP BY session_id' % table, ('%' + term + '%',))
            except Exception:
                continue
            for sid, n in c.fetchall():
                hits[term][sid] = hits[term].get(sid, 0) + n
    conn.close()
    return hits


def build_questions(terms):
    """每个候选查询词一个 noul：这是 Jev 在这个工具里**唯一**被证明有效的用法。

    第一版还有"每个会话一个 noul + 一个单选自检"，实测两者都没有区分度（见模块
    docstring），而且那个自检在分数并列时会误报成"编号错位"，所以一并去掉了——
    留着一个不产生信息的提问，只会让人以为它在做事。
    """
    return {'t%d' % j: {
        'type': 'noul',
        'instructions': '「%s」是用户在找的那个词吗？'
                        '如果它只是中文滑窗切出来的碎片（比如把"淘宝"切成"去陶饱"），答否。'
                        % term,
    } for j, term in enumerate(terms)}


def pick_terms(client, question, terms):
    """让 Jev 从候选词里挑出真正的查询词。返回 (保留的词, 全部得分, 缺字段数)。"""
    if not terms:
        return [], {}, 0
    answers, _ = client.decide(
        '用户在自己的微信聊天记录里找东西。下面是从他的问题里切出的候选搜索词，'
        '其中有些是切错位置产生的碎片。\n用户的问题：%s' % question,
        build_questions(terms))
    kept, scores, missing = [], {}, 0
    for j, term in enumerate(terms):
        score, absent = _noul(answers.get('t%d' % j))
        missing += int(absent)
        if absent:
            continue
        scores[term] = score
        if score >= NOUL_THRESHOLD:
            kept.append(term)
    return kept, scores, missing


def rank_sessions(cards, hits, terms):
    """按本地命中数排序会话。**排序只看命中数，Jev 的分数不参与——这是量出来的结论。**

    最初的设计是让 Jev 排会话（每张卡一个 noul / score 问题），实测不成立：noul 全落
    0.50~0.51（领券群与会务群同档），score 全落 1.74~1.76（命中 247 与命中 8 一样高），
    而同一批数据按命中数排得很准。原因写在模块 docstring 里。

    所以这个函数**只接 (cards, hits, terms)**——没有任何地方可以塞进一个模型分数。
    测试会连签名一起钉住：想把它改回"让模型排"，得先删掉那条测试，而不是顺手加一个
    参数就能改掉行为。
    """
    scored = [(card, max(hits[t].get(card['id'], 0) for t in terms))
              for card in cards if any(hits[t].get(card['id']) for t in terms)]
    # 次键用 id，保证同分时顺序稳定（否则同一份输入两次跑可能给出不同顺序）。
    scored.sort(key=lambda pair: (-pair[1], pair[0]['id']))
    return [card for card, _ in scored]


def fetch_messages(config, session_ids, terms, per_card=5):
    """在选中的会话里取命中消息。LIKE 扫明文——微信的 MATCH 用不了（分词器不在）。"""
    if not terms:
        return {}
    conn, _ = _open_fts(config)
    c = conn.cursor()
    clause = ' OR '.join(['acontent LIKE ?'] * len(terms))
    params_base = ['%' + t + '%' for t in terms]
    out = {}
    for sid in session_ids:
        rows = []
        for table in fts_tables(c):
            try:
                c.execute('SELECT acontent, create_time FROM "%s" WHERE session_id = ? AND (%s) '
                          'ORDER BY create_time DESC LIMIT ?' % (table, clause),
                          [sid] + params_base + [per_card])
                rows.extend(c.fetchall())
            except Exception:
                continue
        rows.sort(key=lambda r: -r[1])
        out[sid] = rows[:per_card]
    conn.close()
    return out


def _fmt_days(value):
    if value is None:
        return '?'
    return ('%.0f' % value) if value >= 1 else '不到1'


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='会话卡片 + Jev 路由（本地检索）')
    sub = parser.add_subparsers(dest='cmd', required=True)

    b = sub.add_parser('build', help='从本地微信库构建会话卡片（不出网）')
    b.add_argument('--out', default=CARDS_PATH)

    a = sub.add_parser('ask', help='Jev 挑查询词 → 本地按命中数排序 → 取消息')
    a.add_argument('question')
    a.add_argument('--keyword', help='手动指定查询词（逗号分隔）；不给就从问题里取')
    a.add_argument('--per-card', type=int, default=5, help='每个会话最多取几条消息')
    a.add_argument('--dry-run', action='store_true', help='只打印将要发送的请求，不发送')
    a.add_argument('--yes', action='store_true', help='确认把卡片发给 Jev（这是出网）')

    args = parser.parse_args()
    config = load_config()

    if args.cmd == 'build':
        data = build_cards(config)
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print('✓ 卡片索引：%s（%d 个会话，%d 条消息）'
              % (args.out, data['sessions'], data['messages']))
        return

    if not os.path.isfile(CARDS_PATH):
        raise SystemExit('还没有卡片索引，先跑：python scripts/route_cards.py build')
    with open(CARDS_PATH, encoding='utf-8') as fh:
        data = json.load(fh)

    terms = ([t.strip() for t in args.keyword.split(',') if t.strip()] if args.keyword
             else candidate_terms(args.question))
    if not terms:
        raise SystemExit('问题里没有可用的中文查询词；用 --keyword 指定')

    # Jev 只做这一件事：从候选词里挑出真正的查询词。会话排序不走它（实测无区分度）。
    kept, scores, missing = terms, {}, 0
    if args.keyword:
        print('=== 查询词（--keyword 指定，不问 Jev）===')
    else:
        print('=== 第一步：让 Jev 从候选词里挑 ===')
        print('  候选：%s' % '、'.join(terms))
        if args.dry_run:
            print('  （每个词一个问题："「X」是用户在找的那个词吗？"）')
        elif not args.yes:
            print('  需要 --yes 才会发给 Jev（出网，发的是候选词与问题，不含聊天内容）')
            return
        else:
            from jev_client import create_client
            client = create_client(config=config)
            if client is None:
                raise SystemExit('没有 TypeSafe key；改用 --keyword 手动给查询词')
            started = time.time()
            kept, scores, missing = pick_terms(client, args.question, terms)
            if missing:
                # 缺字段是**契约变了**，不是"模型判为否"——混在一起说，会把解析 bug
                # 说成模型判断（这一版之前就把读错字段显示成了"它把词全否了"）。
                print('  [WARN] %d 个问题没有返回 noul 字段——契约不符，'
                      '不要当成"模型判为否"。' % missing)
            print('  保留 %d 个（%.1fs）：%s' % (
                len(kept), time.time() - started,
                '、'.join('%s(%.2f)' % (t, scores[t]) for t in kept) or '（无）'))
            dropped = [t for t in terms if t not in kept]
            if dropped:
                print('  否掉：%s' % '、'.join(
                    '%s(%s)' % (t, '%.2f' % scores[t] if t in scores else '缺') for t in dropped))

    use = kept or terms
    if not kept and not args.keyword:
        print('\n  它认为这些候选词都不是你要找的，改用原始候选继续（结果可能不相关）。')

    hits = count_hits(config, use)
    print('\n=== 第二步：本地按命中数排序（共 %d 个会话）===' % len(data['cards']))
    ranked = rank_sessions(data['cards'], hits, use)
    if not ranked:
        print('  一个会话都没有字面命中。换词再试——这条路只匹配字面词，'
              '同义改写要靠 search 的向量那条路。')
        return
    for card in ranked[:10]:
        print('  #%-5s %-3s %-24s %5d 条  最后%s天前  命中 %s' % (
            card['id'], card['kind'], card['label'][:24], card['messages'],
            _fmt_days(card.get('last_days_ago')),
            '、'.join('%s×%d' % (t, hits[t][card['id']]) for t in use
                      if hits[t].get(card['id']))))

    print('\n=== 第三步：在最好的 %d 个会话里取消息 ===' % min(8, len(ranked)))
    hits_by_card = fetch_messages(config, [c['id'] for c in ranked[:8]], use, args.per_card)
    total = 0
    for card in ranked[:8]:
        rows = hits_by_card.get(card['id']) or []
        total += len(rows)
        print('  #%s %s：%d 条' % (card['id'], card['label'][:22], len(rows)))
        for content, ts in rows:
            print('      [%s] %s' % (time.strftime('%m-%d %H:%M', time.localtime(ts)),
                                      (content or '').replace('\n', ' ')[:60]))
    if not total:
        print('  （一条也没取到——命中数来自 LIKE 统计，取不到通常意味着分表结构变了）')


if __name__ == '__main__':
    main()
