"""
weflow-cli 公共工具函数 — 供 biz_daily / classify_daily / chat_report 等共用。
"""
import os, json, base64, socket, urllib.request


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
    def __init__(self, api_key: str, model='deepseek-v4-pro', timeout=60):
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
        return DeepSeekEngine(api_key)
    elif t == 'claude':
        return ClaudeEngine(api_key)
    elif t == 'ollama':
        return OllamaEngine()
    raise ValueError(
        f'未知引擎: {engine_type}，可选: local / deepseek / claude / ollama'
    )


def call_deepseek(prompt: str, api_key: str, max_tokens=2000, timeout=60) -> str:
    """向后兼容：调用 DeepSeek API。"""
    return DeepSeekEngine(api_key, timeout=timeout).chat(prompt, max_tokens=max_tokens)


# ======================================================================
# Config Decrypt
# ======================================================================

def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


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
