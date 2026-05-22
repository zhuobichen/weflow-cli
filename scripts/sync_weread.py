#!/usr/bin/env python3
"""
微信读书同步到 Vault — 将书架、笔记、划线写入 Obsidian。

用法:
  python scripts/sync_weread.py                  # 同步所有
  python scripts/sync_weread.py --type notes     # 仅笔记
  python scripts/sync_weread.py --type shelf     # 仅书架概览
"""

import sys, os, json, urllib.request
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_VAULT = os.path.join(PROJECT_ROOT, 'output', 'wechat-vault')
GATEWAY = 'https://i.weread.qq.com/api/agent/gateway'
SKILL_VERSION = '1.0.3'


def weread_call(api_name: str, api_key: str, **params) -> dict:
    """调用微信读书 API。"""
    body = json.dumps({'api_name': api_name, 'skill_version': SKILL_VERSION, **params}).encode('utf-8')
    req = urllib.request.Request(GATEWAY, data=body, headers={
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json; charset=utf-8',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f'  [WARN] API 调用失败: {e}')
        return {}


def sync_shelf(api_key, vault_dir):
    """同步书架概览到 Vault。"""
    data = weread_call('/shelf/sync', api_key)
    books = data.get('books', [])
    if not books:
        print('  书架为空')
        return

    shelf_dir = Path(vault_dir) / 'Sources' / 'WeRead'
    shelf_dir.mkdir(parents=True, exist_ok=True)

    # 生成书架索引
    lines = [
        '---',
        'type: weread-shelf',
        f'updated: {datetime.now().strftime("%Y-%m-%d %H:%M")}',
        '---',
        '',
        '# 微信读书书架',
        '',
        f'共 {len(books)} 本书',
        '',
    ]
    for b in sorted(books, key=lambda x: x.get('readUpdateTime', 0), reverse=True):
        done = '✓' if b.get('finishReading') == 1 else ''
        lines.append(f'- {done} **{b.get("title", "")}** — {b.get("author", "")}')

    (shelf_dir / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'  书架索引: {shelf_dir / "README.md"} ({len(books)} 本)')

    # 为每本在读的书创建笔记页
    reading_dir = Path(vault_dir) / 'Notes' / 'Reading'
    reading_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for b in books:
        if b.get('finishReading') == 1:
            continue  # 跳过已读完的
        safe_name = b.get('title', 'unknown').replace('/', '_').replace('\\', '_')
        for ch in '<>:"/\\|?*':
            safe_name = safe_name.replace(ch, '')
        safe_name = safe_name[:40].rstrip('. ')
        note_path = reading_dir / f'Weread-{safe_name}.md'
        if note_path.exists():
            continue
        note_path.write_text(f'''---
title: "{b.get('title', '')}"
author: "{b.get('author', '')}"
bookId: "{b.get('bookId', '')}"
type: weread-reading
tags: [weread, {b.get('category', '').lower()}]
---

# {b.get('title', '')}

> 作者: {b.get('author', '')}

## 阅读笔记

## 划线摘录

## 读后感

''', encoding='utf-8')
        created += 1
    if created:
        print(f'  阅读笔记: {created} 本新书')


def sync_notes(api_key, vault_dir):
    """同步笔记划线到 Vault。"""
    data = weread_call('/user/notebooks', api_key, count=50)
    books = data.get('books', [])
    if not books:
        print('  无法获取笔记列表')
        return

    highlights_dir = Path(vault_dir) / 'Notes' / 'WereadHighlights'
    highlights_dir.mkdir(parents=True, exist_ok=True)
    synced = 0

    for nb in books[:10]:  # 最多处理 10 本有笔记的书
        book = nb.get('book', {})
        book_id = book.get('bookId', '')
        title = book.get('title', '')
        if not book_id:
            continue

        marks = weread_call('/book/bookmarklist', api_key, bookId=book_id, count=50)
        updated = marks.get('updated', [])
        if not updated:
            continue

        safe_name = title.replace('/', '_')
        for ch in '<>:"/\\|?*':
            safe_name = safe_name.replace(ch, '')
        safe_name = safe_name[:40].rstrip('. ')
        note_path = highlights_dir / f'{safe_name}-划线.md'

        lines = [
            '---',
            f'title: "{title}"',
            f'bookId: "{book_id}"',
            'type: weread-highlights',
            'tags: [weread, highlights]',
            '---',
            '',
            f'# {title} — 划线笔记',
            '',
        ]
        for m in updated:
            text = m.get('markText', '')
            note = m.get('content', '')
            chapter = m.get('chapterTitle', '')
            if text:
                lines.append(f'> {text}')
                if chapter:
                    lines.append(f'  — {chapter}')
                if note:
                    lines.append(f'  💭 {note}')
                lines.append('')

        note_path.write_text('\n'.join(lines), encoding='utf-8')
        synced += 1

    print(f'  划线笔记: {synced} 本书')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='微信读书 → Vault 同步')
    parser.add_argument('--type', default='all', choices=['all', 'shelf', 'notes'])
    parser.add_argument('--vault', default=DEFAULT_VAULT, help='Vault 路径')
    args = parser.parse_args()

    api_key = os.environ.get('WEREAD_API_KEY', '')
    if not api_key:
        print('[ERROR] 未设置 WEREAD_API_KEY')
        sys.exit(1)

    vault_dir = Path(args.vault)
    (vault_dir / 'Sources' / 'WeRead').mkdir(parents=True, exist_ok=True)

    if args.type in ('all', 'shelf'):
        print('📚 同步书架...')
        sync_shelf(api_key, vault_dir)

    if args.type in ('all', 'notes'):
        print('📝 同步笔记...')
        sync_notes(api_key, vault_dir)

    print('✓ WeRead 同步完成')


if __name__ == '__main__':
    main()
