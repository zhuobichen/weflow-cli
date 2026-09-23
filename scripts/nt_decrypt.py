#!/usr/bin/env python3
"""
WeChat NT (4.x) Database Access Tool
Uses sqlcipher3 to decrypt and query NT-format databases.
"""
import sys
import os
import json
import html
import re
import hmac
import struct
import hashlib
import ctypes
from ctypes import wintypes, c_void_p, c_size_t, create_string_buffer, byref, sizeof
from pathlib import Path

sqlcipher = None


def require_sqlcipher():
    """Load SQLCipher only for database operations, not path discovery."""
    global sqlcipher
    if sqlcipher is not None:
        return sqlcipher
    try:
        from sqlcipher3 import dbapi2 as sqlcipher_module
    except ImportError as error:
        raise RuntimeError("需要 sqlcipher3: pip install sqlcipher3") from error
    sqlcipher = sqlcipher_module
    return sqlcipher

# ========== Memory Scanner ==========
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_void_p),
        ('AllocationBase', ctypes.c_void_p),
        ('AllocationProtect', wintypes.DWORD),
        ('PartitionId', wintypes.WORD),
        ('RegionSize', ctypes.c_size_t),
        ('State', wintypes.DWORD),
        ('Protect', wintypes.DWORD),
        ('Type', wintypes.DWORD),
    ]

IS_WINDOWS = os.name == 'nt'

if IS_WINDOWS:
    kernel32 = ctypes.windll.kernel32
    ReadProcessMemory = kernel32.ReadProcessMemory
    ReadProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID, ctypes.c_size_t, ctypes.POINTER(c_size_t)]
    ReadProcessMemory.restype = wintypes.BOOL
    VirtualQueryEx = kernel32.VirtualQueryEx
    VirtualQueryEx.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
    VirtualQueryEx.restype = ctypes.c_size_t
else:
    kernel32 = None


def find_weixin_pid():
    """Find WeChat process ID (Windows: pymem; Linux: /proc scan)."""
    if not IS_WINDOWS:
        return find_weixin_pid_linux()
    try:
        import pymem, pymem.process
        for proc in pymem.process.list_processes():
            try:
                name = proc.szExeFile
                if isinstance(name, bytes):
                    name = name.decode('utf-8', errors='ignore')
                if name.lower() == 'weixin.exe':
                    return proc.th32ProcessID
            except:
                pass
    except ImportError:
        pass
    return None


_LINUX_WECHAT_COMMS = {'wechat', 'wechatappex', 'weixin'}
_LINUX_EXE_PREFIX_DENY = ('python', 'bash', 'sh', 'zsh', 'node', 'perl', 'ruby', 'electron')


def _is_linux_wechat_process(pid):
    if pid == os.getpid():
        return False
    try:
        with open(f'/proc/{pid}/comm') as f:
            comm = f.read().strip().lower()
        if comm in _LINUX_WECHAT_COMMS:
            return True
        try:
            exe = os.path.realpath(os.readlink(f'/proc/{pid}/exe'))
        except OSError:
            return False
        name = os.path.basename(exe).lower()
        if any(name.startswith(p) for p in _LINUX_EXE_PREFIX_DENY):
            return False
        return 'wechat' in name or 'weixin' in name
    except (PermissionError, FileNotFoundError, ProcessLookupError):
        return False


def find_weixin_pid_linux():
    """Find Linux WeChat main process (largest RSS among candidates)."""
    best = None
    best_rss = -1
    try:
        pids = os.listdir('/proc')
    except OSError:
        return None
    for pid_str in pids:
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        if not _is_linux_wechat_process(pid):
            continue
        try:
            with open(f'/proc/{pid}/statm') as f:
                rss_kb = int(f.read().split()[1]) * 4
        except (OSError, IndexError, ValueError):
            rss_kb = 0
        if rss_kb > best_rss:
            best_rss = rss_kb
            best = pid
    return best


def scan_memory_keys(pid):
    """Scan process memory for x'<64hex_key><32hex_salt>' patterns.

    Returns (keys, error): keys is a list of {"key","salt"} dicts,
    error is None on success or 'permission' / 'gone' / 'not_windows'.
    """
    if not IS_WINDOWS:
        return scan_memory_keys_linux(pid)

    hProcess = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not hProcess:
        return [], None

    pattern = re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'")
    keys_found = []
    address = 0x10000

    while address < 0x7FFFFFFFFFFF:
        mbi = MEMORY_BASIC_INFORMATION()
        result = VirtualQueryEx(hProcess, ctypes.c_void_p(address), ctypes.byref(mbi), sizeof(mbi))
        if result == 0:
            break

        region_addr = mbi.BaseAddress or 0
        region_size = mbi.RegionSize or 0

        if (mbi.State == MEM_COMMIT and
                region_size > 256 and region_size < 200 * 1024 * 1024 and
                mbi.Protect not in (0, PAGE_NOACCESS, PAGE_GUARD)):

            pos = region_addr
            end = region_addr + region_size
            while pos < end:
                chunk_size = min(65536, end - pos)
                buf = create_string_buffer(chunk_size)
                bytesRead = c_size_t(0)
                ok = ReadProcessMemory(hProcess, ctypes.c_void_p(pos), buf, chunk_size, byref(bytesRead))
                if ok and bytesRead.value > 0:
                    data = buf.raw[:bytesRead.value]
                    for m in pattern.finditer(data):
                        key_hex = m.group(1).decode()
                        salt_hex = m.group(2).decode()
                        keys_found.append((key_hex, salt_hex))
                pos += chunk_size

        address = region_addr + region_size

    kernel32.CloseHandle(hProcess)

    # Deduplicate
    seen = set()
    unique_keys = []
    for k, s in keys_found:
        pair = (k, s)
        if pair not in seen:
            seen.add(pair)
            unique_keys.append({"key": k, "salt": s})

    return unique_keys, None


_LINUX_SKIP_MAPPINGS = {'[vdso]', '[vsyscall]', '[vvar]'}
_LINUX_SKIP_PREFIXES = ('/usr/lib/', '/lib/', '/usr/share/')


def scan_memory_keys_linux(pid):
    """Scan /proc/<pid>/maps + /proc/<pid>/mem for the key pattern.

    Requires root or CAP_SYS_PTRACE (or the target being a descendant
    of this process when yama ptrace_scope=1).
    """
    regions = []
    try:
        with open(f'/proc/{pid}/maps') as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2 or 'r' not in parts[1]:
                    continue
                if len(parts) >= 6:
                    name = parts[5]
                    if name in _LINUX_SKIP_MAPPINGS:
                        continue
                    name_lower = name.lower()
                    if name.startswith(_LINUX_SKIP_PREFIXES) and \
                            'wcdb' not in name_lower and 'wechat' not in name_lower and 'weixin' not in name_lower:
                        continue
                try:
                    start_s, end_s = parts[0].split('-')
                    start = int(start_s, 16)
                    size = int(end_s, 16) - start
                except ValueError:
                    continue
                if 0 < size < 500 * 1024 * 1024:
                    regions.append((start, size))
    except PermissionError:
        return [], 'permission'
    except (FileNotFoundError, ProcessLookupError):
        return [], 'gone'

    pattern = re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'")
    keys_found = []
    try:
        with open(f'/proc/{pid}/mem', 'rb') as mem:
            for base, size in regions:
                try:
                    mem.seek(base)
                    data = mem.read(size)
                except (OSError, ValueError):
                    continue
                for m in pattern.finditer(data):
                    keys_found.append((m.group(1).decode(), m.group(2).decode()))
    except PermissionError:
        return [], 'permission'
    except (FileNotFoundError, ProcessLookupError):
        return [], 'gone'

    seen = set()
    unique_keys = []
    for k, s in keys_found:
        pair = (k, s)
        if pair not in seen:
            seen.add(pair)
            unique_keys.append({"key": k, "salt": s})

    return unique_keys, None


# ========== NT Database Discovery ==========

def _is_nt_account_dir(path):
    return os.path.isdir(os.path.join(path, 'db_storage')) or \
        os.path.isdir(os.path.join(path, 'Msg'))


def _normalize_nt_root(root):
    if not root:
        return None
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(root)))
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        return None

    try:
        for entry in os.listdir(path):
            candidate = os.path.join(path, entry)
            if os.path.isdir(candidate) and _is_nt_account_dir(candidate):
                return path
    except OSError:
        return None

    for _ in range(6):
        if _is_nt_account_dir(path):
            return os.path.dirname(path)
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent

    return None


def find_nt_databases(root=None):
    """Find all NT-format databases under xwechat_files (message + contact)."""
    if root:
        normalized_root = _normalize_nt_root(root)
        candidates = [normalized_root] if normalized_root else []
    elif IS_WINDOWS:
        candidates = [
            os.path.expandvars(r'%USERPROFILE%\xwechat_files'),
            os.path.expandvars(r'%USERPROFILE%\Documents\xwechat_files'),
        ]
    else:
        home = os.path.expanduser('~')
        candidates = [
            os.path.join(home, '.local', 'share', 'xwechat_files'),
            os.path.join(home, 'xwechat_files'),
            os.path.join(home, 'Documents', 'xwechat_files'),
            os.path.join(home, '文档', 'xwechat_files'),
        ]
    xwechat = None
    for c in candidates:
        if os.path.isdir(c):
            xwechat = c
            break
    if not xwechat:
        return []

    databases = []
    for wxid_dir in os.listdir(xwechat):
        # Scan message databases
        msg_storage = os.path.join(xwechat, wxid_dir, 'db_storage', 'message')
        if os.path.isdir(msg_storage):
            for f in os.listdir(msg_storage):
                if f.endswith('.db') and not any(x in f for x in ['-shm', '-wal']):
                    full_path = os.path.join(msg_storage, f)
                    try:
                        with open(full_path, 'rb') as fh:
                            salt = fh.read(16)
                        databases.append({
                            "path": full_path,
                            "name": f"message/{f}",
                            "salt": salt.hex(),
                            "size": os.path.getsize(full_path),
                            "wxid": wxid_dir,
                        })
                    except:
                        pass

        # Scan contact database
        contact_db = os.path.join(xwechat, wxid_dir, 'db_storage', 'contact', 'contact.db')
        if os.path.isfile(contact_db):
            try:
                with open(contact_db, 'rb') as fh:
                    salt = fh.read(16)
                databases.append({
                    "path": contact_db,
                    "name": "contact/contact.db",
                    "salt": salt.hex(),
                    "size": os.path.getsize(contact_db),
                    "wxid": wxid_dir,
                })
            except:
                pass

        # Scan SNS (朋友圈) database
        sns_db = os.path.join(xwechat, wxid_dir, 'db_storage', 'sns', 'sns.db')
        if os.path.isfile(sns_db):
            try:
                with open(sns_db, 'rb') as fh:
                    salt = fh.read(16)
                databases.append({
                    "path": sns_db,
                    "name": "sns/sns.db",
                    "salt": salt.hex(),
                    "size": os.path.getsize(sns_db),
                    "wxid": wxid_dir,
                })
            except:
                pass

        # Scan favorites (收藏) database
        fav_db = os.path.join(xwechat, wxid_dir, 'db_storage', 'favorite', 'favorite.db')
        if os.path.isfile(fav_db):
            try:
                with open(fav_db, 'rb') as fh:
                    salt = fh.read(16)
                databases.append({
                    "path": fav_db,
                    "name": "favorite/favorite.db",
                    "salt": salt.hex(),
                    "size": os.path.getsize(fav_db),
                    "wxid": wxid_dir,
                })
            except:
                pass

    return databases


def find_contact_db_path(message_db_path):
    """Derive contact.db path from message_0.db path.

    message_0.db:  <xwechat_files>/<wxid>/db_storage/message/message_0.db
    contact.db:    <xwechat_files>/<wxid>/db_storage/contact/contact.db
    """
    msg_dir = os.path.dirname(message_db_path)
    wxid_dir = os.path.dirname(msg_dir)     # .../db_storage
    xwechat_dir = os.path.dirname(wxid_dir) # .../<wxid>
    contact_db = os.path.join(xwechat_dir, 'db_storage', 'contact', 'contact.db')
    if os.path.isfile(contact_db):
        return contact_db
    return None


def load_contact_names(contact_db_path, contact_key_hex, contact_salt_hex):
    """Load wxid -> {remark, nick_name} map from contact.db.

    Returns dict: {wxid: display_name}
    display_name priority: remark > nick_name > alias > wxid

    取不到就返回空表（名字是装饰性的，调用方会退回 wxid）。**连接必须在 finally
    里关**：原先 `conn.close()` 写在 try 末尾，异常路径上泄漏——而在 Windows 上
    一个没关的连接会把文件锁住（同一个坑早先在这个文件的分片读取里踩过一次）。
    读了一半也保留已经拿到的名字，而不是整份丢掉。
    """
    if not contact_db_path or not contact_key_hex or not contact_salt_hex:
        return {}
    if not os.path.isfile(contact_db_path):
        return {}

    name_map = {}
    conn = None
    try:
        raw_key = f"x'{contact_key_hex}{contact_salt_hex}'"
        conn = require_sqlcipher().connect(contact_db_path)
        c = conn.cursor()
        c.execute(f'PRAGMA key = "{raw_key}";')

        # contact.db schema: username, alias, remark, nick_name, ...
        c.execute("SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), NULLIF(alias,''), username) FROM contact")
        for username, display in c.fetchall():
            if username:
                name_map[username] = display
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return name_map


def filter_contacts(contacts, keyword, limit=0):
    """按关键字过滤联系人，**然后**才截断到 `limit`。

    顺序是这里的全部要点：先截断会让第 `limit` 个之后的人搜不到（实测 500 人的通讯录里，
    按备注名找 10 个只命中 3 个）。关键字比对 `username + displayName + remark + nickname`，
    大小写不敏感。
    """
    if not keyword:
        return contacts[:limit] if limit else contacts
    needle = keyword.lower()
    hits = [
        c for c in contacts
        if needle in (c.get('username', '') + c.get('displayName', '')
                      + c.get('remark', '') + c.get('nickname', '')).lower()
    ]
    return hits[:limit] if limit else hits


def apply_contact_names(sessions, name_map):
    """Apply contact names to session list, replacing bare wxid displayNames."""
    if not name_map:
        return sessions
    for s in sessions:
        username = s.get('username', '')
        if username in name_map:
            s['displayName'] = name_map[username]
    return sessions


def match_keys_to_databases(keys, databases):
    """Match memory keys to databases by comparing salts."""
    salt_to_key = {}
    for k in keys:
        salt_to_key[k["salt"]] = k["key"]

    matched = []
    for db in databases:
        if db["salt"] in salt_to_key:
            db["key"] = salt_to_key[db["salt"]]
            matched.append(db)

    return matched


# ========== Database Operations ==========

def connect_nt_db(db_path, key_hex, salt_hex):
    """Connect to an NT database using sqlcipher3."""
    raw_key = f"x'{key_hex}{salt_hex}'"
    conn = require_sqlcipher().connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    return conn, c


# WeChat rolls a conversation into a new shard over time, and every shard is
# encrypted with its own key. Reading only the configured database therefore
# shows a transcript that stops wherever the first shard's last write left off
# - which is silent, because the query still succeeds. Only the HTML exporter
# used to merge shards, so the same chat exported two different histories
# depending on the format asked for.

# 分片发现、密钥派生、列探测都在 nt_common 里——**只此一份**。
# 原先 `nt_decrypt` 与 `export_chat_html` 各有一份，而契约并不相同。
#
# 这一行不能省：本文件被 `spec_from_file_location` 加载时（测试就是这么加的），
# 解释器**不会**把脚本目录放进 sys.path——只有直接运行才会。少了它，单独跑
# `nt_decrypt_shards_test.py` 会以 ModuleNotFoundError 失败，而全套一起跑却能过
# （别的测试文件先插了路径）。那种"碰巧能过"正是要避免的。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nt_common import (discover_message_shards, derive_database_key,
                       table_columns, MESSAGE_ANCHOR_COLUMNS)


def verify_passphrase_native(passphrase_hex, db_path, internal_key_hex=''):
    """Check a passphrase against a database without sqlcipher3.

    Verifies the SQLCipher page-1 HMAC directly with stdlib primitives:

        mac_salt = header_salt XOR 0x3a
        key      = PBKDF2-HMAC-SHA512(passphrase, header_salt, 256000, 32)
        mac_key  = PBKDF2-HMAC-SHA512(key, mac_salt, 2, 32)
        compare HMAC-SHA512(mac_key, page1_body || page_number=1) with the
        digest stored at the end of page 1

    Why this exists: the normal way to check a key is to open the database,
    which needs the native library. When sqlcipher3 is missing or misbehaving,
    that route says nothing about whether the key itself is right - and the
    two failures look identical. This separates them.

    Returns True (correct), False (wrong), or None (cannot tell - bad input,
    file too small, or a passphrase that is not hex).
    """
    try:
        raw_passphrase = bytes.fromhex(passphrase_hex)
    except (TypeError, ValueError):
        return None
    if internal_key_hex:
        # Some WeChat builds XOR the passphrase with a key embedded in
        # Weixin.dll before deriving. Optional: the installs seen so far do
        # not need it, so an empty value must stay the default.
        try:
            internal = bytes.fromhex(internal_key_hex)
        except (TypeError, ValueError):
            return None
        if len(internal) == len(raw_passphrase):
            raw_passphrase = bytes(a ^ b for a, b in zip(raw_passphrase, internal))

    try:
        with open(db_path, 'rb') as handle:
            page = handle.read(4096)
    except OSError:
        return None
    if len(page) < 4096 or page[:16] == b'\x00' * 16:
        return None

    salt = page[:16]
    mac_salt = bytes(byte ^ 0x3a for byte in salt)
    key = hashlib.pbkdf2_hmac('sha512', raw_passphrase, salt, 256000, 32)
    mac_key = hashlib.pbkdf2_hmac('sha512', key, mac_salt, 2, 32)

    reserve = 16 + 64                      # IV + HMAC-SHA512
    reserve = ((reserve + 15) // 16) * 16  # rounded up to the AES block size
    body_end = 4096 - reserve + 16
    mac = hmac.new(mac_key, page[16:body_end], hashlib.sha512)
    mac.update(struct.pack('<I', 1))       # page number
    return hmac.compare_digest(mac.digest(), page[body_end:body_end + 64])


def connect_message_shards_detailed(db_path, key_hex, salt_hex, passphrase=''):
    """Open every shard and report what happened to each one.

    Returns (pairs, failures):
      pairs    [(shard_path, conn)] for the shards that opened
      failures [{"name": basename, "reason": "KEY_REJECTED" | "OPEN_FAILED"}]

    A shard that fails is still skipped rather than fatal - a partially
    readable transcript beats a command that refuses to run at all - but the
    failure stops being invisible. `shardsFailed` is the difference between
    "read everything" and "read what happened to be reachable", which is the
    whole point of the coverage report.

    Only the basename is reported: the caller stores this in a state file, and
    absolute paths must not end up there.
    """
    pairs = []
    failures = []
    for shard in discover_message_shards(db_path):
        derived_key, derived_salt = derive_database_key(
            shard, key_hex, salt_hex, passphrase)
        # Try the derived key first; fall back to the configured pair so
        # installs without a passphrase keep working exactly as before.
        candidates = [(derived_key, derived_salt)]
        if (derived_key, derived_salt) != (key_hex, salt_hex):
            candidates.append((key_hex, salt_hex))
        failure_reason = 'OPEN_FAILED'
        for candidate_key, candidate_salt in candidates:
            conn = None
            try:
                conn, _ = connect_nt_db(shard, candidate_key, candidate_salt)
                # PRAGMA key alone never fails; only a read surfaces a bad key.
                conn.execute('SELECT count(*) FROM sqlite_master').fetchone()
            except Exception:
                # Reached the database but could not decrypt it: the key is
                # wrong, not the file. Close before retrying, or the handle
                # leaks and on Windows the file stays locked.
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                failure_reason = 'KEY_REJECTED'
                continue
            pairs.append((shard, conn))
            break
        else:
            failures.append({'name': os.path.basename(shard), 'reason': failure_reason})
    return pairs, failures


def connect_message_shards(db_path, key_hex, salt_hex, passphrase=''):
    """Open the configured database plus every sibling shard.

    Returns the connections that actually opened. A shard that fails is
    skipped rather than fatal: a partially readable transcript beats a
    command that refuses to run at all.

    Behaviour-preserving delegate: callers that do not ask for the failure
    detail get exactly what they got before.
    """
    pairs, _failures = connect_message_shards_detailed(
        db_path, key_hex, salt_hex, passphrase)
    return [conn for _path, conn in pairs]


def count_talker_rows(conn, msg_table):
    """Rows this conversation has in one shard, or None if it has no table there.

    Distinguishes "this shard holds nothing for the conversation" from "this
    shard could not be read" - without it both look like zero.
    """
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name=?", (msg_table,))
        if cur.fetchone()[0] == 0:
            return None
        return conn.execute('SELECT COUNT(*) FROM "%s"' % msg_table).fetchone()[0]
    except Exception:
        return None


def msg_tables(conn):
    """Names of the per-conversation Msg_ tables in one shard."""
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg\\_%' ESCAPE '\\'"
        ).fetchall()
    except Exception:
        return set()
    return {row[0] for row in rows}


def get_fav_schema(db_path, key_hex):
    """Dump favorite.db schema + sample rows (key verification / exploration)."""
    try:
        with open(db_path, 'rb') as fh:
            salt = fh.read(16).hex()
        conn = require_sqlcipher().connect(db_path)
        conn.execute(f'PRAGMA key = "x\'{key_hex}{salt}\'";')
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        result = {"success": True, "tables": [t[0] for t in tables]}
        if not tables:
            result["error"] = "数据库为空或密钥错误"
            result["success"] = False
        conn.close()
        return result
    except Exception as e:
        return {"success": False, "error": str(e).split('\n')[0][:200]}


FAV_TYPE_NAMES = {
    1: 'text',        # 文字
    2: 'image',       # 图片
    4: 'video',       # 视频
    5: 'article',     # 公众号文章/网页链接
    14: 'chatrecord', # 聊天记录
}


def parse_fav_content(content):
    """Extract display fields (title/link/desc/source/cover) from favitem XML."""
    out = {}
    if not content:
        return out
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return out

    def text(path):
        el = root.find(path)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
        return None

    title = (text('weburlitem/pagetitle')
             or text('datalist/dataitem/datatitle')
             or text('title')
             or text('desc'))
    if title:
        out['title'] = title
    link = (text('weburlitem/clean_url')
            or text('source/link')
            or text('datalist/dataitem/stream_weburl'))
    if link:
        out['link'] = link
    desc = text('weburlitem/pagedesc') or text('datalist/dataitem/datadesc')
    if desc and desc != out.get('title'):
        out['desc'] = desc
    src = text('weburlitem/appmsgshareitem/srcdisplayname')
    if src:
        out['source_name'] = src
    cover = (text('weburlitem/pagethumb_url')
             or text('datalist/dataitem/dataext')
             or text('datalist/dataitem/cdn_thumburl'))
    if cover:
        out['cover'] = cover
    fmt = text('datalist/dataitem/datafmt')
    if fmt:
        out['format'] = fmt
    return out


def get_favorites(db_path, key_hex, limit=100, offset=0, keyword=None, fav_type=None):
    """List favorite items from favorite.db with parsed content."""
    try:
        with open(db_path, 'rb') as fh:
            salt = fh.read(16).hex()
        conn = require_sqlcipher().connect(db_path)
        conn.execute(f'PRAGMA key = "x\'{key_hex}{salt}\'";')
        c = conn.cursor()

        tables = [t[0] for t in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if 'fav_db_item' not in tables:
            conn.close()
            return {"error": f"未找到 fav_db_item 表，现有表: {tables[:20]}"}

        total = c.execute('SELECT count(*) FROM fav_db_item').fetchone()[0]

        sql = ('SELECT local_id, server_id, type, update_time, fromusr, realchatname, content '
               'FROM fav_db_item WHERE 1=1')
        params = []
        if fav_type is not None:
            sql += ' AND type = ?'
            params.append(fav_type)
        if keyword:
            sql += ' AND content LIKE ?'
            params.append(f'%{keyword}%')
        sql += ' ORDER BY update_time DESC LIMIT ? OFFSET ?'
        params.extend([limit, offset])
        rows = c.execute(sql, params).fetchall()
        conn.close()

        items = []
        for local_id, server_id, ftype, update_time, fromusr, realchatname, content in rows:
            ct = content if isinstance(content, str) else (
                content.decode('utf-8', errors='replace') if content else '')
            item = {
                'local_id': local_id,
                'server_id': server_id,
                'type': ftype,
                'type_name': FAV_TYPE_NAMES.get(ftype, 'type_%s' % ftype),
                'update_time': update_time,
                'from_user': fromusr,
                'chat_name': realchatname or None,
            }
            item.update(parse_fav_content(ct))
            items.append(item)
        return {"favorites": items, "total": total, "count": len(items),
                "limit": limit, "offset": offset}
    except Exception as e:
        return {"error": str(e).split('\n')[0][:200]}


def _summarise(row):
    """(last_time, summary) from the newest message row of a conversation."""
    last_time = row[0] or 0
    msg_type = row[3] or 0
    source_text = row[1]
    content_text = row[2]

    if msg_type == 1:
        # Text message: use message_content
        if isinstance(content_text, str) and content_text:
            return last_time, content_text[:50]
        if isinstance(content_text, bytes):
            return last_time, content_text.decode('utf-8', errors='ignore')[:50]
        return last_time, ""

    if isinstance(source_text, str) and source_text:
        # Non-text: try to extract from source, stripping XML tags
        clean = re.sub(r'<[^>]+>', '', source_text)
        lines = clean.split('\n')
        if len(lines) > 1 and lines[1].strip():
            return last_time, lines[1].strip()[:50]
        if clean.strip():
            return last_time, clean.strip()[:50]
    return last_time, ""


def get_sessions(conns):
    """Get chat sessions, newest message per conversation across every shard."""
    sessions = {}

    # NT format: each chat has its own Msg_<MD5> table
    # The Name2Id table maps usernames to IDs (user_name, is_session)
    for conn in conns:
        c = conn.cursor()
        try:
            c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1 LIMIT 500")
            usernames = [row[0] for row in c.fetchall() if row[0]]
        except Exception:
            continue

        tables = msg_tables(conn)
        for username in usernames:
            entry = sessions.setdefault(username, {"summary": "", "last_time": 0})
            msg_table = f"Msg_{hashlib.md5(username.encode()).hexdigest()}"
            if msg_table not in tables:
                continue
            try:
                c.execute(f'SELECT create_time, source, message_content, local_type FROM "{msg_table}" ORDER BY create_time DESC LIMIT 1')
                row = c.fetchone()
                if row:
                    last_time, summary = _summarise(row)
                    # A conversation spans shards; only the newest speaks for it.
                    if last_time >= entry["last_time"]:
                        entry["last_time"] = last_time
                        entry["summary"] = summary
            except Exception:
                continue

    result = [{
        "username": username,
        "type": 1 if "@chatroom" in username else 0,
        "unreadCount": 0,
        "summary": entry["summary"],
        "sortTimestamp": entry["last_time"],
        "lastTimestamp": entry["last_time"],
        "displayName": username,
    } for username, entry in sessions.items()]

    # Sort by timestamp descending
    result.sort(key=lambda s: s.get("sortTimestamp", 0), reverse=True)
    return {"sessions": result}


def _strip_group_speaker(content, known_ids):
    """Drop the `wxid_...:` prefix group rows carry in their content.

    Only an id the shard's Name2Id actually knows is accepted, so a message
    that merely starts with `note: ...` is left alone.
    """
    match = re.match(r'^([A-Za-z0-9_@.-]{5,64})\s*[:：]\s', str(content or ''))
    if not match or match.group(1) not in known_ids:
        return content
    return content[match.end():]


# The columns the reader actually consumes. The SELECT is built from these
# only: a WeChat version that renames or drops any *other* column of the
# message table cannot break a read of a column nobody looks at.
MESSAGE_COLUMNS = ('local_id', 'server_id', 'local_type', 'real_sender_id',
                   'create_time', 'message_content')

# Any one of these is enough to give rows an identity and an order. With none
# of them a row cannot become a message at all, and that is a schema mismatch
# rather than an empty conversation.
# 定义搬去了 `nt_common`：`ORDER BY` 那处（同一个文件下面）过去自己又写了一份
# 字面量，导出器再写一份——顺序在这里就是行为（先按 create_time 排），
# 三份各自为政等于谁改一处谁静默换一种排法。


# ---- 非文本消息的显示形态 ---------------------------------------------------
#
# 这条路径此前**把非文本消息丢掉了**：`parsedContent` 只在 local_type == 1 时才有值，
# 而 BLOB 内容不是 str，会在下面被置成空串。于是图片/表情/文件/引用/撤回/红包在下游
# 一律表现为"空内容"——实测那 44 条里"空"的三条，分别是**一条撤回提示**和两个表情。
#
# 类型编码有结构：`local_type = apptype * 2**32 + 49`，49 表示 appmsg 家族。所以这里
# 是**推导**而不是一张长表；只有少数几个固定类型需要单独列。
APPMSG_SUBTYPE = 49
NON_TEXT_LABELS = {
    3: '图片', 34: '语音', 42: '名片', 43: '视频', 47: '表情', 48: '位置',
}
APPMSG_LABELS = {
    4: '链接', 5: '链接', 6: '文件', 19: '聊天记录', 33: '小程序', 57: '引用', 63: '直播',
    2000: '转账', 2001: '红包',
}
# 不认识的 apptype 回 `[应用消息]` 而不是猜成"链接"：标签是给下游读的，不能支撑不起也写。
APPMSG_UNKNOWN_LABEL = '应用消息'

# 引用消息（apptype 57）的正文与被引原文之间的分隔。用词而不是符号，因为下游会把它读成
# 一句话；`|` 在别处会被当表格分隔。
QUOTE_SEP = ' ｜ 引：'
# 被引原文的截断长度。实测 37 条真实引用消息：中位 36 字、17 条超过 60 字、最长的
# 12733 字（引用了一整篇公众号文章）。60 会切掉近一半，120 能留住大多数完整句子，
# 又把那个极端值压住。
QUOTE_CLIP = 120


ZSTD_MAGIC = bytes([0x28, 0xB5, 0x2F, 0xFD])
def _decode_content(raw):
    """消息内容 → 文本。BLOB 先按 zstd 解压（公众号消息同一套机制）。解不出回空串。"""
    if isinstance(raw, str):
        return raw
    if not raw:
        return ''
    data = bytes(raw)
    if data[:4] == ZSTD_MAGIC:        # zstd frame header
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(data).decode('utf-8', 'ignore')
        except Exception:
            return ''
    return data.decode('utf-8', 'ignore')


def _xml_block(xml, tag):
    """取一个元素的起始标签到它自己的结束标签之间的整段（含标签）。

    只在区块内找 `title`/`des`：整份 payload 里还有 `<emotionpageshared><title>` 这类
    同名标签，在全文里搜会匹配到那个去（自验时真的把一段 XML 当文本吐出来过）。
    """
    text = xml or ''
    match = re.search(r'<%s(?:\s[^>]*)?>.*?</%s>' % (tag, tag), text, re.S)
    return match.group(0) if match else ''


def _xml_text(xml, tag):
    """取一个标签的文本（含 CDATA）。取不到回空串——`<title />` 这种自闭合就是取不到。

    起始标签里**不许出现 `/`**：否则 `<title />` 会被当成开标签，一路吃到后面某个
    `</title>`，把中间整段 XML 当成文本返回（自验时真的吐出来过）。

    实体在这**解掉**：显示形态是给人（和模型）读的，`a&amp;b` 该显示成 `a&b`。
    TS 侧的 `appMsgFormat.tagText` 同样解，两边一致——否则同一条消息 3.x 与 4.x 读起来
    不一样。
    """
    match = re.search(
        r'<%s(?:\s[^>/]*)?>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</%s>' % (tag, tag), xml or '', re.S)
    return html.unescape(match.group(1).strip()) if match else ''


def _clip(text, limit):
    """超长截断并**留下省略号**。

    不标记的截断读起来像一句说完的话——`title[:60]` 此前就是这样，被引原文最长的
    12733 字，切完看着像"这就是全部"。
    """
    if len(text) <= limit:
        return text
    return text[:limit] + '…'


def non_text_display(local_type, raw):
    """非文本消息 → 给下游看的文本。**绝不返回空串**——这是这次修复的全部要点。

    宁可写 `[未识别的消息类型 81604378673]`，也不要让下游看到空字符串：空串在下游与
    "这条消息不存在"无法区分，而它的真实后果是助手回答"我没解析出内容"。
    """
    lt = int(local_type or 0)
    text = _decode_content(raw)

    if lt & 0xFFFFFFFF == APPMSG_SUBTYPE:
        apptype = lt >> 32
        label = APPMSG_LABELS.get(apptype, APPMSG_UNKNOWN_LABEL)
        block = _xml_block(text, 'appmsg')
        title = _xml_text(block, 'title') or _xml_text(block, 'des')
        # 实测 payload 里 `<emotionpageshared>` 这类**嵌套容器**也带 `<title>`，取到的是
        # 它们自己的占位值（`null`）。这种不是标题，按"没有标题"处理。
        if title.lower() in ('null', 'undefined', '0'):
            title = ''
        # 截断：解析万一走偏，也不许把一段 XML 当成标题交给下游。
        title = _clip(title, 60)
        # 引用消息（apptype 57）：`title` 是**回复正文**，被引用的原文在 `refermsg/content`。
        # 实测 37 条真实引用消息，被引原文中位 36 字。此前只留回复，模型看到的是一句
        # "是呀，够得意个"却不知道在回什么——引用不带原文等于没引用。
        if apptype == 57:
            quoted = _xml_text(_xml_block(text, 'refermsg'), 'content')
            if quoted.lower() in ('null', 'undefined', '0'):
                quoted = ''
            quoted = _clip(quoted, QUOTE_CLIP)
            if quoted:
                body = (title + QUOTE_SEP + quoted) if title else quoted
                return '[%s] %s' % (label, body)
        return '[%s] %s' % (label, title) if title else '[%s]' % label

    label = NON_TEXT_LABELS.get(lt)
    if label:
        return '[%s]' % label
    if lt == 10000:                       # 系统消息：撤回、拍一拍、入群提示
        return _xml_text(text, 'content') or '[系统消息]'
    return '[未识别的消息类型 %d]' % lt


def _message_dict(row, sender_id_map, name_map, own_wxid, is_group=False):
    """One message row -> the CLI's message shape.

    `row` is a column-name keyed dict, not a tuple. Positional access assumes
    every column exists; when a WeChat version drops one, the whole shard read
    fails and the conversation looks empty. Reading by name means a missing
    column costs that one field instead.
    """
    local_type = row.get('local_type') or 0
    create_time = row.get('create_time') or 0
    real_sender_id = row.get('real_sender_id') or 0

    # Resolve sender: real_sender_id -> Name2Id -> user_name
    sender_username = sender_id_map.get(real_sender_id, "")

    # Determine if message is from self
    # own_wxid may have _xxxx suffix (from xwechat_files dir), try both
    is_self = bool(own_wxid and (
        sender_username == own_wxid or
        (own_wxid.endswith('_') is False and sender_username.startswith(own_wxid))
    ))
    if not is_self and own_wxid:
        # Strip _xxxx suffix and retry
        parts = own_wxid.rsplit('_', 1)
        if len(parts) == 2 and len(parts[1]) == 4 and parts[1].isalnum():
            is_self = (sender_username == parts[0])

    # Resolve sender display name from contact map
    if is_self:
        sender_display = ""  # Let the CLI show "我"
    else:
        sender_display = name_map.get(sender_username, sender_username) if sender_username else sender_username

    # Parse message_content - TEXT column
    raw_content = row.get('message_content')
    content = raw_content if isinstance(raw_content, str) else ""

    # `content`/`rawContent` stay exactly as stored; only `parsedContent` - the
    # field every consumer reads first - gets the display-ready form.
    #
    # 非文本消息（图片/表情/文件/引用/撤回/红包…）在过去得到的是**空串**：它们的内容是
    # JSON 里不是 str 的 BLOB，上面那行会把它置空。现在它们走 `non_text_display`，
    # 至少拿到 `[图片]`，多数还能带上文件名、被引用的原文或"谁撤回了一条消息"。
    if content:
        display = _strip_group_speaker(content, set(sender_id_map.values())) if is_group else content
    else:
        display = non_text_display(local_type, raw_content)

    return {
        "localId": row.get('local_id') or 0,
        "serverId": str(row.get('server_id') or ''),
        "localType": local_type,
        "createTime": create_time,
        "isSend": 1 if is_self else 0,  # 1 = I sent this
        "senderUsername": sender_username,
        "senderDisplay": sender_display,
        "content": content,
        "rawContent": content,
        "parsedContent": display[:200] if local_type == 1 else display[:200],
    }


def get_messages(conns, talker, limit=100, offset=0, name_map=None, own_wxid=None,
                 shard_names=None, shard_report=None, from_time=None, to_time=None):
    """Get messages for a specific talker, merged across every shard.

    Args:
        name_map: optional {wxid: display_name} dict for resolving sender names
        own_wxid: account owner wxid for self-message detection
        shard_names: optional basename per connection, parallel to `conns`
        shard_report: optional list to append one outcome dict per shard to.
            Purely additive: the returned messages are identical with or
            without it, which is asserted by test/nt_decrypt_shards_test.py.
        from_time: optional inclusive lower bound on `create_time` (unix
            seconds), pushed into SQL so a bounded read does not have to fetch
            and discard the whole conversation.
        to_time: optional inclusive upper bound, same units.

    A requested window is only ever honoured or refused, never approximated: a
    shard whose table has no `create_time` is skipped and reported as
    WINDOW_UNAVAILABLE rather than returning out-of-range rows that a caller
    tracking coverage would count as covered. Every in-tree caller that passes
    a window also passes `shard_report`, so that refusal is always visible.
    """
    if name_map is None:
        name_map = {}

    window_requested = from_time is not None or to_time is not None

    msg_table = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
    is_group = '@chatroom' in talker

    def record(index, opened, has_table, rows_for_talker, reason, missing=None):
        if shard_report is None:
            return
        name = shard_names[index] if shard_names and index < len(shard_names) else ''
        shard_report.append({
            'name': name,
            'opened': opened,
            'hasTalkerTable': has_table,
            'rowsForTalker': rows_for_talker,
            'reason': reason,
            # Columns this shard does not have. A non-empty list together with
            # a null `reason` means the read succeeded but lost fields, which is
            # a different thing from a failed read and has to stay visible.
            'missingColumns': list(missing or ()),
        })

    # Each shard only needs to yield its newest window: once every shard's rows
    # are merged and re-sorted, nothing older than that can reach this page.
    window = 0 if limit <= 0 else limit + offset
    collected = []
    found = False

    for index, conn in enumerate(conns):
        c = conn.cursor()
        try:
            c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name=?", (msg_table,))
            if c.fetchone()[0] == 0:
                # The shard is readable and simply holds nothing for this
                # conversation - distinct from not being readable at all.
                record(index, True, False, None, None)
                continue
            found = True
            rows_for_talker = c.execute(
                'SELECT COUNT(*) FROM "%s"' % msg_table).fetchone()[0]

            available = table_columns(c, msg_table)
            selected = [col for col in MESSAGE_COLUMNS if col in available]
            missing = [col for col in MESSAGE_COLUMNS if col not in available]
            if not [col for col in MESSAGE_ANCHOR_COLUMNS if col in available]:
                # The row shape no longer matches anything the reader can name.
                record(index, True, True, rows_for_talker, 'SCHEMA_MISMATCH',
                       missing=missing)
                continue
            if window_requested and 'create_time' not in available:
                record(index, True, True, rows_for_talker, 'WINDOW_UNAVAILABLE',
                       missing=missing)
                continue

            order = [col for col in MESSAGE_ANCHOR_COLUMNS
                     if col in available]
            sql = 'SELECT %s FROM "%s"' % (
                ', '.join('"%s"' % col for col in selected), msg_table)
            params = []
            clauses = []
            if from_time is not None:
                clauses.append('"create_time" >= ?')
                params.append(from_time)
            if to_time is not None:
                clauses.append('"create_time" <= ?')
                params.append(to_time)
            if clauses:
                sql += ' WHERE ' + ' AND '.join(clauses)
            sql += ' ORDER BY ' + ', '.join('"%s" DESC' % col for col in order)
            if window:
                sql += ' LIMIT ?'
                params.append(window)
            c.execute(sql, params)
            # Dicts rather than driver rows: the SELECT list varies per shard,
            # so position no longer identifies a column. Built here rather than
            # via row_factory, which every other reader sharing these
            # connections would inherit.
            rows = [dict(zip(selected, values)) for values in c.fetchall()]

            # Sender ids are rowids, so the map has to come from the same
            # shard. Absent, it costs sender names and nothing else, so it is
            # probed rather than left to raise and lose the whole shard.
            has_name_table = c.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='Name2Id'").fetchone()[0] > 0
            sender_id_map = {}
            if has_name_table:
                c.execute("SELECT rowid, user_name FROM Name2Id")
                sender_id_map = {rowid: uname for rowid, uname in c.fetchall()}
        except Exception:
            # This shard had the conversation's table but could not be read.
            # Previously this was indistinguishable from "no rows here".
            record(index, True, None, None, 'READ_FAILED')
            continue
        record(index, True, True, rows_for_talker, None, missing=missing)

        for row in rows:
            collected.append(_message_dict(row, sender_id_map, name_map, own_wxid, is_group))

    if not found:
        return {"error": f"未找到会话: {talker}"}

    collected.sort(key=lambda m: (m["createTime"], m["localId"]), reverse=True)
    if limit > 0:
        collected = collected[offset:offset + limit]
    return {"messages": collected}


def get_contacts(conns, limit=200, keyword=None):
    """Get contacts from NT database, merged across every shard.

    **给了 `keyword` 就不加 `LIMIT`**，由调用方在名字解析之后过滤、再按 `limit` 截断。
    原来无条件 `LIMIT`，于是过滤发生在**截断之后**：关键字只能在前 `limit` 行里找，
    后面的人搜不到（实测 500 人的通讯录、按备注名找 10 个只命中 3 个）。名字解析要用
    contact.db 的备注/昵称，那是调用方才有的东西，所以截断必须挪到过滤之后。
    """
    seen = set()
    contacts = []
    for conn in conns:
        try:
            sql = "SELECT user_name FROM Name2Id" if keyword else "SELECT user_name FROM Name2Id LIMIT ?"
            rows = conn.execute(sql, () if keyword else (limit,)).fetchall()
        except Exception:
            continue
        for (username,) in rows:
            # Name2Id carries a placeholder row with no user_name; rendering it
            # produced a blank line at the top of every contact list.
            if not username or username in seen:
                continue
            seen.add(username)
            contacts.append({"username": username, "displayName": username})
    return {"contacts": contacts}


# ========== SNS (朋友圈) Queries ==========

def parse_sns_content(content_str):
    """Parse SNS content XML/Protobuf text to extract title, description, media etc."""
    result = {
        'content': '',
        'create_time': 0,
        'username': '',
        'object_id': '',
        'media_count': 0,
    }
    if not content_str:
        return result

    import re

    # Extract createTime
    m = re.search(r'<createTime>(\d+)</createTime>', content_str)
    if m:
        result['create_time'] = int(m.group(1))

    # Extract username
    m = re.search(r'<username>([^<]+)</username>', content_str)
    if m:
        result['username'] = m.group(1)

    # Extract id
    m = re.search(r'<id>(\d+)</id>', content_str)
    if m:
        result['object_id'] = m.group(1)

    # Extract contentDesc (main text)
    m = re.search(r'<contentDesc>([^<]*)</contentDesc>', content_str)
    if m:
        result['content'] = m.group(1)

    # Extract contentDesc CDATA
    m = re.search(r'<contentDesc>\s*<!\[CDATA\[(.*?)\]\]>\s*</contentDesc>', content_str, re.DOTALL)
    if m:
        result['content'] = m.group(1).strip()

    # Extract title if present
    m = re.search(r'<title>([^<]*)</title>', content_str)
    if m:
        title = m.group(1)
        if title and not result['content']:
            result['content'] = title
        elif title:
            result['content'] = title + '\n' + result['content']

    # Count media (ContentObject tags)
    result['media_count'] = len(re.findall(r'<ContentObject[ >]', content_str))

    return result


def get_sns_timeline(cursor, limit=20, offset=0, usernames=None, keyword=None,
                     start_time=None, end_time=None):
    """Query SNS timeline posts from SnsTimeLine table."""
    # Check if SnsTimeLine exists, fall back to SnsTopItem_1
    tables = [r[0] for r in cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table';").fetchall()]

    table = 'SnsTimeLine' if 'SnsTimeLine' in tables else None
    if not table:
        table = 'SnsTopItem_1' if 'SnsTopItem_1' in tables else None
    if not table:
        return {'success': False, 'error': 'No SNS table found'}

    conditions = []
    params = []

    if table == 'SnsTimeLine':
        if usernames:
            placeholders = ','.join(['?' for _ in usernames])
            conditions.append(f'user_name IN ({placeholders})')
            params.extend(usernames)
        if keyword:
            conditions.append('content LIKE ?')
            params.append(f'%{keyword}%')
        if start_time:
            conditions.append("CAST(substr(content, instr(content, '<createTime>') + 12, 10) AS INTEGER) >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("CAST(substr(content, instr(content, '<createTime>') + 12, 10) AS INTEGER) <= ?")
            params.append(end_time)
    elif table == 'SnsTopItem_1':
        uname_col = 'username' if 'username' in [r[1] for r in cursor.execute(f'PRAGMA table_info([{table}]);').fetchall()] else 'user_name'
        if usernames:
            placeholders = ','.join(['?' for _ in usernames])
            conditions.append(f'{uname_col} IN ({placeholders})')
            params.extend(usernames)
        if keyword:
            conditions.append('summary LIKE ?')
            params.append(f'%{keyword}%')
        if start_time:
            conditions.append('create_time >= ?')
            params.append(start_time)
        if end_time:
            conditions.append('create_time <= ?')
            params.append(end_time)

    where = ' AND '.join(conditions) if conditions else '1=1'
    order = 'tid DESC' if table == 'SnsTimeLine' else 'create_time DESC'

    try:
        cols = [r[1] for r in cursor.execute(f'PRAGMA table_info([{table}]);').fetchall()]
        # Only select known text columns to avoid binary decode errors
        text_cols = [c for c in cols if c in ('tid', 'user_name', 'content', 'username', 'summary',
                                               'create_time', 'last_read_time', 'is_read',
                                               'from_username', 'from_nickname', 'to_username',
                                               'to_nickname', 'comment_id', 'feed_id',
                                               'createTime', 'userName')]
        if not text_cols:
            text_cols = ['*']
        select_str = ', '.join(text_cols)
        rows = cursor.execute(
            f'SELECT {select_str} FROM [{table}] WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?',
            params + [limit, offset]
        ).fetchall()

        timeline = []

        for row in rows:
            item = dict(zip(text_cols, row))

            if table == 'SnsTimeLine':
                # Parse XML content
                content_str = item.get('content', '') or ''
                parsed = parse_sns_content(content_str)
                create_time = parsed['create_time'] or 0
                username = parsed['username'] or item.get('user_name', '')
                text = parsed['content'] or ''
                media_count = parsed['media_count']
            else:
                create_time = item.get('create_time', 0) or 0
                username = item.get('username', '') or item.get('user_name', '')
                text = item.get('summary', '') or ''
                media_count = 0

            timeline.append({
                'create_time': create_time,
                'username': username,
                'content': text,
                'media_count': media_count,
                'table': table,
            })

        return {'success': True, 'timeline': timeline, 'table': table}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def get_sns_usernames(cursor):
    """Get unique usernames from SnsTopItem_1."""
    tables = [r[0] for r in cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table';").fetchall()]

    # Prefer SnsTopItem_1 for user listing (larger)
    table = 'SnsTopItem_1' if 'SnsTopItem_1' in tables else ('SnsTimeLine' if 'SnsTimeLine' in tables else None)
    if not table:
        return {'success': False, 'error': 'No SNS table found'}

    # Discover username column
    cols = [r[1] for r in cursor.execute(f'PRAGMA table_info([{table}]);').fetchall()]
    uname_col = 'username' if 'username' in cols else 'user_name'

    try:
        rows = cursor.execute(
            f'SELECT [{uname_col}], COUNT(*) as cnt FROM [{table}] GROUP BY [{uname_col}] ORDER BY cnt DESC'
        ).fetchall()
        usernames = [r[0] for r in rows if r[0]]
        counts = {r[0]: r[1] for r in rows if r[0]}
        return {'success': True, 'usernames': usernames, 'counts': counts}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def get_sns_stats(cursor, my_wxid=None):
    """Get SNS statistics from SnsTopItem_1."""
    tables = [r[0] for r in cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table';").fetchall()]

    table = 'SnsTopItem_1' if 'SnsTopItem_1' in tables else ('SnsTimeLine' if 'SnsTimeLine' in tables else None)
    if not table:
        return {'success': False, 'error': 'No SNS table found'}

    try:
        total = cursor.execute(f'SELECT COUNT(*) FROM [{table}]').fetchone()[0]

        cols = [r[1] for r in cursor.execute(f'PRAGMA table_info([{table}]);').fetchall()]
        uname_col = 'username' if 'username' in cols else 'user_name'

        total_friends = 0
        if uname_col:
            total_friends = cursor.execute(
                f'SELECT COUNT(DISTINCT [{uname_col}]) FROM [{table}] WHERE [{uname_col}] IS NOT NULL'
            ).fetchone()[0]

        my_posts = None
        if my_wxid and uname_col:
            my_posts = cursor.execute(
                f'SELECT COUNT(*) FROM [{table}] WHERE [{uname_col}] = ?',
                (my_wxid,)).fetchone()[0]

        return {
            'success': True,
            'data': {
                'totalPosts': total,
                'totalFriends': total_friends,
                'myPosts': my_posts,
            }
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ========== Main CLI ==========

def main():
    import argparse
    parser = argparse.ArgumentParser(description='WeChat NT Database Tool')
    sub = parser.add_subparsers(dest='command')

    # scan command
    scan_parser = sub.add_parser('scan', help='Scan memory for keys and match NT databases')
    scan_parser.add_argument('--json', action='store_true', help='Output as JSON')
    scan_parser.add_argument('--root', default=os.environ.get('WEFLOW_SCAN_ROOT'), help='xwechat_files root directory override')

    # sessions command
    sessions_parser = sub.add_parser('sessions', help='List chat sessions')
    sessions_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to NT database')
    sessions_parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')
    sessions_parser.add_argument('--salt', default=os.environ.get('WEFLOW_NT_SALT'), required=not os.environ.get('WEFLOW_NT_SALT'), help='Salt hex (32 chars)')
    sessions_parser.add_argument('--passphrase', default=os.environ.get('WEFLOW_NT_PASSPHRASE'), help='Shared passphrase for deriving per-shard keys')
    sessions_parser.add_argument('--keyword', default=os.environ.get('WEFLOW_QUERY_KEYWORD'), help='Filter by keyword')
    sessions_parser.add_argument('--contact-db', default=os.environ.get('WEFLOW_CONTACT_DB'), help='Path to contact.db for display names')
    sessions_parser.add_argument('--contact-key', default=os.environ.get('WEFLOW_CONTACT_KEY'), help='Contact DB key hex (64 chars)')
    sessions_parser.add_argument('--contact-salt', default=os.environ.get('WEFLOW_CONTACT_SALT'), help='Contact DB salt hex (32 chars)')

    # messages command
    msg_parser = sub.add_parser('messages', help='Get messages')
    msg_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to NT database')
    msg_parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')
    msg_parser.add_argument('--salt', default=os.environ.get('WEFLOW_NT_SALT'), required=not os.environ.get('WEFLOW_NT_SALT'), help='Salt hex (32 chars)')
    msg_parser.add_argument('--passphrase', default=os.environ.get('WEFLOW_NT_PASSPHRASE'), help='Shared passphrase for deriving per-shard keys')
    msg_parser.add_argument('--talker', default=os.environ.get('WEFLOW_TALKER'), required=not os.environ.get('WEFLOW_TALKER'), help='Talker username')
    msg_parser.add_argument('--limit', type=int, default=100)
    msg_parser.add_argument('--offset', type=int, default=0)
    # Unix seconds, inclusive at both ends. Pushed into SQL, so a bounded read
    # no longer has to fetch the whole conversation to discard most of it.
    msg_parser.add_argument('--from', dest='from_time', type=int,
                            default=os.environ.get('WEFLOW_FROM_TIME'),
                            help='Only messages at or after this unix time')
    msg_parser.add_argument('--to', dest='to_time', type=int,
                            default=os.environ.get('WEFLOW_TO_TIME'),
                            help='Only messages at or before this unix time')
    msg_parser.add_argument('--contact-db', default=os.environ.get('WEFLOW_CONTACT_DB'), help='Path to contact.db for sender names')
    msg_parser.add_argument('--contact-key', default=os.environ.get('WEFLOW_CONTACT_KEY'), help='Contact DB key hex (64 chars)')
    msg_parser.add_argument('--contact-salt', default=os.environ.get('WEFLOW_CONTACT_SALT'), help='Contact DB salt hex (32 chars)')
    msg_parser.add_argument('--own-wxid', default=os.environ.get('WEFLOW_OWN_WXID'), help='Account owner wxid (for self-message detection)')
    # Opt-in: without it the JSON is byte-identical to before this flag existed.
    msg_parser.add_argument('--report-shards', action='store_true',
                            help='Add a per-shard read report to the result (additive)')

    # contacts command
    contacts_parser = sub.add_parser('contacts', help='List contacts')
    contacts_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to NT database')
    contacts_parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')
    contacts_parser.add_argument('--salt', default=os.environ.get('WEFLOW_NT_SALT'), required=not os.environ.get('WEFLOW_NT_SALT'), help='Salt hex (32 chars)')
    contacts_parser.add_argument('--passphrase', default=os.environ.get('WEFLOW_NT_PASSPHRASE'), help='Shared passphrase for deriving per-shard keys')
    contacts_parser.add_argument('--keyword', default=os.environ.get('WEFLOW_QUERY_KEYWORD'), help='Filter by keyword')
    contacts_parser.add_argument('--limit', type=int, default=200)
    contacts_parser.add_argument('--contact-db', default=os.environ.get('WEFLOW_CONTACT_DB'), help='Path to contact.db for display names')
    contacts_parser.add_argument('--contact-key', default=os.environ.get('WEFLOW_CONTACT_KEY'), help='Contact DB key hex (64 chars)')
    contacts_parser.add_argument('--contact-salt', default=os.environ.get('WEFLOW_CONTACT_SALT'), help='Contact DB salt hex (32 chars)')

    # sns-timeline command
    sns_tl_parser = sub.add_parser('sns-timeline', help='Get SNS/Moments timeline')
    sns_tl_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to sns.db')
    sns_tl_parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')
    sns_tl_parser.add_argument('--salt', default=os.environ.get('WEFLOW_NT_SALT'), required=not os.environ.get('WEFLOW_NT_SALT'), help='Salt hex (32 chars)')
    sns_tl_parser.add_argument('--limit', type=int, default=20)
    sns_tl_parser.add_argument('--offset', type=int, default=0)
    sns_tl_parser.add_argument('--usernames', default=os.environ.get('WEFLOW_QUERY_USERNAMES'), help='JSON array of usernames to filter')
    sns_tl_parser.add_argument('--keyword', default=os.environ.get('WEFLOW_QUERY_KEYWORD'), help='Search keyword')
    sns_tl_parser.add_argument('--start-time', type=int, help='Start timestamp')
    sns_tl_parser.add_argument('--end-time', type=int, help='End timestamp')

    # sns-usernames command
    sns_un_parser = sub.add_parser('sns-usernames', help='List usernames with SNS posts')
    sns_un_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to sns.db')
    sns_un_parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')
    sns_un_parser.add_argument('--salt', default=os.environ.get('WEFLOW_NT_SALT'), required=not os.environ.get('WEFLOW_NT_SALT'), help='Salt hex (32 chars)')

    # sns-stats command
    sns_stats_parser = sub.add_parser('sns-stats', help='SNS statistics')
    sns_stats_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to sns.db')
    sns_stats_parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')
    sns_stats_parser.add_argument('--salt', default=os.environ.get('WEFLOW_NT_SALT'), required=not os.environ.get('WEFLOW_NT_SALT'), help='Salt hex (32 chars)')
    sns_stats_parser.add_argument('--my-wxid', default=os.environ.get('WEFLOW_OWN_WXID'), help='Account owner wxid for my-posts count')

    # fav-schema command
    fav_schema_parser = sub.add_parser('fav-schema', help='Dump favorite.db schema (key verification)')
    fav_schema_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to favorite.db')
    fav_schema_parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')

    verify_parser = sub.add_parser('verify', help='Verify a key+salt can open a database')
    verify_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to NT database')
    verify_parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')
    verify_parser.add_argument('--salt', default=os.environ.get('WEFLOW_NT_SALT'), required=not os.environ.get('WEFLOW_NT_SALT'), help='Salt hex (32 chars)')

    # Same question as `verify`, answered without the native library - so a
    # missing or broken sqlcipher3 does not hide whether the key is correct.
    native_parser = sub.add_parser('verify-native',
                                   help='Verify a passphrase from the file header alone (no sqlcipher3)')
    native_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to any database in the set')
    native_parser.add_argument('--passphrase', default=os.environ.get('WEFLOW_NT_PASSPHRASE'), required=not os.environ.get('WEFLOW_NT_PASSPHRASE'), help='Passphrase hex (64 chars)')
    native_parser.add_argument('--internal-key', default=os.environ.get('WEFLOW_INTERNAL_DB_KEY', ''), help='Optional 64-char hex XOR key (some builds)')

    # fav-list command
    fav_list_parser = sub.add_parser('fav-list', help='List favorite items')
    fav_list_parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to favorite.db')
    fav_list_parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')
    fav_list_parser.add_argument('--limit', type=int, default=100)
    fav_list_parser.add_argument('--offset', type=int, default=0)
    fav_list_parser.add_argument('--keyword', default=os.environ.get('WEFLOW_QUERY_KEYWORD'), help='Search keyword')
    fav_list_parser.add_argument('--type', type=int, dest='fav_type',
                                 help='Filter by type: 1=text 2=image 4=video 5=article 14=chatrecord')

    args = parser.parse_args()

    if args.command == 'scan':
        # Discover the database files before touching the process. Walking the
        # filesystem does not need WeChat running, but key matching does - and
        # the caller derives keys from the passphrase when the memory scan
        # finds nothing, which only needs the file list. Returning early on
        # "process not running" without the databases threw that list away and
        # left the caller with nothing to derive from.
        databases = find_nt_databases(getattr(args, 'root', None))

        pid = find_weixin_pid()
        if not pid:
            if IS_WINDOWS:
                print(json.dumps({"error": "Weixin.exe 未运行", "databases": databases}))
            else:
                print(json.dumps({"error": "未检测到 Linux 微信进程，请确认微信已启动并登录",
                                  "databases": databases}))
            return

        if not args.json:
            print(f"扫描进程 PID {pid}...")

        keys, scan_err = scan_memory_keys(pid)
        if scan_err == 'permission':
            print(json.dumps({"error": "PERMISSION_DENIED: 读取微信进程内存需要 root 或 CAP_SYS_PTRACE 权限。可使用 sudo 运行，或执行: sudo setcap cap_sys_ptrace=ep $(which python3)",
                              "databases": databases}))
            return
        if scan_err == 'gone':
            print(json.dumps({"error": "微信进程已退出，请重试", "databases": databases}))
            return
        if not args.json:
            print(f"找到 {len(keys)} 个密钥")

        if not args.json:
            print(f"找到 {len(databases)} 个 NT 数据库")

        matched = match_keys_to_databases(keys, databases)
        if not args.json:
            print(f"匹配 {len(matched)} 个数据库")
            for db in matched:
                print(f"  {db['name']} ({db['size']/1024/1024:.1f}MB) key={db['key'][:16]}... salt={db['salt'][:16]}...")
        else:
            print(json.dumps({"keys": keys, "databases": databases, "matched": matched}))
        return

    if args.command == 'verify':
        # sqlcipher 打开+读 sqlite_master 才触发解密, 错误密钥在此报 "file is not a database"
        try:
            conn, _ = connect_nt_db(args.db, args.key, args.salt)
            n = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            conn.close()
            print(json.dumps({"success": n > 0, "tables": n}, ensure_ascii=True))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e).split('\n')[0][:200]}, ensure_ascii=True))
        return

    if args.command == 'verify-native':
        verdict = verify_passphrase_native(args.passphrase, args.db,
                                           getattr(args, 'internal_key', '') or '')
        if verdict is None:
            print(json.dumps({"success": False, "verified": None,
                              "error": "无法判定（文件不可读、过短，或 passphrase 不是 64 位 hex）"},
                             ensure_ascii=True))
            return
        print(json.dumps({"success": True, "verified": verdict}, ensure_ascii=True))
        return

    # Build contact name map once if contact db provided
    contact_name_map = {}
    contact_db = getattr(args, 'contact_db', None)
    contact_key = getattr(args, 'contact_key', None)
    contact_salt = getattr(args, 'contact_salt', None)
    if contact_db and contact_key and contact_salt:
        contact_name_map = load_contact_names(contact_db, contact_key, contact_salt)

    if args.command == 'sessions':
        conns = connect_message_shards(args.db, args.key, args.salt, getattr(args, 'passphrase', '') or '')
        if not conns:
            print(json.dumps({"error": "无法打开消息数据库，请检查密钥"}, ensure_ascii=True))
            return
        result = get_sessions(conns)
        if 'sessions' in result:
            result['sessions'] = apply_contact_names(result['sessions'], contact_name_map)
            if args.keyword:
                kw = args.keyword.lower()
                result['sessions'] = [
                    s for s in result['sessions']
                    if kw in (s.get('username', '') + s.get('displayName', '') + s.get('summary', '')).lower()
                ]
        print(json.dumps(result, ensure_ascii=True))
        for conn in conns:
            conn.close()

    elif args.command == 'messages':
        passphrase = getattr(args, 'passphrase', '') or ''
        own_wxid = getattr(args, 'own_wxid', None)

        if getattr(args, 'report_shards', False):
            pairs, failures = connect_message_shards_detailed(
                args.db, args.key, args.salt, passphrase)
            if not pairs:
                print(json.dumps({"error": "无法打开消息数据库，请检查密钥"}, ensure_ascii=True))
                return
            conns = [conn for _path, conn in pairs]
            names = [os.path.basename(path) for path, _conn in pairs]
            shard_items = []
            result = get_messages(conns, args.talker, args.limit, args.offset,
                                  contact_name_map, own_wxid,
                                  shard_names=names, shard_report=shard_items,
                                  from_time=args.from_time, to_time=args.to_time)
            # Failure entries carry no opened connection, so they are not in
            # `conns` and could not be recorded by get_messages itself.
            result['shards'] = {
                'scanned': len(pairs) + len(failures),
                'opened': len(pairs),
                'failed': len(failures),
                'items': failures + shard_items,
            }
        else:
            conns = connect_message_shards(args.db, args.key, args.salt, passphrase)
            if not conns:
                print(json.dumps({"error": "无法打开消息数据库，请检查密钥"}, ensure_ascii=True))
                return
            result = get_messages(conns, args.talker, args.limit, args.offset,
                                  contact_name_map, own_wxid,
                                  from_time=args.from_time, to_time=args.to_time)

        print(json.dumps(result, ensure_ascii=True))
        for conn in conns:
            conn.close()

    elif args.command == 'contacts':
        conns = connect_message_shards(args.db, args.key, args.salt, getattr(args, 'passphrase', '') or '')
        if not conns:
            print(json.dumps({"error": "无法打开消息数据库，请检查密钥"}, ensure_ascii=True))
            return
        result = get_contacts(conns, args.limit, keyword=args.keyword)
        if 'contacts' in result:
            result['contacts'] = apply_contact_names(result['contacts'], contact_name_map)
            result['contacts'] = filter_contacts(result['contacts'], args.keyword, args.limit)
        print(json.dumps(result, ensure_ascii=True))
        for conn in conns:
            conn.close()

    elif args.command == 'sns-timeline':
        conn, _ = connect_nt_db(args.db, args.key, args.salt)
        usernames = None
        if args.usernames:
            try:
                usernames = json.loads(args.usernames)
            except: pass
        result = get_sns_timeline(conn.cursor(), args.limit, args.offset,
                                   usernames, args.keyword,
                                   args.start_time, args.end_time)
        print(json.dumps(result, ensure_ascii=True, default=str))
        conn.close()

    elif args.command == 'sns-usernames':
        conn, _ = connect_nt_db(args.db, args.key, args.salt)
        result = get_sns_usernames(conn.cursor())
        print(json.dumps(result, ensure_ascii=True, default=str))
        conn.close()

    elif args.command == 'sns-stats':
        conn, _ = connect_nt_db(args.db, args.key, args.salt)
        result = get_sns_stats(conn.cursor(), args.my_wxid)
        print(json.dumps(result, ensure_ascii=True, default=str))
        conn.close()

    elif args.command == 'fav-schema':
        result = get_fav_schema(args.db, args.key)
        print(json.dumps(result, ensure_ascii=True))

    elif args.command == 'fav-list':
        result = get_favorites(args.db, args.key, args.limit, args.offset,
                               args.keyword, getattr(args, 'fav_type', None))
        print(json.dumps(result, ensure_ascii=True, default=str))

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
