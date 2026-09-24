#!/usr/bin/env python3
"""起草回复 —— **判断在前、起草在后、绝不发送**。

这套形状借自 `github.com/jev-chat/jev-chat-jarvis`（一个 Android 助手：无障碍读屏 →
判断意图/危险 → 起草 3 条候选 → 只填输入框、绝不发送）。**两处它没做好、这里做对了**：

1. 它那边「危险等级」是**纯 UI**——只驱动一块颜色徽章，不拒绝起草、不降级、不阻断
   （全仓库对 `dangerLevel` 的消费只有 `OverlayController.kt:401-405` 一处展示）。
   这里的闸门**真的会拒绝**：涉钱或风险 ≥ 7 就**不起草**，改成说清风险与该先确认什么。
2. 它那边判断结果**没进起草提示词**（`ReplyClient.draft(snapshot, relationship, ctx)` 签名里
   没有 `Analysis`，判断只喂给了排序题）。这里**注入**：意图/需求/动作/该不该给实质，
   连同风险档一起写进起草提示词。

它**不需要**的东西这里也不需要——反过来：它是**没有数据访问**才去读屏，
我们直接读本机数据库。这不是抄近路，是这套东西在 Windows 上本来就更短。

判断用决策模型（Jev，D-031：判断交给决策模型），起草用生成模型（DeepSeek）。
**三次调用**：判断一次、起草一次、给候选排序一次。

## 两条输入路径（脱敏逼出来的，不是设计洁癖）

`privacyGate` 在 TS 侧，而且它打的是**工具返回值**——管不到"Python 脚本往外发了什么"。
Python 侧没有任何脱敏实现（全 `scripts/` 只有两处无关的位掩码），所以：

- `--talker <名字>`：**脚本自己读库**，不做遮罩。这是命令行路径，与 `reply_debt.py` 的
  做法一致——用户显式运行、`--dry-run` 里说清要发多少字符给哪两个模型。
- `--stdin`：从标准输入收一段**已经遮罩好的**对话（助手工具路径，由 TS 侧逐条过
  `maskMessageBody`）。**不在 Python 里再实现一份脱敏**——同一件事两处实现，早晚有一处会漏。

判断、起草、闸门这三段只有一份实现，两条路只差"输入怎么来"。

用法：

    python scripts/draft_reply.py --talker 老王 --dry-run
    python scripts/draft_reply.py --talker 老王 --yes --json
    echo '{"name":"老王","lines":["[09-24 10:00] 对方：文件发我"]}' | python scripts/draft_reply.py --stdin --yes --json

**它不发送任何东西**，也不碰微信窗口。候选只是文本，回不回、怎么回由人决定。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import call_deepseek, get_api_key, load_config, decrypt_lock   # noqa: E402
from jev_client import JevError, create_client                            # noqa: E402
from reply_debt import format_line, TRANSCRIPT_SIZE, TYPE_LABELS, MAX_MSG_CHARS  # noqa: E402

import nt_decrypt                                                          # noqa: E402

TZ = timezone(timedelta(hours=8))
DEFAULT_COUNT = 3
MAX_COUNT = 5
DRAFT_MAX_CHARS = 60
RISK_REFUSE_AT = 7          # 风险档 ≥ 这个数就不起草（0-9 十档，见 RISK_CRITERIA）
NOUL_TRUE = 0.5             # noul 是**概率**不是布尔（把 0.31 当 True 会让每问都读成"是"）


# ---------------------------------------------------------------- 判断：七道题
#
# 判据用英文、语料保留中文原文——这是参考实现的第一手口径（它们的 tools/jev/TASK.md:39：
# 「Jev 主训练语言是英文，中文效果明显差」），它们的题目集也是据此从中文改成英文的。
#
# **本仓库既有的 `reply_debt.py` 用的是中文判据**，这是同一个模型上的两套口径，
# 我没做过 A/B（记在 D-047 的"已知边界"里）。

INTENT_CRITERIA = {
    'confirm_you_care': 'They want to see that I still care or still notice them - they are testing that. '
                        'If they mainly want a new deliverable or a yes on a time, do not use this.',
    'vent_anger': 'They are letting off steam or attacking. They want to be heard, not fixed. '
                  'Ending the relationship, telling me not to reply, or deleting me belongs here.',
    'request_action': 'They want a concrete thing done: send the file, pay, book it, fix it, name a time.',
    'seek_explanation': 'They want to know why something happened, or why it did not.',
    'casual_chat': 'Just chatting or sharing; no request and no complaint.',
    'close_topic': 'Winding the topic down - "ok", "got it", "fine". '
                   "Not a breakup, not 'don't contact me', not sarcastic 'I am used to it'.",
}

NEED_CRITERIA = {
    'apology': 'An apology for a specific thing, said plainly.',
    'action': 'Something actually done, or a concrete next step with a time.',
    'explanation': 'The reason why, or the missing fact.',
    'care': 'Attention and warmth; they want to feel remembered. A sarcastic "I am used to it" belongs here.',
    'nothing': 'They are genuinely satisfied and the topic is closed. '
               'A sarcastic remark or an unanswered complaint is NOT satisfaction - do not choose nothing.',
}

ACTION_CRITERIA = {
    'check_history': 'My reply needs a fact I do not have in this excerpt - read the earlier chat first.',
    'apologize': 'Acknowledge a specific fault and apologize. '
                 'Not for an unnamed forgotten thing when I should first find out what it was.',
    'give_commitment': 'Commit to a specific time or deliverable I can actually keep.',
    'explain': 'Explain what happened or why, without deflecting.',
    'acknowledge': 'Show I heard it and that it matters, without promising anything.',
    'say_less': 'Keep it short or add nothing. Extra words would over-explain, reopen a closed topic, '
                'or pour fuel on an ultimatum that told me not to talk.',
    'make_plan': 'Turn it into a concrete plan or timeline together.',
}

# 十档，每档写**具体情景**（不写抽象程度，官方要求）。语义借自参考实现
# `JevQuestions.kt:87-98`，但把"分手/上报领导"那套亲密关系场景换成我们的（工作/家庭/客户）。
RISK_CRITERIA = [
    'Light chat or joking; nothing is at stake and a clumsy reply would not matter.',
    'A mild tease or a small reminder that is easy to laugh off; a clumsy reply would only feel slightly awkward.',
    'A mild complaint or "please remember next time" said without heat; they still send warm or practical follow-ups.',
    'Noticeable unhappiness; they mention being forgotten, ignored, or kept waiting, but still give me a chance.',
    'Sarcasm, cold short replies, or "you better"; they are testing me, and a sloppy or fake-confident reply escalates.',
    'Openly upset; they say I do not listen or do not care; they expect a real response, not a joke.',
    'Clearly angry and blaming me; a wrong reply turns this into a fight.',
    'Last-chance warning: they will not cover for me any more, or will take it to my boss or the client, '
    'or say trust is almost gone.',
    'An ultimatum is already on the table even if a practical next step is also named: cancel the contract, '
    'report it tonight, stop working together if this is missed.',
    'Active rupture or real-world exposure: it is over, they told me not to reply, or the money or the legal '
    'consequence is already in motion.',
]

RISK_WORDS = [(8, '很危险'), (6, '偏危险'), (3, '留神'), (0, '安全')]


def build_questions():
    """一次请求问全（参考实现的实测：多问几道几乎不加成本）。

    两条设计约束来自参考实现踩过的坑（`tools/jev/TASK.md:35` 记录了它们两题打架的实测数字）：

    1. **题目之间不许互相矛盾**：`should_reply` 只问"该不该给**实质内容**"，
       `action` 那题显式写 `Do not decide whether to send a message immediately`，
       否则会出现"该先翻记录 0.60"和"该马上答 0.77"同时成立的矛盾。
    2. `money` 与 `commitment` 逐字复用 `reply_debt.py` 的中文原文——那是我们自己的措辞，
       且被测试钉住；新题才用英文。
    """
    return {
        'intent': {
            'type': 'choice',
            'instructions': 'What does the other person actually want from me right now? '
                            'Judge the real intent behind their last message, not its literal wording.',
            'criteria': INTENT_CRITERIA,
        },
        'need': {
            'type': 'choice',
            'instructions': 'What would actually settle this for them? Choose one.',
            'criteria': NEED_CRITERIA,
        },
        'action': {
            'type': 'choice',
            'instructions': 'What is the best action for MY next message? '
                            'Do not decide whether to send a message immediately. Ignore timing. '
                            'Choose only the action type. '
                            'If they asked me to recall a specific past message or event and the facts are not '
                            'in this excerpt, choose check_history - do not apologize or invent a plan instead.',
            'criteria': ACTION_CRITERIA,
        },
        'should_reply': {
            'type': 'noul',
            'instructions': 'Should my next message contain SUBSTANTIVE content - an answer, a decision, a fact, '
                            'a commitment - rather than just an acknowledgement or nothing at all? '
                            'Answer FALSE if the thing they want me to recite or prove is not present in this '
                            'snippet (you would be guessing). '
                            "'Then say it' / 'you better' while I am stalling is FALSE. "
                            'Answer FALSE if they already accepted and closed the topic. '
                            'Answer TRUE only if the needed fact, plan, or named fault is already in this snippet '
                            'or is mine to state.',
        },
        'risk': {
            'type': 'score',
            'instructions': 'How close is this to a fight, or to real damage - money, a deadline, the '
                            'relationship? Match the current scene. '
                            'If they genuinely accepted an apology or confirmed a happy plan, score the '
                            'cooled-down present, not an earlier complaint. '
                            'If an ultimatum (cancel the deal, report it, stop working together) is still in '
                            'force and has not been withdrawn, stay in that high bin even if the latest line '
                            'names a specific task.',
            'criteria': RISK_CRITERIA,
        },
        'money': {
            'type': 'noul',
            'instructions': '涉及金钱、交付物或明确期限这类硬承诺吗？',
        },
        'commitment': {
            'type': 'noul',
            'instructions': '我在这段对话里承诺过什么，而到现在还没有下文？',
        },
    }


def risk_word(score):
    for threshold, word in RISK_WORDS:
        if score >= threshold:
            return word
    return '安全'


def to_judgment(answers):
    """把决策模型的答案摊平成一份判断。**纯函数**——测试要能直接喂一组答案进来。"""
    def noul(key):
        value = (answers.get(key) or {}).get('noul')
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    risk = answers.get('risk') or {}
    return {
        'intent': (answers.get('intent') or {}).get('choice'),
        'need': (answers.get('need') or {}).get('choice'),
        'action': (answers.get('action') or {}).get('choice'),
        'shouldReply': noul('should_reply'),
        'risk': risk.get('score'),
        'riskMax': max(len(RISK_CRITERIA) - 1, 1),
        'money': noul('money'),
        'commitment': noul('commitment'),
    }


def judge(client, name, lines):
    """问一次决策模型。返回 (判断, usage)；问不出来就是 (None, None)。"""
    state = '会话：%s\n\n最近的对话（时间从早到晚）：\n%s' % (name, '\n'.join(lines))
    try:
        answers, usage = client.decide(state, build_questions())
    except Exception as error:
        print('[WARN] 判断失败（%s）：%s' % (type(error).__name__, error), file=sys.stderr)
        return None, None
    return to_judgment(answers), usage


# ---------------------------------------------------------------- 闸门
#
# **闸门搭在二值问题上，不搭在 0-9 的分上。** 二值问题只有两个取值，误判成本低得多；
# 0-9 那档虽有校准过的语义，但阈值是连续量，7 和 6.5 的区别没有意义。0-9 用来**解释**。

def evaluate_gate(judgment, gate_commitment=False):
    """返回 (原因, 该怎么办) 或 None（可以起草）。"""
    if (judgment.get('money') or 0) >= NOUL_TRUE:
        return ('这段对话涉及金钱、交付物或明确期限——替你起草等于替你表态',
                ['先想清楚能给什么、什么时候给，再自己写一句',
                 '涉及钱的话，把金额与时间写清楚比写得好听重要'])
    risk = judgment.get('risk')
    if risk is not None and risk >= RISK_REFUSE_AT:
        return ('风险 %d/%d（%s）——这个距离上，一条拟好的回复会让你更容易把话说出去'
                % (risk, judgment.get('riskMax') or 9, risk_word(risk)),
                gate_advice(judgment))
    if gate_commitment and (judgment.get('commitment') or 0) >= NOUL_TRUE:
        return ('这段对话里有你承诺过、还没下文的事，而 `--gate-commitment` 打开了',
                ['先说清那件旧事，再谈新的'])
    return None


def gate_advice(judgment):
    """拒绝时给的不是空话，而是"下一步该干什么"——照最佳动作类型来。"""
    by_action = {
        'check_history': '先去翻一下更早的聊天记录，把事实找齐——现在回什么都是在猜',
        'apologize': '先想清楚到底哪件事错了，认错要具体，不要笼统地说"抱歉"',
        'give_commitment': '只承诺你做得到的，并且带上具体时间',
        'explain': '先把原因想清楚，再决定要不要解释——急着解释容易变成辩解',
        'acknowledge': '可以先只回一句"我看到了"，不必现在给方案',
        'say_less': '这种时候少说是上策——多一句都可能火上浇油',
        'make_plan': '把话说成"我们一起定个时间"，而不是替对方定',
    }
    advice = [by_action.get(judgment.get('action'), '想清楚对方要的是什么，再决定回不回')]
    if (judgment.get('commitment') or 0) >= NOUL_TRUE:
        advice.append('这段里还有你没兑现的承诺，先把那件事说清楚')
    if (judgment.get('shouldReply') or 0) < NOUL_TRUE:
        advice.append('判断是"这条不必给实质内容"——一句收到就够，不用展开')
    return advice


# ---------------------------------------------------------------- 起草

DRAFT_PROMPT = """你是中文即时通讯回复助手。下面是一段对话，以及一段对它的判断。
请给出 {count} 条候选回复，**{count} 条必须策略不同**（例如：一条稳妥承接、一条给具体行动或时间、一条简短低姿态）。

要求：
1. 只输出一个 JSON 数组，恰好 {count} 个字符串，不要任何解释、不要加引号以外的内容
2. 每条不超过 {max_chars} 字，口语、自然，像真人在聊天软件里随手发的
3. 用「我」的语气{style_hint}
4. **不要编造对话里没有的事实**；不知道的就问，或者先不承诺
{constraints}
判断（供参考，**不要原样复述**）：
- 对方真实意图：{intent}
- 对方需要的是：{need}
- 我这条的最佳动作类型：{action}
- 该不该给实质内容：{should_reply}
- 风险档位：{risk}

最近对话（时间从早到晚，「我」是我，「对方」是{name}）：
{convo}
"""


def style_hint(own_lines):
    if not own_lines:
        return ''
    sample = '\n'.join('  %s' % line for line in own_lines[-5:])
    return ('；**参考下面几行我平时说话的口气**（只学口气，不要照抄内容）：\n%s' % sample)


def draft_constraints(judgment):
    lines = []
    if (judgment.get('commitment') or 0) >= NOUL_TRUE:
        lines.append('- **我在这段对话里有承诺还没下文：不要许新的承诺**，可以给时间但不要加码')
    risk = judgment.get('risk') or 0
    if risk >= 4:
        lines.append('- 对方现在带着情绪：**先接住情绪再谈事**，不要辩解、不要讲道理')
    if (judgment.get('shouldReply') or 0) < NOUL_TRUE:
        lines.append('- 判断是"这条不必给实质内容"：候选要短，别硬凑内容')
    return '\n'.join(lines)


def own_voice_lines(lines):
    """从渲染好的对话里挑出"我"说过的话，当语气样本。"""
    return [line.split('：', 1)[1] for line in lines if '] 我：' in line][-5:]


def build_draft_prompt(name, lines, judgment, count):
    return DRAFT_PROMPT.format(
        count=count, max_chars=DRAFT_MAX_CHARS, name=name,
        style_hint=style_hint(own_voice_lines(lines)),
        constraints=draft_constraints(judgment),
        intent=judgment.get('intent'), need=judgment.get('need'),
        action=judgment.get('action'),
        should_reply='是' if (judgment.get('shouldReply') or 0) >= NOUL_TRUE else '否',
        risk='%s/%s（%s）' % (judgment.get('risk'), judgment.get('riskMax'), risk_word(judgment.get('risk') or 0)),
        convo='\n'.join(lines),
    )


def parse_candidates(text, count):
    """从模型输出里抠出候选。**抠不出来就如实说抠不出来**，不拿占位句凑数
    （参考实现会补一句"（稍等，我看下）"兜底，那是它的取舍；这里宁可少给几条）。"""
    body = (text or '').strip()
    if body.startswith('```'):
        body = body.split('\n', 1)[1] if '\n' in body else ''
        if body.endswith('```'):
            body = body[:-3]
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        items = [str(item).strip() for item in parsed if str(item).strip()]
    else:
        # 退一步：按行切（模型偶尔会加前缀或忘记套 JSON）。
        # 去掉行首的 `1.` / `-` / 引号——**行号不是候选内容**，留着会被原样发出去。
        items = []
        for line in body.split('\n'):
            item = line.strip()
            if item.startswith('```'):
                continue
            item = re.sub(r'^\s*(?:\d+[.、)]|[-*])\s*', '', item)   # 行号与项目符号
            item = item.strip().strip('",').strip()
            if item:
                items.append(item)
    return items[:count]


def build_rank_question(candidates):
    """排序题。criteria 的值就是候选的**中文原文**——这是唯一允许 criteria 用中文的地方
    （参考实现的 `tools/jev/TASK.md:53` 也是这么说的：它本身就是待选内容）。"""
    return {
        'best': {
            'type': 'choice',
            'instructions': 'Which candidate reply is the most appropriate next message, given the conversation '
                            "and the other person's true need? Prefer a reply that matches the best action type. "
                            'Penalize dismissive, over-promising, or off-topic replies. '
                            'If the facts are not confirmed, prefer the candidate that asks or looks them up '
                            'instead of faking memory or a vague apology.',
            'criteria': {('candidate_%d' % index): text for index, text in enumerate(candidates, 1)},
        }
    }


def rank(client, name, lines, candidates, judgment):
    """给候选排序。排不出来就按原顺序返回，并如实标注。"""
    if len(candidates) < 2:
        return [{'text': text, 'why': None} for text in candidates], False
    state = ('会话：%s\n\n最近的对话：\n%s\n\n我的最佳动作类型：%s'
             % (name, '\n'.join(lines[-10:]), judgment.get('action')))
    try:
        answers, usage = client.decide(state, build_rank_question(candidates))
    except Exception as error:
        print('[WARN] 排序失败（%s）：%s' % (type(error).__name__, error), file=sys.stderr)
        return [{'text': text, 'why': None} for text in candidates], False

    best = answers.get('best') or {}
    probs = best.get('probabilities') or {}
    order = sorted(range(len(candidates)),
                   key=lambda index: -(probs.get('candidate_%d' % (index + 1)) or 0))
    top = best.get('choice')
    ranked = []
    for index in order:
        why = '判断这最合适' if ('candidate_%d' % (index + 1)) == top else None
        ranked.append({'text': candidates[index], 'why': why})
    return ranked, True


# ---------------------------------------------------------------- 取数

def read_from_db(talker, days):
    """命令行路径：脚本自己读库（不做遮罩，与 reply_debt.py 一致）。"""
    config = load_config()
    db = config.get('ntDbPath', '')
    if not db:
        print('配置里没有 ntDbPath，先运行 weflow-cli init', file=sys.stderr)
        return None, 2
    conns = nt_decrypt.connect_message_shards(
        db, decrypt_lock(config.get('ntKey', '')), config.get('ntSalt', ''),
        decrypt_lock(config.get('favPassphrase') or config.get('decryptKey') or ''))
    if not conns:
        print('无法打开消息数据库，请检查密钥（weflow-cli check）', file=sys.stderr)
        return None, 2
    try:
        # 名字要经**联系人库**解析：`get_sessions` 不接受 name_map，它给的 displayName
        # 往往就是 wxid 本身，拿备注名去匹配会一个都找不到（实测踩过）。
        name_map = {}
        contact_db = nt_decrypt.find_contact_db_path(db)
        if contact_db:
            name_map = nt_decrypt.load_contact_names(
                contact_db, decrypt_lock(config.get('contactKey', '')),
                config.get('contactSalt', ''))

        sessions = nt_decrypt.get_sessions(conns).get('sessions') or []
        match = []
        for session in sessions:
            talker_id = session.get('username') or ''
            resolved = name_map.get(talker_id) or session.get('displayName') or talker_id
            # 备注名优先、模糊匹配；全等（含 wxid 本身）也可
            if talker == resolved or talker == talker_id or talker in resolved:
                match.append((session, resolved))
        if not match:
            print('没找到会话：%s（用 weflow-cli sessions 看有哪些）' % talker, file=sys.stderr)
            return None, 1
        # 同名多个时取最近有动静的那个
        match.sort(key=lambda pair: -(pair[0].get('lastTimestamp') or 0))
        picked, resolved = match[0]
        result = nt_decrypt.get_messages(conns, picked['username'], TRANSCRIPT_SIZE,
                                         name_map=name_map, own_wxid=config.get('wxid', ''))
        messages = result.get('messages') or []
        if not messages:
            print('这个会话最近没有消息', file=sys.stderr)
            return None, 1
        lines = [format_line(m) for m in reversed(messages)]
        return {'name': resolved, 'lines': lines}, 0
    finally:
        for conn in conns:
            conn.close()


# ---------------------------------------------------------------- 主流程

def run(payload, count, config, gate_commitment=False):
    """判断 → 闸门 → 起草 → 排序。**两条输入路径共用这一段。**"""
    name, lines = payload['name'], payload['lines']
    if not lines:
        return {'success': False, 'error': '没有对话内容可用'}

    jev_key = os.environ.get('TYPESAFE_API_KEY') or ''
    if not jev_key:
        from _utils import get_typesafe_key
        jev_key = get_typesafe_key(config)
    client = create_client(jev_key, config=config)
    if client is None:
        return {'success': False, 'error': '没有配置 typesafeApiKey，判断这一步跑不了'
                                           '（weflow-cli config set typesafeApiKey "..."）'}

    api_key = get_api_key(config)
    if not api_key:
        return {'success': False, 'error': '没有配置 deepseekApiKey，起草这一步跑不了'
                                           '（weflow-cli config set deepseekApiKey "..."）'}

    judgment, usage = judge(client, name, lines)
    if judgment is None:
        return {'success': False, 'error': '判断没跑通（看上面的 WARN），这一轮不猜'}
    judgment['usage'] = usage

    refused = evaluate_gate(judgment, gate_commitment)
    if refused:
        reason, advice = refused
        return {'success': True, 'gate': 'refused', 'name': name,
                'reason': reason, 'advice': advice, 'judgment': judgment, 'drafts': []}

    prompt = build_draft_prompt(name, lines, judgment, count)
    try:
        raw = call_deepseek(prompt, api_key, max_tokens=800, timeout=90)
    except Exception as error:
        return {'success': False, 'error': '起草失败：%s' % error}
    candidates = parse_candidates(raw, count)
    if not candidates:
        return {'success': False, 'error': '模型没给出可用的候选（原始输出不是 JSON 数组也不是逐行文本）'}

    drafts, ranked_ok = rank(client, name, lines, candidates, judgment)
    return {'success': True, 'gate': 'draft', 'name': name, 'judgment': judgment,
            'drafts': drafts, 'ranked': ranked_ok,
            'candidateCount': len(candidates), 'askedCount': count}


def main():
    parser = argparse.ArgumentParser(description='起草回复（判断在前，起草在后，绝不发送）')
    parser.add_argument('--talker', help='会话名（命令行路径：脚本自己读库）')
    parser.add_argument('--stdin', action='store_true',
                        help='从标准输入收一段已遮罩的对话（助手工具路径），JSON: {name, lines}')
    parser.add_argument('--count', type=int, default=DEFAULT_COUNT, help='要几条候选（默认 3）')
    parser.add_argument('--gate-commitment', action='store_true',
                        help='承诺未兑现时也拒绝起草（默认不拒，只在提示词里约束）')
    parser.add_argument('--dry-run', action='store_true', help='只报要发多少字符给哪两个模型，不调用')
    parser.add_argument('--yes', action='store_true', help='确认调用两个云端模型')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    if not 1 <= args.count <= MAX_COUNT:
        print('--count 必须在 1-%d 之间' % MAX_COUNT, file=sys.stderr)
        return 2

    if args.stdin:
        try:
            payload = json.loads(sys.stdin.read() or '{}')
        except ValueError as error:
            print('stdin 不是一个合法的 JSON：%s' % error, file=sys.stderr)
            return 2
        payload.setdefault('name', '对方')
        payload.setdefault('lines', [])
    elif args.talker:
        payload, code = read_from_db(args.talker, 7)
        if payload is None:
            return code
    else:
        print('要 --talker <会话名> 或者 --stdin', file=sys.stderr)
        return 2

    config = load_config()
    state_chars = len('\n'.join(payload['lines']))
    if args.dry_run:
        preview = {'success': True, 'dryRun': True, 'action': 'draft-reply',
                   'name': payload['name'], 'messages': len(payload['lines']),
                   'stateChars': state_chars, 'count': args.count,
                   'models': {'judge': 'Jev（决策）', 'draft': 'DeepSeek（生成）'},
                   'calls': '判断 1 次 + 起草 1 次 + 排序 1 次',
                   'readsLocalChat': True, 'invokesAI': True, 'writesNothing': True}
        if args.json:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        else:
            print('会话：%s（%d 条消息，%d 字符会发给判断模型与生成模型）'
                  % (preview['name'], preview['messages'], state_chars))
            print('要问的判断：意图 / 需要 / 动作 / 该不该给实质 / 风险 / 涉钱 / 未兑现承诺')
            print('三次调用：判断（Jev）→ 起草 %d 条（DeepSeek）→ 排序（Jev）' % args.count)
        return 0

    if not args.yes:
        print('会把这段对话发给 Jev 与 DeepSeek 两个云端模型。加 --yes 确认。', file=sys.stderr)
        return 1

    result = run(payload, args.count, config, args.gate_commitment)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not result.get('success'):
        print('没跑通：%s' % result.get('error'), file=sys.stderr)
    elif result['gate'] == 'refused':
        print('**不起草。** %s' % result['reason'])
        for item in result['advice']:
            print('  · %s' % item)
    else:
        judgment = result['judgment']
        print('判断：意图 %s · 需要 %s · 动作 %s · 该给实质 %s · 风险 %s（%s）'
              % (judgment.get('intent'), judgment.get('need'), judgment.get('action'),
                 '是' if (judgment.get('shouldReply') or 0) >= NOUL_TRUE else '否',
                 judgment.get('risk'), risk_word(judgment.get('risk') or 0)))
        print()
        for index, draft in enumerate(result['drafts'], 1):
            print('%d. %s' % (index, draft['text']))
        if not result.get('ranked'):
            print()
            print('（排序那一步没跑通，以上是模型给出的原顺序）')
        print()
        print('这些只是文本，没有发送任何东西；回不回、怎么回由你决定。')
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    sys.exit(main())
