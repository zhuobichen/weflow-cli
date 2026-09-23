#!/usr/bin/env python3
"""取一张聊天图片：本机解密、缩小、给出 base64（给助手的 `look_at_image` 用）。

用法: read_image.py --talker <username> --local-id <id> [--json]

输出（JSON 在最后一行）:
  {"success": true, "b64": "...", "mime": "image/jpeg", "width": 720, "height": 431, ...}
  {"success": true, "reason": "...", "hint": "..."}      # 取不到，且说清是哪一种取不到
  {"success": false, "error": "..."}                     # 配置/解密层面的错

**为什么是独立脚本，而不是让助手进程 import 导出模块**：媒体索引是每次重建的（实测单聊
约 2 秒、一个万级文件的大群 18–24 秒），而导出模块的缩略 memo 是模块级全局、没有淘汰。
一次一个子进程，代价是重建索引，换来的是内存不随常驻时长增长。

**只做一件事**：把某条消息的图片取出来。找不到就说找不到——不猜、不漏。

怎么找：**不重写**导出侧已经写好、且已被真实数据验证过的那套（`.dat` v2 解密、会话缓存
索引、账号媒体索引、`MessageResourceInfo` 的 md5 映射），只按实测出来的顺序调它们。下面
这三条是实测出来的，不是文档里写的：

1. `resource_md5s` 是**承重的**：给了 50/50 能取到，不给 0/50（群聊）；
2. `shrink_embedded` 不传 `force=True` 是**空操作**（它默认只在 `--full-images` 下才缩）；
3. `resolve_v2_media_key` 依赖**微信在运行**。微信关着时它返回 None，于是绝大多数 `.dat`
   缩略图会**静默地**掉出索引。"这张图本机没有"与"我现在看不到"是两回事，所以下面把
   这两种情形分开报。
"""
import argparse
import hashlib
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import _utils                      # noqa: E402
import export_chat_html as E       # noqa: E402

# 取到的图放在仓库的 output/.cache 下（与抓取缓存同一层），键是 (talker, local_id)。
# 只缓存**取到的**图：取不到可能是"微信没在运行"这种会变的状态，缓存住就永远取不到了。
CACHE_DIR = os.path.join(SCRIPT_DIR, 'output', '.cache', 'read-image')
MAX_SIDE = 720


def _ok(payload):
    print(json.dumps(dict(success=True, **payload), ensure_ascii=False))
    return 0


def _fail(message):
    print(json.dumps({'success': False, 'error': message}, ensure_ascii=False))
    return 1


def _cache_path(talker, local_id):
    return os.path.join(CACHE_DIR, hashlib.md5(talker.encode()).hexdigest(), '%d.json' % local_id)


def _read_cache(path):
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if data.get('b64') else None


def _write_cache(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='') as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except Exception:
        pass          # 缓存写不进去不影响这一次取图


def _dimensions(b64_bytes):
    """图的长宽。取不到就回 (None, None)——尺寸是附带信息，不是这条路的成败条件。"""
    try:
        import base64
        import io
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(b64_bytes))) as img:
            return img.width, img.height
    except Exception:
        return None, None


def _passphrase(config):
    for name in ('favPassphrase', 'decryptKey'):
        value = config.get(name) or ''
        if not value:
            continue
        try:
            value = _utils.decrypt_lock(value)
        except Exception:
            continue
        if value:
            return value
    return ''


def main():
    parser = argparse.ArgumentParser(description='取一张聊天图片')
    parser.add_argument('--talker', required=True, help='会话的 username（不是显示名）')
    parser.add_argument('--local-id', required=True, type=int, help='消息的 local_id')
    parser.add_argument('--json', action='store_true', help='输出 JSON（本脚本只输出 JSON）')
    args = parser.parse_args()

    cache_file = _cache_path(args.talker, args.local_id)
    cached = _read_cache(cache_file)
    if cached:
        cached['cached'] = True
        print(json.dumps(cached, ensure_ascii=False))
        return 0

    config = _utils.load_config()
    db_path = config.get('ntDbPath') or ''
    if not db_path or not os.path.exists(db_path):
        return _fail('配置里没有可用的 ntDbPath')
    try:
        key_hex = _utils.decrypt_lock(config.get('ntKey', ''))
    except Exception as exc:
        return _fail('解不出数据库密钥: %s' % exc)
    salt = config.get('ntSalt', '')
    passphrase = _passphrase(config)

    account_dir = os.path.dirname(os.path.dirname(os.path.dirname(db_path)))
    cache_dir = os.path.join(account_dir, 'cache')
    own_wxid = E.clean_account_wxid(config.get('wxid', ''))

    try:
        messages = E.fetch_messages_from_shards(db_path, key_hex, salt, args.talker, '', passphrase)
    except Exception as exc:
        return _fail('读消息失败: %s' % exc)

    row = next((r for r in messages if int(r[0] or 0) == args.local_id), None)
    if row is None:
        return _ok({'reason': '这个会话里没有编号 #%d 的消息' % args.local_id})
    if int(row[2] or 0) != 3:
        return _ok({'reason': '编号 #%d 不是图片（消息类型 %s）' % (args.local_id, row[2])})

    server_id = row[1] or 0
    create_time = row[5] or 0
    content = row[8] or ''

    image_map = E.scan_nt_cache(cache_dir, args.talker, account_dir, own_wxid)
    account_media = E.scan_account_media(account_dir, own_wxid, args.talker)
    for media_key, media in account_media.items():
        if media_key in image_map:
            continue
        if len(media[0]) > E.EMBED_SHRINK_THRESHOLD:
            media = E.shrink_embedded(media, max_side=MAX_SIDE, force=True)
        image_map[media_key] = media

    resource_map = {}
    try:
        resource_map = E.load_resource_media_map(account_dir, key_hex, salt, messages, image_map, passphrase)
    except Exception:
        resource_map = {}      # 缺它只是取到率下降（群聊明显），不该让整次调用失败

    hit = E.resolve_media(image_map, args.local_id, create_time, content,
                          resource_map.get('server:%s' % server_id), server_id)
    if not hit:
        hint = ''
        try:
            if E.resolve_v2_media_key(account_dir, own_wxid) is None:
                hint = ('取缩略图密钥需要微信在运行；微信关着时这类图会看不到，'
                        '这不是"本机没有这张图"')
        except Exception:
            pass
        return _ok({'reason': '本机没有这张图的副本', 'hint': hint})

    b64, mime = E.shrink_embedded((hit[0], hit[1]), max_side=MAX_SIDE, force=True)
    width, height = _dimensions(b64)
    payload = {'b64': b64, 'mime': mime or 'image/jpeg', 'bytes': len(b64),
               'width': width, 'height': height, 'cached': False}
    _write_cache(cache_file, payload)
    print(json.dumps(dict(success=True, **payload), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
