#!/usr/bin/env python3
"""Jev（TypeSafe System One）决策客户端 —— **只做判断，不生成文本**。

日报现在把"主题 + 相关度"塞在同一个散文 prompt 里让 LLM 顺手答，再用两条正则抠回来。
实测（60 篇真实中文文章）结果是：`relevance` 全库 2199/2201 都是默认值「中」，
`tags` 全库都是 `[主题名]`，`topic` 靠关键词兜底猜。这个模块把**判断**换成类型化的
决策调用：输入 state，返回带概率的 choice/score/noul，没有自由文本需要解析。

契约不是转述的，来自服务自己的 OpenAPI（`GET https://api.typesafe.ai/openapi.json`）
加上实测：

    POST https://api.typesafe.ai/v1/systemone      Authorization: Bearer <key>
    request  {model, state, questions}
    response {model, answers, usage}

    choice: criteria 是**对象** {选项名: 何时选它}，答案 {type, choice, confidence, probabilities}
    score : criteria 是**有序数组**，位置即分值（零索引），答案多一个 score + legend
    noul  : 不需要 criteria，答案 {type, noul} —— **没有 confidence**

失败取向：**本模块一律抛 `JevError`**（fail-loud），由调用方决定退到哪条老路
（fail-soft）。分类被问不出来，不该让整天的日报崩掉；但客户端自己不许把失败
伪装成一个看起来正常的答案。
"""
import json
import os
import time
import urllib.error
import urllib.request

ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
MODELS_ENDPOINT = 'https://api.typesafe.ai/v1/models'
DEFAULT_MODEL = 'jev-latest'
# 瞬时故障：官方参考实现（AI SDK 的 evaluate）默认对它们重试 2 次，这里同样。
# 实测遇到过 529 `system_overloaded`——服务 2026-09-15 才发布，被打满是常态。
RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 529})
INPUT_USD_PER_TOKEN = 0.042 / 1_000_000  # 输出 token 目前不计费

# 分类法（列表 + 每类的判据）只有一份，在 `_utils` 里。这里直接引用它，
# 所以提示词那条路和决策模型这条路**判的是同一套定义**——回退时不会换标准。
from _utils import TOPIC_CRITERIA, RELEVANCE_NAMES  # noqa: E402  (scripts/ 在 sys.path 上)

# **顺序即语义**：score.criteria 零索引，第一个元素是 0 分。
# 两件事必须分开：判据文本是**给模型看**的（照抄 TOPIC_PROMPT 里对高/中/低的定义，
# 少了这段说明模型就没有判档依据），而落盘的值必须是**裸的三个字**——下游
# generate_ai_report.py:123、enrich_backlinks.py:75,87、generate_html.py:817-820
# 都在做字面量比较。名字表在 `_utils`（词表与 TOPICS 同处），下面这份判据文本
# 按下标与它一一对应（有测试钉住），所以两者都不能重排。
RELEVANCE_LEVELS = [
    '低：信息性阅读（纯新闻/娱乐/文学）',
    '中：有启发性（思路/趋势/跨领域技术）',
    '高：可直接用于科研（新工具/新方法/数据源/代码库）',
]


class JevError(RuntimeError):
    """调用或契约出问题。消息面向人，**不含 API key**。"""


def resolve_key(explicit='', config=None):
    """key 的取值顺序：显式传入 → `TYPESAFE_API_KEY` → 配置里（自动解密）。

    不落盘、不打印。配置那条走 `_utils.decrypt_lock`，所以 `config set` 写的
    机器绑定密文能直接用。
    """
    key = (explicit or os.environ.get('TYPESAFE_API_KEY') or '').strip()
    if key:
        return key
    try:
        import _utils
        return (getattr(_utils, 'get_typesafe_key')(config) or '').strip()
    except Exception:
        # 配置读不到、密文解不开（比如 config.json 是从别的机器拷来的）——
        # 回空串让调用方走老路，而不是把日报打挂。
        return ''


def score_to_relevance(score):
    """把 `score` 期望值映射回「低/中/高」。

    criteria 是零索引的有序量表，所以 `score` 落在 [0, len-1] 区间里。
    用 `int(score + 0.5)` 取最近档而不是 `round()`：Python 的 round 是银行家舍入，
    `round(0.5)` 得 0、`round(1.5)` 得 2，边界会跳档。

    阈值是**暂定**的（没有金标准可校准），所以原始分要一起落盘（`relevanceScore`），
    将来重新校准不用重跑整天的日报。
    """
    if score is None:
        raise JevError('score 答案缺少 score 字段')
    index = int(float(score) + 0.5)
    index = max(0, min(len(RELEVANCE_NAMES) - 1, index))
    return RELEVANCE_NAMES[index]


def build_questions(topics, criteria=None):
    """与 `TOPIC_PROMPT` 等价的类型化版本：一个 choice + 一个 score + 一个 noul。

    `worth_including` 是**回答一个以前没人问过的问题**。日报原来拿 `relevance != '高'`
    当收录门，而那是在用"对读者的实用价值"回答"该不该进今天的日报"——两件事不一样，
    阈值怎么调都调不准，因为问题本身错了。多问一个问题的边际成本近零（实测 2 个问题
    0.84s，12 个问题 0.91s，`state` 才是开销大头），所以这个判断基本是白送的。

    这里刻意不放"探测型"问题（比如"是不是科研论文"）：问完没人读，就是
    `COVER_STATE` 那种"算了就扔"，不如不加。
    """
    table = criteria or TOPIC_CRITERIA
    # 判据必须覆盖传入的每个主题：choice 的 criteria 就是选项集合本身，
    # 少一个选项，模型就永远选不到它。
    choice_criteria = {topic: table.get(topic, topic) for topic in topics}
    return {
        'topic': {'type': 'choice',
                  'instructions': '这篇文章属于哪个主题？',
                  'criteria': choice_criteria},
        'relevance': {'type': 'score',
                      'instructions': '对「环境科学研究生，做计算机与环境交叉」的实用价值有多高？',
                      'criteria': list(RELEVANCE_LEVELS)},
        'worth_including': {
            'type': 'noul',
            'instructions': '这篇文章含有读者今天就能用上的具体内容'
                            '（新工具/新方法/数据源/代码库/可复现的结论），'
                            '而不是仅有信息性的新闻、观点或生活随笔？'},
    }


class JevClient:
    """一次请求问多个问题，共享同一份 state。"""

    def __init__(self, api_key, model=DEFAULT_MODEL, timeout=120):
        if not api_key:
            raise JevError('缺少 TypeSafe API key')
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _post(self, url, payload=None, attempts=3, backoff=0.8):
        """带重试的请求。

        退避故意很短（最坏约 2.4s）：调用方是**逐篇**分类日报的，服务整体过载时
        长退避只会把一整天的日报拖成几十分钟，而那时调用方本来就该退回老路。
        """
        last_error = None
        for attempt in range(attempts):
            try:
                return self._post_once(url, payload)
            except JevError as error:
                if attempt == attempts - 1 or not getattr(error, 'retryable', False):
                    raise
                last_error = error
                time.sleep(backoff * (2 ** attempt))
        raise last_error  # pragma: no cover - 循环内已覆盖所有出口

    def _post_once(self, url, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            url, data=data, method='POST' if data else 'GET',
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {self.api_key}'})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = ''
            try:
                # 截断：别把整个响应体（可能很长）带进日志。
                detail = error.read().decode('utf-8', 'replace')[:400]
            except Exception:
                pass
            if error.code in (401, 403):
                raise JevError(f'鉴权被拒（HTTP {error.code}）。key 可能不对或已过期：{detail}')
            if error.code == 422:
                # 422 说明我理解的契约和服务端不一致，是最值得看的一种失败。
                raise JevError(f'请求被拒（HTTP 422），契约与预期不符：{detail}')
            failure = JevError(f'HTTP {error.code}: {detail}')
            failure.retryable = error.code in RETRY_STATUS
            raise failure
        except urllib.error.URLError as error:
            failure = JevError(f'连不上 {url}: {error.reason}')
            failure.retryable = True   # 连接层失败通常是瞬时的
            raise failure

    def models(self):
        """可用别名。用它可以确认 `--model` 没写错。"""
        return self._post(MODELS_ENDPOINT).get('models', [])

    def decide(self, state, questions):
        """返回 (answers, usage)。state 可以是字符串、JSON 对象或数组。"""
        body = self._post(ENDPOINT, {'model': self.model,
                                     'state': state, 'questions': questions})
        answers = body.get('answers')
        if not isinstance(answers, dict):
            raise JevError(f'响应里没有 answers：{json.dumps(body, ensure_ascii=False)[:300]}')
        return answers, body.get('usage', {}) or {}

    def decide_article(self, title, body, topics, criteria=None, max_chars=4000):
        """给一篇文章定主题与相关度。**问不出来就抛 `JevError`。**

        state 只放生产环境真会给模型的东西（标题 + 正文），不放日期、来源、
        也不放 frontmatter 里的 topic/relevance —— 那些是答案的味道。
        """
        state = f'标题：{title}\n\n正文：\n{(body or "")[:max_chars]}'
        answers, usage = self.decide(state, build_questions(topics, criteria))

        topic_answer = answers.get('topic') or {}
        relevance_answer = answers.get('relevance') or {}
        picked = topic_answer.get('choice')
        if picked not in topics:
            # criteria 就是 topics，正常不该发生。真发生了说明契约变了，
            # 与其把一个陌生主题写进 frontmatter 污染下游，不如报错让调用方回退。
            raise JevError(f'返回的主题不在候选里：{picked!r}')

        score = relevance_answer.get('score')
        return {
            'topic': picked,
            'topicConfidence': topic_answer.get('confidence'),
            'topicProbabilities': topic_answer.get('probabilities'),
            'relevance': score_to_relevance(score),
            'relevanceScore': score,
            # 「该不该收录」的原始概率。落盘而不是在这儿切成布尔：切点是暂定的，
            # 留着原始值，改阈值不用重跑。
            'includeScore': (answers.get('worth_including') or {}).get('noul'),
            'usage': usage,
        }


def create_client(api_key='', model=DEFAULT_MODEL, timeout=120, config=None):
    """拿到 key 就返回客户端，没有就返回 None（调用方据此走老路）。"""
    key = resolve_key(api_key, config)
    if not key:
        return None
    return JevClient(key, model=model, timeout=timeout)
