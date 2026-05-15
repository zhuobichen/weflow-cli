"""
weflow-cli 公共工具函数 — 供 biz_daily / classify_daily / chat_report 共用。
"""
import os, json, base64, socket
import urllib.request

# ====== Config ======

CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.weflow-cli', 'config.json')


# ====== DeepSeek ======

def call_deepseek(prompt: str, api_key: str, max_tokens=2000, timeout=60) -> str:
    """Call DeepSeek V4 API. Returns response text."""
    payload = json.dumps({
        'model': 'deepseek-v4-pro',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.deepseek.com/v1/chat/completions',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data['choices'][0]['message']['content']


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
