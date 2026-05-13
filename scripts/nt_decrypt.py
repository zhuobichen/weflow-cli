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

kernel32 = ctypes.windll.kernel32
ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
ReadProcessMemory.restype = wintypes.BOOL
VirtualQueryEx = kernel32.VirtualQueryEx
VirtualQueryEx.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
VirtualQueryEx.restype = ctypes.c_size_t


def find_weixin_pid():
    """Find Weixin.exe process ID."""
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


def scan_memory_keys(pid):
    """Scan process memory for x'<64hex_key><32hex_salt>' patterns."""
    hProcess = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not hProcess:
        return []

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

    return unique_keys


# ========== NT Database Discovery ==========

def find_nt_databases():
    """Find all NT-format databases under xwechat_files (message + contact)."""
    candidates = [
        os.path.expandvars(r'%USERPROFILE%\xwechat_files'),
        os.path.expandvars(r'%USERPROFILE%\Documents\xwechat_files'),
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


def get_messages(conn, talker, limit=100, offset=0):
    """Get messages for a specific talker from NT database."""
    import hashlib
    c = conn.cursor()

    msg_table = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"

    try:
        # Check if table exists
        c.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE name='{msg_table}'")
        if c.fetchone()[0] == 0:
            return {"error": f"未找到会话: {talker}"}

        c.execute(f'''
            SELECT local_id, server_id, local_type, sort_seq, real_sender_id,
                   create_time, status, upload_status, download_status,
                   server_seq, origin_source, source, message_content, compress_content
            FROM "{msg_table}"
            ORDER BY create_time DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset))

        rows = c.fetchall()
        messages = []

        for row in rows:
            local_type = row[2] or 0
            create_time = row[5] or 0
            is_send = (row[4] or 0) == 0  # real_sender_id == 0 means self

            # Parse source (sender info) - TEXT column
            source_text = row[11]
            sender_username = ""
            if isinstance(source_text, str) and source_text:
                # Format: "wxid_xxx:\ncontent..."
                if ':' in source_text:
                    sender_username = source_text.split(':')[0]

            # Parse message_content - TEXT column
            content = row[12] if isinstance(row[12], str) else ""

            messages.append({
                "localId": row[0] or 0,
                "serverId": str(row[1] or ''),
                "localType": local_type,
                "createTime": create_time,
                "isSend": 1 if is_send else 0,
                "senderUsername": sender_username,
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


# ========== Main CLI ==========

def main():
    import argparse
    parser = argparse.ArgumentParser(description='WeChat NT Database Tool')
    sub = parser.add_subparsers(dest='command')

    # scan command
    scan_parser = sub.add_parser('scan', help='Scan memory for keys and match NT databases')
    scan_parser.add_argument('--json', action='store_true', help='Output as JSON')

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

    args = parser.parse_args()

    if args.command == 'scan':
        pid = find_weixin_pid()
        if not pid:
            print(json.dumps({"error": "Weixin.exe 未运行"}))
            return

        if not args.json:
            print(f"扫描进程 PID {pid}...")

        keys = scan_memory_keys(pid)
        if not args.json:
            print(f"找到 {len(keys)} 个密钥")

        databases = find_nt_databases()
        if not args.json:
            print(f"找到 {len(databases)} 个 NT 数据库")

        matched = match_keys_to_databases(keys, databases)
        if not args.json:
            print(f"匹配 {len(matched)} 个数据库")
            for db in matched:
                print(f"  {db['name']} ({db['size']/1024/1024:.1f}MB) key={db['key'][:16]}... salt={db['salt'][:16]}...")
        else:
            print(json.dumps({"keys": keys, "databases": databases, "matched": matched}))

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
        result = get_messages(conn, args.talker, args.limit, args.offset)
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

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
