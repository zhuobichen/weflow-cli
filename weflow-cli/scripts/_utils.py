"""
weflow-cli 公共工具函数 — 供 biz_daily / classify_daily / chat_report 共用。
"""
import os, json, base64, socket
import urllib.request

# ====== Config ======

CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.weflow-cli', 'config.json')


# ====== AI Engine 抽象 ======

class AIEngine:
    """OpenAI-compatible API 引擎基类。"""
    def __init__(self, api_key: str, base_url: str, model: str, temperature=0.2, timeout=60):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def chat(self, prompt: str, max_tokens=2000) -> str:
        """发送 prompt，返回文本响应。"""
        payload = json.dumps({
            'model': self.model,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens,
            'temperature': self.temperature,
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

    def check_connectivity(self) -> tuple[bool, str]:
        """检测连通性，返回 (ok, message)。"""
        try:
            self.chat('Hi', max_tokens=1)
            return True, 'OK'
        except Exception as e:
            return False, str(e)


class DeepSeekEngine(AIEngine):
    def __init__(self, api_key: str, model='deepseek-v4-pro', timeout=60):
        super().__init__(api_key, 'https://api.deepseek.com/v1', model, timeout=timeout)


class ClaudeEngine(AIEngine):
    def __init__(self, api_key: str, model='claude-sonnet-4-6', timeout=90):
        super().__init__(api_key, 'https://api.anthropic.com/v1', model, timeout=timeout)


class OllamaEngine(AIEngine):
    def __init__(self, model='llama3', timeout=120):
        super().__init__('ollama', 'http://localhost:11434/v1', model, timeout=timeout)


def create_engine(engine_type: str, api_key='') -> AIEngine:
    """工厂函数：根据类型创建 AI 引擎实例。"""
    engines = {
        'deepseek': lambda: DeepSeekEngine(api_key),
        'claude': lambda: ClaudeEngine(api_key),
        'ollama': lambda: OllamaEngine(),
    }
    t = engine_type.lower()
    if t in engines:
        return engines[t]()
    raise ValueError(f"未知引擎: {engine_type}，可选: {', '.join(engines.keys())}")


# ====== DeepSeek (向后兼容) ======

def call_deepseek(prompt: str, api_key: str, max_tokens=2000, timeout=60) -> str:
    """调用 DeepSeek V4 API（向后兼容，内部使用 AIEngine）。"""
    engine = DeepSeekEngine(api_key, timeout=timeout)
    return engine.chat(prompt, max_tokens=max_tokens)


# ====== Config Decrypt ======

def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def decrypt_lock(locked_str: str) -> str:
    """Decrypt a 'lock:' prefixed value (AES-256-GCM)."""
    if not locked_str or not locked_str.startswith('lock:'):
        return locked_str
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.backends import default_backend

    raw = base64.b64decode(locked_str[5:])
    salt, iv, auth_tag, ciphertext = raw[:16], raw[16:28], raw[28:44], raw[44:]
    machine_id = f'{socket.gethostname()}-{os.environ.get("USERNAME", "")}-weflow-cli'
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000, backend=default_backend())
    key = kdf.derive(machine_id.encode('utf-8'))
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(iv, ciphertext + auth_tag, None).decode()


def write_with_frontmatter(filepath: str, frontmatter: dict, body: str):
    """原子写入带 YAML frontmatter 的 Markdown 文件。"""
    import tempfile as _tmp
    fm_lines = ['---']
    for k, v in frontmatter.items():
        if isinstance(v, list):
            fm_lines.append(f'{k}: [{", ".join(v)}]')
        elif isinstance(v, str) and ('"' in v or ':' in v or '#' in v):
            # Only wrap if not already quoted
            v_stripped = v.strip()
            if not (v_stripped.startswith('"') and v_stripped.endswith('"')):
                fm_lines.append(f'{k}: "{v}"')
            else:
                fm_lines.append(f'{k}: {v}')
        else:
            fm_lines.append(f'{k}: {v}')
    fm_lines.append('---')
    fm_block = '\n'.join(fm_lines) + '\n\n'

    dir_name = os.path.dirname(filepath)
    fd, tmp_path = _tmp.mkstemp(suffix='.md', dir=dir_name or '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(fm_block)
            f.write(body)
        os.replace(tmp_path, filepath)
    except:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """从 Markdown 内容中提取 YAML frontmatter，返回 (frontmatter_dict, body)。

    无 frontmatter 时返回 ({}, content)。
    兼容 YAML 格式，也兼容简单的 key: value / key: [v1, v2] 格式（无需 PyYAML）。
    """
    import re as _re
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
        # list: [a, b, c]
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
    """将概念列表格式化为 [[Wiki Links]] 段落。

    concepts: [(name, description), ...]
    """
    lines = ['## 相关概念', '']
    for name, desc in concepts:
        if desc:
            lines.append(f'- [[{name}]] — {desc}')
        else:
            lines.append(f'- [[{name}]]')
    return '\n'.join(lines) + '\n'


def get_db_config(config=None):
    """Extract NT database paths and keys from config."""
    if config is None:
        config = load_config()
    nt_db = config.get('ntDbPath', '')
    nt_key_enc = config.get('ntKey', '')
    nt_salt = config.get('ntSalt', '')
    contact_db = config.get('contactDbPath', '')
    contact_key_enc = config.get('contactKey', '')
    contact_salt = config.get('contactSalt', '')

    return {
        'nt_db': nt_db, 'nt_key': decrypt_lock(nt_key_enc) if nt_key_enc else '',
        'nt_salt': nt_salt,
        'contact_db': contact_db, 'contact_key': decrypt_lock(contact_key_enc) if contact_key_enc else '',
        'contact_salt': contact_salt,
    }


# ====== 用户定位 & 行动建议 ======

DEFAULT_USER_PROFILE = (
    "你是一名对AI感兴趣的环境科学研究生，研究方向是计算机与环境的交叉领域"
    "（如环境模型、大气污染模拟、遥感反演、环境大数据分析、LCA生命周期评估等）。"
    "你关注AI工具如何提升科研效率、环境数据处理新技术、以及交叉领域的学术机会。"
)

ACTION_PROMPT = """基于以下文章，为读者生成**可落地的行动建议**。

【读者定位】
{profile}

【文章信息】
标题：{title}
来源：{source}
主题：{topic}
摘要：{summary}

【正文节选】
{content}

请根据读者定位，判断这篇文章与他/她的相关性，然后给出建议。严格按以下格式返回：

### 相关度
（高/中/低，一句话解释原因）

### 行动建议
- **立即可做**：1-2个今天就能执行的具体动作（如"试用某工具"、"收藏某论文"、"关注某公众号"）
- **本周计划**：1个本周可以推进的中期动作（如"跑一个demo"、"写一段代码"、"整理一个文献列表"）
- **长期关注**：1个值得持续跟踪的方向（仅当相关度为高或中时输出）

如果相关度为低，行动建议部分可以简短，重点解释为什么与读者关系不大即可。"""


def generate_action_suggestion(title: str, source: str, topic: str,
                                summary: str, content: str,
                                api_key: str, profile: str = '',
                                engine_type: str = 'deepseek',
                                max_tokens: int = 1500) -> str:
    """调用 AI 生成个性化行动建议。

    Args:
        title: 文章标题
        source: 文章来源（公众号名）
        topic: 文章主题分类
        summary: 文章摘要
        content: 文章正文（节选）
        api_key: AI API key
        profile: 用户定位描述（默认使用 DEFAULT_USER_PROFILE）
        engine_type: AI 引擎类型
        max_tokens: 最大 token 数

    Returns:
        AI 生成的行动建议 markdown 文本
    """
    if not profile:
        profile = DEFAULT_USER_PROFILE
    prompt = ACTION_PROMPT.format(
        profile=profile,
        title=title,
        source=source,
        topic=topic,
        summary=summary[:500],
        content=content[:3000],
    )
    engine = create_engine(engine_type, api_key)
    return engine.chat(prompt, max_tokens=max_tokens)


# ====== 对话待办提取 ======

TODO_EXTRACT_PROMPT = """从以下聊天记录中提取所有待办事项、承诺、截止日期和重要约定。

【聊天记录】
{messages}

请提取所有隐含和明确的待办事项，严格按以下格式返回（每行一条）：

- [ ] 【截止: YYYY-MM-DD】事项描述
- [ ] 【无期限】事项描述
- [ ] 【截止: YYYY-MM-DD】事项描述（来源：某人说"xxx"）

规则：
1. 截止日期尽量精确，如果只说了"下周一"则推算具体日期
2. "帮我看看"、"回头聊"等模糊表述不要提取
3. 论文分享、工具推荐等有明确行动指向的才提取
4. 如果没有任何待办事项，返回：暂无待办事项
5. 只输出列表，不要其他解释"""


def extract_todos_from_messages(messages: list[dict], api_key: str,
                                  current_date: str = '') -> list[dict]:
    """从聊天消息中批量提取待办事项。

    Args:
        messages: 消息列表，每项包含 sender, content, timestamp
        api_key: DeepSeek API key
        current_date: 当前日期（用于推算相对日期），格式 YYYY-MM-DD

    Returns:
        待办事项列表，每项包含 deadline, task, source
    """
    if not current_date:
        from datetime import datetime, timezone, timedelta
        tz = timezone(timedelta(hours=8))
        current_date = datetime.now(tz).strftime('%Y-%m-%d')

    # 格式化消息
    msg_lines = []
    for m in messages:
        sender = m.get('sender', '未知')
        content = m.get('content', '').strip()
        ts = m.get('timestamp', '')
        if content and len(content) > 2:
            msg_lines.append(f'[{ts}] {sender}: {content}')

    if not msg_lines:
        return []

    # 分批处理（每批约 3000 字）
    batch_size = 50
    all_todos = []

    for i in range(0, len(msg_lines), batch_size):
        batch = msg_lines[i:i + batch_size]
        prompt = TODO_EXTRACT_PROMPT.format(
            messages='\n'.join(batch),
            current_date=current_date,
        )
        response = call_deepseek(prompt, api_key, max_tokens=2000, timeout=60)

        # 解析返回的 todo 列表
        for line in response.strip().split('\n'):
            line = line.strip()
            if line.startswith('- [ ]') or line.startswith('- [x]'):
                # 提取截止日期
                deadline = '无期限'
                date_match = __import__('re').search(r'【截止:\s*(\d{4}-\d{2}-\d{2})】', line)
                if date_match:
                    deadline = date_match.group(1)
                elif '【无期限】' in line:
                    deadline = '无期限'
                else:
                    date_match2 = __import__('re').search(r'【截止:\s*(.+?)】', line)
                    if date_match2:
                        deadline = date_match2.group(1)

                # 提取任务描述
                task = __import__('re').sub(r'【[^】]*】', '', line)
                task = __import__('re').sub(r'^- \[[ x]\]\s*', '', task).strip()

                # 提取来源
                source = ''
                src_match = __import__('re').search(r'（来源：(.+?)）', task)
                if src_match:
                    source = src_match.group(1)
                    task = __import__('re').sub(r'\s*（来源：.+?）', '', task).strip()

                if task and task != '暂无待办事项':
                    all_todos.append({
                        'deadline': deadline,
                        'task': task,
                        'source': source,
                    })

    return all_todos
