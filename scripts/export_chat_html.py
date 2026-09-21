#!/usr/bin/env python3
"""
Export WeChat NT chat history as self-contained HTML files.
Splits large conversations into multiple parts.
Embeds cached image thumbnails from NT cache directory.
"""
import sys
import time
import os
import hashlib
import datetime
import json
import re
import base64
import urllib.request
import urllib.error
import concurrent.futures
import struct
import json
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print("Install sqlcipher3: pip install sqlcipher3")
    sys.exit(1)

# 与 nt_decrypt 共用同一份实现（原先两边各有一份，契约还不一样）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nt_common import (discover_message_shards, derive_database_key,
                       table_columns, MESSAGE_ANCHOR_COLUMNS)

PAGE_SIZE = 4096
MSG_TYPES = {
    1: 'text', 3: 'image', 34: 'voice', 42: 'card',
    43: 'video', 47: 'emoji', 48: 'location', 49: 'link',
    50: 'voip', 10000: 'system', 10002: 'quote',
}
MAX_EMBED_SIZE = 8 * 1024 * 1024  # Bound self-contained HTML growth per image.
V2_MAGIC = b'\x07\x08V2\x08\x07'
V2_CIPHERTEXT_START = 0x0F
BUILTIN_EMOJI_DIR = os.path.join(os.path.dirname(__file__), '..', 'resources', 'wechat-emoji')
# Labels used by WeChat's built-in default emoji.  These messages may only
# retain a PUA/signature marker in the export, so their CDN media is not
# recoverable from the message row itself.
#
# The table is derived from the artwork actually present in BUILTIN_EMOJI_DIR
# rather than hand-maintained: adding a PNG named after the face is enough.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import wechat_emoji
    BUILTIN_EMOJI_MAP = {f'[{name}]': name for name in wechat_emoji.IMAGE_FACES}
    _WECHAT_EMOJI = True
    import wechat_emoji as _face_index
except Exception:
    BUILTIN_EMOJI_MAP = {}
    _WECHAT_EMOJI = False

try:
    import wechat_emoticon
    _WECHAT_EMOTICON = True
except Exception:
    _WECHAT_EMOTICON = False

try:
    import wechat_image
    _WECHAT_IMAGE = True
except Exception:
    _WECHAT_IMAGE = False

try:
    import wechat_voice
    _WECHAT_VOICE = True
except Exception:
    _WECHAT_VOICE = False

# Remote media is the single slowest step of an export: each miss costs a page
# fetch plus an image download, and inline thumbnails alone run to several
# hundred per conversation. Bound it per run and keep results on disk, misses
# included, so re-exports are instant.
#
# Two separate pools: share-page covers are rare and expensive, inline appmsg
# thumbnails are common and cheap. Sharing one would let a run of thumbnails
# starve the covers that actually change how a page looks.
COVER_FETCH_LIMIT = 60
THUMB_FETCH_LIMIT = 300

# Seeded with the full budgets rather than 0: main() re-sets them, but anything
# that reaches the fetchers without going through main() used to find a budget
# of 0 and silently skip every remote fetch, returning None with no error.
COVER_STATE = {
    'dir': '', 'budget': COVER_FETCH_LIMIT, 'fetched': 0, 'cached': 0, 'skipped': 0,
    'thumb_budget': THUMB_FETCH_LIMIT,
    'thumb_fetched': 0, 'thumb_cached': 0, 'thumb_skipped': 0,
}

# Off by default: full-resolution originals are 5-24MB / 3000-5700px each, and
# downscaling ~500 of them costs minutes (PIL decode dominates). The cache's
# own thumbnails need no processing at all and are what an export normally
# wants. Turn on for maximum fidelity at the cost of a much slower export.
FULL_IMAGES = os.environ.get('WEFLOW_FULL_IMAGES', '') == '1'

# og:image lives in <head>; WeChat article pages are multi-MB, so reading the
# whole thing to find it wastes seconds per article.
COVER_HEAD_BYTES = 64 * 1024

# Base64 length above which an account-index original is re-encoded before
# embedding. Below it the file is already display-sized and PIL would only
# cost time, which matters across thousands of media entries.
EMBED_SHRINK_THRESHOLD = 400 * 1024

# Set up by main(); custom stickers decrypted from WeChat's local cache.
STICKER_STATE = {'key': b'', 'dirs': [], 'cache_dir': ''}

# {wxid: remark/nickname}, loaded by main(). Group rows name their sender only
# by wxid, so without this a group transcript is unreadable. Empty when the
# contact database is unavailable, in which case ids are shown as-is.
CONTACT_NAMES = {}

# Voice messages: {local_id: silk bytes} for this conversation, plus the
# transcript cache. The export only ever *reads* transcripts - recognising
# speech runs ~1.4x the audio's own length on CPU, so it is a separate,
# resumable pass (`scripts/wechat_voice.py`) rather than part of an export.
VOICE_STATE = {'map': {}, 'cache': None}

# Remote media is discovered one message at a time, but a conversation needs
# it from hundreds of messages at once - and a single message rarely needs
# more than one URL, so nothing is ever fetched in parallel. main() therefore
# runs a throwaway formatting pass that only records the URLs (0.1s, no
# network), fetches them concurrently, then formats for real against a warm
# cache. Set to a list during that pass; None otherwise.
# Why the most recent remote fetch produced nothing. The skip counter alone
# said "not fetched" without saying why, so "no budget left" and "the URL is
# dead" were indistinguishable in the export - and both looked like a bare
# placeholder to the reader.
MEDIA_STATE = {'remote_reason': None}

PREFETCH = {'sink': None, 'active': False}
PREFETCH_WORKERS = 24
PREFETCH_MAX_URLS = 800


# The columns the row-consuming code below reads by position. Order is part
# of the contract: `row[4]` is `real_sender_id` and nothing else.
EXPORT_MESSAGE_COLUMNS = ('local_id', 'server_id', 'local_type', 'sort_seq',
                          'real_sender_id', 'create_time', 'status', 'source',
                          'message_content', 'compress_content')

# A column this shard lacks is selected as NULL rather than dropped. Dropping
# it would shift every later field by one and put, say, `create_time` where
# `real_sender_id` is expected - wrong output with no error anywhere, which is
# worse than the empty export a hard failure would produce.
MISSING_COLUMN_WARNING = '  WARNING: shard has no column %s; reading it as NULL'


def connect(db_path, key_hex, salt_hex):
    raw_key = f"x'{key_hex}{salt_hex}'"
    conn = sqlcipher.connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    return conn, c


def fetch_messages(conn, talker, date=''):
    """Fetch all messages for a talker, ordered by time ascending."""
    tbl = 'Msg_' + hashlib.md5(talker.encode()).hexdigest()
    c = conn.cursor()

    c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
    if not c.fetchone():
        return []

    c.execute(f"SELECT COUNT(*) FROM \"{tbl}\"")
    total = c.fetchone()[0]
    print(f"Total messages: {total}")

    date_filter = ''
    date_params = []
    if date:
        try:
            day = datetime.datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise ValueError('date must use YYYY-MM-DD')
        date_filter = ' WHERE create_time >= ? AND create_time < ?'
        date_params = [int(day.timestamp()), int((day + datetime.timedelta(days=1)).timestamp())]

    available = table_columns(c, tbl)
    for col in EXPORT_MESSAGE_COLUMNS:
        if col not in available:
            print(MISSING_COLUMN_WARNING % col)
    if date and 'create_time' not in available:
        # A requested day cannot be expressed here. Exporting everything
        # instead would silently widen a bounded export into an unbounded one.
        raise ValueError('shard has no create_time column, so a date-bounded export cannot be produced')
    projection = ', '.join('"%s"' % col if col in available else 'NULL AS "%s"' % col
                           for col in EXPORT_MESSAGE_COLUMNS)
    order = [col for col in MESSAGE_ANCHOR_COLUMNS
             if col in available]
    order_sql = ' ORDER BY ' + ', '.join('"%s" ASC' % col for col in order) if order else ''

    c.execute(f'SELECT {projection} FROM "{tbl}"{date_filter}{order_sql}', date_params)

    messages = []
    batch = 0
    while True:
        rows = c.fetchmany(5000)
        if not rows:
            break
        for row in rows:
            messages.append(row)
        batch += 1
        print(f"  Fetched {len(messages)}/{total}...")

    return messages


def fetch_messages_from_shards(db_path, key_hex, salt_hex, talker, date='', passphrase=''):
    """Read and merge messages from every NT message shard."""
    # `discover_message_shards` 保证不返回空表，所以这里不需要再兜底——
    # 原先的 `if not shards: shards = [db_path]` 是给"可能返回空"的那一份实现擦屁股的。
    shards = discover_message_shards(db_path)
    messages = []
    for shard in shards:
        shard_conn = None
        try:
            shard_key, shard_salt = derive_database_key(shard, key_hex, salt_hex, passphrase)
            shard_conn, _ = connect(shard, shard_key, shard_salt)
            messages.extend(fetch_messages(shard_conn, talker, date))
        except Exception:
            continue
        finally:
            if shard_conn is not None:
                shard_conn.close()
    messages.sort(key=lambda row: (int(row[5] or 0), int(row[0] or 0)))
    return messages


def build_sender_map(conn, talker):
    """Map sender_id -> display name using Name2Id table and contact DB."""
    sender_map = {}
    c = conn.cursor()

    # Get all sender IDs from the message table
    tbl = 'Msg_' + hashlib.md5(talker.encode()).hexdigest()
    c.execute(f'SELECT DISTINCT real_sender_id FROM \"{tbl}\"')
    sender_ids = [row[0] for row in c.fetchall()]

    # Map sender_id -> user_name using Name2Id
    c2 = conn.cursor()
    for sid in sender_ids:
        c2.execute('SELECT user_name FROM Name2Id WHERE rowid = ?', (sid,))
        row = c2.fetchone()
        if row and row[0]:
            sender_map[sid] = row[0]

    return sender_map


def build_sender_map_from_shards(db_path, key_hex, salt_hex, talker, passphrase=''):
    """Merge sender mappings from every message shard."""
    sender_map = {}
    for shard in discover_message_shards(db_path):
        shard_conn = None
        try:
            shard_key, shard_salt = derive_database_key(shard, key_hex, salt_hex, passphrase)
            shard_conn, _ = connect(shard, shard_key, shard_salt)
            sender_map.update(build_sender_map(shard_conn, talker))
        except Exception:
            continue
        finally:
            if shard_conn is not None:
                shard_conn.close()
    return sender_map


def scan_nt_cache(nt_cache_dir, talker, account_dir='', own_wxid=''):
    """Scan NT cache directory for image thumbnails and temp images.

    NT cache structure:
        cache/YYYY-MM/Message/<talker_md5>/
            Thumb/<local_id>_<timestamp>_thumb.jpg
            ImageTemp/<local_id>_<timestamp>_hd_temp_convert
            ImageTemp/<local_id>_<timestamp>_mid_temp_convert

    Returns dict: {local_id: (base64_data, mime_type)}
    """
    talker_md5 = hashlib.md5(talker.encode()).hexdigest()
    image_map = {}

    if not nt_cache_dir or not os.path.isdir(nt_cache_dir):
        return image_map

    for month_dir in sorted(os.listdir(nt_cache_dir)):
        msg_dir = os.path.join(nt_cache_dir, month_dir, 'Message', talker_md5)
        if not os.path.isdir(msg_dir):
            continue

        # Priority 1: ImageTemp (HD/mid quality)
        img_temp_dir = os.path.join(msg_dir, 'ImageTemp')
        if os.path.isdir(img_temp_dir):
            for fname in os.listdir(img_temp_dir):
                fpath = os.path.join(img_temp_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                size = os.path.getsize(fpath)
                if size > MAX_EMBED_SIZE:
                    continue
                # Parse local_id from filename: <local_id>_<timestamp>_...
                parts = fname.split('_', 1)
                if parts and parts[0].isdigit():
                    local_id = int(parts[0])
                    mime = detect_mime(fpath)
                    if mime:
                        try:
                            with open(fpath, 'rb') as fh:
                                data = fh.read()
                            if len(data) < MAX_EMBED_SIZE:
                                if _WECHAT_IMAGE and FULL_IMAGES:
                                    data, mime = wechat_image.shrink(data, mime, max_side=480)
                                image_map[local_id] = (base64.b64encode(data).decode(), mime)
                                cache_time = parse_cache_timestamp(fname)
                                if cache_time:
                                    image_map[f'pair:{local_id}:{cache_time}'] = image_map[local_id]
                                    image_map[f'time:{cache_time}'] = image_map[local_id]
                        except:
                            pass

        # Priority 2: Thumb (fill in gaps)
        thumb_dir = os.path.join(msg_dir, 'Thumb')
        if os.path.isdir(thumb_dir):
            for fname in os.listdir(thumb_dir):
                if not fname.endswith('.jpg'):
                    continue
                parts = fname.split('_', 1)
                if parts and parts[0].isdigit():
                    local_id = int(parts[0])
                    if local_id not in image_map:  # Don't override ImageTemp
                        fpath = os.path.join(thumb_dir, fname)
                        size = os.path.getsize(fpath)
                        if size < MAX_EMBED_SIZE:
                            try:
                                with open(fpath, 'rb') as fh:
                                    data = fh.read()
                                if len(data) < MAX_EMBED_SIZE:
                                    if _WECHAT_IMAGE and FULL_IMAGES:
                                        data, _m = wechat_image.shrink(data, 'image/jpeg', max_side=480)
                                    image_map[local_id] = (base64.b64encode(data).decode(), _m if _WECHAT_IMAGE else 'image/jpeg')
                                    cache_time = parse_cache_timestamp(fname)
                                    if cache_time:
                                        image_map[f'pair:{local_id}:{cache_time}'] = image_map[local_id]
                                        image_map[f'time:{cache_time}'] = image_map[local_id]
                            except:
                                pass

        for root, _, files in os.walk(msg_dir):
            if root == img_temp_dir:
                continue
            for fname in files:
                parts = fname.split('_', 1)
                local_id = int(parts[0]) if parts and parts[0].isdigit() else None
                file_md5 = extract_media_md5(fname)
                if local_id is None and not file_md5:
                    continue
                cache_time = parse_cache_timestamp(fname)
                if local_id is not None and (local_id in image_map or f'time:{cache_time}' in image_map):
                    continue
                if file_md5 and f'md5:{file_md5}' in image_map:
                    continue
                fpath = os.path.join(root, fname)
                try:
                    if os.path.getsize(fpath) >= MAX_EMBED_SIZE:
                        continue
                    mime = detect_mime(fpath)
                    if not mime:
                        continue
                    with open(fpath, 'rb') as fh:
                        data = fh.read()
                    if data:
                        if _WECHAT_IMAGE and FULL_IMAGES:
                            data, mime = wechat_image.shrink(data, mime, max_side=480)
                            if not data:
                                continue
                        image = (base64.b64encode(data).decode(), mime)
                        if local_id is not None:
                            image_map[local_id] = image
                        if cache_time:
                            if local_id is not None:
                                image_map[f'pair:{local_id}:{cache_time}'] = image
                            image_map[f'time:{cache_time}'] = image
                        if file_md5:
                            image_map[f'md5:{file_md5}'] = image
                except OSError:
                    continue

    # The account media index is where full-resolution originals live; without
    # it an export uses only this conversation's own cache thumbnails.
    if FULL_IMAGES and account_dir and os.path.isdir(account_dir):
        for key, image in scan_account_media(account_dir, own_wxid, talker).items():
            if key.startswith('md5:'):
                image_map.setdefault(key, image)

    # `unique:<local_id>`: an id that resolves to exactly one distinct image in
    # this conversation is safe to match on its own. The id restarts across
    # shards, but uniqueness inside one conversation's scan rules that out -
    # which is what separates this from the bare-local_id match that
    # get_cached_image deliberately refuses. Deduped by content, so one picture
    # cached at several timestamps still counts as unique.
    local_candidates = {}
    for key, image in image_map.items():
        if isinstance(key, str) and key.startswith('pair:'):
            parts = key.split(':', 2)
            if len(parts) != 3:
                continue
            local_id = parts[1]
        elif isinstance(key, int):
            local_id = str(key)
        else:
            continue
        digest = hashlib.sha256(image[0].encode('ascii')).hexdigest()
        local_candidates.setdefault(local_id, {})[digest] = image
    for local_id, candidates in local_candidates.items():
        if len(candidates) == 1:
            image_map[f'unique:{local_id}'] = next(iter(candidates.values()))

    return image_map


def scan_account_media(account_dir, own_wxid='', talker=''):
    """Index image resources stored outside a conversation cache directory.

    Scoped to `talker` whenever it is known. Walking the whole account means
    stat-ing and probing ~20k files (and 255-way XOR on every non-image), which
    dominated export time; a conversation only ever needs its own media.
    """
    image_map = {}
    v2_key = resolve_v2_media_key(account_dir, own_wxid)
    if talker:
        talker_md5 = hashlib.md5(talker.encode()).hexdigest()
        roots = [os.path.join(account_dir, 'msg', 'attach', talker_md5)]
        cache_root = os.path.join(account_dir, 'cache')
        if os.path.isdir(cache_root):
            for month in sorted(os.listdir(cache_root)):
                d = os.path.join(cache_root, month, 'Message', talker_md5)
                if os.path.isdir(d):
                    roots.append(d)
    else:
        roots = [
            os.path.join(account_dir, 'cache'),
            os.path.join(account_dir, 'msg'),
            os.path.join(account_dir, 'resource'),
            os.path.join(account_dir, 'business'),
            os.path.join(account_dir, 'temp'),
        ]
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for current_root, _, files in os.walk(root):
            for fname in files:
                path = os.path.join(current_root, fname)
                try:
                    stat = os.stat(path)
                    if stat.st_size <= 16 or stat.st_size > MAX_EMBED_SIZE:
                        continue
                    real_path = os.path.realpath(path)
                    if real_path in seen:
                        continue
                    seen.add(real_path)
                    with open(path, 'rb') as fh:
                        header = fh.read(64)
                    mime = detect_mime_from_bytes(header[:16])
                    if not mime:
                        decoded = decode_wechat_media(header, path, v2_key)
                        if decoded:
                            data, mime = decoded
                    else:
                        with open(path, 'rb') as fh:
                            data = fh.read(MAX_EMBED_SIZE + 1)
                    if not mime or len(data) > MAX_EMBED_SIZE:
                        continue
                    image = (base64.b64encode(data).decode(), mime)
                    for media_md5 in extract_media_md5s(fname):
                        image_map.setdefault(f'md5:{media_md5}', image)
                    content_md5 = hashlib.md5(data).hexdigest()
                    image_map.setdefault(f'md5:{content_md5}', image)
                except (OSError, ValueError):
                    continue
    return image_map


def clean_account_wxid(value):
    value = str(value or '').strip()
    parts = value.rsplit('_', 1)
    if len(parts) == 2 and len(parts[1]) == 4 and parts[1].isalnum():
        return parts[0]
    return value


def resolve_v2_media_key(account_dir, own_wxid='', kvcomm_dir=''):
    """Derive and verify the local WeChat V2 image key without persisting it."""
    if not account_dir:
        return None
    if not kvcomm_dir:
        appdata = os.environ.get('APPDATA', '')
        kvcomm_dir = os.path.join(appdata, 'Tencent', 'xwechat', 'net', 'kvcomm')
    try:
        codes = sorted({
            int(match.group(1))
            for name in os.listdir(kvcomm_dir)
            if (match := re.fullmatch(r'key_(\d+)_.+\.statistic', name, re.IGNORECASE))
        })
    except (OSError, ValueError):
        return None
    if not codes:
        return None

    templates = []
    for root in ('msg', 'cache', 'resource'):
        search_root = os.path.join(account_dir, root)
        if not os.path.isdir(search_root):
            continue
        for current_root, _, files in os.walk(search_root):
            for name in files:
                if not name.lower().endswith('_t.dat'):
                    continue
                path = os.path.join(current_root, name)
                try:
                    with open(path, 'rb') as stream:
                        header = stream.read(V2_CIPHERTEXT_START + 16)
                    if header.startswith(V2_MAGIC) and len(header) >= V2_CIPHERTEXT_START + 16:
                        templates.append(header[V2_CIPHERTEXT_START:V2_CIPHERTEXT_START + 16])
                except OSError:
                    continue
                if len(templates) >= 32:
                    break
            if len(templates) >= 32:
                break
        if len(templates) >= 32:
            break
    if not templates:
        return None

    wxids = list(dict.fromkeys(filter(None, (
        clean_account_wxid(own_wxid),
        clean_account_wxid(Path(account_dir).name),
    ))))
    try:
        from Crypto.Cipher import AES
    except ImportError:
        return None
    for wxid in wxids:
        for code in codes:
            aes_key = hashlib.md5(f'{code}{wxid}'.encode()).hexdigest()[:16].encode('ascii')
            try:
                plaintext = AES.new(aes_key, AES.MODE_ECB).decrypt(templates[0])
            except (TypeError, ValueError):
                continue
            if detect_mime_from_bytes(plaintext) or plaintext.startswith((b'wxgf', b'WXGF')):
                return code & 0xff, aes_key
    return None


def decode_wechat_v2(filepath, xor_key, aes_key):
    try:
        from Crypto.Cipher import AES
        from Crypto.Util import Padding
        with open(filepath, 'rb') as stream:
            data = stream.read(MAX_EMBED_SIZE + 1)
        if len(data) > MAX_EMBED_SIZE or not data.startswith(V2_MAGIC):
            return None
        signature, aes_size, xor_size = struct.unpack('<6sLLx', data[:V2_CIPHERTEXT_START])
        if signature != V2_MAGIC:
            return None
        encrypted_size = aes_size + 16 - aes_size % 16
        encrypted = data[V2_CIPHERTEXT_START:V2_CIPHERTEXT_START + encrypted_size]
        decrypted = Padding.unpad(AES.new(aes_key, AES.MODE_ECB).decrypt(encrypted), 16)
        remainder = data[V2_CIPHERTEXT_START + encrypted_size:]
        if xor_size:
            if xor_size > len(remainder):
                return None
            raw = remainder[:-xor_size]
            tail = bytes(value ^ xor_key for value in remainder[-xor_size:])
        else:
            raw, tail = remainder, b''
        output = decrypted + raw + tail
        mime = detect_mime_from_bytes(output[:16])
        return (output, mime) if mime else None
    except (OSError, ValueError, struct.error):
        return None


def decode_wechat_media(data, filepath=None, v2_key=None):
    """Decode common XOR-obfuscated WeChat image cache payloads."""
    if not data or len(data) < 16:
        return None
    if data.startswith(V2_MAGIC) and filepath and v2_key:
        # No downscaling here: this is called once per file while building the
        # account media index (tens of thousands of files), so doing image work
        # at this layer makes the index build take minutes. Shrinking happens
        # in get_cached_image(), which only runs for images actually embedded.
        return decode_wechat_v2(filepath, *v2_key)
    for key in range(1, 256):
        decoded = bytes(value ^ key for value in data[: min(len(data), 64)])
        mime = detect_mime_from_bytes(decoded)
        if mime:
            if filepath:
                try:
                    with open(filepath, 'rb') as fh:
                        raw = fh.read(MAX_EMBED_SIZE + 1)
                    if len(raw) > MAX_EMBED_SIZE:
                        return None
                except OSError:
                    return None
            else:
                raw = data
            full = bytes(value ^ key for value in raw)
            return full, mime
    return None


def parse_cache_timestamp(filename):
    parts = filename.split('_', 2)
    if len(parts) < 2 or not parts[1].isdigit():
        return 0
    timestamp = int(parts[1])
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return timestamp


def extract_media_md5(value):
    values = extract_media_md5s(value)
    return values[0] if values else ''


def extract_media_md5s(value):
    matches = re.findall(r'(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])', str(value or ''), re.IGNORECASE)
    return list(dict.fromkeys(match.lower() for match in matches))


def extract_blob_md5s(value, known_md5s=None):
    if value is None:
        return []
    if isinstance(value, memoryview):
        data = value.tobytes()
    elif isinstance(value, (bytes, bytearray)):
        data = bytes(value)
    else:
        data = str(value).encode('utf-8', errors='ignore')
    matches = re.findall(rb'(?i)([0-9a-f]{32})(?:[._][thbc])?\.dat', data)
    if not matches:
        matches = re.findall(rb'(?i)(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])', data)
    result = [item.decode('ascii').lower() for item in matches]
    # MessageResourceInfo commonly stores MD5 values as raw 16-byte fields.
    # Index those candidates; get_cached_image will retain only candidates
    # that resolve to an actual local media file.
    for offset in range(0, max(0, len(data) - 15)):
        candidate = data[offset:offset + 16]
        candidate_hex = candidate.hex()
        if (candidate not in (b'\x00' * 16, b'\xff' * 16)
                and (known_md5s is None or candidate_hex in known_md5s)):
            result.append(candidate_hex)
    return list(dict.fromkeys(result))


def load_resource_media_map(account_dir, key_hex, salt_hex, messages, image_map=None, passphrase=''):
    """Load message-resource MD5s keyed by message IDs when available."""
    if not account_dir or not messages:
        return {}
    resource_db = os.path.join(account_dir, 'db_storage', 'message', 'message_resource.db')
    if not os.path.isfile(resource_db):
        return {}
    server_ids = {int(row[1] or 0) for row in messages if row[1]}
    result = {}
    known_md5s = {
        key[4:].lower() for key in (image_map or {})
        if isinstance(key, str) and key.startswith('md5:')
    }
    conn = None
    try:
        resource_key, resource_salt = derive_database_key(resource_db, key_hex, salt_hex, passphrase)
        conn, cursor = connect(resource_db, resource_key, resource_salt)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name)=lower('MessageResourceInfo')")
        table = cursor.fetchone()
        if not table:
            conn.close()
            return result
        cursor.execute('SELECT message_svr_id, message_local_id, packed_info FROM "MessageResourceInfo"')
        for server_id, local_id, packed_info in cursor.fetchall():
            sid = int(server_id or 0)
            # Local IDs can collide across conversations and database shards.
            if not sid or sid not in server_ids:
                continue
            md5s = extract_blob_md5s(packed_info, known_md5s)
            if md5s:
                result.setdefault(f'server:{sid}', []).extend(md5s)
        conn.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
    return {key: list(dict.fromkeys(values)) for key, values in result.items()}


_SHRINK_MEMO = {}


def shrink_embedded(image, max_side=720, force=False):
    """Downscale an (base64, mime) pair on the way out of the media index.

    The index deliberately keeps originals: it is built over the whole account
    and only a fraction of it ever gets embedded, so image work belongs here
    rather than there.

    `force` skips the opt-in gate, for media that has no smaller alternative -
    an original pulled in from the account index because the conversation
    cache never held it at all.
    """
    if not image or not _WECHAT_IMAGE:
        return image
    if not force and not FULL_IMAGES:
        return image
    memo_key = image[0][:64] + ':' + str(len(image[0]))
    if memo_key in _SHRINK_MEMO:
        return _SHRINK_MEMO[memo_key]

    # Disk cache: shrinking a multi-MB photo costs ~0.15s, and the same media
    # comes back on every export. Key is the source digest, so this is safe to
    # share across conversations.
    cache_dir = COVER_STATE.get('imgshrink') or ''
    disk_key = hashlib.md5(image[0][:4096].encode()).hexdigest() + str(len(image[0]))
    disk_path = os.path.join(cache_dir, disk_key) if cache_dir else ''
    if disk_path and os.path.isfile(disk_path):
        try:
            with open(disk_path, 'rb') as fh:
                blob = fh.read()
            nl = blob.find(b'\n')
            if nl > 0:
                result = (blob[:nl].decode(), blob[nl + 1:].decode())
                _SHRINK_MEMO[memo_key] = result
                return result
        except OSError:
            pass

    try:
        raw = base64.b64decode(image[0])
        smaller, mime = wechat_image.shrink(raw, image[1], max_side=max_side)
        result = image if smaller is raw else (base64.b64encode(smaller).decode(), mime)
        _SHRINK_MEMO[memo_key] = result
        if disk_path and smaller is not raw:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                with open(disk_path, 'wb') as fh:
                    fh.write(result[0].encode() + b'\n' + result[1].encode())
            except OSError:
                pass
        return result
    except Exception:
        return image


def resolve_media(image_map, local_id, create_time, content='', resource_md5s=None, server_id=0):
    """Best local media for a message, with the identity that matched.

    Returns `(base64, mime, media_key)` or None. `media_key` is the image_map
    key that produced the hit, which is what the coverage report records - so
    a report can never claim a match the page itself did not get, because both
    come from this one function.

    Deliberately *not* falling back to a bare `local_id`: that id restarts in
    every message shard, so matching on it alone attaches one conversation's
    image to another conversation's message. `unique:<local_id>` is the sound
    version of that idea - scan_nt_cache only emits it when the id maps to a
    single distinct image, so uniqueness has already ruled the collision out.
    """
    if not image_map:
        return None
    for media_md5 in resource_md5s or []:
        key = f'md5:{media_md5}'
        if image_map.get(key):
            return shrink_embedded(image_map[key]) + (key,)
    for media_md5 in extract_media_md5s(content):
        key = f'md5:{media_md5}'
        if image_map.get(key):
            return shrink_embedded(image_map[key]) + (key,)
    try:
        pair_key = f'pair:{int(local_id)}:{int(create_time)}'
    except (TypeError, ValueError):
        return None
    if image_map.get(pair_key):
        return shrink_embedded(image_map[pair_key]) + (pair_key,)
    try:
        unique_key = f'unique:{int(local_id)}'
    except (TypeError, ValueError):
        return None
    if image_map.get(unique_key):
        return shrink_embedded(image_map[unique_key]) + (unique_key,)
    return None


def get_cached_image(image_map, local_id, create_time, content='', resource_md5s=None, server_id=0):
    """`resolve_media` without the matched key, for every pre-existing caller.

    md5 is the strongest key, but it is not always derivable - a group row can
    lack the resource mapping that carries it. The conversation cache also
    indexes by `(local_id, create_time)`, which is identity-aware and was
    already being stored; reading it recovers media the md5 path alone misses.
    """
    hit = resolve_media(image_map, local_id, create_time, content, resource_md5s, server_id)
    return (hit[0], hit[1]) if hit else None


def detect_mime(filepath):
    """Detect MIME type from file header."""
    try:
        with open(filepath, 'rb') as f:
            header = f.read(12)
        if header[:2] == b'\xff\xd8':
            return 'image/jpeg'
        if header[:4] == b'\x89PNG':
            return 'image/png'
        if header[:3] == b'GIF':
            return 'image/gif'
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
            return 'image/webp'
    except:
        pass
    return None


def find_thumbnail(create_time, msg_local_id, wx_dir):
    """Try to find image thumbnail from traditional FileStorage path (fallback)."""
    if not wx_dir or not os.path.isdir(wx_dir):
        return None

    dt = datetime.datetime.fromtimestamp(create_time)
    month_dir = dt.strftime('%Y-%m')

    for sub in ['Image', 'Image2']:
        img_dir = os.path.join(wx_dir, 'FileStorage', sub, month_dir)
        if not os.path.isdir(img_dir):
            continue
        try:
            for f in os.listdir(img_dir):
                fpath = os.path.join(img_dir, f)
                if not os.path.isfile(fpath):
                    continue
                if not f.startswith(f'{msg_local_id}_'):
                    continue
                fstat = os.stat(fpath)
                if os.path.getsize(fpath) < MAX_EMBED_SIZE:
                    with open(fpath, 'rb') as fh:
                        data = fh.read()
                    if len(data) < MAX_EMBED_SIZE:
                        return (base64.b64encode(data).decode(), 'image/jpeg')
        except:
            pass
    return None


def _decrypt_aes_cbc(payload, key_hex):
    key_hex = re.sub(r'[^0-9a-f]', '', str(key_hex or ''), flags=re.IGNORECASE)
    if len(key_hex) != 32:
        return None
    try:
        from Crypto.Cipher import AES
        key = bytes.fromhex(key_hex)
        decrypted = AES.new(key, AES.MODE_CBC, key).decrypt(payload)
        padding = decrypted[-1] if decrypted else 0
        if 0 < padding <= AES.block_size and decrypted.endswith(bytes([padding]) * padding):
            decrypted = decrypted[:-padding]
        return decrypted
    except Exception:
        return None


def _valid_media(data):
    if not data:
        return None
    mime = detect_mime_from_bytes(data[:16])
    if mime:
        return data, mime
    return None


# Writes a file with no '\n', which the (b64, mime) format can never produce.
NEGATIVE_MARKER = b'!'


def _cache_media(cache_file, payload):
    """Store a downloaded (b64, mime) pair, or a negative marker when None.

    Misses are cached too. The bulk of these URLs are dead WeChat CDN links,
    so without a negative cache every re-export re-attempted all of them.
    """
    if not cache_file:
        return
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        if payload is None:
            with open(cache_file, 'wb') as fh:
                fh.write(NEGATIVE_MARKER)
        else:
            with open(cache_file, 'wb') as fh:
                fh.write(payload[0].encode() + b'\n' + payload[1].encode())
    except OSError:
        pass


def _read_cache_media(cache_file):
    """Read a cached download.

    Returns a (b64, mime) pair, None for a cached miss, or 'unknown' when the
    path is absent or unreadable and a fetch should be attempted.
    """
    if not cache_file or not os.path.isfile(cache_file):
        return 'unknown'
    try:
        with open(cache_file, 'rb') as fh:
            blob = fh.read()
    except OSError:
        return 'unknown'
    if blob == NEGATIVE_MARKER:
        return None
    sep = blob.find(b'\n')
    if sep <= 0:
        return 'unknown'
    return (blob[:sep].decode(), blob[sep + 1:].decode())


def download_image_as_base64(url, aes_key='', timeout=10, budgeted=True):
    """Download image from URL and return (base64_data, mime_type) or None.

    Cached on disk and budgeted per run: this is the hot path for every appmsg
    thumbnail and sticker fallback, and it accounted for the entire runtime of
    a link-heavy export before either guard existed.

    `budgeted=False` is for calls made *by* the cover fetchers, which have
    already spent their own budget for this URL.
    """
    if not url or not url.startswith(('http://', 'https://')):
        MEDIA_STATE['remote_reason'] = 'not-http'
        return None

    sink = PREFETCH['sink']
    if sink is not None:
        # Dry pass: record the request, spend no budget, touch no network.
        sink.append(('img', url, aes_key))
        MEDIA_STATE['remote_reason'] = 'deferred-to-prefetch'
        return None

    cache_dir = COVER_STATE.get('dir') or ''
    # The AES key changes the bytes, so it belongs in the cache identity.
    slug = hashlib.md5(f'{url}\x00{aes_key}'.encode()).hexdigest()
    cache_file = os.path.join(cache_dir, slug + '.b64') if cache_dir else ''

    cached = _read_cache_media(cache_file)
    if cached != 'unknown':
        if budgeted:
            COVER_STATE['thumb_cached'] += 1
        MEDIA_STATE['remote_reason'] = None
        return cached

    if budgeted and not PREFETCH['active']:
        if COVER_STATE.get('thumb_budget', 0) <= 0:
            COVER_STATE['thumb_skipped'] += 1
            MEDIA_STATE['remote_reason'] = 'budget-exhausted'
            return None
        COVER_STATE['thumb_budget'] -= 1
        COVER_STATE['thumb_fetched'] += 1

    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://mp.weixin.qq.com/',
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(MAX_EMBED_SIZE + 1)
            # Too large and unparseable both mean "nothing usable here"; record
            # it so the next export does not pay for the same dead URL.
            if len(data) > MAX_EMBED_SIZE:
                _cache_media(cache_file, None)
                MEDIA_STATE['remote_reason'] = 'too-large'
                return None
            candidates = [data]
            decrypted = _decrypt_aes_cbc(data, aes_key)
            if decrypted:
                candidates.insert(0, decrypted)
            detected = next((hit for hit in map(_valid_media, candidates) if hit), None)
            if detected:
                data, mime = detected
                if _WECHAT_IMAGE:
                    # Covers arrive full-size; the reader shows them at 240px.
                    data, mime = wechat_image.shrink(data, mime, max_side=480)
                result = (base64.b64encode(data).decode(), mime)
                _cache_media(cache_file, result)
                MEDIA_STATE['remote_reason'] = None
                return result
        except Exception:
            continue
    _cache_media(cache_file, None)
    MEDIA_STATE['remote_reason'] = 'download-failed'
    return None


def detect_mime_from_bytes(header_bytes):
    """Detect MIME type from byte header."""
    if header_bytes[:2] == b'\xff\xd8':
        return 'image/jpeg'
    if header_bytes[:4] == b'\x89PNG':
        return 'image/png'
    if header_bytes[:3] == b'GIF':
        return 'image/gif'
    if header_bytes[:4] == b'RIFF' and len(header_bytes) >= 12 and header_bytes[8:12] == b'WEBP':
        return 'image/webp'
    return None


def extract_appmsg_image(content):
    """Extract image URL from appmsg XML content."""
    if not content:
        return None
    for tag in ('encrypturl', 'thumburl', 'cdnthumburl', 'appthumburl'):
        m = re.search(rf'<{tag}\b[^>]*>([\s\S]*?)</{tag}>', content, re.IGNORECASE)
        if m:
            url = m.group(1).strip()
            url = url.replace('<![CDATA[', '').replace(']]>', '').strip()
            url = decode_xml(url).replace('\\/', '/').replace('*#*', ':').strip()
            if url.startswith(('http://', 'https://')) and not is_share_page_url(url):
                return url
    for tag in ('encrypturl', 'thumburl', 'cdnthumburl', 'appthumburl'):
        m = re.search(rf'\b{tag}\s*=\s*["\']([^"\']+)', content, re.IGNORECASE)
        if m:
            url = decode_xml(m.group(1).strip()).replace('\\/', '/').replace('*#*', ':').strip()
            if url.startswith(('http://', 'https://')) and not is_share_page_url(url):
                return url
    for raw_url in re.findall(r'https?://[^\s<>"\']+', str(content), re.IGNORECASE):
        url = (decode_xml(raw_url).replace('\\/', '/').replace('\\u0026', '&')
               .replace('*#*', ':').strip(' \t\r\n\\\'"'))
        # App-card URLs (notably b23.tv/Bilibili share links) are page links,
        # not image resources. Never emit them as a broken <img> source.
        if url.startswith(('http://', 'https://')) and not is_share_page_url(url):
            return url
    return None


def is_share_page_url(url):
    return bool(re.match(
        r'https?://(?:www\.)?(?:b23\.tv|bilibili\.com|pan\.quark\.cn|'
        r'y\.music\.163\.com|music\.163\.com|mp\.weixin\.qq\.com|'
        r'schoai\.cn|share\.traecontent\.cn|hycx-gd\.cn|'
        r'campusgateway\.51job\.com|tieba\.baidu\.com)(?:/|$)',
        str(url or ''), re.IGNORECASE,
    ))


def resolve_emoticon_seed(configured, account_dir, out_dir):
    """The account's sticker seed, discovered from memory if unconfigured.

    The seed is a per-account constant that only exists in WeChat's process
    memory. Nothing used to populate it, so `STICKER_STATE['key']` stayed
    empty, local sticker decryption never ran, and every custom sticker
    degraded to a `[表情]` placeholder.

    Returns (seed, discovered). The scan costs a few seconds and is only
    needed once: the result is memoised under the output directory, and
    persisting it via `weflow-cli config set emoticonSeed` skips even that.
    """
    if configured:
        return str(configured), False
    if not account_dir or not _WECHAT_EMOTICON:
        return '', False

    cache_dir = os.path.join(out_dir, '.sticker-cache')
    memo = os.path.join(cache_dir, 'seed')
    try:
        with open(memo, 'r', encoding='utf-8') as fh:
            memoised = fh.read().strip()
        if memoised:
            return memoised, False
    except OSError:
        pass

    dirs = wechat_emoticon.sticker_cache_dirs(account_dir)
    sample = wechat_emoticon.any_sticker_file(dirs)
    if not sample:
        return '', False
    wxid = wechat_emoticon.account_wxid(os.path.basename(os.path.normpath(account_dir)))
    seed = wechat_emoticon.find_seed(wxid, sample)
    if not seed:
        return '', False
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(memo, 'w', encoding='utf-8') as fh:
            fh.write(str(seed))
    except OSError:
        pass
    return str(seed), True


def _page_cache_file(page_url):
    """Cache path for a resolved share-page cover.

    Namespaced apart from download_image_as_base64's entries so a page URL and
    an image URL can never collide on the same digest.
    """
    cache_dir = COVER_STATE.get('dir') or ''
    if not cache_dir:
        return ''
    return os.path.join(cache_dir, 'page-' + hashlib.md5(page_url.encode()).hexdigest() + '.b64')


def download_bilibili_cover(page_url, timeout=10):
    """Resolve a Bilibili share page and embed its og:image cover.

    Guarded like download_page_og_image: Bilibili shares are common in chat,
    and without a cache + shared budget every re-export re-fetched all of
    them, which alone accounted for minutes of an export.
    """
    if not page_url or not re.match(
            r'https?://(?:www\.)?(?:b23\.tv|bilibili\.com)(?:/|$)',
            page_url, re.IGNORECASE):
        return None

    sink = PREFETCH['sink']
    if sink is not None:
        sink.append(('page', page_url, ''))
        return None

    cache_file = _page_cache_file(page_url)
    cached = _read_cache_media(cache_file)
    if cached != 'unknown':
        COVER_STATE['cached'] += 1
        return cached
    if not PREFETCH['active']:
        # The prefetch pass has PREFETCH_MAX_URLS as its own bound and runs
        # concurrently, so charging it here would only make a large
        # conversation hit the cap early and fall back to serial fetching.
        if COVER_STATE.get('budget', 0) <= 0:
            COVER_STATE['skipped'] += 1
            return None
        COVER_STATE['budget'] -= 1
        COVER_STATE['fetched'] += 1

    try:
        req = urllib.request.Request(page_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.bilibili.com/',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(COVER_HEAD_BYTES).decode('utf-8', errors='ignore')
            final_url = resp.geturl()
        match = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            html, re.IGNORECASE,
        )
        cover_url = decode_xml(match.group(1)).replace('\\/', '/').strip() if match else ''
        if not cover_url:
            bvid_match = re.search(r'/(BV[0-9A-Za-z]+)(?:/|\?|$)', final_url, re.IGNORECASE)
            if not bvid_match:
                return None
            api_url = 'https://api.bilibili.com/x/web-interface/view?bvid=' + bvid_match.group(1)
            api_req = urllib.request.Request(api_url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'Referer': final_url,
            })
            with urllib.request.urlopen(api_req, timeout=timeout) as api_resp:
                payload = json.loads(api_resp.read(1024 * 1024).decode('utf-8'))
            cover_url = str((payload.get('data') or {}).get('pic') or '')
        if not cover_url:
            _cache_media(cache_file, None)
            return None
        cover = download_image_as_base64(cover_url, timeout=timeout, budgeted=False)
        _cache_media(cache_file, cover)
        return cover
    except Exception:
        _cache_media(cache_file, None)
        return None


def download_page_og_image(page_url, timeout=10):
    """Fetch a share page's og:image and embed the resolved cover.

    Guarded by a disk cache and a per-run budget: without them a conversation
    full of links spends minutes on network round-trips, and every re-export
    repeats the whole cost.
    """
    if not page_url or not page_url.startswith(('http://', 'https://')):
        return None

    sink = PREFETCH['sink']
    if sink is not None:
        sink.append(('page', page_url, ''))
        return None

    cache_file = _page_cache_file(page_url)
    cached = _read_cache_media(cache_file)
    if cached != 'unknown':
        COVER_STATE['cached'] += 1
        return cached

    if not PREFETCH['active']:
        # The prefetch pass has PREFETCH_MAX_URLS as its own bound and runs
        # concurrently, so charging it here would only make a large
        # conversation hit the cap early and fall back to serial fetching.
        if COVER_STATE.get('budget', 0) <= 0:
            COVER_STATE['skipped'] += 1
            return None
        COVER_STATE['budget'] -= 1
        COVER_STATE['fetched'] += 1

    try:
        req = urllib.request.Request(page_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': page_url,
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(COVER_HEAD_BYTES).decode('utf-8', errors='ignore')
        patterns = (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        )
        cover_url = ''
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                cover_url = decode_xml(match.group(1)).replace('\\/', '/').strip()
                break
        cover = download_image_as_base64(cover_url, timeout=timeout, budgeted=False) if cover_url else None
        _cache_media(cache_file, cover)
        return cover
    except Exception:
        _cache_media(cache_file, None)
        return None


def prefetch_remote(records):
    """Fetch recorded remote media concurrently, filling the disk cache.

    `records` is the PREFETCH sink: (kind, url, aes_key) triples. Everything
    lands in the same cache `download_image_as_base64` reads, so the real
    formatting pass becomes a sequence of cache hits. Returns a short summary
    for the progress line.
    """
    seen = set()
    jobs = []
    for kind, url, aes_key in records:
        key = (kind, url, aes_key)
        if key in seen:
            continue
        seen.add(key)
        jobs.append(key)
    if not jobs:
        return 0, 0

    dropped = 0
    if len(jobs) > PREFETCH_MAX_URLS:
        dropped = len(jobs) - PREFETCH_MAX_URLS
        jobs = jobs[:PREFETCH_MAX_URLS]

    def run(job):
        kind, url, aes_key = job
        try:
            if kind == 'page':
                if download_bilibili_cover(url):
                    return True
                return download_page_og_image(url) is not None
            return download_image_as_base64(url, aes_key) is not None
        except Exception:
            return False

    done = 0
    PREFETCH['active'] = True
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
            for ok in pool.map(run, jobs):
                if ok:
                    done += 1
    finally:
        PREFETCH['active'] = False
    return done, dropped


def extract_xml_attr_url(content, name):
    match = re.search(rf'\b{name}\s*=\s*["\']([^"\']+)', str(content or ''), re.IGNORECASE)
    if not match:
        return None
    url = decode_xml(match.group(1)).replace('\\/', '/').replace('*#*', ':').strip()
    return url if url.startswith(('http://', 'https://')) else None


def extract_xml_attr_value(content, name):
    match = re.search(rf'\b{name}\s*=\s*["\']([^"\']*)', str(content or ''), re.IGNORECASE)
    return decode_xml(match.group(1).strip()) if match else ''


def render_contact_card(content):
    nickname = extract_xml_attr_value(content, 'nickname') or '公众号名片'
    username = extract_xml_attr_value(content, 'username')
    avatar_url = extract_xml_attr_url(content, 'brandIconUrl')
    avatar = download_image_as_base64(avatar_url) if avatar_url else None
    parts = []
    if avatar:
        b64, mime = avatar
        parts.append(f'<img class="msg-app-thumb" src="data:{mime};base64,{b64}" loading="lazy" />')
    parts.append(f'<span class="msg-app-title">{escape_html(nickname)}</span>')
    if username:
        parts.append(f'<div class="msg-app-desc">{escape_html(username)}</div>')
    return '<div class="msg-app">' + ''.join(parts) + '</div>'


def load_contact_names(db_path, key_hex, salt_hex):
    """{username: best available name} from the contact database.

    A group message only identifies its sender by wxid, which is unusable in a
    transcript. Prefer the remark (what the user calls them) over the account
    nickname over the alias, matching how the 1:1 path already resolves the
    conversation partner.
    """
    names = {}
    if not db_path or not key_hex or not salt_hex or not os.path.isfile(db_path):
        return names
    conn = None
    try:
        conn, cursor = connect(db_path, key_hex, salt_hex)
        cursor.execute('SELECT username, remark, nick_name, alias FROM contact')
        for username, remark, nick_name, alias in cursor.fetchall():
            if not username:
                continue
            names[username] = remark or nick_name or alias or username
    except Exception:
        return names
    finally:
        if conn is not None:
            conn.close()
    return names


def contact_name(wxid):
    """Display name for a wxid, falling back to the wxid itself.

    An unresolved id is kept rather than blanked: it is still unique, and a
    transcript that silently drops the speaker is worse than one showing one.
    """
    return CONTACT_NAMES.get(wxid) or wxid


def split_group_speaker(content, sender_map, own_wxid=''):
    """(speaker id, content) with the group sender prefix removed.

    Group rows generally prefix the content with the speaker's id. Only an id
    the sender map actually knows is accepted, so a message that merely starts
    with `note: ...` is not mistaken for one. Returns (None, content) when
    there is no prefix to strip.
    """
    match = re.match(r'^([A-Za-z0-9_@.-]{5,64})\s*[:：]\s', str(content or ''))
    if not match:
        return None, content
    candidate = match.group(1)
    if candidate not in set((sender_map or {}).values()):
        return None, content
    return candidate, content[match.end():]


def image_is_blank(b64_data, threshold=0.985):
    """True for an embedded payload that carries no visible artwork.

    Sticker sources occasionally hand back an all-white frame - a truncated
    local download, or a CDN placeholder. Embedding it renders as an empty
    square, which reads as a broken export rather than as the placeholder it
    is, so callers prefer the text fallback.
    """
    if not b64_data or not _WECHAT_IMAGE:
        return False
    try:
        from collections import Counter
        import io as _io
        from PIL import Image
        with Image.open(_io.BytesIO(base64.b64decode(b64_data))) as image:
            # An animation's first frame can be blank while the animation is
            # not; judging it on frame 1 would drop a perfectly good sticker.
            if getattr(image, 'is_animated', False):
                return False
            pixels = list(image.convert('RGB').resize((32, 32)).getdata())
        if not pixels:
            return True
        return Counter(pixels).most_common(1)[0][1] / len(pixels) > threshold
    except Exception:
        return False


def render_voice(local_id, content):
    """`[语音 6″]` plus its transcript when one has been cached.

    Browsers cannot play SILK and ffmpeg cannot decode it, so there is no audio
    element to offer - the transcript is the only way a voice message can carry
    meaning in an export.
    """
    length = (extract_xml_attr_value(content, 'voicelength')
              or extract_xml_attr_value(content, 'length'))
    seconds = f' {round(int(length) / 1000)}″' if length.isdigit() and int(length) > 0 else ''
    label = f'<span class="msg-media">[语音{seconds}]</span>'
    blob = VOICE_STATE['map'].get(local_id)
    cache = VOICE_STATE['cache']
    if blob is None or cache is None:
        return label
    text = cache.get(wechat_voice.voice_key(blob))
    if not text:
        return label
    # Labelled, not presented as the words themselves. Recognition of dialect
    # speech is approximate: an unlabelled transcript reads as a quote, and a
    # confidently wrong quote is worse than an obvious placeholder.
    return (f'{label}<div class="msg-voice-text">'
            f'<span class="voice-tag">机器转写·粤语欠准</span>{escape_html(text)}</div>')


def render_location(content):
    """Readable label for a type-48 location row instead of its raw XML."""
    label = extract_xml_attr_value(content, 'poiname') or extract_xml_attr_value(content, 'label')
    return f'[位置] {escape_html(label)}' if label else '<span class="msg-media">[位置]</span>'


def extract_xml_text(content, tag):
    """Extract plain or CDATA-wrapped text from one XML element."""
    if not content:
        return ''
    match = re.search(rf'<{tag}\b[^>]*>([\s\S]*?)</{tag}>', content, re.IGNORECASE)
    if not match:
        return ''
    value = match.group(1).strip()
    cdata = re.fullmatch(r'<!\[CDATA\[([\s\S]*)\]\]>', value)
    return decode_xml((cdata.group(1) if cdata else value).strip())


def extract_media_aes_key(content):
    if not content:
        return ''
    for name in ('aeskey', 'aes_key', 'encryptaeskey'):
        match = re.search(rf'\b{name}\s*=\s*["\']([0-9a-f]{{32}})', content, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(rf'<{name}\b[^>]*>\s*([0-9a-f]{{32}})\s*</{name}>', content, re.IGNORECASE)
        if match:
            return match.group(1)
    return ''


def plain_fragment(text, limit=200):
    """Tag-stripped, whitespace-collapsed preview of an XML fragment."""
    if not text:
        return ''
    text = re.sub(r'<\?xml[^>]*\?>', ' ', str(text))
    text = re.sub(r'<[^>]*>', ' ', text)
    text = re.sub(r'\s+', ' ', decode_xml(text)).strip()
    return text[:limit]


def readable_fragment(text, limit=300):
    """`text` with any embedded document reduced to prose.

    Quoted replies (appmsg type 57) put a whole escaped message inside <des>,
    so rendering it verbatim fills the bubble with markup. Plain text passes
    through unchanged.
    """
    if not text:
        return ''
    if '<' in text or '&lt;' in text:
        return plain_fragment(decode_xml(text), limit)
    return text[:limit]


def render_group_template(content):
    """Readable text for a `sysmsgtemplate` group notice, or ''.

    The row carries a sentence template with `$name$` placeholders plus a
    `link_list` that maps each placeholder to member nicknames. Stripping tags
    without substituting left nothing but the chatroom id.
    """
    template = extract_xml_text(content, 'template')
    if not template:
        return ''
    for placeholder in set(re.findall(r'\$(\w+)\$', template)):
        link = re.search(rf'<link\s+name="{placeholder}"[\s\S]*?</link>', content, re.IGNORECASE)
        if not link:
            continue
        nicknames = []
        for raw in re.findall(r'<nickname>([\s\S]*?)</nickname>', link.group(0), re.IGNORECASE):
            value = raw.strip()
            cdata = re.fullmatch(r'<!\[CDATA\[([\s\S]*)\]\]>', value)
            text = (cdata.group(1) if cdata else value).strip()
            if text:
                nicknames.append(decode_xml(text))
        separator = extract_xml_text(link.group(0), 'separator') or '、'
        template = template.replace(f'${placeholder}$', separator.join(nicknames))
    # Any placeholder with no link_list entry is left dangling; drop it rather
    # than print `$username$` into the transcript.
    return re.sub(r'\$\w+\$', '', template).strip()


def render_system_message(content, names=None):
    """Readable text for a type-10000 system row.

    These rows carry XML, but it is WeChat's own display markup
    (`<img src="SystemMessages_HongbaoIcon.png"/>`, `<_wc_custom_link_ ...>`)
    rather than a document worth showing. Escaping it verbatim put a wall of
    `&lt;sysmsg ...&gt;` in the bubble; a revoke notice read as XML instead of
    saying who revoked what.
    """
    if not content:
        return ''
    if '<' not in content:
        return escape_html(content)
    if '<sysmsgtemplate' in content:
        # A join/invite notice: a template plus a member list, which has to be
        # substituted before any of the generic extraction below can help.
        return escape_html(render_group_template(content)) or escape_html(plain_fragment(content, 200))
    revoke = re.search(r'<revokemsg\b[\s\S]*?</revokemsg>', content, re.IGNORECASE)
    scope = revoke.group(0) if revoke else content
    text = ''
    for tag in ('content', 'title', 'text'):
        text = extract_xml_text(scope, tag)
        if text:
            break
    if not text:
        text = plain_fragment(scope, 200)
    for wxid, name in (names or {}).items():
        # The row keeps `$wxid_...$` for the client to expand at render time.
        if wxid and name:
            text = text.replace(f'${wxid}$', name)
    return escape_html(text)


def escape_html(text):
    if not text:
        return ''
    text = ''.join(char for char in str(text) if char in '\n\r\t' or ord(char) >= 32)
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


def load_builtin_emoji(name):
    """One bundled face as (base64, mime), or None."""
    path = wechat_emoji.IMAGE_FACES.get(name) if _WECHAT_EMOJI else None
    if not path:
        return None
    try:
        with open(path, 'rb') as stream:
            data = stream.read(MAX_EMBED_SIZE + 1)
        if len(data) > MAX_EMBED_SIZE:
            return None
        return base64.b64encode(data).decode(), 'image/png'
    except OSError:
        return None


def render_builtin_emoji(content, label=None):
    """Render every built-in face in `content`, in place.

    Faces become <span class="wxface wxf-…">; the artwork itself is emitted
    once per page by face_css(). The previous approach appended one <img> per
    message and only ever matched the first label, so a sentence containing
    several emoji rendered at most one - and repeated the base64 for it.
    """
    if not _WECHAT_EMOJI:
        return escape_html(content)
    return wechat_emoji.render_faces(escape_html(str(content or '')))


def parse_source(source_text):
    """Parse source field to extract sender and content."""
    sender = ''
    content = ''
    if not source_text:
        return sender, content

    if isinstance(source_text, bytes):
        try:
            source_text = source_text.decode('utf-8', errors='ignore')
        except:
            return '', ''

    # Format: "wxid_xxx:\ncontent..."
    if ':\n' in source_text:
        parts = source_text.split(':\n', 1)
        sender = parts[0]
        content = parts[1] if len(parts) > 1 else ''
    elif ':' in source_text:
        parts = source_text.split(':', 1)
        sender = parts[0]
        content = parts[1] if len(parts) > 1 else ''

    return sender, content


def sender_matches_account(sender_user_name, own_wxid):
    """Match the sender against the configured account, including NT suffixes."""
    if not sender_user_name or not own_wxid:
        return False
    if sender_user_name == own_wxid or sender_user_name.startswith(own_wxid + '_'):
        return True
    account_base = own_wxid.rsplit('_', 1)
    if len(account_base) == 2 and len(account_base[1]) == 4 and account_base[1].isalnum():
        return sender_user_name == account_base[0]
    return False


def format_message(row, talker, wx_dir, image_map=None, sender_map=None, display_name='', resource_map=None, own_wxid=''):
    """Format a single message for HTML display.

    Args:
        row: DB row tuple
        talker: target wxid
        wx_dir: traditional FileStorage path (fallback)
        image_map: {local_id: (base64_data, mime_type)} from NT cache scan
        sender_map: {sender_id: user_name} from Name2Id table
        display_name: human-readable name for the target talker
    """
    is_group = '@chatroom' in str(talker or '')
    local_id = row[0] or 0
    server_id = row[1] or 0
    local_type = row[2] or 0
    # WeChat stores some forwards as high-bit variants of type 49. The mask
    # below turns those into plain 49, which is a known type - so the cached
    # image branch further down can only recognise them if the pre-mask value
    # is kept. Dropping this made that branch unreachable.
    raw_local_type = local_type
    if local_type > 0xffffffff:
        local_type &= 0xffffffff
    is_encoded_media_type = raw_local_type > 0xffffffff and local_type == 49
    real_sender_id = row[4] or 0
    create_time = row[5] or 0
    source = row[7]
    message_content = row[8]
    compressed_content = row[9]
    resource_md5s = list((resource_map or {}).get(f'server:{int(server_id)}', [])) if server_id else []

    # Resolve sender name
    sender_user_name = (sender_map or {}).get(real_sender_id, '')
    # Do not infer "self" from a missing mapping. NT shards can have incomplete
    # Name2Id rows, and that would otherwise mark every unresolved message as sent.
    is_self = sender_matches_account(sender_user_name, own_wxid)
    if not is_self and sender_user_name and not own_wxid:
        is_self = sender_user_name != talker

    # Get content
    content = ''
    if isinstance(message_content, str) and message_content:
        content = message_content
    elif isinstance(message_content, bytes):
        content = decode_message_content(message_content)

    if not content and isinstance(compressed_content, bytes):
        content = decode_message_content(compressed_content)

    source_text = decode_message_content(source) if isinstance(source, (bytes, bytearray, memoryview)) else str(source or '')
    if not content and source_text:
        _, content = parse_source(source_text)
    # Some NT rows store the complete emoji XML entity-escaped in the message
    # column (for example ``&lt;msg&gt;...&lt;/msg&gt;``). Normalize it before
    # detecting media metadata so it follows the same path as raw XML.
    if '&lt;' in content.lower():
        normalized_content = content
        for _ in range(2):
            candidate = decode_xml(normalized_content)
            if candidate == normalized_content:
                break
            normalized_content = candidate
        if re.search(r'<(?:msg|emoji)\b', normalized_content, re.IGNORECASE):
            content = normalized_content
    # NT emoji metadata may be split between source XML and message_content.
    # Prefer the representation that actually carries media identity/URLs;
    # source can contain only PUA/signature fields for the same message.
    metadata_parts = []
    if source_text:
        metadata_parts.append(source_text)
    if content and content != source_text:
        metadata_parts.append(content)
    metadata_content = '\n'.join(metadata_parts)
    # A type-49 row carrying a title or url is a link/article card, not a bare
    # image. Without this guard the encoded-media fallback below would hide the
    # card (and its title/link) behind a cached thumbnail.
    has_app_link_metadata = local_type == 49 and bool(
        extract_xml_text(content, 'title') or extract_xml_text(content, 'url'))
    is_emoji_xml = bool(re.search(r'<(?:msg\s*>)?\s*<emoji\b|<emoji\b', metadata_content, re.IGNORECASE))
    is_contact_card = bool(
        re.search(r'<msg\b[^>]*(?:nickname|username)=', metadata_content, re.IGNORECASE)
        and re.search(r'\bbrandIconUrl=', metadata_content, re.IGNORECASE)
    )
    has_builtin_signature = bool(
        re.search(r'<signature\b[^>]*>[^<]+</signature>', metadata_content, re.IGNORECASE)
        and _face_index.has_face(content)
    )
    builtin_emoji_label = _face_index.find_face(content)

    # A group row's `display_name` is the group, not the speaker, so using it
    # put the same name on every bubble and nobody could tell who said what.
    # The speaker is in the content prefix; fall back to the sender map.
    group_speaker = None
    if is_group:
        group_speaker, content = split_group_speaker(content, sender_map)

    if local_type == 10000:
        # Revoke notices and group templates are emitted by the system, and
        # their real_sender_id resolves to nobody. Anything else here reads as
        # if a person had said it.
        sender_display = '系统'
    elif is_self:
        sender_display = '我'
    elif group_speaker:
        sender_display = ('我' if sender_matches_account(group_speaker, own_wxid)
                          else contact_name(group_speaker))
    elif is_group and sender_user_name:
        sender_display = contact_name(sender_user_name)
    elif display_name and not is_group:
        # `display_name` is the conversation. For a group that is the group
        # itself, so using it as a fallback labelled unrelated rows with the
        # group name - three system rows in one export read as "江南试验群".
        sender_display = display_name
    elif sender_user_name:
        sender_display = contact_name(sender_user_name)
    else:
        sender_display = '未知发送者'

    if '\x00' in content or sum(ord(char) < 32 and char not in '\n\r\t' for char in content) > 2:
        content = ''

    # Determine display content
    display = ''
    image_b64 = None

    if is_contact_card:
        display = render_contact_card(metadata_content)
    elif local_type == 1 and '<' not in content:
        # Tested against `content`, not `metadata_content`: the latter always
        # carries the row's `<msgsource>` block, so it always contains '<'.
        # Any text message with sender metadata therefore failed this branch
        # and fell through to the emoji one, which rendered it as `[表情]` plus
        # whatever URL its catch-all found - a meeting invite became a broken
        # image whose src was `https://meeting.tencent.com/dm/...`.
        # Text
        builtin_label = _face_index.find_face(content)
        display = render_builtin_emoji(content, builtin_label) if builtin_label else escape_html(content)
    elif local_type == 3:
        # Image - try cache map first, then traditional FileStorage
        display = '<span class="msg-media">[图片]</span>'
        img_data = None
        mime = 'image/jpeg'

        # Priority 1: NT cache thumbnails
        cached = get_cached_image(image_map, local_id, create_time, metadata_content, resource_md5s)
        if cached:
            img_data, mime = cached
        # Priority 2: Traditional FileStorage
        else:
            result = find_thumbnail(create_time, local_id, wx_dir)
            if result:
                img_data, mime = result
            else:
                thumb_url = extract_appmsg_image(content)
                downloaded = download_image_as_base64(thumb_url, extract_media_aes_key(content)) if thumb_url else None
                if downloaded:
                    img_data, mime = downloaded

        if img_data:
            image_b64 = img_data
            display += f'<br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
    elif (local_type not in MSG_TYPES or is_encoded_media_type) \
            and not has_app_link_metadata \
            and get_cached_image(image_map, local_id, create_time, content, resource_md5s):
        # Some image messages use encoded types (e.g. 21474836529 = images in appmsg)
        # Check image_map for any message type
        img_data, mime = get_cached_image(image_map, local_id, create_time, content, resource_md5s)
        if img_data:
            image_b64 = img_data
            display = f'<span class="msg-media">[图片]</span><br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
    elif local_type == 34:
        display = render_voice(local_id, metadata_content or content)
    elif local_type == 43:
        # A video row has no frame of its own, but a poster image can exist
        # under the same md5, so show it when the index has one.
        seconds = extract_xml_attr_value(metadata_content, 'playlength')
        label = f'[视频 {seconds}″]' if seconds.isdigit() and seconds != '0' else '[视频]'
        display = f'<span class="msg-media">{label}</span>'
        cached = get_cached_image(image_map, local_id, create_time, metadata_content, resource_md5s)
        if cached:
            img_data, mime = cached
            image_b64 = img_data
            display += f'<br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
    elif local_type == 48:
        display = render_location(metadata_content or content)
    elif is_emoji_xml or (local_type in (1, 47) and ('<' in metadata_content or local_type == 47)):
        emoji_label = content if content.startswith('[') and content.endswith(']') else '[表情]'
        cached = get_cached_image(image_map, local_id, create_time, metadata_content, resource_md5s)
        if cached:
            img_data, mime = cached
            image_b64 = img_data
            display = f'<span class="msg-media">{escape_html(emoji_label)}</span><br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
        else:
            # Local first. WeChat's own sticker cache is offline, instant, and
            # normally resolves; the CDN paths need a network round-trip each
            # and usually have nothing left to serve for older stickers.
            if _WECHAT_EMOTICON and STICKER_STATE['key'] and metadata_content:
                sticker_md5 = extract_xml_attr_value(metadata_content, 'md5') or ''
                data, mime = wechat_emoticon.load_sticker(
                    STICKER_STATE['dirs'], sticker_md5, STICKER_STATE['key'],
                    STICKER_STATE['cache_dir'])
                if data:
                    img_data = base64.b64encode(data).decode()
                    image_b64 = img_data
                    display = f'<span class="msg-media">{escape_html(emoji_label)}</span><br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
            thumb_url = ''
            if not image_b64:
                thumb_url = extract_appmsg_image(metadata_content)
                downloaded = download_image_as_base64(thumb_url, extract_media_aes_key(metadata_content)) if thumb_url else None
                if not downloaded:
                    fallback_url = extract_xml_attr_url(metadata_content, 'thumburl')
                    if fallback_url and fallback_url != thumb_url:
                        downloaded = download_image_as_base64(fallback_url)
                if downloaded:
                    img_data, mime = downloaded
                    image_b64 = img_data
                    display = f'<span class="msg-media">{escape_html(emoji_label)}</span><br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
            if image_b64 and image_is_blank(image_b64):
                # A blank square is indistinguishable from a broken page; fall
                # back to the label so the message still reads as an emoji.
                image_b64 = None
                img_data = None
                display = f'<span class="msg-media">{escape_html(emoji_label)}</span>'
            if image_b64:
                pass
            elif has_builtin_signature and builtin_emoji_label:
                display = render_builtin_emoji(content, builtin_emoji_label)
            else:
                # Forwarded/default emoji messages can contain a complete
                # appmsg XML wrapper but no recoverable local media. Keep the
                # export readable instead of dumping the XML into the bubble.
                if is_emoji_xml:
                    title = extract_xml_text(content, 'title')
                    display = escape_html(title or emoji_label)
                elif is_share_page_url(content.strip()):
                    # A webpage URL carried by an emoji-like row is still a
                    # link, not an image. Preserve the original content.
                    display = escape_html(content)
                else:
                    display = escape_html(content) if content else '<span class="msg-media">[表情]</span>'
    elif local_type == 49 and not is_emoji_xml:
        # App message (link/file/article)
        if content:
            # Try to parse XML for title/desc
            title = readable_fragment(extract_xml_text(content, 'title'))
            desc = readable_fragment(extract_xml_text(content, 'des'))
            url = extract_xml_text(content, 'url')
            app_type = extract_xml_text(content, 'type')

            if app_type == '6' and re.search(r'\.\w+$', title):
                display = f'<span class="msg-file">[文件] {escape_html(title)}</span>'
            elif title:
                parts = []
                builtin_title = _face_index.find_face(title)
                title_html = (render_builtin_emoji(title, builtin_title)
                              if builtin_title else escape_html(title))
                # Try the conversation cache before the network: a cached
                # thumbnail is already on disk and costs nothing, and without
                # this the article branch downloaded covers it already had.
                thumb_url = extract_appmsg_image(content)
                img_data = get_cached_image(image_map, local_id, create_time, metadata_content, resource_md5s)
                if not img_data and thumb_url:
                    img_data = download_image_as_base64(thumb_url, extract_media_aes_key(content))
                if not img_data:
                    page_url = extract_xml_text(content, 'url')
                    img_data = download_bilibili_cover(page_url)
                    if not img_data:
                        img_data = download_page_og_image(page_url)
                if img_data:
                    b64, mime = img_data
                    image_b64 = b64
                    parts.append(f'<img class="msg-app-thumb" src="data:{mime};base64,{b64}" loading="lazy" />')
                # No remote-src fallback. The candidate URL came from a
                # catch-all that accepts any URL in the row, which is usually a
                # web page link (`meeting.tencent.com`, `github.com`,
                # `support.weixin.qq.com`) rather than an image; emitting it
                # produced a broken-image icon in 56 of 57 sampled cases.
                if url.startswith(('http://', 'https://')):
                    parts.append(f'<a class="msg-link" href="{escape_html(url)}" target="_blank">{title_html}</a>')
                else:
                    parts.append(f'<span class="msg-app-title">{title_html}</span>')
                if desc:
                    parts.append(f'<div class="msg-app-desc">{escape_html(desc)}</div>')
                display = '<div class="msg-app">' + ''.join(parts) + '</div>'
            else:
                display = '<span class="msg-media">[链接/文件]</span>'
        else:
            display = '<span class="msg-media">[链接/文件]</span>'
    elif local_type == 50:
        display = '<span class="msg-media">[语音通话]</span>'
    elif local_type == 10000:
        names = {talker: display_name or talker, own_wxid: '我'}
        display = f'<span class="msg-sys">{render_system_message(content, names)}</span>'
    elif local_type == 10002:
        display = escape_html(content) if content else '<span class="msg-media">[引用]</span>'
    else:
        if content:
            display = escape_html(content)
        else:
            type_name = MSG_TYPES.get(local_type, f'类型{local_type}')
            display = f'<span class="msg-media">[{type_name}]</span>'

    return {
        'local_id': local_id,
        'create_time': create_time,
        'is_send': is_self,
        'sender': sender_display,
        'is_self': is_self,
        'local_type': local_type,
        'display': display,
        'image_b64': image_b64,
        # Built here rather than accumulated in module state: format_message
        # runs twice (the prefetch dry sweep and the real pass) and the dry
        # pass is discarded, so a counter would double-count.
        'media': build_media_record(
            local_type, image_b64, image_map, local_id, create_time, content,
            resource_md5s, metadata_content, bool(server_id)),
    }


MEDIA_KIND_BY_TYPE = {
    3: 'image', 34: 'voice', 43: 'video-thumb', 47: 'emoji', 49: 'link-cover',
}
MEDIA_KINDS = {'image', 'emoji', 'voice', 'video-thumb', 'link-cover'}


def build_media_report(items, scope, talker, per_page):
    """Coverage summary plus one entry per media item.

    Written beside the HTML rather than into it: the page is for reading, this
    is for answering "what is missing and why" without opening the export.
    Paths, keys and message bodies are not included (D-001/D-002/D-025).
    """
    counts = {}
    for item in items:
        counts[item['status']] = counts.get(item['status'], 0) + 1
    counts['total'] = len(items)
    return {
        'schema': 'weflow-media-report/v1',
        'source': 'weflow-cli',
        'generatedAt': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'scope': scope,
        'counts': counts,
        'budget': {
            'coversFetched': COVER_STATE.get('fetched', 0),
            'coversCached': COVER_STATE.get('cached', 0),
            'coversSkipped': COVER_STATE.get('skipped', 0),
            'thumbsFetched': COVER_STATE.get('thumb_fetched', 0),
            'thumbsCached': COVER_STATE.get('thumb_cached', 0),
            'thumbsSkipped': COVER_STATE.get('thumb_skipped', 0),
        },
        'items': [
            {
                'mediaKey': item['mediaKey'],
                'kind': item['kind'],
                'status': item['status'],
                'mime': item['mime'],
                'bytes': item['bytes'],
                'sha256': item['sha256'],
                'reason': item['reason'],
                'locator': {
                    'part': (item['index'] // per_page + 1) if per_page else 1,
                    'createTime': None,
                },
            }
            for item in items
        ],
    }


def build_media_record(local_type, image_b64, image_map, local_id, create_time,
                       content, resource_md5s, metadata_content, has_server_id):
    """What happened to this message's media, or None for a non-media message.

    Derived from the same `resolve_media` the page used, so the report cannot
    claim a match the page did not get (or vice versa). A media-free message
    returns None: this is a media manifest, not a message manifest.
    """
    if local_type not in MEDIA_KIND_BY_TYPE:
        return None
    kind = MEDIA_KIND_BY_TYPE[local_type]
    record = {
        'mediaKey': None, 'kind': kind, 'status': None,
        'mime': None, 'bytes': None, 'sha256': None, 'reason': None,
    }

    if image_b64:
        hit = resolve_media(image_map, local_id, create_time, metadata_content,
                            resource_md5s)
        if hit:
            # Matched a local key: report which one, so "why this image" is
            # answerable without re-running the export.
            record['mediaKey'] = hit[2]
            record['status'] = 'embedded'
        else:
            # Bytes came from somewhere other than the conversation index.
            if MEDIA_STATE['remote_reason'] is None:
                record['status'] = 'remote-fetched'
                record['mediaKey'] = 'remote'
            else:
                record['status'] = 'embedded'
                record['mediaKey'] = 'unindexed-local'
        raw = base64.b64decode(image_b64)
        record['bytes'] = len(raw)
        record['sha256'] = hashlib.sha256(raw).hexdigest()
        return record

    record['status'] = 'missing'
    if not has_server_id and not extract_media_md5s(metadata_content) and not resource_md5s:
        # D-014: with no reliable identity there is nothing safe to match on,
        # and guessing would attach someone else's media.
        record['reason'] = 'no-reliable-identity'
    elif kind == 'voice':
        record['status'] = 'unsupported'
        record['reason'] = 'voice-not-in-media-index'
    elif MEDIA_STATE['remote_reason']:
        record['reason'] = MEDIA_STATE['remote_reason']
    else:
        record['reason'] = 'not-in-local-cache'
    return record


def decode_message_content(value):
    """Decode NT message content, which may be Zstandard compressed."""
    if not value:
        return ''
    try:
        import zstandard
        value = zstandard.ZstdDecompressor().decompress(value)
    except Exception:
        pass
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='ignore')
    return str(value)


def decode_xml(text):
    """Decode XML entities."""
    return (text
            .replace('&amp;', '&')
            .replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&quot;', '"')
            .replace('&apos;', "'"))


def build_html_page(talker, messages_part, part_num, total_parts, display_name, file_prefix=''):
    """Build a single HTML page for a part.

    `file_prefix` must be the prefix the parts were actually written under.
    Deriving it from the talker instead produced links to
    `wxid_..._part2.html` next to files named after the remark, so every
    navigation link 404'd.
    """
    talker_safe = file_prefix or talker.replace('@', '_').replace('/', '_')
    rows = []
    for m in messages_part:
        dt = datetime.datetime.fromtimestamp(m['create_time'])
        time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
        sender = m['sender']
        is_self = m['is_self']
        content_html = m['display']

        align = 'right' if is_self else 'left'
        bg = '#95ec69' if is_self else '#ffffff'
        sender_display = escape_html(sender)

        rows.append(f'''<div class="msg-row" style="text-align:{align};">
  <div class="msg-bubble" style="background:{bg};">
    <div class="msg-sender">{sender_display} · {time_str}</div>
    <div class="msg-content">{content_html}</div>
  </div>
</div>''')

    name = display_name or talker
    from_time = datetime.datetime.fromtimestamp(messages_part[0]['create_time']).strftime('%Y-%m-%d %H:%M')
    to_time = datetime.datetime.fromtimestamp(messages_part[-1]['create_time']).strftime('%Y-%m-%d %H:%M')

    # Emit artwork only for the faces this page uses; shipping the whole
    # set in every part would add ~1MB to each.
    face_rules = wechat_emoji.face_css(''.join(rows)) if _WECHAT_EMOJI else ''

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>聊天记录 - {escape_html(name)} (第{part_num}/{total_parts}部分)</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #ededed;
  padding: 20px 0;
}}
.container {{
  max-width: 720px;
  margin: 0 auto;
  padding: 0 12px;
}}
.header {{
  background: #fff;
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 16px;
  text-align: center;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}}
.header h2 {{ font-size: 18px; color: #333; margin-bottom: 4px; }}
.header p {{ font-size: 13px; color: #999; }}
.part-nav {{
  display: flex;
  justify-content: center;
  gap: 8px;
  margin: 12px 0;
  flex-wrap: wrap;
}}
.part-nav a {{
  display: inline-block;
  padding: 4px 14px;
  background: #fff;
  border-radius: 6px;
  text-decoration: none;
  color: #576b95;
  font-size: 13px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.06);
}}
.part-nav a.active {{
  background: #07c160;
  color: #fff;
}}
.msg-row {{ margin: 8px 0; }}
.msg-bubble {{
  display: inline-block;
  max-width: 82%;
  padding: 8px 12px;
  border-radius: 8px;
  text-align: left;
  box-shadow: 0 1px 2px rgba(0,0,0,0.06);
  word-break: break-all;
}}
.msg-sender {{ font-size: 11px; color: #999; margin-bottom: 3px; }}
.msg-content {{ font-size: 15px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }}
.msg-content img {{ max-width: 240px; max-height: 240px; border-radius: 4px; margin-top: 6px; display: block; }}
.wxface {{
  display: inline-block; width: 22px; height: 22px; vertical-align: -5px;
  background-size: 22px 22px; background-repeat: no-repeat; margin: 0 1px;
}}
{face_rules}
.msg-media {{ color: #888; font-size: 14px; }}
.msg-voice-text {{ margin-top: 4px; padding: 6px 9px; background: rgba(0,0,0,0.045); border-left: 3px solid #bbb; border-radius: 3px; font-size: 14px; line-height: 1.5; color: #444; }}
.voice-tag {{ display: inline-block; margin-right: 6px; padding: 1px 5px; border-radius: 3px; background: #fdf0e0; color: #b5762a; font-size: 11px; vertical-align: 1px; white-space: nowrap; }}
.msg-sys {{ color: #bbb; font-size: 13px; }}
.msg-file {{ color: #07c160; font-weight: 500; }}
.msg-app {{ margin: 0; }}
.msg-app-title {{ font-size: 14px; font-weight: 600; color: #333; }}
.msg-app-desc {{ font-size: 12px; color: #999; margin-top: 2px; }}
.msg-link {{
  display: block;
  margin-top: 6px;
  padding: 6px 10px;
  background: #f5f5f5;
  border-left: 3px solid #07c160;
  color: #576b95;
  text-decoration: none;
  border-radius: 0 4px 4px 0;
  font-size: 13px;
}}
.msg-app-thumb {{
  max-width: 240px;
  max-height: 180px;
  border-radius: 6px;
  margin-bottom: 6px;
  display: block;
}}
.footer {{
  text-align: center;
  padding: 20px;
  color: #bbb;
  font-size: 12px;
}}
.footer .hint {{
  color: #ccc;
  font-size: 11px;
  margin-top: 4px;
}}
.search-box {{
  margin: 10px 0;
}}
.search-box input {{
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  font-size: 14px;
  outline: none;
}}
.search-box input:focus {{
  border-color: #07c160;
}}
.search-info {{
  font-size: 12px;
  color: #999;
  margin-top: 4px;
  display: none;
}}
.msg-row.hidden {{
  display: none;
}}
</style>
</head>
<body>
<div class="container">
<div class="header">
  <h2>聊天记录 - {escape_html(name)}</h2>
  <p>第 {part_num}/{total_parts} 部分 · {len(messages_part)} 条消息 · {from_time} ~ {to_time}</p>
  <div class="part-nav">
{chr(10).join(f'    <a href="{talker_safe}_part{i+1}.html" class="{"active" if i+1 == part_num else ""}">第{i+1}部分</a>' for i in range(total_parts))}
  </div>
  <div class="search-box">
    <input type="text" placeholder="搜索聊天记录..." oninput="searchMessages(this.value)">
    <div class="search-info" id="search-info"></div>
  </div>
</div>
{chr(10).join(rows)}
</div>
<div class="footer">
  <p>Exported by WeFlow CLI · {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
  <p class="hint">💡 图片与表情来自本机微信数据（会话缓存 + 账号媒体索引）。本机从未下载过的图片、以及未缓存封面的视频，无法显示。</p>
  <p class="hint">⚠️ 语音下方文字为<b>本机机器转写</b>，未经校对。粤语/方言的识别准确率有限，可能出现通顺但并非原话的内容——<b>不可作为原话引用</b>；需要确证时请以录音为准。</p>
</div>
<script>
function searchMessages(query) {{
  const rows = document.querySelectorAll('.msg-row');
  const info = document.getElementById('search-info');
  let found = 0;
  const q = query.toLowerCase().trim();
  rows.forEach(row => {{
    if (!q) {{
      row.classList.remove('hidden');
      found++;
    }} else {{
      const text = row.textContent.toLowerCase();
      if (text.includes(q)) {{
        row.classList.remove('hidden');
        found++;
      }} else {{
        row.classList.add('hidden');
      }}
    }}
  }});
  if (q) {{
    info.style.display = 'block';
    info.textContent = `找到 ${{found}} 条匹配`;
  }} else {{
    info.style.display = 'none';
  }}
}}
</script>
</body>
</html>'''


def build_empty_html(talker, date, display_name):
    """Build a date-scoped empty page without falling back to older messages."""
    name = escape_html(display_name or talker)
    date_text = escape_html(date or '指定日期')
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>聊天记录 - {name} - {date_text}</title>
<style>
body {{ margin:0; padding:48px 20px; background:#ededed; font-family:-apple-system,BlinkMacSystemFont,"Microsoft YaHei",sans-serif; color:#666; }}
.empty {{ max-width:720px; margin:0 auto; padding:40px 20px; text-align:center; background:#fff; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
h2 {{ margin:0 0 12px; color:#333; font-size:18px; }}
p {{ margin:8px 0; font-size:14px; }}
</style>
</head>
<body><main class="empty"><h2>{name}</h2><p>{date_text}没有消息记录</p><p>未加载其他日期的历史消息。</p></main></body>
</html>'''


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Export WeChat NT chat as HTML')
    parser.add_argument('--db', default=os.environ.get('WEFLOW_DB_PATH'), required=not os.environ.get('WEFLOW_DB_PATH'), help='Path to NT database (message_0.db)')
    parser.add_argument('--key', default=os.environ.get('WEFLOW_NT_KEY'), required=not os.environ.get('WEFLOW_NT_KEY'), help='Key hex (64 chars)')
    parser.add_argument('--salt', default=os.environ.get('WEFLOW_NT_SALT'), required=not os.environ.get('WEFLOW_NT_SALT'), help='Salt hex (32 chars)')
    parser.add_argument('--talker', default=os.environ.get('WEFLOW_TALKER'), required=not os.environ.get('WEFLOW_TALKER'), help='Talker username')
    parser.add_argument('--name', default=os.environ.get('WEFLOW_EXPORT_NAME', ''), help='Display name')
    parser.add_argument('--out', default=os.environ.get('WEFLOW_EXPORT_OUTPUT', './output'), help='Output directory')
    parser.add_argument('--parts', type=int, default=5, help='Number of parts to split into')
    parser.add_argument('--wx-dir', default='', help='Traditional WeChat data dir (FileStorage fallback)')
    parser.add_argument('--cache-dir', default=os.environ.get('WEFLOW_EXPORT_CACHE_DIR', ''), help='NT cache directory for image thumbnails')
    parser.add_argument('--account-dir', default=os.environ.get('WEFLOW_EXPORT_ACCOUNT_DIR', ''), help='Account data directory for media resources')
    parser.add_argument('--single', action='store_true', help='Generate a single HTML file (no splitting)')
    parser.add_argument('--per-page', type=int, default=0,
                        help='Messages per file; overrides --parts. Keeps a long history snappy to open')
    parser.add_argument('--date', default=os.environ.get('WEFLOW_EXPORT_DATE', ''), help='Only export messages from local date YYYY-MM-DD')
    parser.add_argument('--limit', type=int, default=int(os.environ.get('WEFLOW_EXPORT_LIMIT') or 0),
                        help='Only export the most recent N messages (0 = all)')
    parser.add_argument('--emoticon-seed', default=os.environ.get('WEFLOW_EMOTICON_SEED', ''),
                        help='Account seed; decrypts custom stickers from the local cache')
    parser.add_argument('--passphrase', default=os.environ.get('WEFLOW_NT_PASSPHRASE', ''), help='Shared NT passphrase for deriving shard keys')
    parser.add_argument('--own-wxid', default=os.environ.get('WEFLOW_OWN_WXID', ''), help='Configured account identifier for self-message detection')
    parser.add_argument('--contact-db', default=os.environ.get('WEFLOW_CONTACT_DB_PATH', ''), help='Contact database, for resolving group senders to names')
    parser.add_argument('--contact-key', default=os.environ.get('WEFLOW_CONTACT_KEY', ''), help='Contact database key hex')
    parser.add_argument('--contact-salt', default=os.environ.get('WEFLOW_CONTACT_SALT', ''), help='Contact database salt hex')
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # Scan NT cache for image thumbnails
    image_map = {}
    _phase("start")
    if args.cache_dir:
        print(f"Scanning NT cache: {args.cache_dir}")
        image_map = scan_nt_cache(args.cache_dir, args.talker, args.account_dir, args.own_wxid)
    else:
        image_map = {}
    # The account media index holds everything the conversation cache does not.
    # A conversation cache only keeps recent months, so without this a group
    # photo from last year resolved 0/1444 - every image and video came out as
    # a bare `[图片]`. Both indexes are merged rather than chosen between.
    if args.account_dir:
        account_media = scan_account_media(args.account_dir, args.own_wxid, args.talker)
        print(f"  Account media index: {len(account_media)} entries")
        for key, value in account_media.items():
            if key in image_map:
                continue
            # Most entries are already thumbnail-sized (median 6KB); only the
            # few large originals are worth re-encoding.
            if len(value[0]) > EMBED_SHRINK_THRESHOLD:
                value = shrink_embedded(value, max_side=720, force=True)
            image_map[key] = value
    print(f"  Found {len(image_map)} media entries for embedding")
    _phase("media scan")

    # Custom stickers: derive the local cache key from the account seed.
    if args.account_dir and _WECHAT_EMOTICON:
        STICKER_STATE['dirs'] = wechat_emoticon.sticker_cache_dirs(args.account_dir)
        STICKER_STATE['cache_dir'] = os.path.join(args.out, '.sticker-cache')
        seed, discovered = resolve_emoticon_seed(args.emoticon_seed, args.account_dir, args.out)
        if seed:
            wxid = wechat_emoticon.account_wxid(os.path.basename(os.path.normpath(args.account_dir)))
            STICKER_STATE['key'] = wechat_emoticon.derive_key(seed, wxid)
            source = 'discovered' if discovered else 'configured'
            print(f"Stickers: {len(STICKER_STATE['dirs'])} cache dir(s), seed {source}")
            if discovered:
                print(f"  建议固化以避免每次扫描: weflow-cli config set emoticonSeed {seed}")
        else:
            print("Stickers: 未找到 seed（需微信正在运行），自定义表情包将显示为 [表情]")

    # Remote fetches: cache on disk and bound the per-run budget, otherwise
    # a link-heavy conversation spends minutes on network round-trips.
    COVER_STATE['dir'] = os.path.join(args.out, '.cover-cache')
    COVER_STATE['imgshrink'] = os.path.join(args.out, '.imgshrink-cache')
    COVER_STATE['budget'] = COVER_FETCH_LIMIT
    COVER_STATE['thumb_budget'] = THUMB_FETCH_LIMIT

    # Contact names make a group transcript readable; the ids alone do not.
    CONTACT_NAMES.update(load_contact_names(args.contact_db, args.contact_key, args.contact_salt))
    if CONTACT_NAMES:
        print(f"Contacts: {len(CONTACT_NAMES)} name(s)")

    # Voice: load the payloads so a cached transcript can be shown. Anything
    # not yet transcribed simply renders as `[语音 N″]`.
    if _WECHAT_VOICE:
        VOICE_STATE['cache'] = wechat_voice.TranscriptCache(os.path.join(args.out, '.voice-cache'))
        try:
            VOICE_STATE['map'] = wechat_voice.load_voice_map(
                args.db, args.key, args.salt, args.passphrase, args.talker)
        except Exception:
            VOICE_STATE['map'] = {}
        if VOICE_STATE['map']:
            done = sum(
                1 for blob in VOICE_STATE['map'].values()
                if VOICE_STATE['cache'].get(wechat_voice.voice_key(blob)) is not None
            )
            print(f"Voice: {len(VOICE_STATE['map'])} clip(s), {done} transcribed")

    # The caller resolves the display name from the session list, which only
    # covers recent conversations. A less recent chat therefore exported as
    # `wxid_..._part1.html` even though its remark was known all along. Fall
    # back to the contact database so files are named after the person.
    if not args.name and args.talker:
        args.name = CONTACT_NAMES.get(args.talker, '')
        if args.name:
            print(f"Name: {args.name} (from contacts)")

    # Connect
    print(f"Connecting to {args.db}...")
    _phase("cover init")
    conn, c = connect(args.db, args.key, args.salt)

    # Fetch messages
    print(f"Fetching messages for {args.talker}...")
    _phase("before fetch")
    messages = fetch_messages_from_shards(args.db, args.key, args.salt, args.talker, args.date, args.passphrase)

    # `--limit` means the most recent N, the same as `messages` and
    # `export json`. Sliced here, before the media and sender maps are built,
    # so neither does work for messages that are about to be dropped.
    if args.limit and args.limit > 0 and len(messages) > args.limit:
        print(f"Limiting to the most recent {args.limit} of {len(messages)} messages")
        messages = messages[-args.limit:]

    if not messages:
        if args.date:
            file_prefix = sanitize_filename(args.name or args.talker)
            filepath = os.path.join(args.out, f'{file_prefix}.html')
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(build_empty_html(args.talker, args.date, args.name or args.talker))
            print(f"No messages found for {args.date}; wrote empty date-scoped page")
            print(json.dumps({"success": True, "total": 0, "parts": 1, "files": [filepath]}))
            conn.close()
            return
        print("No messages found!")
        conn.close()
        sys.exit(1)

    resource_map = load_resource_media_map(
        args.account_dir, args.key, args.salt, messages, image_map, args.passphrase
    )
    if resource_map:
        print(f"  Found resource mappings for {len(resource_map)} message keys")
    _phase("resource map")

    # Build sender name map from every message shard because each shard can
    # contain a different Name2Id mapping.
    print(f"Building sender name map...")
    sender_map = build_sender_map_from_shards(
        args.db, args.key, args.salt, args.talker, args.passphrase
    )
    print(f"  Found {len(sender_map)} sender(s): {list(sender_map.values())}")

    # Format messages
    display_name = args.name or args.talker
    _phase("before formatting")
    wx_dir = args.wx_dir or ''

    # Remote media is the dominant cost and is discovered one message at a
    # time, so doing it inline serialises hundreds of round-trips. Sweep the
    # conversation once to record what it needs (local-only, ~0.1s), fetch it
    # all concurrently, then format against a warm cache.
    sink = []
    PREFETCH['sink'] = sink
    try:
        for row in messages:
            format_message(row, args.talker, wx_dir, image_map, sender_map, display_name, resource_map, args.own_wxid)
    finally:
        PREFETCH['sink'] = None
    if sink:
        print(f"Prefetching {len(sink)} remote media reference(s)...", flush=True)
        _phase("prefetch scan")
        ok, dropped = prefetch_remote(sink)
        print(f"  Resolved {ok} remote item(s)" + (f", skipped {dropped} (cap)" if dropped else ""), flush=True)
        _phase("prefetch fetch")
        # Budgets were not charged during the prefetch, so anything it missed
        # is still bounded by the full per-run allowance here.

    print(f"Formatting {len(messages)} messages...")
    formatted = []
    media_items = []
    img_hit_count = 0
    article_img_count = 0
    progress_step = max(1, len(messages) // 20)
    for i, row in enumerate(messages):
        if i % progress_step == 0:
            print(f"  Formatting {i}/{len(messages)}...", flush=True)
        result = format_message(row, args.talker, wx_dir, image_map, sender_map, display_name, resource_map, args.own_wxid)
        if result.get('image_b64'):
            img_hit_count += 1
            if result.get('local_type') == 49:
                article_img_count += 1
        record = result.get('media')
        if record:
            # Index kept so the report can point back at the page part.
            record = dict(record)
            record['index'] = i
            media_items.append(record)
        formatted.append(result)
    print(f"  Messages with embedded images: {img_hit_count} (including {article_img_count} article thumbnails)")

    # Split into parts (or single file)
    total = len(formatted)
    if args.per_page and args.per_page > 0:
        parts = max(1, (total + args.per_page - 1) // args.per_page)
    elif args.single:
        parts = 1
    else:
        parts = min(args.parts, total)
    per_part = (total + parts - 1) // parts

    _phase(f"formatting {len(messages)} msgs")
    print(f"Splitting into {parts} part(s) (~{per_part} messages each)...")

    # Use display name for filename if provided, otherwise fallback to wxid
    file_prefix = sanitize_filename(display_name) if display_name else args.talker.replace('@', '_').replace('/', '_')

    # Media coverage report. Written beside the HTML parts, under the same
    # prefix, so it is always found next to what it describes.
    media_report = None
    media_report_path = None
    if media_items:
        media_report = build_media_report(media_items, display_name, args.talker, per_part)
        media_report_path = os.path.join(args.out, f'{file_prefix}_media.json')
        with open(media_report_path, 'w', encoding='utf-8') as handle:
            json.dump(media_report, handle, ensure_ascii=False, indent=2)
        summary = media_report['counts']
        missing = summary.get('missing', 0) + summary.get('unsupported', 0)
        print(f"Media report: {len(media_items)} item(s), {missing} unavailable "
              f"-> {os.path.basename(media_report_path)}")

    html_files = []
    for i in range(parts):
        start = i * per_part
        end = min(start + per_part, total)
        chunk = formatted[start:end]

        if not chunk:
            break

        html = build_html_page(args.talker, chunk, i + 1, parts, display_name, file_prefix)
        if parts == 1:
            filename = f"{file_prefix}.html"
        else:
            filename = f"{file_prefix}_part{i+1}.html"
        filepath = os.path.join(args.out, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)

        size_kb = os.path.getsize(filepath) / 1024
        print(f"  Part {i+1}: {filename} ({len(chunk)} msgs, {size_kb:.1f} KB)")
        html_files.append(filepath)

    conn.close()

    print(f"\nDone! {len(html_files)} HTML files written to {args.out}")
    print(f"Total: {total} messages")

    # Print JSON summary for CLI integration.
    # The four original keys are untouched; `media` is purely additive and the
    # one existing consumer (src/services/exportService.ts) ignores it.
    print(json.dumps({
        "success": True,
        "total": total,
        "parts": len(html_files),
        "files": html_files,
        "media": ({
            "schema": "weflow-media-report/v1",
            "records": len(media_items),
            "status": media_report['counts'],
            "report": os.path.basename(media_report_path) if media_report_path else None,
        } if media_report else None),
    }))


_PHASE_T0 = time.time()


def _phase(label):
    global _PHASE_T0
    now = time.time()
    if os.environ.get("WEFLOW_DEBUG"):
        print(f"  [t+{now - _PHASE_T0:6.1f}s] {label}", flush=True)
    _PHASE_T0 = now


def sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80]


if __name__ == '__main__':
    main()
