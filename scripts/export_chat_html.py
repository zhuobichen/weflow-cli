#!/usr/bin/env python3
"""
Export WeChat NT chat history as self-contained HTML files.
Splits large conversations into multiple parts.
Embeds cached image thumbnails from NT cache directory.
"""
import sys
import os
import hashlib
import datetime
import json
import re
import base64
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print("Install sqlcipher3: pip install sqlcipher3")
    sys.exit(1)

try:
    import zstandard as zstd
    _ZSTD_DCTX = zstd.ZstdDecompressor()
except ImportError:
    _ZSTD_DCTX = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wechat_emoji import render_faces, face_css  # noqa: E402
from nt_keys import discover_message_shards  # noqa: E402
import wechat_emoticon  # noqa: E402

PAGE_SIZE = 4096
MSG_TYPES = {
    1: 'text', 3: 'image', 34: 'voice', 42: 'card',
    43: 'video', 47: 'emoji', 48: 'location', 49: 'link',
    50: 'voip', 10000: 'system', 10002: 'quote',
}
MAX_EMBED_SIZE = 256 * 1024  # Max 256KB per embedded image

# WeChat 4.x stores large payloads (appmsg XML, long text) zstd-compressed.
ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'

# Older appmsg shares carry the cover only as a <cdnthumburl> descriptor that
# needs WeChat session credentials to resolve, so fall back to reading the
# article page's own og:image. Bounded so a chat full of links cannot stall.
WECHAT_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
    'MicroMessenger/8.0.38(0x18002633) NetType/WIFI Language/zh_CN'
)
ARTICLE_HOSTS = ('mp.weixin.qq.com',)
ARTICLE_HEAD_BYTES = 48 * 1024
COVER_FETCH_LIMIT = 80
COVER_FETCH_DELAY = 0.3

# Set up by main(); format_message reads it. Keys: dir, budget, stats, last.
COVER_STATE = {'dir': '', 'budget': 0, 'stats': None, 'last': 0.0}

# Set up by main(); custom stickers (local cache + derived key).
STICKER_STATE = {'key': b'', 'dirs': [], 'cache_dir': ''}


def to_bytes(value):
    """Best-effort recovery of the raw bytes behind a DB column value."""
    if value is None:
        return b''
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        # BLOBs come back as latin-1-ish text; real text won't encode as latin-1.
        try:
            return value.encode('latin-1')
        except UnicodeEncodeError:
            return value.encode('utf-8', 'ignore')
    return b''


def decompress_if_zstd(raw):
    """Decompress a zstd payload, returning the input unchanged if it is not one."""
    if not raw or raw[:4] != ZSTD_MAGIC:
        return raw
    if _ZSTD_DCTX is None:
        return raw
    try:
        return _ZSTD_DCTX.decompress(raw)
    except Exception:
        return raw


def normalize_local_type(local_type):
    """WeChat 4.x encodes flags in the high 32 bits (e.g. 21474836529 -> 49)."""
    try:
        value = int(local_type)
    except (TypeError, ValueError):
        return 0
    if value > 0xFFFFFFFF:
        return value & 0xFFFFFFFF
    return value


def warn_if_newer_shard(db_path):
    """Warn when another message_N.db is newer than the one we read.

    WeChat 4.x rotates the message store across message_0..N.db. Only one is
    reachable with the configured key, so a stale pick silently exports a
    partial conversation.
    """
    db_dir = os.path.dirname(os.path.abspath(db_path))
    base = os.path.basename(db_path)
    if not re.match(r'^message_\d+\.db$', base):
        return []
    try:
        our_mtime = os.path.getmtime(db_path)
    except OSError:
        return []
    newer = []
    for fname in os.listdir(db_dir):
        if not re.match(r'^message_\d+\.db$', fname) or fname == base:
            continue
        fpath = os.path.join(db_dir, fname)
        try:
            if os.path.getmtime(fpath) > our_mtime:
                newer.append((os.path.getmtime(fpath), fname))
        except OSError:
            pass
    if newer:
        newer.sort(reverse=True)
        names = ', '.join(name for _, name in newer)
        print(f"  [WARN] {names} 比 {base} 更新，其中可能有本文件未包含的消息。")
        print(f"         {base} 最后修改于 {datetime.datetime.fromtimestamp(our_mtime):%Y-%m-%d %H:%M}。")
        print(f"         导出内容可能不完整；需重新初始化以获取当前分片的密钥。")
    return [name for _, name in newer]


def connect(db_path, key_hex, salt_hex):
    raw_key = f"x'{key_hex}{salt_hex}'"
    conn = sqlcipher.connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    return conn, c


def message_identity(row):
    """Stable identity for de-duplicating one message across shards."""
    return ((row[5] or 0), (row[1] or 0), hashlib.md5(bytes(to_bytes(row[8]))).hexdigest())


def fetch_messages(conn, talker, quiet=False):
    """Fetch all messages for a talker, ordered by time ascending.

    Returns [] when this shard simply has no table for the talker.
    """
    tbl = 'Msg_' + hashlib.md5(talker.encode()).hexdigest()
    c = conn.cursor()

    try:
        c.execute(f"SELECT COUNT(*) FROM \"{tbl}\"")
        total = c.fetchone()[0]
    except Exception:
        return []
    if not quiet:
        print(f"Total messages: {total}")

    c.execute(f'''
        SELECT local_id, server_id, local_type, sort_seq, real_sender_id,
               create_time, status, source, message_content, compress_content
        FROM "{tbl}"
        ORDER BY create_time ASC
    ''')

    messages = []
    batch = 0
    while True:
        rows = c.fetchmany(5000)
        if not rows:
            break
        for row in rows:
            messages.append(row)
        batch += 1
        if not quiet:
            print(f"  Fetched {len(messages)}/{total}...")

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


def xml_tag(content, tag):
    """Text of an XML tag, transparently unwrapping <![CDATA[...]]>.

    WeChat wraps many appmsg fields in CDATA, and `<![CDATA[` contains a '<',
    so a naive r'<tag>([^<]*)</tag>' silently fails on exactly those messages.
    """
    if not content:
        return None
    m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', content, re.S)
    if not m:
        return None
    inner = m.group(1).strip()
    cdata = re.fullmatch(r'<!\[CDATA\[(.*?)\]\]>', inner, re.S)
    if cdata:
        inner = cdata.group(1)
    else:
        # Mixed content: keep the outside-CDATA parts.
        if '<![CDATA[' in inner:
            inner = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'', inner, flags=re.S)
        inner = re.sub(r'<[^>]+>', '', inner).strip()
    return decode_xml(inner) if inner else None


def looks_like_xml(text):
    return bool(text) and str(text).lstrip().startswith('<')


def xml_attr(xml_text, name):
    """Value of an XML attribute, or ''."""
    m = re.search(rf'{name}="([^"]*)"', xml_text or '')
    return m.group(1) if m else ''


def strip_xml_text(text):
    """Plain text of an XML payload, tags and attributes removed."""
    if not text:
        return ''
    text = re.sub(r'<\?xml[^>]*\?>', ' ', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def parse_cache_key(fname):
    """Pull (local_id, create_time) out of a cache filename like 12_1788677969_thumb.jpg.

    Both parts are needed: message ids restart per shard, so the same local_id
    occurs in several message_N.db files, and two messages in one conversation
    can share a create_time. Keying on either alone pairs the wrong image.
    """
    parts = fname.split('_')
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return int(parts[0]), int(parts[1])
    return None


def scan_nt_cache(nt_cache_dir, talker):
    """Scan NT cache directory for image thumbnails and temp images.

    NT cache structure:
        cache/YYYY-MM/Message/<talker_md5>/
            Thumb/<id>_<create_time>_thumb.jpg
            ImageTemp/<id>_<create_time>_hd_temp_convert
            ImageTemp/<id>_<create_time>_mid_temp_convert

    Returns dict: {(local_id, create_time): (base64_data, mime_type)}

    Keyed on the pair embedded in the filename - <local_id>_<create_time>.
    Verified against a live install: every cached thumbnail's id and timestamp
    match a message's local_id and create_time exactly. Both parts are needed,
    because message ids restart per shard and create_time can repeat within a
    conversation.
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
                key = parse_cache_key(fname)
                if key is not None:
                    mime = detect_mime(fpath)
                    if mime:
                        try:
                            with open(fpath, 'rb') as fh:
                                data = fh.read()
                            if len(data) < MAX_EMBED_SIZE:
                                image_map[key] = (base64.b64encode(data).decode(), mime)
                        except:
                            pass

        # Priority 2: Thumb (fill in gaps)
        thumb_dir = os.path.join(msg_dir, 'Thumb')
        if os.path.isdir(thumb_dir):
            for fname in os.listdir(thumb_dir):
                if not fname.endswith('.jpg'):
                    continue
                key = parse_cache_key(fname)
                if key is not None:
                    if key not in image_map:  # Don't override ImageTemp
                        fpath = os.path.join(thumb_dir, fname)
                        size = os.path.getsize(fpath)
                        if size < MAX_EMBED_SIZE:
                            try:
                                with open(fpath, 'rb') as fh:
                                    data = fh.read()
                                if len(data) < MAX_EMBED_SIZE:
                                    image_map[key] = (base64.b64encode(data).decode(), 'image/jpeg')
                            except:
                                pass

    return image_map


def detect_mime(filepath):
    """Detect MIME type from file header."""
    try:
        with open(filepath, 'rb') as f:
            header = f.read(8)
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
                fstat = os.stat(fpath)
                time_diff = abs(fstat.st_mtime - create_time)
                if time_diff < 300 and os.path.getsize(fpath) < MAX_EMBED_SIZE:
                    with open(fpath, 'rb') as fh:
                        data = fh.read()
                    if len(data) < MAX_EMBED_SIZE:
                        return (base64.b64encode(data).decode(), 'image/jpeg')
        except:
            pass
    return None


def download_image_as_base64(url, timeout=10):
    """Download image from URL and return (base64_data, mime_type) or None."""
    if not url or not url.startswith(('http://', 'https://')):
        return None
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://mp.weixin.qq.com/',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_EMBED_SIZE + 1)
            if len(data) > MAX_EMBED_SIZE:
                return None
            mime = resp.headers.get('Content-Type', 'image/jpeg').split(';')[0].strip()
            if mime not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
                # Try detecting from data
                mime = detect_mime_from_bytes(data) or 'image/jpeg'
            return (base64.b64encode(data).decode(), mime)
    except Exception:
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
    # Try common image URL fields in appmsg XML
    for tag in ('thumburl', 'cdnthumburl', 'appthumburl'):
        m = re.search(rf'<{tag}>([^<]+)</{tag}>', content)
        if m:
            url = m.group(1).strip()
            if url.startswith(('http://', 'https://')):
                return url
    # Also check for <msg><appmsg> nested structure
    m = re.search(r'<thumburl>([^<]+)</thumburl>', content)
    if m:
        url = m.group(1).strip()
        if url.startswith(('http://', 'https://')):
            return url
    return None


def extract_appmsg_url(content):
    """Article URL from appmsg XML, unescaped, or None."""
    if not content:
        return None
    m = re.search(r'<url>([^<]+)</url>', content)
    if not m:
        return None
    url = decode_xml(m.group(1).strip())
    return url if url.startswith(('http://', 'https://')) else None


def fetch_article_cover(url):
    """Return (base64, mime) for the article's og:image, or None.

    Only mp.weixin.qq.com is ever contacted - never an arbitrary link pulled out
    of a chat log. Results are cached on disk so re-exports stay instant.
    """
    if not url:
        return None
    host = urllib.parse.urlparse(url).hostname or ''
    if host not in ARTICLE_HOSTS:
        return None

    cache_dir = COVER_STATE.get('dir') or ''
    cache_file = os.path.join(cache_dir, hashlib.md5(url.encode()).hexdigest() + '.bin') if cache_dir else ''
    if cache_file and os.path.isfile(cache_file):
        try:
            with open(cache_file, 'rb') as fh:
                blob = fh.read()
            sep = blob.find(b'\n')
            if sep > 0:
                COVER_STATE['stats']['article_cached'] = COVER_STATE['stats'].get('article_cached', 0) + 1
                return blob[:sep].decode(), blob[sep + 1:].decode()
        except OSError:
            pass

    stats = COVER_STATE.get('stats')
    if COVER_STATE.get('budget', 0) <= 0:
        if stats is not None:
            stats['article_skipped'] = stats.get('article_skipped', 0) + 1
        return None

    # Be polite between requests so a burst does not trip the WAF.
    wait = COVER_FETCH_DELAY - (time.time() - COVER_STATE.get('last', 0.0))
    if wait > 0:
        time.sleep(wait)

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': WECHAT_UA,
            'Referer': 'https://mp.weixin.qq.com/',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        COVER_STATE['budget'] -= 1
        COVER_STATE['last'] = time.time()
        # og:image sits in <head> within the first ~20KB; no need for the full page.
        with urllib.request.urlopen(req, timeout=15) as resp:
            head = resp.read(ARTICLE_HEAD_BYTES)
    except Exception:
        return None

    m = re.search(rb'<meta\s+property="og:image"\s+content="([^"]+)"', head)
    if not m:
        return None
    og_url = m.group(1).decode('utf-8', 'replace').replace('&amp;', '&')
    got = download_image_as_base64(og_url)
    if not got:
        return None

    b64, mime = got
    if cache_file:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_file, 'wb') as fh:
                fh.write(b64.encode() + b'\n' + mime.encode())
        except OSError:
            pass
    if stats is not None:
        stats['article_fetched'] = stats.get('article_fetched', 0) + 1
    return b64, mime


def resolve_appmsg_cover(content, local_id, create_time, image_map):
    """Best available cover for a shared article, or None.

    Order: local NT cache (offline, exact) -> <thumburl> -> the article's own
    og:image. Returns None rather than an unrelated picture.
    """
    if image_map:
        cached = image_map.get((local_id, create_time))
        if cached:
            return cached
    thumb_url = extract_appmsg_image(content)
    if thumb_url:
        got = download_image_as_base64(thumb_url)
        if got:
            return got
    return fetch_article_cover(extract_appmsg_url(content))


def escape_html(text):
    if not text:
        return ''
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


def render_text(text):
    """Escape for HTML, then turn WeChat face codes like [害羞] into artwork.

    Escaping runs first so message content can never inject markup; the face
    replacement only ever inserts spans this project generates.
    """
    return render_faces(escape_html(str(text) if text else ''))


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


def format_message(row, talker, wx_dir, image_map=None, sender_map=None, display_name=''):
    """Format a single message for HTML display.

    Args:
        row: DB row tuple
        talker: target wxid
        wx_dir: traditional FileStorage path (fallback)
        image_map: {local_id: (base64_data, mime_type)} from NT cache scan
        sender_map: {sender_id: user_name} from Name2Id table
        display_name: human-readable name for the target talker
    """
    local_id = row[0] or 0
    local_type = normalize_local_type(row[2] or 0)
    real_sender_id = row[4] or 0
    create_time = row[5] or 0
    source = row[7]
    message_content = row[8]

    # Resolve sender name
    sender_user_name = (sender_map or {}).get(real_sender_id, '')
    is_self = (sender_user_name != talker)  # not the target talker = sent by me

    # Build display sender name
    if is_self:
        sender_display = '我'
    elif display_name:
        sender_display = display_name
    elif sender_user_name:
        sender_display = sender_user_name
    else:
        sender_display = talker

    # Get content. WeChat 4.x keeps large payloads zstd-compressed, so unwrap
    # before treating the value as text.
    content = ''
    raw_content = decompress_if_zstd(to_bytes(message_content))
    if raw_content[:4] != ZSTD_MAGIC and b'\x00' not in raw_content[:64]:
        content = raw_content.decode('utf-8', errors='ignore')
    elif isinstance(message_content, str) and message_content:
        content = message_content
    elif raw_content:
        content = raw_content.decode('utf-8', errors='ignore')

    if not content:
        _, source_content = parse_source(source)
        content = source_content

    # Determine display content
    display = ''
    image_b64 = None

    if local_type == 1:
        # Text
        display = render_text(content)
    elif local_type == 3:
        # Image - try cache map first, then traditional FileStorage
        display = '<span class="msg-media">[图片]</span>'
        img_data = None
        mime = 'image/jpeg'

        # Priority 1: NT cache thumbnails
        if image_map and (local_id, create_time) in image_map:
            img_data, mime = image_map[(local_id, create_time)]
        # Priority 2: Traditional FileStorage
        else:
            result = find_thumbnail(create_time, local_id, wx_dir)
            if result:
                img_data, mime = result

        if img_data:
            image_b64 = img_data
            display += f'<br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
    elif local_type == 34:
        display = '<span class="msg-media">[语音]</span>'
    elif local_type == 43:
        display = '<span class="msg-media">[视频]</span>'
    elif local_type == 42:
        # Contact card: the payload is <msg nickname="..." username="...">.
        nick = xml_attr(content, 'nickname') or xml_attr(content, 'username')
        display = '<span class="msg-media">[名片]</span>'
        if nick:
            display += ' ' + escape_html(nick)
    elif local_type == 47:
        # Custom sticker: the payload is an <emoji> descriptor, not text.
        sticker = None
        if looks_like_xml(content) and STICKER_STATE['key']:
            md5_hex = xml_attr(content, 'md5')
            data, mime = wechat_emoticon.load_sticker(
                STICKER_STATE['dirs'], md5_hex, STICKER_STATE['key'],
                STICKER_STATE['cache_dir'])
            if data:
                sticker = (base64.b64encode(data).decode(), mime)
        if sticker:
            b64, mime = sticker
            image_b64 = b64
            display = f'<span class="msg-media">[表情]</span><br><img src="data:{mime};base64,{b64}" loading="lazy" />'
        elif content and not looks_like_xml(content):
            display = render_text(content)
        else:
            display = '<span class="msg-media">[表情]</span>'
    elif local_type == 49:
        # App message (link/file/article)
        if content:
            title = xml_tag(content, 'title')
            desc = xml_tag(content, 'des')
            url = xml_tag(content, 'url')
            appmsg_type = xml_tag(content, 'type') or ''

            if appmsg_type == '6' and title and re.search(r'\.\w+$', title):
                display = f'<span class="msg-file">[文件] {escape_html(title)}</span>'
            elif title:
                parts = []
                thumb = resolve_appmsg_cover(content, local_id, create_time, image_map)
                if thumb:
                    b64, mime = thumb
                    image_b64 = b64
                    parts.append(f'<img class="msg-app-thumb" src="data:{mime};base64,{b64}" loading="lazy" />')
                if url:
                    parts.append(f'<a class="msg-link" href="{escape_html(url)}" target="_blank">{escape_html(title)}</a>')
                else:
                    parts.append(f'<span class="msg-app-title">{escape_html(title)}</span>')
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
        # System notices arrive as <sysmsg>; show the human-readable <content>.
        text = content
        if looks_like_xml(content):
            m = re.search(r'<content>(.*?)</content>', content, re.S)
            text = m.group(1) if m else strip_xml_text(content)
        # Red-packet notices embed raw ids like 你领取了$wxid_xxx$的 红包.
        if text and '$' in text:
            def _who(mm):
                who = mm.group(1)
                if who == talker and display_name:
                    return display_name
                return who
            text = re.sub(r'\$([^$]+)\$', _who, text)
        display = f'<span class="msg-sys">{render_text(text)}</span>'
    elif local_type == 10002:
        display = render_text(content) if content else '<span class="msg-media">[引用]</span>'
    elif image_map and (local_id, create_time) in image_map:
        # Fallback for any remaining type that has a cached thumbnail.
        img_data, mime = image_map[(local_id, create_time)]
        if img_data:
            image_b64 = img_data
            display = f'<span class="msg-media">[图片]</span><br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
    else:
        type_name = MSG_TYPES.get(local_type, f'类型{local_type}')
        if looks_like_xml(content):
            # Never dump raw markup into the page: an unhandled payload type
            # would otherwise render as pages of XML gibberish.
            display = f'<span class="msg-media">[{type_name}]</span>'
        elif content:
            display = render_text(content)
        else:
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
    }


def decode_xml(text):
    """Decode XML entities."""
    return (text
            .replace('&amp;', '&')
            .replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&quot;', '"')
            .replace('&apos;', "'"))


def build_html_page(talker, messages_part, part_num, total_parts, display_name, newer_shards=None, file_prefix=None):
    """Build a single HTML page for a part."""
    talker_safe = file_prefix or sanitize_filename(display_name) if (file_prefix or display_name) else talker.replace('@', '_').replace('/', '_')
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

    # Only the faces this page uses; a full sprite would bloat every page.
    face_rules = face_css(''.join(rows))

    name = display_name or talker
    from_time = datetime.datetime.fromtimestamp(messages_part[0]['create_time']).strftime('%Y-%m-%d %H:%M')
    to_time = datetime.datetime.fromtimestamp(messages_part[-1]['create_time']).strftime('%Y-%m-%d %H:%M')

    # A newer message shard exists, so this export is a snapshot of an older
    # one. Say so on the page itself - it is invisible otherwise.
    if newer_shards:
        stale_banner = (
            '<div class="stale-banner">⚠️ 本文件数据可能不完整：'
            f'微信已把消息写入更新的分片（{escape_html(", ".join(newer_shards))}），'
            '而当前读取的消息库没有包含它们。'
            f'本站数据止于 {to_time}。</div>'
        )
    else:
        stale_banner = ''

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
.stale-banner {{
  background: #fff4e5; border: 1px solid #f0c36d; border-left: 4px solid #e8a33d;
  color: #7a4b00; border-radius: 8px; padding: 10px 14px; margin-bottom: 16px;
  font-size: 13px; line-height: 1.6;
}}
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
{stale_banner}<div class="header">
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
  <p class="hint">💡 图片优先取自微信本地缓存（约覆盖最近 2 个月），本地没有的公众号封面会从文章页获取。少数来源已失效的文章会没有封面。</p>
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


def main():
    # Emit UTF-8 regardless of the Windows console code page; the caller
    # relays these lines and would otherwise mangle every Chinese character.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

    import argparse
    parser = argparse.ArgumentParser(description='Export WeChat NT chat as HTML')
    parser.add_argument('--db', required=True, help='Path to NT database (message_0.db)')
    parser.add_argument('--key', required=True, help='Key hex (64 chars)')
    parser.add_argument('--salt', required=True, help='Salt hex (32 chars)')
    parser.add_argument('--talker', required=True, help='Talker username')
    parser.add_argument('--name', default='', help='Display name')
    parser.add_argument('--out', default='./output', help='Output directory')
    parser.add_argument('--parts', type=int, default=5, help='Number of parts to split into')
    parser.add_argument('--wx-dir', default='', help='Traditional WeChat data dir (FileStorage fallback)')
    parser.add_argument('--cache-dir', default='', help='NT cache directory for image thumbnails')
    parser.add_argument('--single', action='store_true', help='Generate a single HTML file (no splitting)')
    parser.add_argument('--per-page', type=int, default=0,
                        help='Messages per file; splits large exports so the browser stays responsive')
    parser.add_argument('--no-cover-fetch', action='store_true',
                        help='Do not fetch article covers over the network; use local cache only')
    parser.add_argument('--emoticon-seed', default='',
                        help='Account seed for decrypting custom stickers (表情包) from the local cache')
    parser.add_argument('--master-key', default='',
                        help='WeChat master key; derives every message shard key so the whole conversation is exported')
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # Article-cover fallback budget, shared across all messages in this run.
    COVER_STATE['dir'] = os.path.join(args.out, '.cover-cache')
    COVER_STATE['budget'] = 0 if args.no_cover_fetch else COVER_FETCH_LIMIT
    COVER_STATE['stats'] = {}
    COVER_STATE['last'] = 0.0

    # Custom stickers: derive the cache key and locate the local sticker store.
    if args.emoticon_seed:
        account_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(args.db))))
        wxid = wechat_emoticon.account_wxid(os.path.basename(account_root))
        STICKER_STATE['key'] = wechat_emoticon.derive_key(args.emoticon_seed, wxid)
        STICKER_STATE['dirs'] = wechat_emoticon.sticker_cache_dirs(account_root)
        STICKER_STATE['cache_dir'] = os.path.join(args.out, '.sticker-cache')
        print(f"Stickers: {len(STICKER_STATE['dirs'])} cache dir(s), key {STICKER_STATE['key'].hex()[:8]}...")

    # Scan NT cache for image thumbnails
    image_map = {}
    if args.cache_dir:
        print(f"Scanning NT cache: {args.cache_dir}")
        image_map = scan_nt_cache(args.cache_dir, args.talker)
        print(f"  Found {len(image_map)} cached images for embedding")

    # Connect. With a master key every shard is reachable, so the whole
    # conversation is read rather than whichever single file was configured.
    print(f"Connecting to {args.db}...")
    # With a master key every shard is read, so a "newer shard" is not a gap.
    newer_shards = [] if args.master_key else warn_if_newer_shard(args.db)

    if args.master_key:
        shards = discover_message_shards(args.db, args.master_key)
        if not shards:
            shards = [(args.db, args.key)]
    else:
        shards = [(args.db, args.key)]

    # Fetch messages from every shard and merge them.
    print(f"Fetching messages for {args.talker}...")
    merged = {}
    sender_map = {}
    for shard_path, shard_key in shards:
        try:
            shard_salt = open(shard_path, 'rb').read(16).hex()
            sconn, _ = connect(shard_path, shard_key, shard_salt)
        except Exception as e:
            print(f"  {os.path.basename(shard_path)}: 跳过 ({str(e)[:60]})")
            continue
        rows = fetch_messages(sconn, args.talker, quiet=True)
        for row in rows:
            merged.setdefault(message_identity(row), row)
        try:
            for sid, uname in build_sender_map(sconn, args.talker).items():
                sender_map.setdefault(sid, uname)
        except Exception:
            pass
        if rows:
            print(f"  {os.path.basename(shard_path)}: {len(rows)} 条")
        sconn.close()

    messages = sorted(merged.values(), key=lambda r: (r[5] or 0, r[0] or 0))

    if not messages:
        print("No messages found!")
        sys.exit(1)

    print(f"  Found {len(sender_map)} sender(s): {list(sender_map.values())}")
    if len(shards) > 1:
        print(f"  Merged {len(messages)} messages from {len(shards)} shards")

    # Format messages
    display_name = args.name or args.talker
    print(f"Formatting {len(messages)} messages...")
    wx_dir = args.wx_dir or ''
    formatted = []
    img_hit_count = 0
    article_img_count = 0
    for i, row in enumerate(messages):
        if i % 2000 == 0:
            print(f"  Formatting {i}/{len(messages)}...")
        result = format_message(row, args.talker, wx_dir, image_map, sender_map, display_name)
        if result.get('image_b64'):
            img_hit_count += 1
            if result.get('local_type') == 49:
                article_img_count += 1
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

    print(f"Splitting into {parts} part(s) (~{per_part} messages each)...")

    # Use display name for filename if provided, otherwise fallback to wxid
    file_prefix = sanitize_filename(display_name) if display_name else args.talker.replace('@', '_').replace('/', '_')

    html_files = []
    for i in range(parts):
        start = i * per_part
        end = min(start + per_part, total)
        chunk = formatted[start:end]

        if not chunk:
            break

        html = build_html_page(args.talker, chunk, i + 1, parts, display_name, newer_shards, file_prefix)
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

    print(f"\nDone! {len(html_files)} HTML files written to {args.out}")
    print(f"Total: {total} messages")

    stats = COVER_STATE.get('stats') or {}
    fetched = stats.get('article_fetched', 0)
    reused = stats.get('article_cached', 0)
    skipped = stats.get('article_skipped', 0)
    if fetched or reused or skipped:
        print(f"Article covers: {fetched} fetched, {reused} from local cache")
    if skipped:
        print(f"  {skipped} article(s) skipped - cover fetch budget reached"
              f" (raise with more runs; results are cached)")

    # Print JSON summary for CLI integration
    print(json.dumps({
        "success": True,
        "total": total,
        "parts": len(html_files),
        "files": html_files,
    }))


def sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80]


if __name__ == '__main__':
    main()
