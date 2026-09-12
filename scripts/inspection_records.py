#!/usr/bin/env python3
"""Turn a WeChat line-inspection dump into a table plus named photos.

An inspector posts photos to a chat and captions them, e.g.

    [图片][图片]
    清播线三津I支9-10号杆树障

so each caption is one finding: a location (`清播线三津I支9-10号杆`) and a
problem (`树障`). Photos precede their caption, and one caption can carry
several problems separated by `；`, in which case every problem gets its own
row and they share the location.

Photos are extracted at their **original** resolution. The HTML exporter caps
embedded media at 8MB because it inlines them into a page; inspection evidence
needs the real file, and phone originals run to 8000x6000 / 19MB, so the cap
must not apply here.
"""
import csv
import hashlib
import io
import os
import re
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_chat_html as E

# Original files run well past the HTML exporter's inline limit.
PHOTO_MAX_BYTES = 64 * 1024 * 1024

# 线路名以「线」结尾；支线以「支」结尾；杆号形如 9-10号杆 / 23号 / #12
RE_LINE = re.compile(r'([一-龥]{2,6}线)')
RE_BRANCH = re.compile(r'([一-龥A-Za-z0-9]{2,20}?支)')
RE_POLE = re.compile(r'(\d+(?:\s*[-—~]\s*\d+)?\s*号杆|\d+\s*号(?=[^\d]|$)|#\d+(?:\s*[-—~]\s*#?\d+)?)')
# Location splitting needs the explicit form. The loose `12号` above also
# matches prose like `万科悦江南11、12号配变在1栋1单元负一楼`, which turned a
# descriptive paragraph into rows claiming pole numbers that do not exist.
RE_POLE_STRICT = re.compile(r'(\d+(?:\s*[-—~]\s*\d+)?\s*号杆|#\d+(?:\s*[-—~]\s*#?\d+)?)')
# Captions that narrate completed work or a whole-site audit rather than one
# finding at one location; they do not fit the location+problem shape.
RE_NARRATIVE = re.compile(r'(接收资产巡视|巡视情况|完成对|处理前后)')
RE_STATUS = re.compile(r'[（(]([^）)]{0,30}?(?:已处理|未处理|处理中|待处理)[^）)]{0,30}?)[）)]')
# A clause that is just a location, e.g. the bare `三津I支9号杆` seen before a caption.
RE_NUMBERED = re.compile(r'^\s*\d+\s*[、.．]\s*')


def _split_clauses(text):
    """One caption's problem clauses, with the numbered-list form unwrapped."""
    parts = re.split(r'[；;]', text)
    out = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        # `1、…` / `2、…` list markers carry no meaning of their own.
        out.append(RE_NUMBERED.sub('', stripped))
    return out


def _location_prefix(clause):
    """(location, remainder) - the location tokens at the head of a clause."""
    end = 0
    for pattern in (RE_BRANCH, RE_POLE_STRICT):
        for match in pattern.finditer(clause):
            # Only tokens before the first problem word count as location.
            if match.start() > 24:
                continue
            end = max(end, match.end())
    return clause[:end].strip('，,、 '), clause[end:].strip('，,、 ')


def parse_records(messages, decode=E.decode_message_content):
    """[{time, local_type, text, photos:[local_id]}] for one conversation.

    Photos accumulate and are attached to the caption that follows them, which
    is how the inspector posts: the picture first, then what it shows.
    """
    records = []
    pending = []
    for row in messages:
        local_id = row[0] or 0
        local_type = (row[2] or 0) & 0xffffffff
        create_time = row[5] or 0
        if local_type == 3:
            pending.append(local_id)
            continue
        if local_type != 1:
            continue
        raw = row[8]
        text = decode(raw) if isinstance(raw, (bytes, bytearray)) else str(raw or '')
        # Group rows prefix the speaker; the record itself starts after it.
        text = re.sub(r'^wxid_[A-Za-z0-9_]+:\s*', '', text).strip()
        if not text or len(text) < 4:
            continue
        if not (RE_POLE.search(text) or RE_BRANCH.search(text) or RE_LINE.search(text)):
            # Chatter between records. The photos stay pending rather than
            # being dropped: a caption usually follows its pictures, so
            # clearing here lost 63 of 101 photos in the sample conversation.
            continue
        records.append({
            'time': create_time,
            'text': text,
            'photos': list(pending),
            'local_id': local_id,
        })
        pending = []
    return records


def _head_of(clause):
    """`清上线仁义支` - the line-and-branch a clause opens with, else ''.

    Strict about position: a branch mentioned mid-sentence (`9号杆的仁义支09
    刀闸`) is not the shared head, and treating it as one sends every later
    clause to the wrong line.
    """
    line = RE_LINE.match(clause)
    if line:
        branch = RE_BRANCH.match(clause, line.end())
        return clause[:branch.end()] if branch else clause[:line.end()]
    branch = RE_BRANCH.match(clause)
    return clause[:branch.end()] if branch else ''


def expand_findings(record):
    """One row per problem clause, carrying the caption's location context.

    A caption like `清上线仁义支9-11号杆有树障…；9-10号杆…不足5米；9-13号杆杆号
    牌模糊` names the line once and then lists problems against further poles.
    Later clauses inherit that line-and-branch prefix; without it they arrive
    with an empty location and cannot be attributed to anything.
    """
    rows = []
    head = ''
    # Judged once for the whole caption: a site-by-site audit opens with the
    # marker but only in its first clause, and the rest would otherwise be
    # forced into location+problem rows with invented pole numbers.
    record_narrative = bool(RE_NARRATIVE.search(record['text']))
    for clause in _split_clauses(record['text']):
        narrative = record_narrative
        own = '' if narrative else _head_of(clause)
        if own:
            head = own
        if narrative:
            location, problem = head, clause
        else:
            location, problem = _location_prefix(clause)
        if head and not own:
            location = head + location
            problem = problem or clause[len(location) - len(head):].strip('，,、 ')
        location = location or clause
        if not problem:
            problem = clause[len(location):].strip('，,、 ') or clause

        line = RE_LINE.match(head or location)
        branch = RE_BRANCH.match(head or location, line.end()) if line else RE_BRANCH.match(head or location)
        pole = None if narrative else (RE_POLE_STRICT.search(clause) or RE_POLE.search(clause))
        status = RE_STATUS.search(clause)
        rows.append({
            'time': record['time'],
            'line': line.group(1) if line else '',
            'branch': branch.group(1) if branch else '',
            'pole': pole.group(1).replace(' ', '') if pole else '',
            'location': location,
            'problem': problem,
            'status': status.group(1) if status else '',
            'kind': '叙述' if narrative else ('多问题' if len(_split_clauses(record['text'])) > 1 else '单条'),
            'photos': record['photos'],
            'source': record['text'],
        })
    return rows


def decode_photo(path, v2_key):
    """Full-resolution image bytes from a WeChat V2 container, or None.

    Same container as the exporter decodes, without the size ceiling that
    exists purely to bound inlined HTML.
    """
    from Crypto.Cipher import AES
    from Crypto.Util import Padding
    try:
        with open(path, 'rb') as handle:
            data = handle.read(PHOTO_MAX_BYTES + 1)
        if len(data) > PHOTO_MAX_BYTES or not data.startswith(E.V2_MAGIC):
            return None
        signature, aes_size, xor_size = struct.unpack('<6sLLx', data[:E.V2_CIPHERTEXT_START])
        if signature != E.V2_MAGIC:
            return None
        encrypted_size = aes_size + 16 - aes_size % 16
        encrypted = data[E.V2_CIPHERTEXT_START:E.V2_CIPHERTEXT_START + encrypted_size]
        decrypted = Padding.unpad(AES.new(v2_key[1], AES.MODE_ECB).decrypt(encrypted), 16)
        remainder = data[E.V2_CIPHERTEXT_START + encrypted_size:]
        if xor_size:
            if xor_size > len(remainder):
                return None
            raw = remainder[:-xor_size]
            tail = bytes(value ^ v2_key[0] for value in remainder[-xor_size:])
        else:
            raw, tail = remainder, b''
        output = decrypted + raw + tail
        return output if E.detect_mime_from_bytes(output[:16]) else None
    except Exception:
        return None


def photo_paths(account_dir, talker, resource_md5s):
    """Local candidate files for one message, largest variant first."""
    md5_dir = hashlib.md5(talker.encode()).hexdigest()
    root = os.path.join(account_dir, 'msg', 'attach', md5_dir)
    found = []
    if not os.path.isdir(root):
        return found
    wanted = {}
    for media_md5 in resource_md5s:
        for suffix in ('_h.dat', '.dat'):
            wanted[(media_md5 + suffix).lower()] = len(found)
    for current, _, files in os.walk(root):
        for name in files:
            if name.lower() in wanted:
                found.append((name.lower(), os.path.join(current, name)))
    # `_h.dat` (original) before `.dat` (mid), then by file size.
    found.sort(key=lambda item: (not item[0].endswith('_h.dat'), -os.path.getsize(item[1])))
    return [path for _, path in found]


def sanitize(name):
    """A filename-safe form of a location string."""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', '_', str(name or '')).strip('_')
    return cleaned[:60] or 'unknown'


def extract_photos(rows, account_dir, talker, resource_map, output_dir, own_wxid='', log=print):
    """Write each row's photos at full resolution, named after its location.

    Returns (written, missing) where `missing` lists rows whose photos could
    not be decoded - typically because WeChat never downloaded the original,
    which is worth reporting rather than silently substituting a thumbnail.
    """
    os.makedirs(output_dir, exist_ok=True)
    v2_key = E.resolve_v2_media_key(account_dir, own_wxid)
    written = []
    missing = []
    # One caption's photos belong to every row split out of it, so cache by
    # media md5 - otherwise a 3-photo, 4-clause caption writes 12 copies.
    per_media = {}
    for index, row in enumerate(rows, 1):
        produced = []
        for media_md5 in row.get('resource_md5s', []):
            if media_md5 in per_media:
                produced.extend(per_media[media_md5])
                continue
            names = []
            paths = photo_paths(account_dir, talker, [media_md5])
            for slot, path in enumerate(paths, 1):
                data = decode_photo(path, v2_key)
                if not data:
                    continue
                mime = E.detect_mime_from_bytes(data[:16])
                if not mime:
                    continue
                extension = {'image/jpeg': '.jpg', 'image/png': '.png',
                             'image/gif': '.gif', 'image/webp': '.webp'}.get(mime, '.jpg')
                # The media md5, not a per-photo counter: counters restart at 1
                # for every md5 within a row, so two photos of the same
                # location collided and overwrote each other (101 -> 38 files).
                name = f"{index:03d}_{sanitize(row['location'])}_{media_md5[:8]}{extension}"
                target = os.path.join(output_dir, name)
                if not os.path.exists(target):
                    with open(target, 'wb') as handle:
                        handle.write(data)
                names.append(name)
                written.append(target)
            if names:
                per_media[media_md5] = names
            produced.extend(names)
        row['photo_files'] = produced
        if not produced and row.get('resource_md5s'):
            missing.append((index, row['location']))
        if index % 20 == 0:
            log(f'  照片 {index}/{len(rows)}')
    return written, missing


def write_table(rows, output_dir, name='巡视记录'):
    """CSV plus an .xlsx when ExcelJS-equivalent tooling is available."""
    columns = [('time', '时间'), ('line', '线路'), ('branch', '支线'), ('pole', '杆号'),
               ('location', '地点'), ('problem', '问题'), ('status', '状态'), ('kind', '类型'),
               ('photo_count', '照片数'), ('photo_files', '照片文件'), ('source', '原始描述')]
    csv_path = os.path.join(output_dir, f'{name}.csv')
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow([label for _, label in columns])
        for row in rows:
            import datetime
            record = dict(row)
            record['time'] = datetime.datetime.fromtimestamp(row['time']).strftime('%Y-%m-%d %H:%M:%S')
            record['photo_count'] = len(row.get('photo_files', []))
            record['photo_files'] = '|'.join(row.get('photo_files', []))
            writer.writerow([record.get(key, '') for key, _ in columns])
    return csv_path


def _main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description='把微信里的线路巡视记录整理成表格和照片')
    parser.add_argument('--env', required=True, help='含 db/key/salt/pass/wxid 与账号目录的 JSON')
    parser.add_argument('--talker', required=True)
    parser.add_argument('--account-dir', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()

    config = json.load(open(args.env, encoding='utf-8'))
    messages = E.fetch_messages_from_shards(
        config['db'], config['key'], config['salt'], args.talker, '', config['pass'])
    resource_map = E.load_resource_media_map(
        args.account_dir, config['key'], config['salt'], messages, {}, config['pass'])

    # local_id -> resource md5, so a photo can be found from its message.
    by_local = {}
    for key, values in resource_map.items():
        if key.startswith('server:'):
            continue
        by_local.setdefault(key, []).extend(values)
    for row in messages:
        md5s = list(resource_map.get(f'server:{int(row[1] or 0)}', []))
        if md5s:
            by_local[f'local:{row[0]}'] = md5s

    records = parse_records(messages)
    rows = []
    for record in records:
        for finding in expand_findings(record):
            finding['resource_md5s'] = []
            for local_id in finding['photos']:
                finding['resource_md5s'].extend(by_local.get(f'local:{local_id}', []))
            rows.append(finding)

    print(f'解析出 {len(records)} 条记录 -> {len(rows)} 行')
    written, missing = extract_photos(
        rows, args.account_dir, args.talker, by_local,
        os.path.join(args.out, '照片'), own_wxid=config.get('wxid', ''))
    table = write_table(rows, args.out)
    print(json.dumps({'success': True, 'records': len(records), 'rows': len(rows),
                      'photos': len(written), 'missing': len(missing),
                      'table': table}, ensure_ascii=False))


if __name__ == '__main__':
    _main()
