"""NT 读取的两个入口共用的东西：分片发现、密钥派生、列探测。

为什么要单独一个模块：`nt_decrypt.py`（messages / sessions / CLI 那条路）和
`export_chat_html.py`（导出那条路）**各自实现过同名函数，而它们并不相同**。
差异不会报错，只在边界处让两条路给出不同结果：

* `discover_message_shards` 的**契约**不同——`nt_decrypt` 版在 glob 不到分片时返回
  `[配置的那个库]`（永不返回空），导出器版返回空表、由调用点
  `if not shards: shards = [db_path]` 自己补。补偿写在调用点意味着**任何新调用方
  忘了它就会静默拿到零个分片**。
* `derive_database_key` 两份的代码逐字相同，只有 docstring 不同——纯重复。
* `table_columns` 也逐字相同（两份都是我加的）。

所以这里只留一份实现，**契约取更安全的那一版**（下面的 docstring 各自写明）。
导入本模块不需要 sqlcipher3：这几个函数都不连数据库。
"""

import pathlib
from pathlib import Path

# 与 message_*.db 同目录、但不是消息分片的派生库。
SHARD_EXCLUDED = {'message_fts.db', 'message_resource.db'}

# 消息行的"锚点"列：任意一列在场，一行才谈得上身份与顺序。
#
# **两处语义，顺序只对其中一处有意义**：
#   * 身份/模式检查——`nt_decrypt` 用它判断"这个分片是不是根本没有消息表该有的列"，
#     这一处是集合语义，顺序无关。
#   * `ORDER BY`——两个读取入口都按这个顺序排：`create_time` 优先。这一处顺序**就是
#     行为**：把 `local_id` 排到前面会让会话顺序变成另一回事，而且没有任何报错。
#
# 收在这里是因为它原本有三份：`nt_decrypt` 一份具名、同文件里 `ORDER BY` 又写了一份
# 字面量、`export_chat_html` 再写一份。三份一致时看不出问题，谁改了一处的顺序，
# 另一处就静默按别的顺序排消息——正是 `nt_common` 存在的理由那类分叉。
MESSAGE_ANCHOR_COLUMNS = ('create_time', 'local_id', 'server_id')


def discover_message_shards(db_path):
    """配置库旁边的每一个 NT 消息分片。

    **永不返回空表**：glob 不到任何分片时返回 `[db_path]` 本身。调用方不必自己
    兜底——两份实现里原本只有一份保证这一点，而导出器是在调用点补的。
    """
    path = Path(db_path)
    if not path.parent.is_dir():
        return [str(path)]
    shards = sorted(str(p) for p in path.parent.glob('message_*.db')
                    if p.name.lower() not in SHARD_EXCLUDED)
    return shards or [str(path)]


_KEY_MAP_CACHE = {'mtime': None, 'map': {}}


def scan_key_map():
    """配置里的「盐 → 该库 enc_key」映射；没有/解不开就返回空 dict。

    缓存在进程内，按配置文件 mtime 失效——`derive_database_key` 每个库都会调，
    每次都读盘+解密会明显拖慢查询。
    """
    import json
    import os
    try:
        from _utils import load_config, decrypt_lock
        cfg_path = pathlib.Path(os.path.expanduser('~')) / '.weflow-cli' / 'config.json'
        mtime = cfg_path.stat().st_mtime_ns if cfg_path.exists() else None
        if _KEY_MAP_CACHE['mtime'] == mtime:
            return _KEY_MAP_CACHE['map']
        # 同一条语句里就解密（仓库约定：加密键不能先把密文当变量拿出去，见 encrypted_config_test）
        raw_enc = (load_config() or {}).get('dbKeyMap') or ''
        mapping = {}
        if raw_enc:
            try:
                decoded = decrypt_lock(raw_enc) if raw_enc.startswith('lock:') else raw_enc
                parsed = json.loads(decoded)
                if isinstance(parsed, dict):
                    mapping = {str(k).lower(): str(v).lower() for k, v in parsed.items()}
            except Exception:
                mapping = {}
        _KEY_MAP_CACHE['mtime'] = mtime
        _KEY_MAP_CACHE['map'] = mapping
        return mapping
    except Exception:
        return {}


def derive_database_key(path, fallback_key, fallback_salt, passphrase=''):
    """每分片的 SQLCipher 密钥（微信 4.1.12.26+）。

    顺序：
      1. **配置里的 dbKeyMap**：按该库自己的 salt 精确命中 —— 这是 `dbkey --scan`
         扫出来的，不依赖 passphrase，也不依赖客户端行为
      2. passphrase 与**该库自己的** 16 字节文件头 salt 做 PBKDF2-HMAC-SHA512
      3. 都没命中 → 原样返回配置里那一对（老安装的分片 0 就是这么开的）

    第 1 条不命中时行为与从前完全一致，所以老配置不受影响。
    """
    import hashlib

    try:
        with open(path, 'rb') as fh:
            salt = fh.read(16)
    except OSError:
        return fallback_key, fallback_salt
    if len(salt) != 16:
        return fallback_key, fallback_salt

    salt_hex = salt.hex()
    mapped = scan_key_map().get(salt_hex)
    if mapped:
        return mapped, salt_hex

    if not passphrase:
        return fallback_key, fallback_salt
    try:
        raw_passphrase = bytes.fromhex(passphrase)
        key = hashlib.pbkdf2_hmac('sha512', raw_passphrase, salt, 256000, 32).hex()
        return key, salt_hex
    except ValueError:
        return fallback_key, fallback_salt


def table_columns(cursor, table):
    """`table` 的列名，取不到就返回空集合。

    消息表的列在不同微信版本之间会增删，所以读之前要问一句——直接按位置取字段的话，
    少一列会让后面每个字段都错位，而且不报错。
    """
    try:
        rows = cursor.execute('PRAGMA table_info("%s")' % table).fetchall()
    except Exception:
        return set()
    return {row[1] for row in rows}


#: zstd frame magic。`message_content` 在 4.x 里**可能被压缩**（issue #12）。
ZSTD_MAGIC = bytes([0x28, 0xB5, 0x2F, 0xFD])


def decode_content(value):
    """`message_content` → 文本。**所有读消息的脚本都该走这里。**

    三种形态都要接：`str`（老格式，直接返回）、zstd frame（4.x 压缩后存的）、
    以及普通 bytes。解不出就回空串——调用方拿到的永远是字符串，不需要自己判断类型。

    为什么要一个公共函数：这条规则以前散在五个脚本里，其中四个写的是
    `if not isinstance(content, str): continue`，于是**压缩正文被整条跳过**——
    消息不进索引、不进待办、不进年度统计，而且不报错（issue #12 同源）。
    """
    if isinstance(value, str):
        return value
    if not value:
        return ''
    try:
        data = bytes(value)
    except Exception:
        return ''
    if data[:4] == ZSTD_MAGIC:
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(data).decode('utf-8', 'ignore')
        except Exception:
            # 声称是 zstd 但内容坏了：回退成按字节解码，不抛也不丢整条消息。
            return data.decode('utf-8', 'ignore')
    return data.decode('utf-8', 'ignore')
