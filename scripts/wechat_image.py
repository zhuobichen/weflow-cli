#!/usr/bin/env python3
"""Decrypt WeChat 4.x chat images from the local `.dat` store.

Why this exists: WeChat's thumbnail cache (`cache/YYYY-MM/Message/<会话>/Thumb`)
holds mainly *article covers* and only ~2 months of history, so ordinary chat
photos from older conversations render as an empty `[图片]`. The originals are
on disk the whole time, at

    msg/attach/<md5(会话)>/<YYYY-MM>/Img/<file_id>.dat        (原图)
    msg/attach/<md5(会话)>/<YYYY-MM>/Img/<file_id>_t.dat      (缩略图)

`file_id` is not an md5 of the image and does not appear in the message XML -
it comes from `message_resource.db`, see `load_file_ids`.

Container (V2, the only variant seen on 4.x):

    [6B 07 08 56 32 08 07][4B aes_size LE][4B xor_size LE][1B pad]
        + AES-128-ECB(ciphertext) | plaintext | XOR tail

Key derivation reuses the account seed:

    key = md5(f"{seed}{wxid}").hexdigest()[:16]        # 16 ASCII bytes

Note the asymmetry with stickers, which use the *first 16 bytes* of
md5(f"{seed}{wxid}EMOTICON") - a different string and a different slicing.

Reference: ZedeX/weixin-decrypte-script.
"""
import hashlib
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DAT_SIG = b'\x07\x08\x56\x32\x08\x07'

MAGICS = (
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'\x89PNG', 'image/png'),
    (b'GIF8', 'image/gif'),
    (b'RIFF', 'image/webp'),
    (b'wxgf', 'image/hevc'),
)


def derive_key(seed, wxid):
    """16 ASCII bytes from md5(f"{seed}{wxid}")."""
    return hashlib.md5(f'{seed}{wxid}'.encode()).hexdigest()[:16].encode()


def decrypt(raw, key, seed):
    """Decrypt a V2 `.dat`, or b'' if it is another variant."""
    if len(raw) < 16 or raw[:6] != DAT_SIG:
        return b''
    try:
        aes_size, xor_size = struct.unpack('<II', raw[6:14])
    except struct.error:
        return b''
    if aes_size + xor_size > len(raw) or aes_size % 16:
        return b''
    body = raw[15:15 + aes_size]
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    out = dec.update(body) + dec.finalize()
    end = len(raw) - xor_size if xor_size else len(raw)
    out += raw[15 + aes_size:end]
    if xor_size:
        xk = int(seed) & 0xFF
        out += bytes(b ^ xk for b in raw[end:])
    return out


def sniff(data):
    for magic, mime in MAGICS:
        if data.startswith(magic):
            return mime
    return ''


def load_file_ids(resource_db, master_key, talker):
    """{(message_local_id, create_time): file_id} for one conversation.

    `packed_info` is a small protobuf carrying the on-disk file id as a
    32-character hex string; that string is not derivable from the message, so
    the mapping has to come from this table.
    """
    import re
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
        from nt_keys import derive_db_key
    except ImportError:
        return {}

    if not resource_db or not os.path.isfile(resource_db) or not master_key:
        return {}
    try:
        salt = open(resource_db, 'rb').read(16).hex()
        key = derive_db_key(master_key, resource_db)
        conn = sqlcipher.connect(resource_db)
        cur = conn.cursor()
        cur.execute(f'PRAGMA key = "x\'{key}{salt}\'";')
        chat_id = None
        for rowid, user_name in cur.execute('SELECT rowid, user_name FROM ChatName2Id'):
            if user_name == talker:
                chat_id = rowid
                break
        if chat_id is None:
            conn.close()
            return {}
        out = {}
        cur.execute('''SELECT message_local_id, message_create_time, packed_info
                       FROM MessageResourceInfo WHERE chat_id = ?''', (chat_id,))
        for lid, ts, packed in cur.fetchall():
            if isinstance(packed, str):
                packed = packed.encode('latin-1', 'ignore')
            m = re.search(rb'[0-9a-f]{32}', packed or b'')
            if m:
                out[(lid, ts)] = m.group(0).decode()
        conn.close()
        return out
    except Exception:
        return {}


def shrink(data, mime, max_side=720, quality=82):
    """Downscale to at most `max_side` on the long edge, as JPEG.

    The exported page shows images at ~240px, but a 2-3x display still needs
    more than the 120-210px thumbnails WeChat caches - those upscale into the
    blocky mess users notice. Full images run ~370KB on average though, so
    embedding them untouched would balloon the export; ~720px is the point
    where they look clean at any realistic zoom without the weight.
    """
    try:
        import io as _io
        from PIL import Image
    except ImportError:
        return data, mime
    try:
        im = Image.open(_io.BytesIO(data))
        im.load()
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        elif mime == 'image/jpeg':
            return data, mime          # already small enough and already compact
        # wxgf decodes to PNG, and PNG at ~700px runs several times the size of
        # the same photo as JPEG - so re-encode unless it would grow the file.
        if im.mode in ('RGBA', 'LA', 'P'):
            # Stickers carry transparency; flatten onto white rather than
            # letting the conversion produce a black background.
            im = im.convert('RGBA')
            bg = Image.new('RGB', im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert('RGB')
        buf = _io.BytesIO()
        im.save(buf, 'JPEG', quality=quality, optimize=True)
        out = buf.getvalue()
        return (out, 'image/jpeg') if 0 < len(out) < len(data) else (data, mime)
    except Exception:
        return data, mime


def load_image(account_root, talker, file_id, key, seed, create_time, decode_cache_dir=''):
    """(image bytes, mime) for a chat image, or (b'', '').

    Prefers the full image over the thumbnail: WeChat only caches 120-210px
    thumbnails, and those look blocky at the size the reader renders. The
    result is downscaled to keep the export a sane size.
    """
    if not file_id or not key:
        return b'', ''
    if decode_cache_dir:
        cached = os.path.join(decode_cache_dir, file_id)
        if os.path.isfile(cached):
            try:
                with open(cached, 'rb') as fh:
                    data = fh.read()
                mime = sniff(data)
                if mime:
                    return data, mime
            except OSError:
                pass

    month = ''
    if create_time:
        import datetime
        month = datetime.datetime.fromtimestamp(create_time).strftime('%Y-%m')
    conv_dir = os.path.join(account_root, 'msg', 'attach',
                            hashlib.md5(talker.encode()).hexdigest())

    # The message's own month first, then the rest newest-first: the month a
    # file lives in is derived from its create_time, but older data sometimes
    # sits in a neighbouring bucket.
    search_dirs = []
    if os.path.isdir(conv_dir):
        if month:
            search_dirs.append(os.path.join(conv_dir, month, 'Img'))
        for m in sorted(os.listdir(conv_dir), reverse=True):
            d = os.path.join(conv_dir, m, 'Img')
            if d not in search_dirs:
                search_dirs.append(d)

    # Full image first - the thumbnail is only 120-210px and looks blocky.
    candidates = []
    for d in search_dirs:
        candidates.append(os.path.join(d, f'{file_id}.dat'))
        candidates.append(os.path.join(d, f'{file_id}_t.dat'))

    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'rb') as fh:
                raw = fh.read()
        except OSError:
            continue
        data = decrypt(raw, key, seed)
        mime = sniff(data)
        if mime == 'image/hevc':
            from wechat_emoticon import decode_wxgf
            data = decode_wxgf(data)
            mime = 'image/png' if data else ''
        if not mime:
            continue
        data, mime = shrink(data, mime)
        if decode_cache_dir:
            try:
                os.makedirs(decode_cache_dir, exist_ok=True)
                with open(os.path.join(decode_cache_dir, file_id), 'wb') as fh:
                    fh.write(data)
            except OSError:
                pass
        return data, mime
    return b'', ''
