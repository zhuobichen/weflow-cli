#!/usr/bin/env python3
"""
知识升级管道 — 002_Literature → 003_Ideas → 004_Permanent → 008_MOC

用法:
  python scripts/promote_ideas.py
  python scripts/promote_ideas.py --api-key <key>
"""

import sys, os, re, json
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_VAULT = os.path.join(PROJECT_ROOT, 'output', 'wechat-vault')

sys.path.insert(0, SCRIPTS_DIR)
from _utils import call_deepseek


def collect_metadata(vault_path: str) -> list[dict]:
    """收集所有 002_Literature 笔记的元数据。"""
    lit_dir = Path(vault_path) / '002_Literature' / 'WeChat'
    articles = []
    for date_dir in sorted(lit_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        for note_file in date_dir.glob('*.md'):
            try:
                content = note_file.read_text(encoding='utf-8')
            except Exception:
                continue
            # 提取 frontmatter
            fm = {}
            if content.startswith('---'):
                end = content.find('---', 3)
                if end != -1:
                    for line in content[3:end].split('\n'):
                        if ':' in line:
                            k, _, v = line.partition(':')
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            fm[k] = v

            # 提取概念
            concept_match = re.findall(r'## 关联网络.*?##', content, re.DOTALL)
            concepts = []
            if concept_match:
                concepts = re.findall(r'\[\[([^\]]+)\]\]', concept_match[0])

            # 提取标签
            tags = []
            tag_match = re.search(r'tags:\s*\[(.+?)\]', content)
            if tag_match:
                tags = [t.strip() for t in tag_match.group(1).split(',')]

            articles.append({
                'title': fm.get('title', note_file.stem),
                'source': fm.get('source', ''),
                'date': date_dir.name,
                'topic': fm.get('topic', ''),
                'hasTopic': fm.get('hasTopic', ''),
                'concepts': concepts,
                'tags': tags,
                'path': note_file,
            })
    return articles


def generate_ideas(articles: list[dict], api_key: str, vault_path: str) -> int:
    """AI 分析文章，提取跨领域研究想法 → 003_Ideas。"""
    # 按主题分组，提取代表性样本
    by_topic = defaultdict(list)
    for a in articles:
        topic = a['hasTopic'].replace('[[', '').replace(']]', '') or a.get('topic', '其他')
        by_topic[topic].append(a)

    ideas_dir = Path(vault_path) / '003_Ideas'
    ideas_dir.mkdir(parents=True, exist_ok=True)
    created = 0

    for topic, arts in by_topic.items():
        if len(arts) < 5:
            continue
        # 取代表性文章（高概念数的）
        samples = sorted(arts, key=lambda a: len(a.get('concepts', [])), reverse=True)[:10]
        titles = '\n'.join(f'- {a["title"][:60]} (来源: {a["source"]})' for a in samples[:8])

        # 收集热门概念
        all_concepts = []
        for a in arts:
            all_concepts.extend(a.get('concepts', []))
        top_concepts = [c for c, _ in Counter(all_concepts).most_common(8) if len(c) > 2]

        concepts_str = ', '.join(top_concepts[:8]) if top_concepts else '无'

        prompt = f'''你是一个研究助理。分析以下 {topic} 主题的文章列表，从中提炼 1-2 个研究想法。

文章列表（共 {len(arts)} 篇，样本 {len(samples)} 篇）：
{titles}

热门概念：{concepts_str}

请为每个想法生成以下格式（200字内/想法）：

## 想法1: [标题]
- **背景**: 一句话描述
- **核心思路**: 2-3句
- **相关文章**: 从样本列表中引用2-3篇
- **可行性**: 高/中/低

只返回想法，不要其他内容。'''

        try:
            result = call_deepseek(prompt, api_key, max_tokens=500)
        except Exception as e:
            print(f'  [WARN] {topic} AI 失败: {e}')
            continue

        idea_name = f'idea-{topic}-研究想法.md'
        idea_path = ideas_dir / idea_name
        if idea_path.exists():
            continue

        idea_content = f'''---
title: "{topic} 研究想法"
created: {datetime.now().strftime('%Y-%m-%d')}
type: idea
hasTopic: [[{topic}]]
tags: [idea, {topic.lower()}, research]
inspired_by: [{len(arts)} articles]
---

# {topic} 研究想法

> 基于 {len(arts)} 篇 {topic} 文章自动提炼

{result}

---

## 相关文章

'''
        for a in arts[:15]:
            idea_content += f'- [[{a["date"]}/{a["path"].name}|{a["title"][:50]}]]\n'

        idea_content += f'''

## 热门概念

'''
        for c in top_concepts[:10]:
            idea_content += f'- [[{c}]]\n'

        idea_path.write_text(idea_content, encoding='utf-8')
        created += 1
        print(f'  [{topic}] → {idea_name}')

    return created


def generate_moc(articles: list[dict], vault_path: str) -> int:
    """为每个主题生成 Map of Content → 008_MOC。"""
    by_topic = defaultdict(list)
    for a in articles:
        topic = a['hasTopic'].replace('[[', '').replace(']]', '') or a.get('topic', '其他')
        by_topic[topic].append(a)

    moc_dir = Path(vault_path) / '008_MOC'
    moc_dir.mkdir(parents=True, exist_ok=True)
    created = 0

    for topic, arts in by_topic.items():
        if len(arts) < 5:
            continue

        # 按日期分组
        by_date = defaultdict(list)
        for a in arts:
            by_date[a['date']].append(a)

        # 收集所有概念
        all_concepts = []
        for a in arts:
            all_concepts.extend(a.get('concepts', []))
        top_concepts = Counter(all_concepts).most_common(15)

        moc_path = moc_dir / f'MOC-{topic}.md'
        content = f'''---
title: "{topic} — 主题导航"
created: {datetime.now().strftime('%Y-%m-%d')}
type: moc
hasTopic: [[{topic}]]
tags: [moc, {topic.lower()}]
article_count: {len(arts)}
---

# {topic} — 主题导航地图

> {len(arts)} 篇文章 · 覆盖 {len(by_date)} 天

---

## 📅 按日期

'''
        for date_str in sorted(by_date.keys(), reverse=True):
            day_arts = by_date[date_str]
            content += f'\n### {date_str} ({len(day_arts)} 篇)\n'
            for a in day_arts[:8]:
                content += f'- [[{a["date"]}/{a["path"].name}|{a["title"][:60]}]] — {a["source"]}\n'
            if len(day_arts) > 8:
                content += f'- ... 等 {len(day_arts)} 篇\n'

        content += f'''

## 🔥 热门概念

'''
        for c, count in top_concepts:
            content += f'- [[{c}]] ({count} 次)\n'

        content += f'''

## 🔗 相关 MOC

'''
        for other in by_topic.keys():
            if other != topic:
                content += f'- [[MOC-{other}]]\n'

        content += f'''

## 📊 统计

```dataview
TABLE date, source, rating
FROM "002_Literature"
WHERE contains(hasTopic, "{topic}")
SORT date DESC
```
'''

        moc_path.write_text(content, encoding='utf-8')
        created += 1
        print(f'  [{topic}] → MOC-{topic}.md ({len(arts)} articles)')

    return created


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='知识升级管道')
    parser.add_argument('--vault', default=DEFAULT_VAULT, help='Vault 路径')
    parser.add_argument('--api-key', default='', help='DeepSeek API key')
    parser.add_argument('--skip-ai', action='store_true', help='跳过 AI 部分（仅生成 MOC）')
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')

    print('📊 收集文章元数据...')
    articles = collect_metadata(args.vault)
    print(f'  共 {len(articles)} 篇文章')

    # 统计
    by_topic = defaultdict(int)
    for a in articles:
        topic = a['hasTopic'].replace('[[', '').replace(']]', '') or a.get('topic', '其他')
        by_topic[topic] += 1
    print(f'  主题分布: {dict(by_topic)}')

    # Step 1: MOC（不需要 AI）
    print('\n🗺️ 生成 008_MOC 主题导航...')
    moc_count = generate_moc(articles, args.vault)

    # Step 2: Ideas（需要 AI）
    if not args.skip_ai and api_key:
        print('\n💡 生成 003_Ideas 研究想法...')
        idea_count = generate_ideas(articles, api_key, args.vault)
        print(f'\n✓ 完成: {moc_count} MOC + {idea_count} Ideas')
    else:
        idea_count = 0
        print(f'\n✓ 完成: {moc_count} MOC (跳过 AI)')

    if not api_key and not args.skip_ai:
        print('  提示: 使用 --api-key 或设置 DEEPSEEK_API_KEY 以启用 AI 想法生成')

    total = moc_count + idea_count
    print(f'  共生成 {total} 个文件')


if __name__ == '__main__':
    main()
