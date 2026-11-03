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
import sys
import time
import urllib.error
import urllib.request

# 端点可覆盖：官方 `api.typesafe.ai` 在中国地区不可用，而第三方中转
# （例如 302.AI 的 `api.302ai.cn/typesafeai/v1/systemone`）用的是**同一套
# systemone 协议**，只是 host 不同。写死官方端点等于把这条路堵死。
# 优先级：环境变量 > 配置（judgeEndpoint）> 官方默认。
_DEFAULT_HOST = 'https://api.typesafe.ai'


def _resolve_host():
    host = (os.environ.get('JEV_BASE_URL') or '').strip()
    if not host:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from _utils import load_config
            host = (load_config() or {}).get('judgeEndpoint', '') or ''
        except Exception:
            host = ''
    host = (host or _DEFAULT_HOST).rstrip('/')
    # 允许直接给到 `/systemone` 的全路径
    if host.endswith('/systemone'):
        host = host[: -len('/systemone')]
    return host


_HOST = _resolve_host()
ENDPOINT = _HOST + '/systemone'
MODELS_ENDPOINT = _HOST + '/models'
DEFAULT_MODEL = 'jev-latest'
# 瞬时故障：官方参考实现（AI SDK 的 evaluate）默认对它们重试 2 次，这里同样。
# 实测遇到过 529 `system_overloaded`——服务 2026-09-15 才发布，被打满是常态。
RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 529})
INPUT_USD_PER_TOKEN = 0.042 / 1_000_000  # 输出 token 目前不计费

# 分类法（列表 + 每类的判据）只有一份，在 `_utils` 里。这里直接引用它，
# 所以提示词那条路和决策模型这条路**判的是同一套定义**——回退时不会换标准。
from _utils import TOPIC_CRITERIA, RELEVANCE_NAMES, DEFAULT_JUDGE_LOCAL_URL  # noqa: E402  (scripts/ 在 sys.path 上)

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


def _check_choice(key, question, answer):
    """对 `choice` 答案做硬校验：不合法就抛 `JevError`，**不放行**。

    四条断言（概率的键集 == criteria 的键集、取值都在 [0,1]、和 ≈ 1、`choice`
    就是 argmax）来自 browser-use × TypeSafe 官方示范仓库的 `validate_choice`；
    我按它挑的这四条原样实现，并把**实测**过的真实响应作为基准（`jev-1.13.0`：
    `{"看电影":0.0,"散步":1.0,"睡觉":0.0}` + `choice="散步"`，四条全过）。

    为什么值得在客户端做：契约漂移的失效方式不是报错，而是**选错了但看起来正常**。
    `argmax` 那条尤其关键——我们此前踩过一个同型的坑：把 `noul`（概率浮点）当布尔
    读，每个问题都读成"否"，而输出还把责任推给了模型。
    """
    if question.get('type') != 'choice':
        # noul / score 没有"criteria ↔ probabilities"这层对应关系，不该被这套检查误伤。
        # `decide` 已经按类型过滤，这里再挡一次是为了让这个函数单独调用也安全。
        return
    criteria = question.get('criteria') or {}
    probs = answer.get('probabilities')
    if not isinstance(probs, dict):
        raise JevError('%s：choice 答案里没有 probabilities：%s'
                       % (key, json.dumps(answer, ensure_ascii=False)[:200]))
    if criteria:
        missing = sorted(set(criteria) - set(probs))
        extra = sorted(set(probs) - set(criteria))
        if missing or extra:
            raise JevError('%s：probabilities 的键与 criteria 不一致（缺 %s，多 %s）'
                           % (key, missing, extra))
    out_of_range = {k: v for k, v in probs.items()
                    if not isinstance(v, (int, float)) or isinstance(v, bool)
                    or not 0.0 <= float(v) <= 1.0}
    if out_of_range:
        raise JevError('%s：概率不在 [0,1]：%s' % (key, out_of_range))
    total = sum(float(v) for v in probs.values())
    if probs and abs(total - 1.0) > 0.02:
        raise JevError('%s：概率和是 %.3f，不是 1（差得太多说明契约变了）' % (key, total))
    picked = answer.get('choice')
    if criteria and picked not in criteria:
        raise JevError('%s：choice=%r 不在 criteria 里' % (key, picked))
    if probs:
        top = max(probs, key=lambda k: probs[k])
        if picked != top:
            raise JevError('%s：choice=%r 不是概率的 argmax（%r 才是）——'
                           '这种情况最危险，它看起来是个正常答案'
                           % (key, picked, top))


# ===== OpenRouter（第二条决策模型通路，D-120）=====
#
# **为什么需要它**：原生 `api.typesafe.ai` 在本机所在地区被挡（实测 HTTP 451
# "not available in your region"），于是整层判断静默退化——最近 40 个完整天里
# 0 篇带 relevanceScore。OpenRouter 上 TypeSafe 把同一个决策模型发布成了
# `typesafe/jev-router`，从这里能通（实测 200）。
#
# ⚠️ **地理限制是逐模型的**（实测 2026-10-17）：
#   · `typesafe/jev-router` → 通；
#   · `:free` 的模型 → 通（零成本，但免费额度 50 次/天）；
#   · 付费通用模型（如 `openai/gpt-4o-mini`）→ **HTTP 403 "This model is not available
#     in your region."**，失败在路由的 "Gate Endpoints with Geo Restrictions" 一步。
#   所以换模型时这条路必须重测，不能假定"能调一个就能调所有"。
OPENROUTER_BASE = 'https://openrouter.ai/api/v1'
OPENROUTER_ENDPOINT = OPENROUTER_BASE + '/chat/completions'
OPENROUTER_MODELS_ENDPOINT = OPENROUTER_BASE + '/models'
# 国内端点：这两条是本机实测能通、且都已有 key 的（D-120）。DeepSeek 是默认选择：
# 1.0 秒/篇、key 已在配置里、无需新厂商，成本量级 ¥1/天（500 篇）。
DEEPSEEK_BASE = 'https://api.deepseek.com/v1'
DASHSCOPE_BASE = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
OPENROUTER_DEFAULT_MODEL = 'typesafe/jev-router'
# OpenRouter 的两个归属头（可选，但官方文档建议带；不含任何用户数据）。
OPENROUTER_REFERER = 'https://github.com/zhuobichen/weflow-cli'
OPENROUTER_TITLE = 'weflow-cli'
# 预算给得**大**是有原因的：jev-router 会路由到推理模型（实测 `x-ai/grok-4.7`、
# `z-ai/glm-5.3-flash`），推理 token 会把预算吃光、正文留空。实测 max_tokens=800 时
# 一个免费推理模型 finish_reason=length、content 为空 —— 那不是"模型不会"，是预算给少了。
# 反过来，这个值也是 OpenRouter 预检额度的依据（额度不足会 402 并报"你只能负担 N tokens"）。
OPENROUTER_MAX_TOKENS = 4000


def _check_score(key, question, answer):
    """`score` 答案必须是数字（`score_to_relevance` 会拿它取整索引）。

    没有这一条会怎样：`score` 缺失时 `score_to_relevance` 抛的是 `JevError('缺少 score')`，
    但一个**字符串**分数（模型回 "高"）会一路走到 `int(float('高'))` 抛 `ValueError`——
    调用方的 `except JevError` 接不住，整天日报挂掉。所以在这里挡住。
    """
    if question.get('type') != 'score':
        return
    value = answer.get('score')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevError('%s：score 答案里的 score 不是数字：%r' % (key, value))


def _check_noul(key, question, answer):
    """`noul` 答案必须是 [0,1] 的概率 —— **不是布尔**。

    仓库里踩过同型的坑（D-034 附近）：把 noul 的浮点当布尔读，每个问题都读成"否"，
    而输出还把责任推给模型。所以这里既挡 `true/false`，也挡越界的数。
    """
    if question.get('type') != 'noul':
        return
    value = answer.get('noul')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevError('%s：noul 答案是概率（0~1 的小数），不是布尔：%r' % (key, value))
    if not 0.0 <= float(value) <= 1.0:
        raise JevError('%s：noul 概率不在 [0,1]：%r' % (key, value))


def build_openrouter_prompt(state, questions):
    """把类型化的问题渲染成一段 chat 提示，要求**只输出一个 JSON 对象**。

    形状必须与原生 `systemone` 的 answers 一致，因为下游七个调用方读的是同一套键：
    `choice` → `{probabilities, choice}`、`score` → `{score}`、`noul` → `{noul}`（概率）。
    渲染的是**同一份** `questions`（含 criteria 与判据文本），所以换通路不换判据。
    """
    lines = ['你是判断器。读下面的材料，逐题作答，**只输出一个 JSON 对象**：',
             '不要解释、不要 markdown 代码块、不要在 JSON 前后加任何其它字。', '', '题目（每题都要答，键名照抄）：']
    shape = []
    for name, question in (questions or {}).items():
        kind = (question or {}).get('type')
        instructions = (question or {}).get('instructions') or ''
        if kind == 'choice':
            criteria = (question or {}).get('criteria') or {}
            lines.append('- %s（choice）：%s' % (name, instructions))
            lines.append('  候选（probabilities 的键必须就是这些，且和等于 1）：')
            for option, criterion in criteria.items():
                lines.append('    · %s：%s' % (option, criterion))
            shape.append('"%s": {"probabilities": {%s}, "choice": "<概率最大的候选>"}'
                         % (name, ', '.join('"%s": 0.0' % o for o in criteria)))
        elif kind == 'score':
            criteria = (question or {}).get('criteria') or []
            lines.append('- %s（score）：%s' % (name, instructions))
            for index, criterion in enumerate(criteria):
                lines.append('    %d = %s' % (index, criterion))
            shape.append('"%s": {"score": 0}' % name)
        elif kind == 'noul':
            lines.append('- %s（noul）：%s' % (name, instructions))
            lines.append('    noul 是"该陈述成立"的概率（0~1 的小数），**不是** true/false。')
            shape.append('"%s": {"noul": 0.0}' % name)
    lines += ['', '输出结构（严格照抄形状，数字自己填）：',
              '{%s}' % ', '.join(shape),
              '', '规则：probabilities 的键集与候选完全一致、和等于 1；choice 必须等于概率最大的键；',
              'score 按上面的刻度取值；noul 是 0~1 的小数。', '', '材料：', str(state)]
    return '\n'.join(lines)


def _parse_json_object(content, label='响应'):
    """从模型输出里取一个 JSON 对象。取不到就抛 `JevError`，**不猜、不修补**。"""
    text = (content or '').strip()
    if not text:
        raise JevError('%s 里没有正文（空 content）——常见原因是推理 token 吃光了预算，'
                       '把 OPENROUTER_MAX_TOKENS 调大再试' % label)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise JevError('%s 里没有 JSON 对象：%s' % (label, text[:200]))
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError as error:
        raise JevError('%s 不是合法 JSON（%s）：%s' % (label, error, text[start:end + 1][:200]))
    if not isinstance(parsed, dict):
        raise JevError('%s 的 JSON 顶层不是对象' % label)
    return parsed


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
        # 最近一次响应里**实际服务的模型**（如 `jev-1.13.0`），不是请求的别名。
        self.last_model = None

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
        """返回 (answers, usage)。state 可以是字符串、JSON 对象或数组。

        `usage` 里除了 token 数还带 `model`——那是**实际服务的模型**（实测
        `jev-1.13.0`），不是你请求的别名（`jev-latest`）。落盘它，是为了让
        "线上跑的和我测过的不是同一个东西"这件事**可见**，而不是等到结果不对
        才去猜。

        **每个 choice 答案都过一遍 `_check_choice`**：键集、取值域、概率和、
        argmax 全对不上就抛 `JevError`。契约漂移应当当场炸——漂移的失效方式是
        "看起来正常但选错了"，那正是最难发现的。
        """
        body = self._post(ENDPOINT, {'model': self.model,
                                     'state': state, 'questions': questions})
        answers = body.get('answers')
        if not isinstance(answers, dict):
            raise JevError(f'响应里没有 answers：{json.dumps(body, ensure_ascii=False)[:300]}')
        for key, question in (questions or {}).items():
            if isinstance(question, dict) and question.get('type') == 'choice':
                _check_choice(key, question, answers.get(key) or {})
        usage = dict(body.get('usage') or {})
        if body.get('model'):
            usage['model'] = body['model']
            self.last_model = body['model']
        return answers, usage

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


class ChatJevClient(JevClient):
    """**用任意 OpenAI 兼容的对话模型**充当决策模型（默认指 OpenRouter）。

    与 `JevClient` **同一个鸭子类型**（`decide` / `decide_article` / `last_model`），所以
    日报判断、检索重排、草稿回复、欠话台账那些调用方不需要知道换了通路。差别只有两处：

      · 传输是 chat/completions（原生是 TypeSafe 自己的 `systemone` 端点）；
      · 契约由**我们**在提示里写死、再由 `_check_choice` / `_check_score` / `_check_noul`
        逐条验（原生是服务端直接给结构化答案，客户端只做校验）。多出来的这条"自己保证契约"
        正是**换通路真正的代价**：它不该被藏起来，所以提示词与校验都在这里，且漂移会当场抛。

    **base_url 可换，是这条路的核心价值**：同一个实现既能指 OpenRouter，也能指**中国地区能通的**
    国内端点（`https://api.deepseek.com/v1`、`https://dashscope.aliyuncs.com/compatible-mode/v1`）。
    【实测 2026-10-17】契约都对得上：

      · `typesafe/jev-router`（OpenRouter）→ 过；· `openai/gpt-4o-mini` → **403 地区封锁**；
      · `deepseek-chat` → 过（**1.0s**/篇）；· `deepseek-reasoner` → 过（7.2s）；
      · `qwen-plus`（百炼）→ 过（2.3s）。

    ⚠️ **契约一致不等于判断一致**：同一篇文章上 `deepseek-chat` 给 relevance=1 / noul=0.15，
    `qwen-plus` 给 2 / 0.85。换判断器会改变取值分布，而下游阈值是对着 Jev 拍的——
    这就是为什么原始分与 `usage.model` 都要落盘。
    """

    def __init__(self, api_key, model=OPENROUTER_DEFAULT_MODEL, timeout=120,
                 max_tokens=OPENROUTER_MAX_TOKENS, base_url=OPENROUTER_BASE,
                 extra_headers=None, provider='openrouter', engine=None):
        super().__init__(api_key, model=model, timeout=timeout)
        self.base_url = (base_url or OPENROUTER_BASE).rstrip('/')
        self.extra_headers = dict(extra_headers or {})
        self.max_tokens = max_tokens
        # 记录自己是谁，方便落盘/报错时区分"哪条通路判的"。
        self.provider = provider
        # `engine` 不为空时**走它**（`AIEngine.chat()`）—— 本地 Ollama / LM Studio 就是这条。
        # 这条存在的原因：`judgeProvider=chat` 必须尊重 `aiEngine=local`（见 D-124）。
        self.engine = engine

    def _endpoint(self):
        return self.base_url + '/chat/completions'

    def _post_once(self, url, payload=None):
        """与原生同形，多带一份可配的头（OpenRouter 的归属头由预设给），并把**地区封锁**单独报出来。"""
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json',
                   'Authorization': f'Bearer {self.api_key}'}
        headers.update(self.extra_headers)
        request = urllib.request.Request(
            url, data=data, method='POST' if data else 'GET', headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = ''
            try:
                detail = error.read().decode('utf-8', 'replace')[:400]
            except Exception:
                pass
            # 403 在 OpenRouter 上有**两种**含义，混在一起会让人以为是 key 不对：
            #   "not available in your region"（地区封锁，换模型才有用）vs 鉴权被拒。
            if error.code == 403 and 'region' in detail.lower():
                raise JevError(f'这个模型在当前地区不可用（HTTP 403）：{detail}')
            if error.code == 402:
                # 这条最容易被误读成"模型坏了"。实际是**预检额度**：OpenRouter 按
                # max_tokens×单价 估一个上界，额度不够就拒，并告诉你负担得起多少。
                raise JevError(f'OpenRouter 余额/额度不足（HTTP 402）：{detail}')
            if error.code in (401, 403):
                raise JevError(f'鉴权被拒（HTTP {error.code}）。key 可能不对或已过期：{detail}')
            failure = JevError(f'HTTP {error.code}: {detail}')
            failure.retryable = error.code in RETRY_STATUS
            raise failure
        except urllib.error.URLError as error:
            failure = JevError(f'连不上 {url}: {error.reason}')
            failure.retryable = True
            raise failure

    def models(self):
        """OpenRouter 的模型清单（id 列表）。换地区/换模型时先在这里确认名字。

        只有 OpenRouter 有这条接口；国内端点没有同形的清单，所以命中非 OpenRouter 时返回空列表
        而不是报错 —— 调用方（`jev_probe`）本来就只用它做"名字写错了吗"的自检。
        """
        if 'openrouter.ai' not in self.base_url:
            return []
        body = self._post(OPENROUTER_MODELS_ENDPOINT)
        return [m.get('id') for m in (body.get('data') or []) if isinstance(m, dict)]

    def decide(self, state, questions):
        """返回 `(answers, usage)`，形状与原生一致；**任何契约不成立都抛 `JevError`**。

        `usage` 多带一个 `cost`（OpenRouter 直接回报美元成本，原生没有这个字段），
        并把 OpenRouter 的 token 键名对齐成原生那套（`prompt_tokens`→`input_tokens`）——
        `decide.py` 与 `jev_probe.py` 读的是后一个名字，名字对不上会让成本统计静默变成 0。
        """
        prompt = build_openrouter_prompt(state, questions)
        content, usage = self._chat_once(prompt)
        answers = _parse_json_object(content, 'chat 响应')
        for key, question in (questions or {}).items():
            if not isinstance(question, dict):
                continue
            answer = answers.get(key)
            if not isinstance(answer, dict):
                raise JevError('%s：响应里缺这一题的答案（%r）——契约漂移' % (key, answer))
            _check_choice(key, question, answer)
            _check_score(key, question, answer)
            _check_noul(key, question, answer)
        return answers, usage

    def _chat_once(self, prompt):
        """取一次文字回复，返回 `(content, usage)`。

        有 `engine` 就走它（`AIEngine.chat()`，本地 Ollama / LM Studio 是这条）：那种情况
        **拿不到 usage 里的 token 数与成本** —— 这是引擎层既有的边界（它只回字符串），
        不是这里漏了；`model` 仍然会落盘，所以"谁判的"依然可查。
        没有 `engine` 时走 OpenAI 兼容端点，token 与 cost 都能拿到。
        """
        if self.engine is not None:
            content = self.engine.chat(prompt, max_tokens=self.max_tokens)
            served = getattr(self.engine, 'model', '') or self.model
            self.last_model = served
            return content or '', {'model': served}
        body = self._post(self._endpoint(), {
            'model': self.model,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0,
            'max_tokens': self.max_tokens,
        })
        message = ((body.get('choices') or [{}])[0] or {}).get('message') or {}
        usage = dict(body.get('usage') or {})
        if 'prompt_tokens' in usage and 'input_tokens' not in usage:
            usage['input_tokens'] = usage['prompt_tokens']
        if 'completion_tokens' in usage and 'output_tokens' not in usage:
            usage['output_tokens'] = usage['completion_tokens']
        if body.get('model'):
            # 路由到谁**每次都可能不同**（实测 grok-4.7 / glm-5.3-flash），所以落盘这个值
            # 才有意义：不然"线上跑的和测过的不是同一个东西"永远不可见。
            usage['model'] = body['model']
            self.last_model = body['model']
        return message.get('content') or '', usage


class LocalJevClient(JevClient):
    """**用本机决策模型当判断器**（D-127）：`decide.py serve` 的 `/decide`，不出网、无 key。

    与 `JevClient` 同一个鸭子类型（`decide` / `decide_article` / `last_model`），差别有三处：

      · **一个问题一次请求**：本机服务的 `/decide` 只答一个 primitive，而原生 `systemone`
        一次答一组问题。日报是 3 个问题 ⇒ 3 次调用（本机 0.2–0.6 s/次，整篇仍在 2 s 内）；
      · `score` **没有连续分**：用 `choice` 在有序档位上选，把选中档位的下标当分数。
        下游 `score_to_relevance` 本来就是四舍五入取整，所以语义等价；
      · 概率是**候选集上的相对 softmax、未校准**（APUS 契约原文），`confidence` 只作参考，
        不要直接拿来做自动放行阈值。

    契约不成立时仍由 `_check_choice` / `_check_score` / `_check_noul` 当场抛 ——
    换通路不降低校验标准。
    """

    #: noul / score_level 必须使用固定的 yes/no 候选（本机服务的契约要求）。
    BINARY = [{'id': 'yes', 'description': 'The stated proposition is true.'},
              {'id': 'no', 'description': 'The stated proposition is false.'}]

    #: 主题 → 相关度下限（见 `_calibrate` 的实测依据）。空表就是不做校准。
    TOPIC_RELEVANCE_FLOOR = {'AI': 1}

    def __init__(self, base_url=DEFAULT_JUDGE_LOCAL_URL, model='apus-reflex-4b', timeout=120):
        super().__init__('local', model=model, timeout=timeout)
        self.base_url = (base_url or DEFAULT_JUDGE_LOCAL_URL).rstrip('/')
        self.provider = 'local'

    def models(self):
        """本机服务没有模型清单接口；调用方（`jev_probe`）只拿它做"名字写错了吗"的自检。"""
        return []

    def _decide_one(self, state, question):
        qtype = (question or {}).get('type')
        instructions = (question or {}).get('instructions') or ''
        if isinstance(state, (dict, list)):
            state = json.dumps(state, ensure_ascii=False)
        if qtype == 'choice':
            criteria = question.get('criteria') or {}
            body = self._post(self.base_url + '/decide', {
                'state': state, 'instructions': instructions, 'primitive': 'choice',
                'criteria': [{'id': str(k), 'description': str(v)} for k, v in criteria.items()]})
            return {'choice': body.get('choice'),
                    'probabilities': body.get('probabilities') or {},
                    'confidence': body.get('confidence')}
        if qtype == 'score':
            levels = question.get('criteria') or []
            if len(levels) < 2:
                raise JevError('score：有序档位至少要有 2 档')
            # 把原问题（"…的实用价值有多高？"）改写成陈述式命题：
            # "…的实用价值，达到「中」或更高吗？（中：…；高：…）"。
            # 实测三种写法（同一批 44 篇）：三选一 45.5%、纯判据句 59.1%、
            # 这种"保留主语 + 阈值 + 判据" 70.5%。**保留主语**是关键——
            # 只给判据时模型不知道在判什么。
            stem = instructions.strip().rstrip('？?').strip()
            if stem.endswith('有多高'):
                stem = stem[:-3].rstrip('，, ')
            hits = 0
            last_name = str(levels[-1]).split('：')[0].split(':')[0]
            for index in range(1, len(levels)):
                name = str(levels[index]).split('：')[0].split(':')[0]
                criteria_text = '；'.join(str(x) for x in levels[index:])
                body = self._post(self.base_url + '/decide', {
                    'state': state,
                    'instructions': '这篇文章%s，达到「%s」或「%s」吗？（%s）'
                                    % (stem, name, last_name, criteria_text),
                    'primitive': 'noul', 'criteria': self.BINARY})
                probability = body.get('yes_probability')
                if probability is None:
                    raise JevError('score：第 %d 档阈值没给 yes_probability（%s）'
                                   % (index + 1, json.dumps(body, ensure_ascii=False)[:200]))
                hits += float(probability) >= 0.5
            return {'score': float(hits)}
        if qtype == 'noul':
            body = self._post(self.base_url + '/decide', {
                'state': state, 'instructions': instructions, 'primitive': 'noul',
                'criteria': self.BINARY})
            if body.get('yes_probability') is None:
                raise JevError('noul：本机服务没给 yes_probability（%s）'
                               % json.dumps(body, ensure_ascii=False)[:200])
            return {'noul': float(body['yes_probability'])}
        raise JevError('本机判断器不支持的问题类型：%r' % (qtype,))

    def decide(self, state, questions):
        """返回 `(answers, usage)`，形状与另外两条通路一致；每个答案都过一遍校验。"""
        answers = {}
        started = time.time()
        for key, question in (questions or {}).items():
            if not isinstance(question, dict):
                continue
            answer = self._decide_one(state, question)
            _check_choice(key, question, answer)
            _check_score(key, question, answer)
            _check_noul(key, question, answer)
            answers[key] = answer
        self._calibrate(answers)
        usage = {'model': self.model, 'provider': 'local',
                 'elapsed_ms': round((time.time() - started) * 1000),
                 'calibration': dict(self.TOPIC_RELEVANCE_FLOOR)}
        self.last_model = self.model
        return answers, usage

    def _calibrate(self, answers):
        """按主题给相关度加下限 —— **这是实测出来的，不是拍脑袋**。

        211 篇同题对比（2026-10-16~21，Jev 实判）：本机模型对 `AI` 类文章的相关度
        系统性低估一档——`低→中` 33 次、`中→中` 仅 5 次，该类一致率 20.0%，
        而政治/文学/新闻都在 90% 以上。加下限后 AI 类 77.5%、总体 73.9% → 84.4%。

        为什么是「下限」而不是「整体 +1」：统一 +1 只有 82.9%，且会把本来判 `高`
        的压成越界值；只把 `低` 抬到 `中` 才对得上 Jev 的分布。
        """
        topic_answer = answers.get('topic') or {}
        score_answer = answers.get('relevance') or {}
        topic = topic_answer.get('choice')
        if topic is None or 'score' not in score_answer:
            return
        floor = self.TOPIC_RELEVANCE_FLOOR.get(topic)
        if floor is None:
            return
        if float(score_answer['score']) < floor:
            score_answer['score'] = float(floor)
            score_answer['calibrated'] = True


def create_client(api_key='', model='', timeout=120, config=None):
    """拿到 key 就返回客户端，没有就返回 None（调用方据此走老路）。

    走哪条通路由配置 `judgeProvider` 决定，**默认 `native`**（与引入这个开关之前完全一致）：

    · `native`     → `api.typesafe.ai`（本机被地区封锁，见 D-120）；
    · `chat`       → **中国地区能通的对话模型**，走配置里已有的那套（`aiBaseUrl` 中转站 /
      `aiModel` / `deepseekApiKey`）——一句话就能打开，不需要新厂商、新 key。
      实测 `deepseek-chat` 1.0 秒/篇且契约通过，成本量级 ¥1/天；
    · `openrouter` → OpenRouter 的 chat/completions，模型取 `openrouterJudgeModel`
      （默认 `typesafe/jev-router`），key 取 `openrouterApiKey`；
    · `local`      → **本机决策模型服务**（`judgeLocalUrl`，默认 `http://127.0.0.1:8090`，
      即 `decide.py serve`），不需要 key、不出网（D-127）。
    """
    judge_provider = ''
    openrouter_key = openrouter_model = ''
    chat_key = ''
    chat_base = chat_model = None
    try:
        import _utils
        judge_provider = (getattr(_utils, 'get_judge_provider')(config) or '').strip().lower()
        openrouter_key = (getattr(_utils, 'get_openrouter_key')(config) or '').strip()
        openrouter_model = (getattr(_utils, 'get_openrouter_model')(config) or '').strip()
        # 中转站口径与"配置了 aiBaseUrl 的对话模型"同源（`_utils.get_chat_endpoint` 委托
        # `_custom_endpoint`）：只配了 base 没配 model 时它会补 `deepseek-chat`。
        chat_base, chat_model = getattr(_utils, 'get_chat_endpoint')(config) or (None, None)
        chat_key = (getattr(_utils, 'get_api_key')(config) or '').strip()
    except Exception:
        # 配置读不出来就当没配（与 `get_typesafe_key` 同一态度：跨机器拷配置不该崩）。
        # `judge_provider` 若已经读到而这里抛了，后面的分支会因为没有 key 返回 None ——
        # 那是安全的方向（退回老路），不是静默用错通路。
        pass
    if judge_provider == 'chat':
        # **先看用户选的是哪个引擎**：`judgeProvider=chat` 的语义是"用配置里的对话模型"。
        # 无条件用 `deepseekApiKey` 会让 `aiEngine=local` 的用户被送出境外（D-124）。
        engine_type = 'deepseek'
        local_detect = None
        try:
            engine_type = (getattr(_utils, 'get_ai_engine')(config) or 'deepseek').strip().lower()
            local_detect = getattr(_utils, 'detect_local_engine', None)
        except Exception:
            pass
        if engine_type in ('local', 'ollama'):
            ok, local_engine, desc = (False, None, '检测函数不可用')
            if local_detect is not None:
                try:
                    ok, local_engine, desc = local_detect()
                except Exception as error:               # noqa: BLE001
                    desc = '%s: %s' % (type(error).__name__, error)
            if not ok:
                # **不退回云端**：宁可这次没有判断，也不把正文发去用户没选的地方。
                print('[WARN] aiEngine=%s 但没检测到本地推理服务（%s）：判断器不会退回云端，'
                      '这次不可用，判断将走 LLM 路径' % (engine_type, desc), file=sys.stderr)
                return None
            return ChatJevClient('local', model=getattr(local_engine, 'model', '') or 'local',
                                 base_url=getattr(local_engine, 'base_url', '') or '',
                                 engine=local_engine, timeout=timeout, provider='chat-local')
        if engine_type == 'claude':
            print('[WARN] judgeProvider=chat 只支持 OpenAI 兼容端点，aiEngine=claude 不行：'
                  '要云端判断请用 judgeProvider=openrouter，或把 aiEngine 设回 deepseek',
                  file=sys.stderr)
            return None
        if not chat_key:
            print('[WARN] judgeProvider=chat 但没配对话模型的 key（deepseekApiKey）：'
                  '决策模型这次不可用，判断将退回 LLM 路径', file=sys.stderr)
            return None
        # 配了 `aiBaseUrl`（中转站）就用它，否则直连 DeepSeek。
        return ChatJevClient(chat_key, model=model or chat_model or 'deepseek-chat',
                             base_url=chat_base or DEEPSEEK_BASE, timeout=timeout, provider='chat')
    if judge_provider == 'openrouter':
        if not openrouter_key:
            print('[WARN] judgeProvider=openrouter 但没配 openrouterApiKey：'
                  '决策模型这次不可用，判断将退回 LLM 路径', file=sys.stderr)
            return None
        return ChatJevClient(openrouter_key,
                             model=model or openrouter_model or OPENROUTER_DEFAULT_MODEL,
                             timeout=timeout, provider='openrouter',
                             extra_headers={'HTTP-Referer': OPENROUTER_REFERER,
                                            'X-Title': OPENROUTER_TITLE})
    if judge_provider == 'local':
        local_url = DEFAULT_JUDGE_LOCAL_URL
        try:
            local_url = getattr(_utils, 'get_judge_local_url')(config) or DEFAULT_JUDGE_LOCAL_URL
        except Exception:
            pass
        return LocalJevClient(local_url, timeout=timeout)
    key = resolve_key(api_key, config)
    if not key:
        return None
    return JevClient(key, model=model or DEFAULT_MODEL, timeout=timeout)
