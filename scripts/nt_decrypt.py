#!/usr/bin/env python3
"""
WeChat NT (4.x) Database Access Tool
Uses sqlcipher3 to decrypt and query NT-format databases.
"""
import sys
import os
import json
import re
import ctypes
from ctypes import wintypes, c_void_p, c_size_t, create_string_buffer, byref, sizeof
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print(json.dumps({"error": "需要 sqlcipher3: pip install sqlcipher3"}))
    sys.exit(1)

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
    """
    if not contact_db_path or not contact_key_hex or not contact_salt_hex:
        return {}

    try:
        raw_key = f"x'{contact_key_hex}{contact_salt_hex}'"
        conn = sqlcipher.connect(contact_db_path)
        c = conn.cursor()
        c.execute(f'PRAGMA key = "{raw_key}";')

        # contact.db schema: username, alias, remark, nick_name, ...
        c.execute("SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), NULLIF(alias,''), username) FROM contact")
        name_map = {}
        for username, display in c.fetchall():
            if username:
                name_map[username] = display

        conn.close()
        return name_map
    except Exception as e:
        return {}


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
    conn = sqlcipher.connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    return conn, c


def get_fav_schema(db_path, key_hex):
    """Dump favorite.db schema + sample rows (key verification / exploration)."""
    try:
        with open(db_path, 'rb') as fh:
            salt = fh.read(16).hex()
        conn = sqlcipher.connect(db_path)
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
        conn = sqlcipher.connect(db_path)
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


def get_sessions(conn):
    """Get chat sessions from NT database (Name2Id table)."""
    c = conn.cursor()
    sessions = []

    # NT format: each chat has its own Msg_<MD5> table
    # The Name2Id table maps usernames to IDs (user_name, is_session)
    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1 LIMIT 500")
        rows = c.fetchall()

        import hashlib as hl

        for (username,) in rows:
            summary = ""
            last_time = 0

            # Try to get last message summary
            try:
                tbl_hash = hl.md5(username.encode()).hexdigest()
                msg_table = f"Msg_{tbl_hash}"

                c.execute(f'SELECT create_time, source, message_content, local_type FROM "{msg_table}" ORDER BY create_time DESC LIMIT 1')
                row = c.fetchone()
                if row:
                    last_time = row[0] or 0
                    msg_type = row[3] or 0
                    source_text = row[1]
                    content_text = row[2]

                    if msg_type == 1:
                        # Text message: use message_content
                        if isinstance(content_text, str) and content_text:
                            summary = content_text[:50]
                        elif isinstance(content_text, bytes):
                            summary = content_text.decode('utf-8', errors='ignore')[:50]
                    elif isinstance(source_text, str) and source_text:
                        # Non-text: try to extract from source
                        # Strip XML tags for summary
                        import re as _re
                        clean = _re.sub(r'<[^>]+>', '', source_text)
                        lines = clean.split('\n')
                        if len(lines) > 1 and lines[1].strip():
                            summary = lines[1].strip()[:50]
                        elif clean.strip():
                            summary = clean.strip()[:50]
            except:
                pass

            sessions.append({
                "username": username,
                "type": 1 if "@chatroom" in username else 0,
                "unreadCount": 0,
                "summary": summary,
                "sortTimestamp": last_time,
                "lastTimestamp": last_time,
                "displayName": username,
            })
    except Exception as e:
        return {"error": str(e)}

    # Sort by timestamp descending
    sessions.sort(key=lambda s: s.get("sortTimestamp", 0), reverse=True)
    return {"sessions": sessions}


def get_messages(conn, talker, limit=100, offset=0, name_map=None, own_wxid=None):
    """Get messages for a specific talker from NT database.

    Args:
        name_map: optional {wxid: display_name} dict for resolving sender names
        own_wxid: account owner wxid for self-message detection
    """
    import hashlib
    if name_map is None:
        name_map = {}
    c = conn.cursor()

    msg_table = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"

    try:
        # Check if table exists
        c.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE name='{msg_table}'")
        if c.fetchone()[0] == 0:
            return {"error": f"未找到会话: {talker}"}

        if limit > 0:
            c.execute(f'''
                SELECT local_id, server_id, local_type, sort_seq, real_sender_id,
                       create_time, status, upload_status, download_status,
                       server_seq, origin_source, source, message_content, compress_content
                FROM "{msg_table}"
                ORDER BY create_time DESC
                LIMIT ? OFFSET ?
            ''', (limit, offset))
        else:
            c.execute(f'''
                SELECT local_id, server_id, local_type, sort_seq, real_sender_id,
                       create_time, status, upload_status, download_status,
                       server_seq, origin_source, source, message_content, compress_content
                FROM "{msg_table}"
                ORDER BY create_time DESC
            ''')

        rows = c.fetchall()
        messages = []

        # Build sender_id -> username map from Name2Id (one query for all messages)
        c.execute("SELECT rowid, user_name FROM Name2Id")
        sender_id_map = {rowid: uname for rowid, uname in c.fetchall()}

        for row in rows:
            local_type = row[2] or 0
            create_time = row[5] or 0
            real_sender_id = row[4] or 0

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
            content = row[12] if isinstance(row[12], str) else ""

            messages.append({
                "localId": row[0] or 0,
                "serverId": str(row[1] or ''),
                "localType": local_type,
                "createTime": create_time,
                "isSend": 1 if is_self else 0,  # 1 = I sent this
                "senderUsername": sender_username,
                "senderDisplay": sender_display,
                "content": content,
                "rawContent": content,
                "parsedContent": content[:200] if local_type == 1 else "",
            })

        return {"messages": messages}
    except Exception as e:
        return {"error": str(e)}


def get_contacts(conn, limit=200):
    """Get contacts from NT database."""
    c = conn.cursor()
    try:
        c.execute("SELECT user_name FROM Name2Id LIMIT ?", (limit,))
        rows = c.fetchall()
        contacts = [{"username": r[0], "displayName": r[0]} for r in rows]
        return {"contacts": contacts}
    except Exception as e:
        return {"error": str(e)}


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
    scan_parser.add_argument('--root', help='xwechat_files root directory override')

    # sessions command
    sessions_parser = sub.add_parser('sessions', help='List chat sessions')
    sessions_parser.add_argument('--db', required=True, help='Path to NT database')
    sessions_parser.add_argument('--key', required=True, help='Key hex (64 chars)')
    sessions_parser.add_argument('--salt', required=True, help='Salt hex (32 chars)')
    sessions_parser.add_argument('--keyword', help='Filter by keyword')
    sessions_parser.add_argument('--contact-db', help='Path to contact.db for display names')
    sessions_parser.add_argument('--contact-key', help='Contact DB key hex (64 chars)')
    sessions_parser.add_argument('--contact-salt', help='Contact DB salt hex (32 chars)')

    # messages command
    msg_parser = sub.add_parser('messages', help='Get messages')
    msg_parser.add_argument('--db', required=True, help='Path to NT database')
    msg_parser.add_argument('--key', required=True, help='Key hex (64 chars)')
    msg_parser.add_argument('--salt', required=True, help='Salt hex (32 chars)')
    msg_parser.add_argument('--talker', required=True, help='Talker username')
    msg_parser.add_argument('--limit', type=int, default=100)
    msg_parser.add_argument('--offset', type=int, default=0)
    msg_parser.add_argument('--contact-db', help='Path to contact.db for sender names')
    msg_parser.add_argument('--contact-key', help='Contact DB key hex (64 chars)')
    msg_parser.add_argument('--contact-salt', help='Contact DB salt hex (32 chars)')
    msg_parser.add_argument('--own-wxid', help='Account owner wxid (for self-message detection)')

    # contacts command
    contacts_parser = sub.add_parser('contacts', help='List contacts')
    contacts_parser.add_argument('--db', required=True, help='Path to NT database')
    contacts_parser.add_argument('--key', required=True, help='Key hex (64 chars)')
    contacts_parser.add_argument('--salt', required=True, help='Salt hex (32 chars)')
    contacts_parser.add_argument('--keyword', help='Filter by keyword')
    contacts_parser.add_argument('--limit', type=int, default=200)
    contacts_parser.add_argument('--contact-db', help='Path to contact.db for display names')
    contacts_parser.add_argument('--contact-key', help='Contact DB key hex (64 chars)')
    contacts_parser.add_argument('--contact-salt', help='Contact DB salt hex (32 chars)')

    # sns-timeline command
    sns_tl_parser = sub.add_parser('sns-timeline', help='Get SNS/Moments timeline')
    sns_tl_parser.add_argument('--db', required=True, help='Path to sns.db')
    sns_tl_parser.add_argument('--key', required=True, help='Key hex (64 chars)')
    sns_tl_parser.add_argument('--salt', required=True, help='Salt hex (32 chars)')
    sns_tl_parser.add_argument('--limit', type=int, default=20)
    sns_tl_parser.add_argument('--offset', type=int, default=0)
    sns_tl_parser.add_argument('--usernames', help='JSON array of usernames to filter')
    sns_tl_parser.add_argument('--keyword', help='Search keyword')
    sns_tl_parser.add_argument('--start-time', type=int, help='Start timestamp')
    sns_tl_parser.add_argument('--end-time', type=int, help='End timestamp')

    # sns-usernames command
    sns_un_parser = sub.add_parser('sns-usernames', help='List usernames with SNS posts')
    sns_un_parser.add_argument('--db', required=True, help='Path to sns.db')
    sns_un_parser.add_argument('--key', required=True, help='Key hex (64 chars)')
    sns_un_parser.add_argument('--salt', required=True, help='Salt hex (32 chars)')

    # sns-stats command
    sns_stats_parser = sub.add_parser('sns-stats', help='SNS statistics')
    sns_stats_parser.add_argument('--db', required=True, help='Path to sns.db')
    sns_stats_parser.add_argument('--key', required=True, help='Key hex (64 chars)')
    sns_stats_parser.add_argument('--salt', required=True, help='Salt hex (32 chars)')
    sns_stats_parser.add_argument('--my-wxid', help='Account owner wxid for my-posts count')

    # fav-schema command
    fav_schema_parser = sub.add_parser('fav-schema', help='Dump favorite.db schema (key verification)')
    fav_schema_parser.add_argument('--db', required=True, help='Path to favorite.db')
    fav_schema_parser.add_argument('--key', required=True, help='Key hex (64 chars)')

    verify_parser = sub.add_parser('verify', help='Verify a key+salt can open a database')
    verify_parser.add_argument('--db', required=True, help='Path to NT database')
    verify_parser.add_argument('--key', required=True, help='Key hex (64 chars)')
    verify_parser.add_argument('--salt', required=True, help='Salt hex (32 chars)')

    # fav-list command
    fav_list_parser = sub.add_parser('fav-list', help='List favorite items')
    fav_list_parser.add_argument('--db', required=True, help='Path to favorite.db')
    fav_list_parser.add_argument('--key', required=True, help='Key hex (64 chars)')
    fav_list_parser.add_argument('--limit', type=int, default=100)
    fav_list_parser.add_argument('--offset', type=int, default=0)
    fav_list_parser.add_argument('--keyword', help='Search keyword')
    fav_list_parser.add_argument('--type', type=int, dest='fav_type',
                                 help='Filter by type: 1=text 2=image 4=video 5=article 14=chatrecord')

    args = parser.parse_args()

    if args.command == 'scan':
        pid = find_weixin_pid()
        if not pid:
            if IS_WINDOWS:
                print(json.dumps({"error": "Weixin.exe 未运行"}))
            else:
                print(json.dumps({"error": "未检测到 Linux 微信进程，请确认微信已启动并登录"}))
            return

        if not args.json:
            print(f"扫描进程 PID {pid}...")

        keys, scan_err = scan_memory_keys(pid)
        if scan_err == 'permission':
            print(json.dumps({"error": "PERMISSION_DENIED: 读取微信进程内存需要 root 或 CAP_SYS_PTRACE 权限。可使用 sudo 运行，或执行: sudo setcap cap_sys_ptrace=ep $(which python3)"}))
            return
        if scan_err == 'gone':
            print(json.dumps({"error": "微信进程已退出，请重试"}))
            return
        if not args.json:
            print(f"找到 {len(keys)} 个密钥")

        databases = find_nt_databases(getattr(args, 'root', None))
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

    # Build contact name map once if contact db provided
    contact_name_map = {}
    contact_db = getattr(args, 'contact_db', None)
    contact_key = getattr(args, 'contact_key', None)
    contact_salt = getattr(args, 'contact_salt', None)
    if contact_db and contact_key and contact_salt:
        contact_name_map = load_contact_names(contact_db, contact_key, contact_salt)

    if args.command == 'sessions':
        conn, _ = connect_nt_db(args.db, args.key, args.salt)
        result = get_sessions(conn)
        if 'sessions' in result:
            result['sessions'] = apply_contact_names(result['sessions'], contact_name_map)
            if args.keyword:
                kw = args.keyword.lower()
                result['sessions'] = [
                    s for s in result['sessions']
                    if kw in (s.get('username', '') + s.get('displayName', '') + s.get('summary', '')).lower()
                ]
        print(json.dumps(result, ensure_ascii=True))
        conn.close()

    elif args.command == 'messages':
        conn, _ = connect_nt_db(args.db, args.key, args.salt)
        own_wxid = getattr(args, 'own_wxid', None)
        result = get_messages(conn, args.talker, args.limit, args.offset, contact_name_map, own_wxid)
        print(json.dumps(result, ensure_ascii=True))
        conn.close()

    elif args.command == 'contacts':
        conn, _ = connect_nt_db(args.db, args.key, args.salt)
        result = get_contacts(conn, args.limit)
        if 'contacts' in result:
            result['contacts'] = apply_contact_names(result['contacts'], contact_name_map)
            if args.keyword:
                kw = args.keyword.lower()
                result['contacts'] = [
                    c for c in result['contacts']
                    if kw in (c.get('username', '') + c.get('displayName', '') + c.get('remark', '') + c.get('nickname', '')).lower()
                ]
        print(json.dumps(result, ensure_ascii=True))
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
