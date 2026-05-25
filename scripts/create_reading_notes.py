#!/usr/bin/env python3
"""
阅读笔记系统 V2 — 参考 obsidian-research-vault-template 改造。

改进:
  - 数字前缀目录 (000_Inbox ~ 999_Archive)
  - Frontmatter: aliases, rating, reading-progress, typed links
  - Obsidian callout 语法 ([!info], [!tip], [!quote])
  - 内嵌 Dataview 查询

用法:
  python scripts/create_reading_notes.py --date 2026-05-22
  python scripts/create_reading_notes.py --date 2026-05-22 --vault output/wechat-vault
"""

import sys, os, re
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_VAULT = os.path.join(PROJECT_ROOT, 'output', 'wechat-vault')
DEFAULT_SOURCE = os.path.join(PROJECT_ROOT, 'output', 'biz-daily')

TOPIC_ORDER = ['AI', '学术', '新闻', '文学', '投资']

# ====== V2 模板 ======

NOTE_TEMPLATE = '''---
title: "{title}"
aliases: [{aliases}]
created: {created}
last-updated: {created}

# 来源
source: "{source}"
source_url: "{url}"
source_type: wechat-article
local_source: "{local_source}"
published: {published}

# 分类
hasTopic: [[{topic}]]
tags: [source/wechat, {topic_tag}, status/unread]

# 评价
rating:
importance:
reading-progress: 0
---

# {title}

> [!info] 文献信息
> - **来源**: {source} | {published}
> - **主题**: [[{topic}]]
> - **网页**: [微信原文]({url})
> - **本地**: [📂 打开源文件]({local_source})

---

## 📋 摘要

{summary}

---

## 💡 核心观点

> [!tip] 作者主张
> -

---

## 📝 阅读笔记

### 初读印象


### 关键发现
1.

### 方法亮点
-

### 局限与质疑
-

---

## ✨ 高亮与摘录

> [!quote]
>

---

## 🧠 个人思考

### 与我的研究关联


### 可借鉴之处
-

### 待深入问题
- [ ]

---

## 🔗 关联网络

### 相关概念
{concepts}

### 相关文献
-

---

## 📊 相关文章

```dataview
TABLE rating, importance, reading-progress
FROM "002_Literature"
WHERE contains(hasTopic, "{topic}")
SORT date DESC
LIMIT 10
```

---

#review/pending #source/wechat
'''

DAILY_TEMPLATE = '''---
date: {date}
type: daily-review
tags: [daily]
mood:
energy:
---

# {date} 阅读回顾

> [!abstract] 今日概览
> - **阅读**: {total} 篇文章
> - **笔记**: {created} 篇新建

---

## 📥 今日捕获

### 阅读清单
{reading_list}

### 新想法
-

### 待跟进
- [ ]

---

## 💡 今日收获

### 关键洞察
-

### 方法启发
-

### 疑问待解
-

---

## 🔗 概念连接

```dataview
LIST
FROM "002_Literature"
WHERE created = date({date})
```

---

## 🎯 明日计划

- [ ]

---

#daily #review
'''

VAULT_DIRS = [
    '000_Inbox',
    '001_Daily',
    '002_Literature/WeChat',
    '002_Literature/WeRead',
    '003_Ideas',
    '004_Permanent',
    '005_Reference/Tools',
    '005_Reference/Methods',
    '006_Projects',
    '007_Wiki/Concepts',
    '008_MOC',
    '999_Archive',
    '_attachments',
]


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
    lines = []
    for line in body.split('\n'):
        line = line.strip()
        if line.startswith('#') or line.startswith('---') or line.startswith('## 相关阅读'):
            continue
        if line.startswith('>') or line.startswith('!['):
            continue
        line = line.replace('[[', '').replace(']]', '')
        if line:
            lines.append(line)
    summary = ' '.join(lines[:10])
    if len(summary) > max_len:
        summary = summary[:max_len] + '...'
    return summary or '(无摘要)'


def extract_concepts(body: str) -> str:
    import re
    concepts = re.findall(r'\[\[([^\]]+)\]\]', body)
    concepts = [c.split('|')[0].strip() for c in concepts if not c.startswith('20')]
    unique = list(set(concepts))[:10]
    if not unique:
        return '-\n-'
    return '\n'.join(f'- [[{c}]]' for c in unique)


def generate_aliases(title: str) -> str:
    """从标题提取有意义的别名（中文词≥2字，英文词≥4字符）。"""
    words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{4,}', title)
    # 去重、排除纯数字
    seen = set()
    aliases = []
    for w in words:
        wl = w.lower()
        if wl not in seen and not w.isdigit():
            aliases.append(w)
            seen.add(wl)
    return ', '.join(f'"{a}"' for a in aliases[:5])


def safe_filename(title: str, max_len=50) -> str:
    safe = title.replace('/', '_').replace('\\', '_').replace(':', ' -')
    for ch in '<>:"/\\|?*':
        safe = safe.replace(ch, '')
    return safe[:max_len].rstrip('. ')


def create_reading_note(article_path, vault_path, date_str):
    try:
        content = article_path.read_text(encoding='utf-8')
    except Exception:
        return None, None

    fm, body = parse_frontmatter(content)
    title = fm.get('title', article_path.stem)
    source = fm.get('source', '')
    url = fm.get('url', '')
    topic = fm.get('topic', '')
    topic_tag = topic.lower() if topic else 'general'
    published = fm.get('date', date_str)
    summary = extract_summary(body)
    concepts = extract_concepts(body)
    aliases = generate_aliases(title)
    created = datetime.now().strftime('%Y-%m-%d')
    # 源文件绝对路径 (file:// Obsidian 可点击)
    local_source = 'file:///' + str(article_path.resolve()).replace('\\', '/')

    # 路径: 002_Literature/WeChat/2026-05-22/文章.md
    note_path = Path(vault_path) / '002_Literature' / 'WeChat' / date_str / f'{date_str}-{safe_filename(title)}.md'
    if note_path.exists():
        return 'skip', title

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_content = NOTE_TEMPLATE.format(
        title=title, aliases=aliases, created=created,
        source=source, url=url, local_source=local_source, published=published,
        topic=topic, topic_tag=topic_tag,
        summary=summary, concepts=concepts,
    )
    note_path.write_text(note_content, encoding='utf-8')
    return 'created', title


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='创建 Obsidian 阅读笔记 V2')
    parser.add_argument('--date', required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--source', default=DEFAULT_SOURCE, help='文章目录')
    parser.add_argument('--vault', default=DEFAULT_VAULT, help='Vault 目录')
    args = parser.parse_args()

    date_dir = os.path.join(args.source, args.date)
    if not os.path.isdir(date_dir):
        print(f'[ERROR] 目录不存在: {date_dir}')
        sys.exit(1)

    vault = Path(args.vault)
    # 创建编号目录结构
    for d in VAULT_DIRS:
        (vault / d).mkdir(parents=True, exist_ok=True)

    created, skipped = 0, 0
    titles = []
    for topic in TOPIC_ORDER:
        topic_dir = Path(date_dir) / topic
        if not topic_dir.is_dir():
            continue
        for md_file in sorted(topic_dir.glob('*.md')):
            result, title = create_reading_note(md_file, vault, args.date)
            if not result:
                continue
            if result == 'created':
                created += 1
                titles.append(f'- [[{args.date}-{safe_filename(title)}|{title}]]')
            elif result == 'skip':
                skipped += 1

    total = created + skipped
    print(f'✓ 阅读笔记 V2: {created} 篇新建, {skipped} 篇已存在')
    print(f'  位置: {vault / "002_Literature" / "WeChat" / args.date}')

    # 每日日记
    daily_path = vault / '001_Daily' / f'{args.date}.md'
    if not daily_path.exists():
        daily_content = DAILY_TEMPLATE.format(
            date=args.date, total=total, created=created,
            reading_list='\n'.join(titles[:20]) if titles else '(无)',
        )
        daily_path.write_text(daily_content, encoding='utf-8')
        print(f'  日记: {daily_path}')


if __name__ == '__main__':
    main()
