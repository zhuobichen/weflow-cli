#!/usr/bin/env python3
"""
聊天知识卡 — 把一段时间的会话变成 Wiki 的输入。

用法:
  # 先看代价（读本地库、**不调用任何模型**）：几个会话、多少字会出境
  python scripts/chat_notes.py --days 30 --dry-run --json

  # 真的生成（会把每个会话的对话发给 DeepSeek）
  python scripts/chat_notes.py --days 30 --yes

  # 再聚成概念页（那一步早就支持 --source，不用改它）
  python scripts/compile_wiki.py --source output/chat-notes

产出: output/chat-notes/<会话名>.md —— **每个会话一张卡**，覆盖最近 `--days` 天。

## 为什么这么切（而不是新写一套知识库）

`compile_wiki.py` 那条路吃的是"带 frontmatter + `[[wikilink]]` 的 markdown"，它负责聚合概念、
生成概念页、写反向来源。文章线（`output/biz-daily`）就是喂给它 `--source`。所以这里**只做产出卡**，
聚合、概念页、反链全部复用现成的那条路——同一件事不写第二套实现。

三种形状就这么落下来：
- **按会话的知识卡** = 卡本身（`## 摘要/时间线/主题与人物/欠着什么`）；
- **主题汇总** = 卡里 `[[主题]]` 聚出来的概念页；
- **人物/事件时间线** = 卡里 `[[人名]] — 那时候发生了什么` 聚出来的概念页（每条描述就是一处事件）。

## 两条不能松的纪律

1. **`[[…]]` 由本脚本渲染，不由模型自由写**。模型返回结构化列表（主题/人物 + 一句话），
   这里拼成 `[[名字]] — 描述`。否则模型在正文里随手写一个 `[[…]]`，就会变成一个凭空冒出来的
   概念（`compile_wiki` 的 `scan_articles` 收正文里**所有** wikilink）。
2. **只依据给定的对话**。提示词里写死"没提到的不写、时间不许推断"，摘要与时间线照原样收，
   细节缺口就留空——宁可少写，不要补一个看起来合理的事实。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import (CHAT_CARD_PREFIX, SEPARATOR, call_deepseek, decrypt_lock,  # noqa: E402
                    get_api_key, load_config,
                    note_path as _shared_note_path, plain, write_with_frontmatter)
from reply_debt import collect_conversations, format_line  # noqa: E402

import nt_decrypt  # noqa: E402

TZ = timezone(timedelta(hours=8))
OUTPUT_ROOT = 'output/chat-notes'
DEFAULT_DAYS = 30
# 分页的**页大小**。以前这个值就是"每个会话取多少条"的上限（120），而那是错的：
# 实测窗口内 138 个会话共 **8 万条**，而它只送了 **9,010 条（约 19%）**——每个会话
# 无论多大都只看最新 120 条，`--days` 那个窗口因此几乎不起作用（先取 120 条再按天筛，
# 那 120 条必然在窗口内）。现在按 offset **分页取到取完为止**。
NOTE_MESSAGES = 2000
# 单个会话的取数上限——只是个防跑飞的保险，不是常态。为什么敢开这么大：
# **用户的要求是"同一个群聊必须在同一个上下文"**（切块会让模型看不到前因后果），
# 而实测 API 接受 **1,000,006** token 的提示词；单个会话最大（3 万条）约 **78 万 token**。
# 撞到它要**报出来**，不能静默截断。
MAX_MESSAGES_PER_CONVERSATION = 60000
# 每张卡最多出几话题/几人。**原来是 6**——那时每个会话只看 120 条，6 条勉强够；
# 现在正文全进来了（一个群可能 3 万条、几百人），6 个明显是抽样。
PER_CARD_ITEMS = 20
# 输出的 token 上限。**2000 太小了**：正文全进来之后，一个会话要产出的 JSON
# （摘要 + 时间线 + 最多 20 个话题/人物）能到四千多 token——实测 34 个会话里绝大多数
# 是 `finish_reason=length`，JSON 断在半路，而报出来的原因是"模型没返回可用 JSON"，
# 看不出是被截断。实测 16000 被接受。
MAX_OUTPUT_TOKENS = 16000
# 输入的字数预算。实测该模型上下文 **1,048,576 token**，而最容易踩线的是那个 3 万条的群。
# 按保守的 1.5 字符/token 换算，并给输出留出 MAX_OUTPUT_TOKENS，定在下面这个数。
# **超了就从最旧的丢，并报出丢了几条**——不许静默截断。
PROMPT_CHAR_BUDGET = 1450000


def trim_to_budget(lines, budget=PROMPT_CHAR_BUDGET):
    """超预算就从**最旧的**开始丢，返回 `(保留的 lines, 丢掉的条数)`。

    丢最旧的而不是最新的：知识卡要回答的是"最近这段在聊什么"，而丢最新的会让时间线
    断在中间——最近的对话也恰恰是用户最可能关心的。
    """
    total = len('\n'.join(lines))
    if total <= budget:
        return lines, 0
    dropped = 0
    while dropped < len(lines) and total > budget:
        total -= len(lines[dropped]) + 1
        dropped += 1
    return lines[dropped:], dropped
# 每条消息进提示词的字数上限。这是**本功能自己的口径**（知识卡要的是内容，不是起草那种短句），
# 与 `draft_reply.DRAFT_MSG_CHARS=160` 是两件事，别互相套用。
NOTE_MSG_CHARS = 300
MAX_CONVERSATIONS = 40
# 太薄的会话不值得产卡：几句话的对话写出来的"知识卡"只会是"只说了两句话"，
# 而它**照样会占掉一次模型调用、并往概念页里灌一条没有信息量的来源**。
# 借自 WeKnora 那道"内容太少就拒绝调用 LLM"的闸门（`wiki_ingest_batch.go:1286`）。
MIN_MESSAGES_FOR_CARD = 3
MIN_CHARS_FOR_CARD = 60

NOTE_PROMPT = """你在为一个人整理他和某个会话最近 {days} 天的聊天，产出一张**知识卡**。

会话：{name}
材料（时间从早到晚，「我」是我，其他是对方；这是全部材料，**没有别的来源**）：

{convo}

请只依据上面的材料，返回一个 JSON 对象（不要加任何解释、不要用代码块围栏）：
{{
  "summary": "这段对话在谈什么、进展到哪（2-4 句，只写材料里有的）",
  "timeline": [{{"when": "材料里出现的时间，如 09-20 或 上周三", "what": "那件事，一句话"}}],
  "topics":   [{{"name": "话题名（2-8 字，不要带书名号引号）", "desc": "关于这个话题，这段对话说了什么（一句话）"}}],
  "people":   [{{"name": "这段对话里提到的**人**的名字", "desc": "关于这个人，这段对话说了什么（一句话）"}}],
  "owed": "这段对话里谁欠着谁什么、或下次该怎么开口；没有就写空字符串"
}}

要求：
1. **你是编译者，不是作者**。只写材料里有的：没提到的话题/人不要列；时间不许推断，
   材料里没有具体时间的事件就不进 timeline；**材料里相互矛盾的地方照实并列**，不要替它们调和成一句。
2. topics 与 people 各不超过 {per_card} 条，**按重要度排序**；人名只收真实的人（不要把公众号、机构当人）。
3. 每条 desc 都要是**这句话/这段对话**关于它的说法，不要写成百科定义。
4. 全部用中文，不要 markdown 语法（不要 #、*、-）。
   （第 1 条与"编译者不是作者"的措辞借自 Tencent/WeKnora 的 wiki 提示词，
   `third-party/WeKnora/internal/agent/prompts_wiki.go`。）
"""


def note_path(out_root, name):
    """本功能的兜底名（共用实现见 `_utils.note_path`）"""
    return _shared_note_path(out_root, name, fallback='未命名会话')


def parse_note(raw):
    """把模型返回的东西解成结构化卡片。解不出来返回 None（**不猜**）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    # 容忍模型套了代码块或加了几句前言：取第一个 {...} 块
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, dict) or not str(data.get('summary') or '').strip():
        return None

    def items(key, limit):
        out = []
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            # 概念名里也不许带方括号：它要被拼进 `[[…]]`，名字里再有一个就毁了链接
            item_name = plain(item.get('name')).strip().strip('[]')
            desc = plain(item.get('desc')).strip()
            if item_name and desc:
                out.append({'name': item_name, 'desc': desc})
            if len(out) >= limit:
                break
        return out

    timeline = []
    for item in data.get('timeline') or []:
        if isinstance(item, dict) and str(item.get('what') or '').strip():
            timeline.append({'when': plain(item.get('when')).strip(),
                             'what': plain(item['what']).strip()})
    return {
        'summary': plain(data['summary']).strip(),
        'timeline': timeline[:20],
        'topics': items('topics', PER_CARD_ITEMS),
        'people': items('people', PER_CARD_ITEMS),
        'owed': plain(data.get('owed')).strip(),
    }


def render_note(note, name, days, updated, messages):
    """卡片 → (frontmatter, body)。

    frontmatter 的键是**消费者定的**：`compile_wiki.scan_articles` 读 `title`/`source`/`topic`/`tags`。
    body 的 `## AI 摘要` 标题也是它定的（`_extract_summary` 就找这一段）。
    """
    frontmatter = {
        'title': f'{name}',
        'type': 'chat-card',
        'source': name,
        'topic': '聊天',
        'tags': ['聊天', '知识卡'],
        'updated': updated,
        'window': f'最近 {days} 天 / {messages} 条',
    }

    parts = ['# %s\n' % name, '## AI 摘要\n', note['summary'] + '\n']
    if note['timeline']:
        parts.append('## 时间线\n')
        for item in note['timeline']:
            when = ('%s ' % item['when']) if item['when'] else ''
            parts.append('- %s%s' % (when, item['what']))
        parts.append('')
    if note['topics'] or note['people']:
        # **这一节是唯一出现 `[[…]]` 的地方**（见文件头纪律），而且由这里拼、不由模型写。
        # 类型用**小标题**分，不塞进行尾——那行破折号后面的话是要喂给概念页的原料
        # （`compile_wiki` 取的是整行剩余部分），多一个「（话题）」就把它污染了。
        parts.append('## 主题与人物\n')
        for label, key in (('话题', 'topics'), ('人', 'people')):
            if not note[key]:
                continue
            parts.append('### %s\n' % label)
            for item in note[key]:
                parts.append('- [[%s]] %s %s' % (item['name'], SEPARATOR, item['desc']))
            parts.append('')
    if note['owed']:
        parts.append('## 欠着什么\n')
        parts.append(note['owed'] + '\n')
    return frontmatter, '\n'.join(parts)


def build_note_prompt(name, lines, days):
    return NOTE_PROMPT.format(name=name, days=days, convo='\n'.join(lines),
                              per_card=PER_CARD_ITEMS)


def worth_a_card(messages, chars, min_messages=MIN_MESSAGES_FOR_CARD,
                 min_chars=MIN_CHARS_FOR_CARD):
    """这个会话够不够给一张卡——不够就跳过，**并报出来**（不是静默丢弃）。

    两个阈值都是"别浪费一次调用、也别往概念页里灌没信息量的来源"的实用闸门，
    不是"重要/不重要"的判断。跳过的会话名会出现在结果里，方便你决定要不要放宽。
    """
    return messages >= min_messages and chars >= min_chars


def within_window(messages, cutoff):
    """窗口内、且**方向明确**的消息。

    `get_messages` 只能给"最近 N 条"，没有时间区间参数，所以窗口要在这里自己筛。
    方向为 `None` 的（`isSend` 缺失）也丢掉：这一行的标签是「我」还是对方全靠它，
    拿不准的话那一行就只能瞎标——宁可少一条，不要给模型的材料里混一句标错的。
    """
    return [m for m in messages
            if int(m.get('createTime') or 0) >= cutoff and m.get('isSend') is not None]


def fetch_all_messages(conns, talker, name_map, own_wxid):
    """**把一个会话的消息取完**（分页），返回 `(messages, truncated)`。

    `get_messages` 是"最近 N 条 + offset"的形式，所以一页一页往回翻直到取空。
    返回的 `truncated` 为真表示撞到了 `MAX_MESSAGES_PER_CONVERSATION`——那种情况要
    **报出来**，不能装作读全了。

    为什么非要取完：用户的要求是"同一个群聊必须在同一个上下文"。切块省事，但模型
    看不到前因后果，写出来的时间线和人物关系会失真。
    """
    out, offset = [], 0
    while len(out) < MAX_MESSAGES_PER_CONVERSATION:
        page = nt_decrypt.get_messages(
            conns, talker, NOTE_MESSAGES, offset=offset,
            name_map=name_map, own_wxid=own_wxid).get('messages') or []
        if not page:
            break
        out.extend(page)
        offset += len(page)
        if len(page) < NOTE_MESSAGES:
            break
    return out, len(out) >= MAX_MESSAGES_PER_CONVERSATION


def copy_to_vault(out_root, vault_root):
    """把聊天卡拷进 Vault 的 `Sources/Chat/`，**文件名带 `会话-` 前缀**。

    为什么要带前缀：聊天卡的**名字就是会话名**（`白马非马`、`老表亲戚群`），而概念页
    也可能叫同一个名字。两个文件同名时 `[[白马非马]]` 在 Obsidian 里是二义的——
    那会把**已经解析得了的链接弄坏**（实测：概念页里有 30 条来源链接正指向自己）。

    为什么要拷进来：不拷的话，129 张卡（时间线、人物、"欠着什么"）在 Vault 里完全
    看不见，而且概念页那 **1,176 条**来源链接全都指不到东西。人看知识库时只能看到
    "概念"，看不到产生它的会话。
    """
    cards = sorted(Path(out_root).glob('*.md'))
    target_dir = Path(vault_root) / 'Sources' / 'Chat'
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for card in cards:
        if card.name.startswith('README'):
            continue
        target = target_dir / (CHAT_CARD_PREFIX + card.name)
        target.write_bytes(card.read_bytes())
        copied += 1
    return {'copied': copied, 'dir': str(target_dir)}


def collect(out_root, days, limit):
    """读本地库：最近有动静的会话 + 每个会话窗口内的对话。**不调用任何模型。**"""
    config = load_config()
    db = config.get('ntDbPath', '')
    if not db:
        print('配置里没有 ntDbPath，先运行 weflow-cli init', file=sys.stderr)
        return None, 'no-db'
    conns = nt_decrypt.connect_message_shards(
        db, decrypt_lock(config.get('ntKey', '')), config.get('ntSalt', ''),
        # 口令的回退链与 `draft_reply.py` 那条路逐字一致：两处读出不同的库才是怪事
        decrypt_lock(config.get('favPassphrase') or config.get('decryptKey') or ''))
    if not conns:
        print('无法打开消息数据库，请检查密钥（weflow-cli check）', file=sys.stderr)
        return None, 'no-conn'
    try:
        name_map = {}
        contact_db = nt_decrypt.find_contact_db_path(db)
        if contact_db:
            name_map = nt_decrypt.load_contact_names(
                contact_db, decrypt_lock(config.get('contactKey', '')),
                config.get('contactSalt', ''))
        picked = collect_conversations(conns, name_map, config.get('wxid', ''), days, limit)
        cutoff = int((datetime.now(TZ) - timedelta(days=days)).timestamp())
        cards = []
        thin = []
        for item in picked:
            messages, truncated = fetch_all_messages(conns, item['talker'], name_map,
                                                     config.get('wxid', ''))
            in_window = within_window(messages, cutoff)
            if not in_window:
                continue
            lines = [format_line(m, NOTE_MSG_CHARS) for m in reversed(in_window)]
            # 超上下文预算就从最旧的丢（见 `trim_to_budget`），丢了几条要写进卡里
            lines, dropped = trim_to_budget(lines)
            chars = len('\n'.join(lines))
            if not worth_a_card(len(lines), chars):
                thin.append({'name': item['name'], 'messages': len(lines), 'chars': chars})
                continue
            cards.append({'talker': item['talker'], 'name': item['name'],
                          'messages': len(lines), 'chars': chars, 'lines': lines,
                          'truncated': truncated, 'dropped': dropped})
        return {'cards': cards, 'skipped': thin, 'outRoot': out_root}, None
    finally:
        for conn in conns:
            conn.close()


def exit_code(written: int) -> int:
    """**部分成功算成功**（退出码 0），失败清单是数据不是进程状态。

    原来写的是"只要有失败就 exit 1"——于是"3 个会话里 2 张卡写出来了"被上层当成
    **整体失败**，连脚本自己那份写着原因的 JSON 都被丢掉（CLI 只报一句退出码）。
    只有**一张都没写出来**才算失败。
    """
    return 0 if written else 1


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='聊天知识卡（把会话变成 Wiki 的输入；只产文本，不发送）')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS, help='看最近多少天（默认 30）')
    parser.add_argument('--limit', type=int, default=MAX_CONVERSATIONS,
                        help='最多处理几个会话（默认 %d）' % MAX_CONVERSATIONS)
    parser.add_argument('--out', default=OUTPUT_ROOT, help='卡片输出目录')
    parser.add_argument('--dry-run', action='store_true', help='只报要发多少给模型，不调用')
    parser.add_argument('--yes', action='store_true', help='确认调用云端模型')
    parser.add_argument('--vault-root', default='output/wechat-vault',
                        help='Vault 根目录（--vault-copy 用）')
    parser.add_argument('--vault-copy', action='store_true',
                        help='把聊天卡拷进 Vault 的 Sources/Chat（本地，不调模型）')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    if args.days < 1:
        print(json.dumps({'success': False, 'error': '--days 要 ≥ 1'}))
        return 1

    collected, failure = collect(args.out, args.days, args.limit)
    if collected is None:
        if args.json:
            print(json.dumps({'success': False, 'error': failure}))
        return 1
    cards = collected['cards']
    skipped = collected.get('skipped') or []

    if args.dry_run:
        preview = {'success': True, 'dryRun': True, 'action': 'chat-notes',
                   'days': args.days, 'conversations': len(cards),
                   'messages': sum(c['messages'] for c in cards),
                   'stateChars': sum(c['chars'] for c in cards),
                   'skipped': len(skipped),
                   'skippedNames': [s['name'] for s in skipped][:20],
                   'model': 'DeepSeek（生成）', 'calls': len(cards),
                   'readsLocalData': True, 'invokesAI': False, 'writesFiles': True,
                   'note': '每个会话一次调用；卡片写到 %s，之后用 compile_wiki --source 聚合' % args.out}
        if args.json:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        else:
            print('会话 %d 个、消息 %d 条、%d 字符会发给 DeepSeek（每会话一次调用）'
                  % (len(cards), preview['messages'], preview['stateChars']))
            if skipped:
                print('另有 %d 个会话太薄（<%d 条或 <%d 字）不产卡：%s'
                      % (len(skipped), MIN_MESSAGES_FOR_CARD, MIN_CHARS_FOR_CARD,
                         '、'.join(s['name'] for s in skipped[:8])))
            print('卡片写到 %s；之后跑 python scripts/compile_wiki.py --source %s 聚成概念页'
                  % (args.out, args.out))
        return 0

    if not cards:
        if args.json:
            print(json.dumps({'success': False, 'error': '最近 %d 天没有够得上产卡的会话' % args.days,
                              'skipped': len(skipped)}))
        else:
            print('最近 %d 天没有够得上产卡的会话（%d 个太薄）' % (args.days, len(skipped)),
                  file=sys.stderr)
        return 1

    if not args.yes:
        print('会把上面 %d 个会话的 %d 条消息发给 DeepSeek。加 --yes 确认。'
              % (len(cards), sum(c['messages'] for c in cards)), file=sys.stderr)
        return 1

    config = load_config()
    api_key = get_api_key(config)
    if not api_key:
        print('没有配置 deepseekApiKey（weflow-cli config set deepseekApiKey "..."）', file=sys.stderr)
        return 1

    updated = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
    written, failed = [], []
    for card in cards:
        prompt = build_note_prompt(card['name'], card['lines'], args.days)
        try:
            # 2000 而不是 1200：长对话要产出的 JSON（摘要+时间线+话题+人物）中文很吃 token，
            # 被截断的 JSON 解不出来，而失败清单只说「没返回可用 JSON」——查不出是哪种失败。
            raw = call_deepseek(prompt, api_key, max_tokens=MAX_OUTPUT_TOKENS, timeout=600)
        except Exception as error:
            failed.append({'name': card['name'], 'reason': '调用失败：%s' % error})
            continue
        note = parse_note(raw)
        if note is None:
            # **不猜**：模型没按形状返回，就说这张卡没生成，不拿半成品糊上去
            # **带上现场**：末尾一截原文能直接看出是「被截断」还是「答成了散文」——
            # 只写「没返回可用 JSON」的话，只能靠猜（而猜错就会去调错的东西）
            failed.append({'name': card['name'], 'reason': '模型没返回可用 JSON',
                           'rawTail': str(raw)[-200:] if raw else '(空)'})
            continue
        path = note_path(args.out, card['name'])
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter, body = render_note(note, card['name'], args.days, updated, card['messages'])
        write_with_frontmatter(str(path), frontmatter, body)
        written.append({'name': card['name'], 'file': str(path),
                        'topics': len(note['topics']), 'people': len(note['people'])})

    if getattr(args, 'vault_copy', False):
        sync = copy_to_vault(args.out, args.vault_root)
        print('  入 Vault: %d 张卡 → %s' % (sync['copied'], sync['dir']))

    result = {'success': True, 'action': 'chat-notes', 'days': args.days,
              'model': 'DeepSeek（生成）', 'sendsNothing': True, 'writesNothingRemote': True,
              'written': written, 'failed': failed, 'skipped': skipped,
              'conversations': len(cards), 'calls': len(cards)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in written:
            print('✓ %s → %s（话题 %d、人物 %d）'
                  % (item['name'], item['file'], item['topics'], item['people']))
        for item in failed:
            print('✗ %s：%s' % (item['name'], item['reason']), file=sys.stderr)
        print('\n共 %d 张卡；接着跑 python scripts/compile_wiki.py --source %s'
              % (len(written), args.out))
    return exit_code(len(written))


if __name__ == '__main__':
    sys.exit(main())
