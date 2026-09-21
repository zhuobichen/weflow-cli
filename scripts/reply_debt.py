#!/usr/bin/env python3
"""谁在等我回话 —— 给每个会话判一次"欠账"。

**这和 `extract_todos.py` 不是同一件事。** 那个脚本扫最多 80 个会话、每会话 50 条，
然后把**全部**塞进一次 LLM 调用，问的是"有没有承诺/待办被提到"。它答不了这里要问的
问题：**是谁在等**、等了多久、该不该先回他。把 4000 条消息一次性交给模型，
"是哪个人在等"这件事在输出里根本没有立足之地。

本脚本按会话逐个判：一个会话一份 state，一次请求问 6 个问题，所以每条判断都
知道它属于哪个会话。这是决策模型（而非生成模型）才划算的做法——实测一次判断约 1 秒，
且**多问几个问题几乎不加成本**（2 个问题 0.84s / 12 个问题 0.91s），
所以 6 个问题基本是白送的。

用法：

    python scripts/reply_debt.py                 # 最近 14 天有动静的会话
    python scripts/reply_debt.py --days 30 --limit 40
    python scripts/reply_debt.py --json

**它不是事实，是提示。** 判断来自一个决策模型，会有判错的时候，尤其是反讽、玩笑
和"对方只是随口一说"。输出里带着概率，就是为了让人自己决定信到哪一档；
`--min-prob` 可以把它当阈值用。**没有金标准校准过**，这一点不要忘。
"""
import argparse
import html
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import load_config, decrypt_lock          # noqa: E402
from jev_client import JevError, create_client        # noqa: E402

import nt_decrypt                                      # noqa: E402

TZ = timezone(timedelta(hours=8))
WORKERS = 6          # 与服务保持克制：Jev 新到会返 529（已实测）
MAX_MSG_CHARS = 120  # 每条消息进 state 前的截断

# 消息类型标签。**这不是装饰**：非文本消息在 message_content 里是空的，不给标签
# 的话 state 里就只剩一行"对方："后面什么都没有——模型是在空白上给分。
# 完整的类型处理在 export_chat_html.format_message 那条 if/elif 里，这里只取常见的几种。
TYPE_LABELS = {1: '文本', 3: '图片', 34: '语音', 42: '名片', 43: '视频',
               47: '表情', 48: '位置', 49: '链接/文件/小程序',
               10000: '系统消息', 10002: '系统消息'}
SUBSTANTIVE_CHARS = 5  # 少于这个字数算不上"实质发言"，只用来算证据强度
TRANSCRIPT_SIZE = 30 # 每个会话取最近多少条

# 会话类型：用来把"客服/推销/通知"从欠账里摘出去——它们也在"等回复"，
# 但那不是人情债，混进来会把这份榜单变成噪音。
KINDS = {
    '闲聊': '寒暄、闲聊、朋友间随口聊',
    '工作': '工作事务、协作、交付',
    '家庭': '家人、亲戚',
    '客户': '客户、合作方、外部对接',
    '群聊': '群里的讨论',
    '服务': '客服、推销、验证码、公众号推送、系统通知',
}


PAGE_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0 auto; padding: 40px 24px 64px; max-width: 860px;
  font: 15px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  color: #1a1a1a; background: #fafafa; }
h1 { font-size: 21px; margin: 0 0 4px; }
.sub { color: #777; font-size: 13px; margin-bottom: 28px; }
.lede { display: flex; gap: 36px; align-items: baseline; margin: 0 0 28px;
  padding: 20px 24px; background: #fff; border: 1px solid #e6e6e6; border-radius: 10px; }
.lede b { font-size: 30px; font-weight: 650; }
.lede span { color: #666; font-size: 13px; }
table { width: 100%; border-collapse: collapse; background: #fff;
  border: 1px solid #e6e6e6; border-radius: 10px; overflow: hidden; }
th, td { text-align: left; padding: 11px 14px; border-bottom: 1px solid #f0f0f0; }
th { font-size: 12px; font-weight: 600; color: #666; background: #fcfcfc; }
tr:last-child td { border-bottom: 0; }
.num { font-variant-numeric: tabular-nums; }
.thin { color: #b26a00; }
.muted { color: #888; }
.note { margin-top: 26px; font-size: 12.5px; color: #777; }
.note strong { color: #444; }
"""


def render_html(debts, uncertain, noise_count, meta):
    """一份**不含任何聊天内容**的可分享页面。

    这是刻意的约束，不是遗漏：只输出概率、天数、类别与证据量，所以它可以给别人看
    而不泄露对话。名字要转义——联系人的显示名是外部数据，里面完全可能有 `<`。
    """
    def esc(value):
        return html.escape(str(value if value is not None else ''), quote=True)

    def row(item, dim=False):
        proof = item.get('evidence') or {}
        thin = proof.get('theirLastChars', 0) < SUBSTANTIVE_CHARS
        cls = ' class="muted"' if dim else ''
        return (
            '<tr%s><td>%s</td><td class="num">%.1f 天</td><td>%s</td>'
            '<td class="num">%.2f</td><td class="num">%.2f</td>'
            '<td class="num%s">对方末条 %s 字</td></tr>'
            % (cls, esc(item.get('name')), item.get('days') or 0, esc(item.get('kind') or '?'),
               item.get('waiting') or 0, item.get('urgencyScore') or 0,
               ' thin' if thin else '', proof.get('theirLastChars', '?')))

    longest = max((item.get('days') or 0 for item in debts), default=0)
    kinds = {}
    for item in debts:
        key = item.get('kind') or '?'
        kinds[key] = kinds.get(key, 0) + 1
    lede = [
        '<div class="lede"><div><b>%d</b> <span>个会话在等我回话</span></div>'
        '<div><b>%.1f</b> <span>最久等了（天）</span></div>'
        '<div><b>%d</b> <span>个拿不太准</span></div></div>'
        % (len(debts), longest, len(uncertain)),
    ]
    header = ('<tr><th>会话</th><th>等了</th><th>类别</th>'
              '<th>在等我</th><th>紧急</th><th>证据</th></tr>')

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<title>谁在等我回话</title><style>%s</style></head><body>' % PAGE_CSS,
             '<h1>谁在等我回话</h1>',
             '<div class="sub">最近 %s 天有动静的会话 · 生成于 %s</div>'
             % (esc(meta.get('days')), esc(meta.get('generatedAt')))]
    parts.extend(lede)
    if debts:
        parts.append('<table>%s%s</table>' % (header, ''.join(row(i) for i in debts)))
    else:
        parts.append('<p class="muted">没有判定为欠账的会话（阈值 %.2f）。</p>'
                     % (meta.get('minProb') or 0))
    if uncertain:
        parts.append('<h2 style="font-size:15px;margin:30px 0 10px;">拿不太准</h2>')
        parts.append('<table>%s%s</table>'
                     % (header, ''.join(row(i, dim=True) for i in uncertain)))
    note = ['<div class="note">',
            '<strong>这不是事实，是提示。</strong>判断来自一个决策模型，没有金标准校准过；'
            '页面里的概率就是让你自己决定信到哪一档；'
            '同一批数据两次跑的分数会有出入，阈值附近的差别不必细究。<br>',
            '「证据」是对方最后一条消息的字数——少于 %d 字的那几条，'
            '分数建立在很薄的证据上，不足为凭。<br>' % SUBSTANTIVE_CHARS]
    if noise_count:
        note.append('另有 %d 个判定为客服/推销/通知类，未计入。' % noise_count)
    note.append('<br><strong>本页不含任何聊天内容</strong>——只有概率、天数与计数，'
                '所以它可以被分享。')
    note.append('</div></body></html>')
    parts.extend(note)
    return ''.join(parts)


def build_questions():
    return {
        'waiting': {'type': 'noul',
                    'instructions': '这段对话停在了对方在等我回应的位置吗？'
                                    '也就是最后是对方发来的、并且内容需要我答复，'
                                    '而不是我刚回过他或他只是随口一说。'},
        'urgency': {'type': 'score',
                    'instructions': '这件事有多急着回？',
                    'criteria': ['不急，回不回都行',
                                 '一般，这几天回一下',
                                 '该回了，再拖不合适',
                                 '拖不得了，对方在等我给准信']},
        'commitment': {'type': 'noul',
                       'instructions': '我在这段对话里承诺过什么，而到现在还没有下文？'},
        'money': {'type': 'noul',
                  'instructions': '涉及金钱、交付物或明确期限这类硬承诺吗？'},
        'kind': {'type': 'choice',
                 'instructions': '这段对话属于哪一类？',
                 'criteria': KINDS},
    }


def build_state(name, is_group, lines):
    who = '群聊' if is_group else '单聊'
    return ('会话：%s（%s）\n\n最近的对话（时间从早到晚）：\n%s'
            % (name, who, '\n'.join(lines)))


def format_line(message):
    when = datetime.fromtimestamp(message.get('createTime') or 0, tz=TZ).strftime('%m-%d %H:%M')
    speaker = '我' if message.get('isSend') else (message.get('senderDisplay')
                                                or message.get('senderUsername') or '对方')
    text = (message.get('parsedContent') or message.get('content') or '').strip()
    text = text.replace(chr(10), ' ')[:MAX_MSG_CHARS]
    if not text:
        label = TYPE_LABELS.get(message.get('localType'))
        text = '[%s]' % label if label else '[非文本 localType=%s]' % message.get('localType')
    return '[%s] %s：%s' % (when, speaker, text)


def evidence(messages):
    """这条判断建立在多少证据上。

    "在等我 0.69" 如果是靠对方最后那条 **2 个字**得出的，它和靠一段完整说明得出的
    0.69 完全不是一回事。模型看不到这个区别（它只看到文本），所以由输出告诉人。
    """
    theirs = [m for m in messages if not m.get('isSend')]
    last = theirs[-1] if theirs else {}
    last_text = (last.get('parsedContent') or last.get('content') or '').strip()
    substantive = sum(
        1 for m in theirs
        if len((m.get('parsedContent') or m.get('content') or '').strip()) >= SUBSTANTIVE_CHARS)
    return {'theirLastChars': len(last_text), 'theirSubstantive': substantive,
            'theirCount': len(theirs)}


def self_check(verdict, lines):
    """判定说"对方在等我"，可最后一条明明是我发的——两者必有一个错。

    这是这份输出里唯一能**机械证伪**的一类判断（"他是不是在等我"没法自动核验），
    所以宁可啰嗦也要报出来：一个自相矛盾的数字不该被当成线索用。
    """
    if not lines or (verdict.get('waiting') or 0) < 0.5:
        return None
    if lines[-1].startswith('[') and '] 我：' in lines[-1]:
        return '最后一条是我发的，却判成"在等我"'
    return None


def decide_one(client, name, is_group, lines):
    """一个会话一次请求。返回 dict，或 None（问不出来）。"""
    from jev_client import score_to_relevance  # 复用同一套零索引映射
    try:
        answers, usage = client.decide(build_state(name, is_group, lines),
                                      build_questions())
    except Exception as error:
        print('    [WARN] %s 判断失败（%s）：%s' % (name, type(error).__name__, error))
        return None

    def noul(key):
        value = (answers.get(key) or {}).get('noul')
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    urgency = answers.get('urgency') or {}
    kind = (answers.get('kind') or {}).get('choice')
    return {
        'name': name,
        'waiting': noul('waiting'),
        'commitment': noul('commitment'),
        'money': noul('money'),
        'urgencyScore': urgency.get('score'),
        'kind': kind if kind in KINDS else None,
        'usage': usage,
    }


def collect_conversations(conns, name_map, own_wxid, days, limit):
    """最近有动静的会话，按最后一条消息倒序。"""
    sessions = nt_decrypt.get_sessions(conns).get('sessions') or []
    cutoff = int((datetime.now(TZ) - timedelta(days=days)).timestamp())
    picked = []
    for session in sessions:
        talker = session.get('username')
        # 字段名是 lastTimestamp（get_sessions 自己就已经解析好了显示名）。
        last = session.get('lastTimestamp') or session.get('last_time') or 0
        if not talker or last < cutoff:
            continue
        picked.append({'talker': talker, 'last': last,
                       'unread': session.get('unreadCount') or 0,
                       # get_sessions 不接受 name_map，所以 displayName 往往就是
                       # wxid 本身；用在联系人库里解析出来的名字优先。
                       'name': name_map.get(talker) or session.get('displayName') or talker})
    picked.sort(key=lambda item: -item['last'])
    return picked[:limit] if limit else picked


def main():
    parser = argparse.ArgumentParser(description='谁在等我回话（用决策模型逐会话判断）')
    parser.add_argument('--days', type=int, default=14, help='只看最近多少天有动静的会话')
    parser.add_argument('--limit', type=int, default=40, help='最多判多少个会话')
    parser.add_argument('--min-prob', type=float, default=0.5,
                        help='waiting 概率低于此值就不算欠账（默认 0.5）')
    parser.add_argument('--html', metavar='PATH',
                        help='额外写一份不含聊天内容的单页 HTML（可分享）')
    parser.add_argument('--dry-run', action='store_true',
                        help='只列出会判哪些会话、要发多少字符，不调用决策模型')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    config = load_config()
    db = config.get('ntDbPath', '')
    if not db:
        print('配置里没有 ntDbPath，先运行 weflow-cli init', file=sys.stderr)
        return 2
    key = decrypt_lock(config.get('ntKey', ''))
    salt = config.get('ntSalt', '')
    passphrase = decrypt_lock(config.get('favPassphrase') or config.get('decryptKey') or '')
    own_wxid = config.get('wxid', '')

    conns = nt_decrypt.connect_message_shards(db, key, salt, passphrase)
    if not conns:
        print('无法打开消息数据库，请检查密钥（weflow-cli check）', file=sys.stderr)
        return 2

    contact_db = nt_decrypt.find_contact_db_path(db)
    name_map = {}
    if contact_db:
        name_map = nt_decrypt.load_contact_names(
            contact_db,
            decrypt_lock(config.get('contactKey', '')),
            config.get('contactSalt', ''))

    try:
        picked = collect_conversations(conns, name_map, own_wxid, args.days, args.limit)
        if not picked:
            print('最近 %d 天没有活跃会话' % args.days)
            return 0
        print('扫描 %d 个最近 %d 天有动静的会话…\n' % (len(picked), args.days))

        # 每个会话的最近若干条消息，一次读完（连接只开一次）
        transcripts = {}
        owed = {}
        proofs = {}
        for item in picked:
            result = nt_decrypt.get_messages(conns, item['talker'], TRANSCRIPT_SIZE,
                                             name_map=name_map, own_wxid=own_wxid)
            messages = result.get('messages') or []
            if not messages:
                continue
            transcripts[item['talker']] = [format_line(m) for m in reversed(messages)]
            proofs[item['talker']] = evidence(list(reversed(messages)))
            # 对方最后一次说话的时间——欠账天数的基准。
            theirs = [m.get('createTime') or 0 for m in messages if not m.get('isSend')]
            owed[item['talker']] = max(theirs) if theirs else 0

        if args.dry_run:
            # 只读本地、零出境。列出要判什么、要发多少字符——不读一遍就报不出这个数，
            # 而读数本来也不出境。真正的判断调用在下面。
            total = sum(len(chr(10).join(lines)) for lines in transcripts.values())
            preview = {'success': True, 'dryRun': True, 'action': 'reply-debt.scan',
                       'days': args.days, 'conversations': len(transcripts),
                       'stateChars': total, 'readsLocalChat': True,
                       'invokesAI': True, 'writesNothing': True}
            if args.json:
                print(json.dumps(preview, ensure_ascii=False))
            else:
                print('预览：将把 %d 个会话、约 %d 字符的聊天正文发给决策模型'
                      % (len(transcripts), total))
                print('      （api.typesafe.ai）。只读，不写任何本地文件。')
            return 0

        # key 检查放在预览**之后**：预览要回答的正是"值不值得跑"，
        # 拿 key 当它的前置条件，等于让人先掏钱再看菜单。
        client = create_client()
        if client is None:
            print('缺少 TypeSafe key。这个命令的全部价值就在那次判断调用上，'
                  '没有 key 就没有可降级的行为：' + chr(10) +
                  '  weflow-cli config set typesafeApiKey "..."', file=sys.stderr)
            return 2

        started = time.time()
        rows = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(decide_one, client, item['name'],
                                   '@chatroom' in item['talker'],
                                   transcripts.get(item['talker'], [])): item
                       for item in picked if transcripts.get(item['talker'])}
            for future in as_completed(futures):
                verdict = future.result()
                if verdict:
                    item = futures[future]
                    verdict['last'] = item['last']
                    verdict['owed_since'] = owed.get(item['talker']) or item['last']
                    verdict['selfCheck'] = self_check(
                        verdict, transcripts.get(item['talker'], []))
                    verdict['evidence'] = proofs.get(item['talker'], {})
                    rows.append(verdict)
        print('判断完成 %d/%d，耗时 %.1fs\n' % (len(rows), len(futures), time.time() - started))
    finally:
        for conn in conns:
            conn.close()

    now = datetime.now(TZ)
    for row in rows:
        # 欠账天数必须从**对方最后一条**算起，而不是会话最后活动时间：
        # 如果我最后回过他，会话时间是"我"的消息时间，算出来永远是 0 天——
        # 那恰好把最该看见的情况（对方说完就没下文了）抹平。对方没发过就是 0。
        owed_since = row.pop('owed_since', None) or row['last']
        row['days'] = round((now - datetime.fromtimestamp(owed_since, tz=TZ)).total_seconds()
                            / 86400, 1)
        # 排序键：概率为主，紧急度为辅；金额与承诺只用来标注，不参与排序，
        # 因为那会让"涉及钱"压过"确实在等我"。
        row['rank'] = (row['waiting'] or 0) * 2 + (row['urgencyScore'] or 0) / 4
    rows.sort(key=lambda item: -item['rank'])

    # 服务号/推销也在"等回复"，但那不是人情债；把它们从榜单里摘掉，
    # 但保留计数说明摘了多少，免得看起来像是没扫到。
    debts = [r for r in rows if (r['waiting'] or 0) >= args.min_prob and r['kind'] != '服务']
    noise = [r for r in rows if r['kind'] == '服务']
    uncertain = [r for r in rows if 0.3 <= (r['waiting'] or 0) < args.min_prob]

    if args.html:
        # 名字是外部数据，转义由 render_html 负责；这里只保证目录存在。
        target = os.path.abspath(args.html)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, 'w', encoding='utf-8') as handle:
            handle.write(render_html(debts, uncertain, len(noise), {
                'days': args.days, 'minProb': args.min_prob,
                'generatedAt': now.strftime('%Y-%m-%d %H:%M'),
            }))
        print('已写出：%s（不含聊天内容，可直接分享）' % target)

    if args.json:
        print(json.dumps({'debts': debts, 'excluded_service': len(noise),
                          'uncertain': uncertain}, ensure_ascii=False, indent=2))
        return 0

    print('=' * 62)
    if not debts:
        print('在等你回话的会话：0 个（阈值 %.2f）' % args.min_prob)
    else:
        longest = max(item['days'] for item in debts)
        kinds = {}
        for item in debts:
            kinds[item['kind']] = kinds.get(item['kind'], 0) + 1
        print('在等你回话：**%d 个会话**，最久的等了 **%.0f 天**'
              % (len(debts), longest))
        print('  构成：' + ' · '.join('%s %d' % (k, v) for k, v in
                                    sorted(kinds.items(), key=lambda kv: -kv[1])))
        hard = [i for i in debts if (i['money'] or 0) >= 0.5 or (i['commitment'] or 0) >= 0.5]
        if hard:
            print('  其中 %d 个涉及明确承诺或涉及钱' % len(hard))
    print('=' * 62)

    for item in debts:
        flags = []
        if (item['commitment'] or 0) >= 0.5:
            flags.append('承诺未兑现')
        if (item['money'] or 0) >= 0.5:
            flags.append('涉及钱/交付')
        proof = item.get('evidence') or {}
        print(chr(10) + '%-22s 等了 %4.1f 天   类别 %s   在等我 %.2f   紧急 %.2f'
              % (item['name'][:22], item['days'], item['kind'] or '?',
                 item['waiting'] or 0, item['urgencyScore'] or 0))
        thin = proof.get('theirLastChars', 0) < SUBSTANTIVE_CHARS
        print('    证据：对方末条 %s 字 · 对方实质发言 %s 条%s'
              % (proof.get('theirLastChars', '?'), proof.get('theirSubstantive', '?'),
                 '   ← 证据很薄，这个分数不可当结论' if thin else ''))
        if flags:
            print('    ' + ' · '.join(flags))

    contradictory = [r for r in debts + uncertain if r.get('selfCheck')]
    if contradictory:
        print(chr(10) + '-' * 62 + chr(10) +
              '自检报出 %d 条自相矛盾的判断（按定义不该采信）：' % len(contradictory))
        for item in contradictory:
            print('  %-22s 在等我 %.2f  %s'
                  % (item['name'][:22], item['waiting'] or 0, item['selfCheck']))

    if uncertain:
        print('\n%s\n另外 %d 个拿不太准（waiting 在 0.3~%.2f 之间），'
              '可能是玩笑或随口一说：' % ('-' * 62, len(uncertain), args.min_prob))
        for item in uncertain:
            print('  %-22s %.2f  %s' % (item['name'][:22], item['waiting'], item['kind'] or '?'))
    if noise:
        print('\n另有 %d 个判定为客服/推销/通知类，未计入欠账。' % len(noise))
    print('\n（判断由决策模型给出，**没有金标准校准过**，当提示看不当事实用。）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
