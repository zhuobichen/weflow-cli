#!/usr/bin/env python3
"""
阅读笔记系统 — 在 Vault 中为每篇文章创建可编辑笔记页。

用法:
  python scripts/create_reading_notes.py --date 2026-05-22
  python scripts/create_reading_notes.py --date 2026-05-22 --vault output/wechat-vault
"""

import sys, os
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_VAULT = os.path.join(PROJECT_ROOT, 'output', 'wechat-vault')
DEFAULT_SOURCE = os.path.join(PROJECT_ROOT, 'output', 'biz-daily')

TOPIC_ORDER = ['AI', '学术', '新闻', '文学', '投资']

NOTE_TEMPLATE = '''---
title: "{title}"
source: "{source}"
source_url: "{url}"
date: {date}
type: reading-note
status: unread
tags: [reading, {topic_tag}]
---

# {title}

> 来源: {source}
> 日期: {date}

## 摘要
{summary}

## 高亮

## 笔记

## 个人思考

## 相关概念
{concepts}
'''


def parse_frontmatter(text: str) -> tuple:
    if not text.startswith('---'):
        return {}, text
    end = text.find('---', 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 3:].strip()
    meta = {}
    for line in fm_text.split('\n'):
        line = line.strip()
        if ':' in line:
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith('[') and val.endswith(']'):
                val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(',') if v.strip()]
            meta[key] = val
    return meta, body


def extract_summary(body: str, max_len=300) -> str:
    """从正文提取摘要。"""
    # 去掉 Wikilinks 标记和标题
    lines = []
    for line in body.split('\n'):
        line = line.strip()
        # 跳过标题、元数据
        if line.startswith('#') or line.startswith('---') or line.startswith('## 相关阅读'):
            continue
        if line.startswith('>') or line.startswith('!['):
            continue
        # 清理 Wikilinks
        line = line.replace('[[', '').replace(']]', '')
        if line:
            lines.append(line)
    summary = ' '.join(lines[:10])
    if len(summary) > max_len:
        summary = summary[:max_len] + '...'
    return summary or '(无摘要)'


def extract_concepts(body: str) -> str:
    """提取 Wikilinks 转为概念列表。"""
    import re
    concepts = re.findall(r'\[\[([^\]]+)\]\]', body)
    concepts = [c.split('|')[0].strip() for c in concepts if not c.startswith('20')]  # 排除日期链
    unique = list(set(concepts))[:10]
    if not unique:
        return '暂无'
    return '\n'.join(f'- [[{c}]]' for c in unique)


def create_reading_note(article_path, vault_path, date_str):
    """为一篇文章创建阅读笔记。"""
    try:
        content = article_path.read_text(encoding='utf-8')
    except Exception:
        return None

    fm, body = parse_frontmatter(content)
    title = fm.get('title', article_path.stem)
    source = fm.get('source', '')
    url = fm.get('url', '')
    topic = fm.get('topic', '')
    topic_tag = topic.lower() if topic else 'general'
    summary = extract_summary(body)
    concepts = extract_concepts(body)

    # 笔记文件名
    safe_title = title.replace('/', '_').replace('\\', '_').replace(':', ' -')
    # Windows 文件名非法字符
    for ch in '<>:"/\\|?*"':
        safe_title = safe_title.replace(ch, '')
    safe_title = safe_title[:50].rstrip('. ')
    note_name = f'{date_str}-{safe_title}.md'
    note_path = Path(vault_path) / 'Notes' / 'Reading' / note_name

    if note_path.exists():
        return 'skip'

    # 渲染模板
    note_content = NOTE_TEMPLATE.format(
        title=title,
        source=source,
        url=url,
        date=date_str,
        topic_tag=topic_tag,
        summary=summary,
        concepts=concepts,
    )

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(note_content, encoding='utf-8')
    return 'created'


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='为文章创建 Obsidian 阅读笔记')
    parser.add_argument('--date', required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--source', default=DEFAULT_SOURCE, help='文章目录')
    parser.add_argument('--vault', default=DEFAULT_VAULT, help='Vault 目录')
    args = parser.parse_args()

    date_dir = os.path.join(args.source, args.date)
    if not os.path.isdir(date_dir):
        print(f'[ERROR] 目录不存在: {date_dir}')
        sys.exit(1)

    # 确保 Vault 目录结构
    vault = Path(args.vault)
    (vault / 'Notes' / 'Reading').mkdir(parents=True, exist_ok=True)
    (vault / 'Notes' / 'Daily').mkdir(parents=True, exist_ok=True)

    created = 0
    skipped = 0
    for topic in TOPIC_ORDER:
        topic_dir = Path(date_dir) / topic
        if not topic_dir.is_dir():
            continue
        for md_file in sorted(topic_dir.glob('*.md')):
            result = create_reading_note(md_file, vault, args.date)
            if result == 'created':
                created += 1
            elif result == 'skip':
                skipped += 1

    print(f'✓ 阅读笔记: {created} 篇新建, {skipped} 篇已存在')
    print(f'  位置: {vault / "Notes" / "Reading"}')

    # 每日日记
    daily_path = vault / 'Notes' / 'Daily' / f'{args.date}.md'
    if not daily_path.exists():
        daily_content = f'''---
date: {args.date}
type: daily-review
tags: [daily]
---

# {args.date} 阅读回顾

## 今日阅读
共 {created + skipped} 篇文章

## 收获
-

## 行动项
- [ ]

## 关联概念

'''
        daily_path.write_text(daily_content, encoding='utf-8')
        print(f'  日记: {daily_path}')


if __name__ == '__main__':
    main()
