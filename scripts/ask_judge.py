"""承诺判定（ask 的判断层）：规则给证据，Jev 做闸门，判不了就弃权。

设计对应三件事：

1. **核心隔离**（hexagonal）：判据（哪些话算交付、哪些算催）留在本文件；
   模型只是 `judge` 适配器，换掉它不改编据。没配 Jev 时全链路照常工作。
2. **弃权是一等公民**：三种出口 `delivered / chased / unknown`，外加
   `insufficient`——证据在中间地带时明确说"判不了"，而不是挑一个最像的。
3. **只在拿不准时花钱**：规则强命中直接返回，不调模型。Jev 的计费单位是 state，
   一次调用约 $0.00001，但没必要为确定的事付费。

与 v1（当前 `scripts/promises.py` 的 `classify`）的差别只有一处：
v1 把"字面命中"当成"语义命中"（`什么时候` 当催、`安排了` 当交付），v2 对这些弱
模式再过一道判断闸门。
"""
from __future__ import annotations

import sys
import pathlib

SCRIPTS = pathlib.Path(__file__).resolve().parents[3] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# 强证据：这些话本身就说明"我已经交付了"
STRONG_DELIVER = ('发了', '发你了', '发您了', '已发', '寄了', '已上传', '传了',
                  '做好了', '弄好了', '整理好了', '搞定了', '回复了', '处理好了',
                  '重发一遍', '已经发到', '发到群里')
# 弱证据：字面像交付，但可能不是在说这件事（订会议室、安排日程）
WEAK_DELIVER = ('安排了', '联系了', '确认了')

# 强催：明确在要东西
STRONG_CHASE = ('发了吗', '寄了吗', '好了吗', '弄好了吗', '还没', '催一下',
                '怎么还没', '在等', '客户在等')
# 弱催：`什么时候` 是约时间，不是催交付
WEAK_CHASE = ('什么时候',)

ACK = ('收到', '好的', '好滴', '谢谢', '多谢', 'ok', 'OK', '嗯嗯', '好嘞')


def _match_any(text, pats):
    return any(p in text for p in pats)


def _is_ack(text):
    return text.strip().startswith(ACK)


def classify_v2(messages, judge=None, accept=0.65, reject=0.35):
    """返回 (state, evidence_text, verdict, path)。

    messages: [(is_me, text)]
    state:    delivered / chased / unknown
    verdict:  answered / insufficient
    path:     rule-strong / rule-weak+judge / rule-weak-rejected / no-evidence
    """
    deliver_at = None
    deliver_strength = None
    for i, (is_me, text) in enumerate(messages):
        if is_me and _match_any(text, STRONG_DELIVER):
            deliver_at, deliver_strength = i, 'strong'
            break
        if is_me and _match_any(text, WEAK_DELIVER) and deliver_at is None:
            deliver_at, deliver_strength = i, 'weak'
            break

    if deliver_at is not None:
        text = messages[deliver_at][1]
        if deliver_strength == 'strong':
            # 已发之后对方说收到 ⇒ 确认收到；否则就是"我声称已发"
            later = [m for m in messages[deliver_at + 1:] if not m[0] and _is_ack(m[1])]
            if later:
                return 'acked', later[0][1], 'answered', 'rule-strong'
            return 'delivered', text, 'answered', 'rule-strong'
        # 弱交付证据：问一句"这能算已交付吗"
        score = _ask(judge, '这句话能证明"我已经把东西交给了对方"吗？', text)
        if score is None:
            return 'unknown', None, 'insufficient', 'rule-weak-no-judge'
        if score >= accept:
            return 'delivered', text, 'answered', 'rule-weak+judge'
        if score <= reject:
            return 'unknown', None, 'answered', 'rule-weak-rejected'
        return 'unknown', None, 'insufficient', 'rule-weak-mid'

    chase = None
    for is_me, text in messages:
        if is_me:
            continue
        if _match_any(text, STRONG_CHASE):
            return 'chased', text, 'answered', 'rule-strong'
        if _match_any(text, WEAK_CHASE) and chase is None:
            chase = text

    if chase is not None:
        score = _ask(judge, '这句话是在催我交付某个东西吗？', chase)
        if score is None:
            return 'unknown', None, 'insufficient', 'rule-weak-no-judge'
        if score >= accept:
            return 'chased', chase, 'answered', 'rule-weak+judge'
        if score <= reject:
            return 'unknown', None, 'answered', 'rule-weak-rejected'
        return 'unknown', None, 'insufficient', 'rule-weak-mid'

    return 'unknown', None, 'answered', 'no-evidence'


def _ask(judge, instruction, text):
    """问一次判断模型，返回 [0,1] 的置信度；没有 judge 或出错时返回 None。"""
    if judge is None:
        return None
    try:
        return judge(instruction, text)
    except Exception:
        return None


def make_jev_judge():
    """造一个 judge 适配器：只做判断的 Jev。没配 key 时返回 None（全链路降级）。"""
    try:
        import jev_client as jc
        client = jc.create_client()
    except Exception:
        return None

    def judge(instruction, text):
        answers, _usage = client.decide(
            state=text,
            questions={'q': {
                'type': 'noul',
                'instructions': instruction,
                'criteria': {'true': '是', 'false': '不是'},
            }},
        )
        return float(answers['q']['noul'])

    return judge


if __name__ == '__main__':
    demo = [(False, '明天什么时候开会？'), (True, '我安排了明天的会议室')]
    print('无 judge:', classify_v2(demo))
    j = make_jev_judge()
    print('有 judge:', classify_v2(demo, judge=j) if j else '（本机未配置 Jev）')
