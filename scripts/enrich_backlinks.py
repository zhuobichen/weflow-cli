#!/usr/bin/env python3
"""
双向链接增强 — 为文章添加同主题、共享概念的相关文章链接。

用法:
  python scripts/enrich_backlinks.py --date 2026-05-22
  python scripts/enrich_backlinks.py --date 2026-05-22 --dry-run
"""

import sys, os, re
from pathlib import Path
from collections import defaultdict

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_SOURCE = os.path.join(PROJECT_ROOT, 'output', 'biz-daily')

TOPIC_ORDER = ['AI', '学术', '新闻', '文学', '投资']


def parse_frontmatter(text: str) -> dict:
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


def collect_articles(date_dir: str) -> list[dict]:
    """收集目录下所有文章，提取元数据。"""
    articles = []
    for topic in TOPIC_ORDER:
        topic_dir = Path(date_dir) / topic
        if not topic_dir.is_dir():
            continue
        for md_file in sorted(topic_dir.glob('*.md')):
            try:
                content = md_file.read_text(encoding='utf-8')
            except Exception:
                continue
            fm, body = parse_frontmatter(content)
            # 检查是否已经有相关阅读段落
            if '## 相关阅读' in body:
                continue  # 已处理，跳过

            articles.append({
                'path': md_file,
                'topic': topic,
                'filename': f'{topic}/{md_file.name}',
                'title': fm.get('title', md_file.stem),
                'source': fm.get('source', ''),
                'tags': fm.get('tags', []),
                'concepts': fm.get('concepts', fm.get('wikilinks', [])),
                'relevance': fm.get('relevance', ''),
            })
    return articles


def find_related(article: dict, all_articles: list[dict]) -> list[dict]:
    """查找与给定文章相关的文章。"""
    # 同主题文章（排除自身）
    same_topic = [a for a in all_articles
                  if a['topic'] == article['topic']
                  and a['filename'] != article['filename']]

    # 共享概念的文章
    my_concepts = set(article['concepts']) if isinstance(article['concepts'], list) else set()
    shared_concept = []
    if my_concepts:
        shared_concept = [a for a in all_articles
                          if a['filename'] != article['filename']
                          and a['topic'] != article['topic']
                          and set(a['concepts'] if isinstance(a['concepts'], list) else []) & my_concepts]

    # 优先：同主题 + 高相关 + 共享概念
    related = []
    seen = set()

    # 先加同主题高相关
    for a in same_topic:
        if a['relevance'] == '高':
            related.append(a)
            seen.add(a['filename'])

    # 共享概念
    for a in shared_concept:
        if a['filename'] not in seen:
            related.append(a)
            seen.add(a['filename'])

    # 补充同主题中相关
    for a in same_topic:
        if a['filename'] not in seen and a['relevance'] == '中':
            related.append(a)
            seen.add(a['filename'])

    # 最后补任意来源
    for a in same_topic:
        if a['filename'] not in seen:
            related.append(a)
            seen.add(a['filename'])

    return related[:5]


def enrich_article(article: dict, related: list[dict], dry_run=False):
    """为文章末尾追加相关阅读段落。"""
    if not related:
        return 0

    lines = ['', '---', '', '## 相关阅读', '']
    for a in related:
        label = f"📎 {a['title']}"
        if a.get('source'):
            label += f" — {a['source']}"
        # 使用相对路径 Wikilink
        link_path = a['filename'].replace('/', '/')
        lines.append(f'- [[{link_path}|{label}]]')

    appendix = '\n'.join(lines) + '\n'

    if dry_run:
        print(f'  → {article["title"][:40]}')
        print(f'    相关: {", ".join(a["title"][:30] for a in related)}')
        return len(related)

    filepath = article['path']
    current = filepath.read_text(encoding='utf-8')
    filepath.write_text(current.rstrip() + '\n' + appendix, encoding='utf-8')
    return len(related)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='为文章添加双向链接（相关阅读）')
    parser.add_argument('--date', required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--dry-run', action='store_true', help='仅预览不写入')
    parser.add_argument('--source', default=DEFAULT_SOURCE, help='源目录')
    args = parser.parse_args()

    date_dir = os.path.join(args.source, args.date)
    if not os.path.isdir(date_dir):
        print(f'[ERROR] 目录不存在: {date_dir}')
        sys.exit(1)

    articles = collect_articles(date_dir)
    print(f'找到 {len(articles)} 篇文章')
    print(f'主题分布: {dict((t, len([a for a in articles if a["topic"]==t])) for t in TOPIC_ORDER if any(a["topic"]==t for a in articles))}')
    print()

    enriched = 0
    skipped = 0
    for article in articles:
        related = find_related(article, articles)
        if not related:
            skipped += 1
            continue
        n = enrich_article(article, related, args.dry_run)
        if n > 0:
            enriched += 1

    print(f'\n✓ 完成: {enriched} 篇已增强, {skipped} 篇无关联文章')
    if args.dry_run:
        print('  (--dry-run 模式，未实际写入)')


if __name__ == '__main__':
    main()
