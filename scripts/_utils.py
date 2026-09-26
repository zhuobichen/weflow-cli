"""
weflow-cli 公共工具函数 — 供 biz_daily / classify_daily / chat_report 等共用。
"""
import os, json, base64, socket, urllib.request, hashlib
from functools import wraps


# ======================================================================
# 工具库（静态知识，供报告「行动指南」自动匹配）
# 按关键词 → GitHub 项目/工具，匹配越靠前优先级越高
# ======================================================================

TOOL_LIBRARY = [
    # --- AI 编程 / Agent ---
    {'keywords': ['cursor', 'agent', 'ide', '编程助手', '代码生成'],
     'name': 'Cursor', 'url': 'https://github.com/getcursor/cursor',
     'desc': 'AI 原生 IDE，支持多文件编辑与 Agent 模式'},
    {'keywords': ['windsurf', 'agent', 'ide'],
     'name': 'Windsurf', 'url': 'https://codeium.com/windsurf',
     'desc': 'AI 原生 IDE，类 Cursor，支持任务级自动编程'},
    {'keywords': ['continue', 'agent', 'ide', 'vscode'],
     'name': 'Continue', 'url': 'https://github.com/continuedev/continue',
     'desc': '开源 VS Code 插件，接入本地/云端大模型'},

    # --- 大模型 / 推理 ---
    {'keywords': ['deepseek', 'r1', '开源模型'],
     'name': 'DeepSeek R1', 'url': 'https://github.com/deepseek-ai/DeepSeek-R1',
     'desc': 'DeepSeek 开源推理模型，中文表现强'},
    {'keywords': ['llama', 'meta', '开源模型'],
     'name': 'Llama 3', 'url': 'https://github.com/meta-llama/llama3',
     'desc': 'Meta 开源大模型，社区生态最成熟'},
    {'keywords': ['ollama', '本地部署', '本地推理'],
     'name': 'Ollama', 'url': 'https://github.com/ollama/ollama',
     'desc': '一行命令运行本地大模型（macOS/Linux/Windows）'},
    {'keywords': ['lmstudio', '本地部署', '本地推理'],
     'name': 'LM Studio', 'url': 'https://lmstudio.ai',
     'desc': '桌面端本地大模型运行器，有 GUI'},

    # --- 信息聚合 / 日报 / RAG ---
    {'keywords': ['rag', '检索增强', '知识库', '论文'],
     'name': 'AnythingLLM', 'url': 'https://github.com/Mintplex-Labs/anything-llm',
     'desc': '开箱即用的 RAG 知识库 + 聊天桌面应用'},
    {'keywords': ['rss', '信息源', '订阅'],
     'name': 'Fluent Reader', 'url': 'https://github.com/yang991178/fluent-reader',
     'desc': '现代化 RSS 阅读器，聚合公众号/博客/新闻'},

    # --- 环境科学 / 交叉方向 ---
    {'keywords': ['遥感', '卫星', '反演', 'landsat', 'sentinel'],
     'name': 'Google Earth Engine', 'url': 'https://earthengine.google.com',
     'desc': '大规模遥感影像在线分析平台'},
    {'keywords': ['遥感', '卫星', 'python'],
     'name': 'xarray', 'url': 'https://github.com/pydata/xarray',
     'desc': 'N 维数组处理，气象/遥感数据标配'},
    {'keywords': ['大气', '排放清单', '排放'],
     'name': 'MEIC', 'url': 'https://meicmodel.org',
     'desc': '中国多尺度排放清单模型'},
    {'keywords': ['lca', '生命周期', '生命周期评估'],
     'name': 'brightway', 'url': 'https://github.com/brightway-lca/brightway2',
     'desc': '开源生命周期评估（LCA）框架'},
    {'keywords': ['数值模拟', '大气', '模型', '模拟'],
     'name': 'WRF', 'url': 'https://github.com/wrf-model/WRF',
     'desc': '中尺度数值天气预报模型'},

    # --- 效率工具 ---
    {'keywords': ['obsidian', '笔记', '知识管理'],
     'name': 'Obsidian', 'url': 'https://obsidian.md',
     'desc': '本地优先的 Markdown 笔记，双链/图谱'},
    {'keywords': ['anki', '记忆', '复习'],
     'name': 'Anki', 'url': 'https://apps.ankiweb.net',
     'desc': '基于间隔重复的闪卡记忆工具'},

    # --- 一般关键词兜底 ---
    {'keywords': ['投资', '财报', '股价', '股票'],
     'name': 'Tushare', 'url': 'https://tushare.pro',
     'desc': '免费金融数据接口（Python）'},
]


def match_tools(articles: list[dict], top_n: int = 8) -> list[dict]:
    """从文章的标题/摘要/标签中抽取关键词，匹配 TOOL_LIBRARY。"""
    collected_text = ''
    for a in articles:
        tags = a.get('tags') or []
        text_parts = [
            a.get('title', ''),
            a.get('summary', ''),
            ' '.join(tags) if isinstance(tags, list) else str(tags),
        ]
        collected_text += ' ' + ' '.join(text_parts)
    collected_text = collected_text.lower()

    ranked = []
    for tool in TOOL_LIBRARY:
        score = sum(1 for kw in tool['keywords'] if kw.lower() in collected_text)
        if score > 0:
            ranked.append((score, tool))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in ranked[:top_n]]


# ======================================================================
# Config
# ======================================================================

CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.weflow-cli', 'config.json')


# ======================================================================
# AI Engine 抽象
# ======================================================================

class AIEngine:
    """OpenAI-compatible API 引擎基类。"""
    def __init__(self, api_key: str, base_url: str, model: str, timeout=60):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout = timeout

    def chat(self, prompt: str, max_tokens=2000) -> str:
        payload = json.dumps({
            'model': self.model,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens,
            'temperature': 0.2,
        }).encode('utf-8')
        req = urllib.request.Request(
            f'{self.base_url}/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        return data['choices'][0]['message']['content']


class AnthropicEngine(AIEngine):
    """Anthropic Messages API 引擎（/v1/messages）。"""
    def __init__(self, api_key: str, base_url: str, model: str, timeout=120):
        super().__init__(api_key, base_url, model, timeout=timeout)

    def chat(self, prompt: str, max_tokens=2000) -> str:
        payload = json.dumps({
            'model': self.model,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens,
        }).encode('utf-8')
        req = urllib.request.Request(
            f'{self.base_url}/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': self.api_key,
                'anthropic-version': '2023-06-01',
                'Authorization': f'Bearer {self.api_key}',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        return data['content'][0]['text']


class DeepSeekEngine(AIEngine):
    def __init__(self, api_key: str, model='deepseek-chat', timeout=180):
        super().__init__(api_key, 'https://api.deepseek.com/v1', model, timeout=timeout)


class ClaudeEngine(AIEngine):
    def __init__(self, api_key: str, model='claude-sonnet-4-6', timeout=90):
        super().__init__(api_key, 'https://api.anthropic.com/v1', model, timeout=timeout)


class OllamaEngine(AIEngine):
    def __init__(self, model='llama3', timeout=120):
        super().__init__('ollama', 'http://localhost:11434/v1', model, timeout=timeout)


# ======================================================================
# 本地引擎自动检测
# ======================================================================

_LOCAL_ENDPOINTS = [
    # (name, base_url, api_style)
    ('ollama',    'http://localhost:11434',  'openai'),
    ('lmstudio',  'http://localhost:1234', 'openai'),
    ('claude',    'http://localhost:8080',  'anthropic'),
    ('lmstudio',  'http://localhost:8000', 'openai'),
    ('lmstudio',  'http://localhost:3000', 'openai'),
]


def _try_local_endpoint(name: str, base_url: str, api_style: str,
                       timeout: int = 5) -> tuple[bool, str, str, str]:
    health_path = f'{base_url}/v1/models'
    try:
        req = urllib.request.Request(health_path, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        models = data.get('data', data.get('models', []))
        if not models:
            return True, name, base_url, 'local'
        first = models[0]
        model_name = first.get('id', first.get('model', 'local'))
        return True, name, base_url, model_name
    except Exception as e:
        return False, name, base_url, str(e)


def detect_local_engine(timeout: int = 5) -> tuple[bool, AIEngine, str]:
    """自动检测本地推理服务。返回 (detected, engine, description)。"""
    checked = set()
    for name, base_url, api_style in _LOCAL_ENDPOINTS:
        key = (name, base_url)
        if key in checked:
            continue
        checked.add(key)
        ok, dname, durl, model = _try_local_endpoint(name, base_url, api_style, timeout)
        if not ok:
            continue
        if api_style == 'anthropic':
            engine = AnthropicEngine('local', durl, model, timeout=180)
        else:
            engine = OllamaEngine(model=model, timeout=180)
            engine.base_url = durl.rstrip('/') + '/v1'
        desc = f'{dname} ({model}@{durl})'
        return True, engine, desc
    return False, None, '无可用本地推理服务'


def create_engine(engine_type: str, api_key: str = '') -> AIEngine:
    """工厂函数：根据类型创建 AI 引擎。

    引擎类型: local(自动检测) / deepseek / claude / ollama
    配置 aiBaseUrl 时, deepseek 引擎走自定义 OpenAI 兼容端点 (中转站)。
    """
    t = engine_type.lower()
    if t == 'local':
        ok, engine, desc = detect_local_engine(timeout=8)
        if not ok:
            raise RuntimeError(
                '未检测到本地推理服务。请启动以下任一服务后重试：\n'
                '  • Ollama:     ollama serve          (11434)\n'
                '  • LM Studio:  lmstudio server start (1234)\n'
                '  • Claude Code 本地服务 (8080)\n'
                '或使用 --engine deepseek/claude 指定云端引擎。'
            )
        return engine
    elif t == 'deepseek':
        base_url, model = _custom_endpoint()
        if base_url:
            return AIEngine(api_key, base_url, model)
        return DeepSeekEngine(api_key)
    elif t == 'claude':
        return ClaudeEngine(api_key)
    elif t == 'ollama':
        return OllamaEngine()
    raise ValueError(
        f'未知引擎: {engine_type}，可选: local / deepseek / claude / ollama'
    )


def _custom_endpoint():
    """读取 config.json 的 aiBaseUrl/aiModel; 未配置返回 (None, None)。"""
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
        base_url = (cfg.get('aiBaseUrl') or '').strip().rstrip('/')
        model = (cfg.get('aiModel') or '').strip() or 'deepseek-chat'
        return (base_url or None), (model if base_url else None)
    except Exception:
        return None, None


def call_deepseek(prompt: str, api_key: str, max_tokens=2000, timeout=60) -> str:
    """向后兼容：调用 DeepSeek API。"""
    return DeepSeekEngine(api_key, timeout=timeout).chat(prompt, max_tokens=max_tokens)


def call_ai(prompt: str, engine_type: str = 'local', api_key: str = '', max_tokens=2000) -> str:
    """通用 AI 调用：支持 local/deepseek/claude/ollama 引擎。"""
    engine = create_engine(engine_type, api_key)
    return engine.chat(prompt, max_tokens=max_tokens)


# ======================================================================
# 缓存机制 — 避免重跑 pipeline 时重复调用 API
# ======================================================================

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'output', '.cache')


def _cache_key(*args, **kwargs) -> str:
    """根据参数生成 MD5 缓存 key。"""
    raw = json.dumps({'args': args, 'kwargs': kwargs}, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def cached_call(func, *args, cache_subdir: str = '', **kwargs):
    """带缓存的函数调用。命中缓存则直接返回，否则调用 func 并存储结果。

    Args:
        func: 要调用的函数（需返回可 JSON 序列化的结果）
        cache_subdir: 缓存子目录名（如 'classify'、'wiki'）
    """
    cache_dir = os.path.join(_CACHE_DIR, cache_subdir) if cache_subdir else _CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    key = _cache_key(func.__name__, *args, **kwargs)
    cache_file = os.path.join(cache_dir, f'{key}.json')

    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass  # 缓存损坏，重新调用

    result = func(*args, **kwargs)
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False)
    except (TypeError, OSError):
        pass  # 结果不可序列化或写入失败，跳过缓存

    return result


def clear_cache(cache_subdir: str = ''):
    """清空指定子目录的缓存，或全部缓存。"""
    target = os.path.join(_CACHE_DIR, cache_subdir) if cache_subdir else _CACHE_DIR
    if not os.path.isdir(target):
        return
    for fname in os.listdir(target):
        fpath = os.path.join(target, fname)
        if os.path.isfile(fpath):
            os.unlink(fpath)
# ======================================================================
# Config Decrypt
# ======================================================================

# ======================================================================
# 主题分类法 —— **唯一一处定义**
#
# 列表与定义都收在这里，因为「散文写的枚举会漂」在这个仓库里已经真的发生过：
# `biz_daily.TOPIC_PROMPT` 的第一行按 TOPICS 生成了六类，但同一段提示词后面两处
# 提醒是硬编码的五类（少了政治），判断规则里也没有政治那一栏——而政治在语料里占 26%。
# 两条路径（LLM 提示词 / 决策模型的判据）现在读同一张表，所以它们不可能再对不上。
#
# **顺序有意义**：`generate_ai_report` 按这个顺序给报告分栏。
TOPICS = ['AI', '学术', '新闻', '文学', '投资', '政治']

# 一篇文章**没能被分类**时落到哪一类。
#
# 收成一处是因为它曾经在写入路径上有两个不同的答案、相隔七行：
# `.articles.json` 那条路默认 `''`（空主题），而分组/落 md 那条路默认 `'学术'`。
# 于是一篇文章在 md 里写 `topic: 学术`、在 json 里写 `topic: ""`——
# **同一次运行、同一篇文章，两个产物给出不同的主题**，而且都不报错。
#
# 真实后果（2026-09-04 的产出，178 篇）：md 全落在 `学术/` 目录、frontmatter 写
# `topic: 学术`（其中两篇是「OpenAI 深夜发布 GPT-6」「专为高管准备的 AI 助手」，
# 明显不该是学术），而 json 里同一批全是 `""` —— 报告那条路读 json，
# 判据 `topic != FOCUS_TOPIC and relevance != '高'` 就把它们整批静默排除了。
#
# 什么情况下会走到这里：`daily --no-ai`、或者没配 API key 时，Phase 2（分类）整段
# 不跑，文章 dict 里**根本没有 `topic` 键**，而 Phase 3 照常写文件。
#
# 这个值是**兜底不是判断**：落到这里的文章其实是"未分类"。之所以不新造一个
# `未分类` 类别，是因为它要额外维护一个目录、一处 HTML 分栏、以及下游对
# 主题字面量的比较；改用 `未分类` 属于改既有契约，得单独决策。
DEFAULT_TOPIC = '学术'

# 每一类的判据。既生成提示词里的判断规则，也直接作为 Jev 的 choice criteria。
TOPIC_CRITERIA = {
    'AI': 'AI大模型/Agent/编程/开源/科技产品/工具教程',
    '投资': '股票基金/融资/经济分析/商业市场',
    '新闻': '时事政策/社会热点/娱乐/招聘促销/会议通知',
    '文学': '散文小说/美食旅游/生活随笔/历史文化',
    '学术': '科研论文/期刊文章/实验室成果/学术会议/高校研究',
    '政治': '党政理论/政策解读/领导人讲话/官方评论',
}

# 相关度的三个档位。与 `TOPICS` 一样是**下游按字面量比较**的封闭词表：
# `generate_ai_report.py`、`enrich_backlinks.py`、`generate_html.py` 都在比
# `'高'`/`'中'`/`'低'` 这三个字。定义收在这里，`jev_client` 与 `biz_daily`
# 都从这里取——原先 `jev_client` 一份、`biz_daily` 的成员校验里再写一份。
#
# **顺序即语义**：`jev_client.RELEVANCE_LEVELS` 是给模型看的判据文本，按下标与
# 这个列表一一对应（有测试钉住），所以不要重排。
RELEVANCE_NAMES = ['低', '中', '高']

# 一篇文章**没能被判相关度**时落到哪一档。和 `DEFAULT_TOPIC` 是同一类东西：
# 兜底不是判断。之所以要具名，是因为"全库 2199/2201 篇都是「中」"这件事的答案
# 就在这里——分类失败的每条路径最终都落到这个值，而 `'中'` 读起来是
# 「有启发性」（正面评价），不是「没判断」。值不变更（改档位会动到下游的
# 收录判据），只是把它写在一处，好让它可被搜索、可被讨论。
DEFAULT_RELEVANCE = '中'


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


# 配置里"不想在日报里看到的主题"的键名。值形如 `新闻,投资,学术`。
EXCLUDED_TOPICS_KEY = 'dailyExcludeTopics'


def excluded_topics(config=None, explicit='', protected=()):
    """用户不想在日报里看到的主题集合。`explicit`（命令行）覆盖配置。

    **这是展示层的开关，不是抓取层的**：正文照常抓取、照常归档，只是不出现在
    日报里。理由是实测：拉之前只有来源+标题+摘要，按它判类型**不可靠**（与来源级
    比对一致率仅 60%、误伤 48/217），而**排除是不可逆的**——没抓取就没归档。
    放在展示层还换来一件事：改主意不用重抓，改个配置重新生成报告即可。

    写错的主题名会被忽略并**打一行 WARN**（静默忽略会让人以为过滤生效了）。

    `protected` 是不许被排除的主题（呼叫方传自己的「焦点主题」）。它比「未知主题名」
    更值得拦一下：把焦点主题排掉，报告要么没有主体、要么直接报「未找到文章」
    **指错方向**（那句话说去跑 biz_daily，可文章其实在）。同样 WARN 后忽略。
    """
    raw = explicit or (config or {}).get(EXCLUDED_TOPICS_KEY, '') or ''
    names = [t.strip() for t in str(raw).replace('，', ',').split(',') if t.strip()]
    out = set()
    for name in names:
        if name in TOPICS:
            if name in protected:
                print('[WARN] %s 里的「%s」是这份报告的主体，不能排除，已忽略'
                      % (EXCLUDED_TOPICS_KEY, name))
                continue
            out.add(name)
        else:
            print('[WARN] %s 里的「%s」不是已知主题，已忽略（可选：%s）'
                  % (EXCLUDED_TOPICS_KEY, name, '/'.join(TOPICS)))
    return out


# === 来源级先验："哪个号稳定发哪一类"从真实判断里长出来 ===
#
# 为什么不写死一张表：拉取之前只有来源+标题+摘要，按它判类型实测只有 60% 一致率、
# 误伤 48/217（见 excluded_topics 的说明）。但**抓回来之后**每次都有 Jev 按正文判的
# 主题，日积月累就看得出某个号一贯发什么。这张表因此是长出来的，不是我编的。
#
# 它现在只用来**报数**，不用来跳过任何东西：跳过是不可逆的。
SOURCE_TOPICS_FILE = 'source_topics.json'
SOURCE_PRIOR_MIN_SAMPLES = 8   # 少于这么多篇不下结论
SOURCE_PRIOR_SHARE = 0.8       # 某一类占比到这个数才算"稳定地只发这一路"


def source_topics_path(path=None):
    """先验表的落盘位置。默认与 config.json 同目录（家目录）。"""
    return path or os.path.join(os.path.dirname(CONFIG_PATH), SOURCE_TOPICS_FILE)


def load_source_topics(path=None):
    """读先验表：`{来源: {主题: 次数}}`。

    读不出、坏掉、不是字典——一律返回 `{}`。这是**辅助**数据，坏了不该让日报挂掉。
    """
    try:
        with open(source_topics_path(path), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for source, counts in data.items():
        if isinstance(source, str) and source.strip() and isinstance(counts, dict):
            out[source] = counts
    return out


def record_source_topics(pairs, path=None):
    """把这一批 `(来源, 主题)` 累加进先验表，返回摘要 dict。

    **只增不减**（与 frontmatter 的 only-increase 同一个纪律）：累计次数是你事后判断
    "这个号到底稳不稳"的唯一依据，抹掉就回不来了。
    来源为空、主题不在 TOPICS 里的都不计——计了会污染分母，让占比算错。
    """
    import tempfile as _tmp   # 模块顶没导入它：本文件里它是函数内导入的
    store = load_source_topics(path)
    added = skipped = 0
    for source, topic in pairs:
        source = (source or '').strip()
        topic = (topic or '').strip()
        if not source or topic not in TOPICS:
            skipped += 1
            continue
        bucket = store.setdefault(source, {})
        try:
            bucket[topic] = int(bucket.get(topic, 0)) + 1
        except (TypeError, ValueError):
            bucket[topic] = 1      # 值被改坏了，从 1 重新数，别让它把整个文件废掉
        added += 1
    target = source_topics_path(path)
    try:
        os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
        fd, tmp_path = _tmp.mkstemp(suffix='.json', dir=os.path.dirname(target) or '.')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(store, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp_path, target)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    except OSError as exc:
        # 写不进去（权限、磁盘）不能让日报失败——它只是记个账。
        return {'sources': len(store), 'added': added, 'skipped': skipped,
                'error': str(exc)}
    return {'sources': len(store), 'added': added, 'skipped': skipped, 'error': ''}


def stable_source_topic(counts, min_samples=SOURCE_PRIOR_MIN_SAMPLES,
                        share=SOURCE_PRIOR_SHARE):
    """某个来源是不是已经**稳定地只发某一类**。

    返回 `(主题, 占比, 样本数)`；还判不了返回 `None`。
    **`None` 是"还不知道"，不是"没有主题"**——调用方必须把这两种情况分开，
    别把 `None` 当成某个默认主题用（那正好是"来源判不准"最坏的那种错）。
    """
    clean = {}
    for topic, count in (counts or {}).items():
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        if value > 0:
            clean[topic] = value
    total = sum(clean.values())
    if total < min_samples:
        return None
    topic, top = max(clean.items(), key=lambda kv: kv[1])
    ratio = top / total
    if ratio < share:
        return None
    return (topic, ratio, total)


def source_prior_candidates(store, exclude, **kwargs):
    """先验里"已经稳到能判"且落在排除集里的来源，按样本数从多到少。

    只报出来给人看，**不据此跳过任何东西**：跳过是不可逆的，而这张表还在长。"""
    rows = []
    for source, counts in (store or {}).items():
        stable = stable_source_topic(counts, **kwargs)
        if stable and stable[0] in (exclude or ()):
            rows.append((source, stable[0], stable[1], stable[2]))
    return sorted(rows, key=lambda row: (-row[3], row[0]))

def decrypt_lock(locked_str: str) -> str:
    if not locked_str or not locked_str.startswith('lock:'):
        return locked_str
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.backends import default_backend

    raw = base64.b64decode(locked_str[5:])
    salt, iv, auth_tag, ciphertext = raw[:16], raw[16:28], raw[28:44], raw[44:]
    machine_id = f'{socket.gethostname()}-{os.environ.get("USERNAME", "")}-weflow-cli'
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000,
                     backend=default_backend())
    key = kdf.derive(machine_id.encode('utf-8'))
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(iv, ciphertext + auth_tag, None).decode()


def get_db_config(config=None):
    if config is None:
        config = load_config()
    return {
        'nt_db': config.get('ntDbPath', ''),
        'nt_key': decrypt_lock(config.get('ntKey', '')),
        'nt_salt': config.get('ntSalt', ''),
        'contact_db': config.get('contactDbPath', ''),
        'contact_key': decrypt_lock(config.get('contactKey', '')),
        'contact_salt': config.get('contactSalt', ''),
    }


def get_api_key(config=None) -> str:
    """读取 AI API key (自动解密 lock: 前缀)。"""
    if config is None:
        config = load_config()
    return decrypt_lock(config.get('deepseekApiKey', ''))


def get_dashscope_key(config=None) -> str:
    """读取阿里云百炼（DashScope）的 embedding key，自动解密 lock: 前缀。

    和 `get_api_key` / `get_typesafe_key` 同一形状：**不要直接 config.get**——
    这个键进了 `ENCRYPTED_KEYS`，磁盘上是密文。解密失败回空串而不是抛，
    理由同 get_typesafe_key（跨机器拷配置时它该表现为"没配"，不是崩）。
    """
    if config is None:
        try:
            config = load_config()
        except Exception:
            return ''
    try:
        return decrypt_lock(config.get('dashscopeApiKey', ''))
    except Exception:
        return ''


def get_typesafe_key(config=None) -> str:
    """读取 TypeSafe (Jev) 决策模型的 key，自动解密 lock: 前缀。

    解密失败**回空串而不是抛**：TS 侧 lockDecrypt() 失败就是静默回 ''，而
    decrypt_lock() 是抛的。两份 config.json 一跨机器拷贝，同一份密文在
    TS 侧表现为"没配 key"、在这里表现为崩——那会让日报整个挂掉，而它本该
    只是退回到 LLM 解析路径。
    """
    if config is None:
        try:
            config = load_config()
        except Exception:
            return ''
    try:
        return decrypt_lock(config.get('typesafeApiKey', ''))
    except Exception:
        return ''


# ======================================================================
# Markdown / Frontmatter
# ======================================================================

def write_with_frontmatter(filepath: str, frontmatter: dict, body: str):
    import tempfile as _tmp
    fm_lines = ['---']
    for k, v in frontmatter.items():
        if isinstance(v, list):
            fm_lines.append(f'{k}: [{", ".join(v)}]')
        elif isinstance(v, str) and ('"' in v or ':' in v or '#' in v):
            vs = v.strip()
            fm_lines.append(f'{k}: "{vs}"' if not (vs.startswith('"') and vs.endswith('"'))
                            else f'{k}: {v}')
        else:
            fm_lines.append(f'{k}: {v}')
    fm_lines.append('---')
    fm_block = '\n'.join(fm_lines) + '\n\n'
    dir_name = os.path.dirname(filepath) or '.'
    fd, tmp_path = _tmp.mkstemp(suffix='.md', dir=dir_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(fm_block)
            f.write(body)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 Markdown 文件中的 YAML frontmatter。"""
    if not content.startswith('---'):
        return {}, content
    end = content.find('---', 3)
    if end == -1:
        return {}, content
    fm_text = content[3:end].strip()
    body = content[end + 3:].lstrip('\n')
    result = {}
    for line in fm_text.split('\n'):
        line = line.strip()
        if not line or ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip()
        val = val.strip()
        if val.startswith('[') and val.endswith(']'):
            inner = val[1:-1]
            items = [v.strip().strip('"\'') for v in inner.split(',')] if inner.strip() else []
            result[key] = items
        elif val.startswith('"') and val.endswith('"'):
            result[key] = val[1:-1]
        elif val.startswith("'") and val.endswith("'"):
            result[key] = val[1:-1]
        else:
            result[key] = val
    return result, body


def format_wikilinks(concepts: list[tuple[str, str]]) -> str:
    lines = ['## 相关概念', '']
    for name, desc in concepts:
        if desc:
            lines.append(f'- [[{name}]] — {desc}')
        else:
            lines.append(f'- [[{name}]]')
    return '\n'.join(lines) + '\n'


# ======================================================================
# 用户定位 & 行动建议（向后兼容）
# ======================================================================

DEFAULT_USER_PROFILE = (
    '你是一名对AI感兴趣的环境科学研究生，研究方向是计算机与环境的交叉领域'
    '（如环境模型、大气污染模拟、遥感反演、环境大数据分析、LCA生命周期评估等）。'
    '你关注AI工具如何提升科研效率、环境数据处理新技术、以及交叉领域的学术机会。'
)

ACTION_PROMPT = """基于以下文章，为读者生成可落地的行动建议。

【读者定位】
{profile}

【文章信息】
标题：{title}
来源：{source}
主题：{topic}
摘要：{summary}

【正文节选】
{content}

【输出格式】
### 相关度
（高/中/低 + 一句话解释）

### 行动建议
- **立即可做**：1-2个今天就能执行的具体动作
- **本周计划**：1个本周可以推进的中期动作
- **长期关注**：1个值得持续跟踪的方向（相关度为低时省略）"""


def generate_action_suggestion(title: str, source: str, topic: str,
                                summary: str, content: str,
                                api_key: str, profile: str = '',
                                engine_type: str = 'deepseek',
                                max_tokens: int = 1500) -> str:
    if not profile:
        profile = DEFAULT_USER_PROFILE
    prompt = ACTION_PROMPT.format(
        profile=profile, title=title, source=source, topic=topic,
        summary=summary[:500], content=content[:3000],
    )
    engine = create_engine(engine_type, api_key)
    return engine.chat(prompt, max_tokens=max_tokens)

# ---------------------------------------------------------------- 知识卡片的公共件
#
# `chat_notes.py`（对话线）与 `article_notes.py`（文章线）产出的是**同一种卡片**：
# 带 frontmatter + `## AI 摘要` + `[[概念]] — 说明` 的 markdown，都喂给 `compile_wiki`。
# 所以这几样放在这里，一份实现两边用——两处各写一份，早晚有一处会漏。

SEPARATOR = '—'   # 与 `compile_wiki` 认的破折号一致（那条描述要能被它读回描述）

# 文件名里不许出现的字符（Windows 一套 + 控制字符）
_ILLEGAL_IN_NAME = set('\\/:*?"<>|') | {chr(code) for code in range(0, 32)}


def plain(text) -> str:
    """自由文本里的 `[[…]]` 一律拆掉。

    纪律是「wikilink 只由我们的代码渲染，不由模型写」：`compile_wiki.scan_articles` 收正文里
    **所有** wikilink，它不会问这是谁写的——模型在摘要里随手一个 `[[X]]`，就会凭空长出一个概念。
    """
    out, index = [], 0
    source = str(text or '')
    while True:
        start = source.find('[[', index)
        if start < 0:
            out.append(source[index:])
            break
        end = source.find(']]', start)
        if end < 0:
            out.append(source[index:])
            break
        out.append(source[index:start])
        out.append(source[start + 2:end])
        index = end + 2
    return ''.join(out).replace('[[', '').replace(']]', '')


def note_path(out_root, name: str, fallback: str = '未命名'):
    """一张卡一个文件。名字要过文件名安全：会话/文章名里有 `/` 会写到别的目录去。"""
    from pathlib import Path as _Path
    cleaned = ''.join('_' if ch in _ILLEGAL_IN_NAME else ch for ch in str(name))
    cleaned = ' '.join(cleaned.split())[:80].strip().strip('.')
    return _Path(out_root) / ('%s.md' % (cleaned or fallback))
