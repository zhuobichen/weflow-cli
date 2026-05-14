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
