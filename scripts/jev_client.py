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
import urllib.error
import urllib.request

ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
MODELS_ENDPOINT = 'https://api.typesafe.ai/v1/models'
DEFAULT_MODEL = 'jev-latest'
INPUT_USD_PER_TOKEN = 0.042 / 1_000_000  # 输出 token 目前不计费

# 判据文本与 biz_daily.TOPIC_PROMPT 里的"判断规则"逐条对应，好让新旧路径同类比同类。
TOPIC_CRITERIA = {
    'AI': 'AI大模型/Agent/编程/开源/科技产品/工具教程',
    '投资': '股票基金/融资/经济分析/商业市场',
    '新闻': '时事政策/社会热点/娱乐/招聘促销/会议通知',
    '文学': '散文小说/美食旅游/生活随笔/历史文化',
    '学术': '科研论文/期刊文章/实验室成果/学术会议/高校研究',
    '政治': '党政理论/政策解读/领导人讲话/官方评论',
}

# **顺序即语义**：score.criteria 零索引，第一个元素是 0 分。
# 两件事必须分开：判据文本是**给模型看**的（照抄 TOPIC_PROMPT 里对高/中/低的定义，
# 少了这段说明模型就没有判档依据），而落盘的值必须是**裸的三个字**——下游
# generate_ai_report.py:123、enrich_backlinks.py:75,87、generate_html.py:817-820
# 都在做字面量比较。所以有一份名字表，两者按下标一一对应（有测试钉住）。
RELEVANCE_NAMES = ['低', '中', '高']
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


def build_questions(topics, criteria=None, with_paper_probe=True):
    """与 `TOPIC_PROMPT` 等价的类型化版本。

    两个是非题基本白送（问题之间并行、几乎不加延迟）：`is_research_paper`
    能把「学术 vs 新闻」那类混淆拆开看，`needs_followup` 是留给下游的兜底信号。
    """
    table = criteria or TOPIC_CRITERIA
    # 判据必须覆盖传入的每个主题：choice 的 criteria 就是选项集合本身，
    # 少一个选项，模型就永远选不到它。
    choice_criteria = {topic: table.get(topic, topic) for topic in topics}
    questions = {
        'topic': {'type': 'choice',
                  'instructions': '这篇文章属于哪个主题？',
                  'criteria': choice_criteria},
        'relevance': {'type': 'score',
                      'instructions': '对「环境科学研究生，做计算机与环境交叉」的实用价值有多高？',
                      'criteria': list(RELEVANCE_LEVELS)},
    }
    if with_paper_probe:
        questions['is_research_paper'] = {
            'type': 'noul', 'instructions': '这是一篇科研论文或期刊文章吗？'}
    return questions


class JevClient:
    """一次请求问多个问题，共享同一份 state。"""

    def __init__(self, api_key, model=DEFAULT_MODEL, timeout=120):
        if not api_key:
            raise JevError('缺少 TypeSafe API key')
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _post(self, url, payload=None):
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
            raise JevError(f'HTTP {error.code}: {detail}')
        except urllib.error.URLError as error:
            raise JevError(f'连不上 {url}: {error.reason}')

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
            'isResearchPaper': (answers.get('is_research_paper') or {}).get('noul'),
            'usage': usage,
        }


def create_client(api_key='', model=DEFAULT_MODEL, timeout=120, config=None):
    """拿到 key 就返回客户端，没有就返回 None（调用方据此走老路）。"""
    key = resolve_key(api_key, config)
    if not key:
        return None
    return JevClient(key, model=model, timeout=timeout)
