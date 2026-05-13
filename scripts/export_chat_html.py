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
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print("Install sqlcipher3: pip install sqlcipher3")
    sys.exit(1)

PAGE_SIZE = 4096
MSG_TYPES = {
    1: 'text', 3: 'image', 34: 'voice', 42: 'card',
    43: 'video', 47: 'emoji', 48: 'location', 49: 'link',
    50: 'voip', 10000: 'system', 10002: 'quote',
}
MAX_EMBED_SIZE = 256 * 1024  # Max 256KB per embedded image


def connect(db_path, key_hex, salt_hex):
    raw_key = f"x'{key_hex}{salt_hex}'"
    conn = sqlcipher.connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    return conn, c


def fetch_messages(conn, talker):
    """Fetch all messages for a talker, ordered by time ascending."""
    tbl = 'Msg_' + hashlib.md5(talker.encode()).hexdigest()
    c = conn.cursor()

    c.execute(f"SELECT COUNT(*) FROM \"{tbl}\"")
    total = c.fetchone()[0]
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


def scan_nt_cache(nt_cache_dir, talker):
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
                                image_map[local_id] = (base64.b64encode(data).decode(), mime)
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
                                    image_map[local_id] = (base64.b64encode(data).decode(), 'image/jpeg')
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


def escape_html(text):
    if not text:
        return ''
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


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
    local_type = row[2] or 0
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

    # Get content
    content = ''
    if isinstance(message_content, str) and message_content:
        content = message_content
    elif isinstance(message_content, bytes):
        try:
            content = message_content.decode('utf-8', errors='ignore')
        except:
            pass

    if not content and isinstance(source, str):
        _, content = parse_source(source)

    # Determine display content
    display = ''
    image_b64 = None

    if local_type == 1:
        # Text
        display = escape_html(content)
    elif local_type == 3:
        # Image - try cache map first, then traditional FileStorage
        display = '<span class="msg-media">[图片]</span>'
        img_data = None
        mime = 'image/jpeg'

        # Priority 1: NT cache thumbnails
        if image_map and local_id in image_map:
            img_data, mime = image_map[local_id]
        # Priority 2: Traditional FileStorage
        else:
            result = find_thumbnail(create_time, local_id, wx_dir)
            if result:
                img_data, mime = result

        if img_data:
            image_b64 = img_data
            display += f'<br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
    elif image_map and local_id in image_map:
        # Some image messages use encoded types (e.g. 21474836529 = images in appmsg)
        # Check image_map for any message type
        img_data, mime = image_map[local_id]
        if img_data:
            image_b64 = img_data
            display = f'<span class="msg-media">[图片]</span><br><img src="data:{mime};base64,{img_data}" loading="lazy" />'
    elif local_type == 34:
        display = '<span class="msg-media">[语音]</span>'
    elif local_type == 43:
        display = '<span class="msg-media">[视频]</span>'
    elif local_type == 47:
        display = escape_html(content) if content else '<span class="msg-media">[表情]</span>'
    elif local_type == 49:
        # App message (link/file)
        if content:
            # Try to parse XML for title/desc
            title_m = re.search(r'<title>([^<]*)</title>', content)
            desc_m = re.search(r'<des>([^<]*)</des>', content)
            url_m = re.search(r'<url>([^<]*)</url>', content)
            type_m = re.search(r'<type>(\d+)</type>', content)
            fname_m = re.search(r'<title>([^<]+\.\w+)</title>', content)

            if type_m and type_m.group(1) == '6' and fname_m:
                display = f'<span class="msg-file">[文件] {escape_html(fname_m.group(1))}</span>'
            elif title_m:
                parts = []
                if url_m:
                    parts.append(f'<a class="msg-link" href="{escape_html(url_m.group(1))}" target="_blank">{escape_html(decode_xml(title_m.group(1)))}</a>')
                else:
                    parts.append(f'<span class="msg-app-title">{escape_html(decode_xml(title_m.group(1)))}</span>')
                if desc_m:
                    parts.append(f'<div class="msg-app-desc">{escape_html(decode_xml(desc_m.group(1)))}</div>')
                display = '<div class="msg-app">' + ''.join(parts) + '</div>'
            else:
                display = '<span class="msg-media">[链接/文件]</span>'
        else:
            display = '<span class="msg-media">[链接/文件]</span>'
    elif local_type == 50:
        display = '<span class="msg-media">[语音通话]</span>'
    elif local_type == 10000:
        display = f'<span class="msg-sys">{escape_html(content)}</span>'
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
    }


def decode_xml(text):
    """Decode XML entities."""
    return (text
            .replace('&amp;', '&')
            .replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&quot;', '"')
            .replace('&apos;', "'"))


def build_html_page(talker, messages_part, part_num, total_parts, display_name):
    """Build a single HTML page for a part."""
    talker_safe = talker.replace('@', '_').replace('/', '_')
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
  <p class="hint">💡 图片来自微信本地缓存，仅覆盖最近2个月。滚动查看更多聊天可生成更多缩略图。</p>
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
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # Scan NT cache for image thumbnails
    image_map = {}
    if args.cache_dir:
        print(f"Scanning NT cache: {args.cache_dir}")
        image_map = scan_nt_cache(args.cache_dir, args.talker)
        print(f"  Found {len(image_map)} cached images for embedding")

    # Connect
    print(f"Connecting to {args.db}...")
    conn, c = connect(args.db, args.key, args.salt)

    # Fetch messages
    print(f"Fetching messages for {args.talker}...")
    messages = fetch_messages(conn, args.talker)

    if not messages:
        print("No messages found!")
        conn.close()
        sys.exit(1)

    # Build sender name map
    print(f"Building sender name map...")
    sender_map = build_sender_map(conn, args.talker)
    print(f"  Found {len(sender_map)} sender(s): {list(sender_map.values())}")

    # Format messages
    display_name = args.name or args.talker
    print(f"Formatting {len(messages)} messages...")
    wx_dir = args.wx_dir or ''
    formatted = []
    img_hit_count = 0
    for i, row in enumerate(messages):
        if i % 2000 == 0:
            print(f"  Formatting {i}/{len(messages)}...")
        result = format_message(row, args.talker, wx_dir, image_map, sender_map, display_name)
        if result.get('image_b64'):
            img_hit_count += 1
        formatted.append(result)
    print(f"  Messages with embedded images: {img_hit_count}")

    # Split into parts (or single file)
    total = len(formatted)
    if args.single:
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

        html = build_html_page(args.talker, chunk, i + 1, parts, display_name)
        if args.single:
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
