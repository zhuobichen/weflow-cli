#!/usr/bin/env python3
"""
全自动知识升级 — 003_Ideas → 004_Permanent → 005_Reference → 006_Projects

用法:
  python scripts/promote_all.py --api-key <key>
"""

import sys, os, re
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_VAULT = os.path.join(PROJECT_ROOT, 'output', 'wechat-vault')

sys.path.insert(0, SCRIPTS_DIR)
from _utils import call_deepseek


def read_frontmatter(path: Path) -> dict:
    c = path.read_text(encoding='utf-8')
    if not c.startswith('---'):
        return {}
    end = c.find('---', 3)
    if end == -1:
        return {}
    fm = {}
    for line in c[3:end].split('\n'):
        if ':' in line:
            k, _, v = line.partition(':')
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def collect_articles(vault_path: str) -> list[dict]:
    lit = Path(vault_path) / '002_Literature' / 'WeChat'
    articles = []
    for d in lit.iterdir():
        if not d.is_dir(): continue
        for f in d.glob('*.md'):
            fm = read_frontmatter(f)
            articles.append({
                'title': fm.get('title', f.stem),
                'source': fm.get('source', ''),
                'date': d.name,
                'topic': fm.get('hasTopic', '').replace('[[','').replace(']]',''),
                'path': f,
            })
    return articles


def generate_permanent(api_key, vault_path, articles):
    """004_Permanent: 将 Best Ideas 扩展为原子永久笔记。"""
    perm_dir = Path(vault_path) / '004_Permanent'
    perm_dir.mkdir(parents=True, exist_ok=True)

    # 按主题取 articles 样本
    by_topic = defaultdict(list)
    for a in articles:
        t = a['topic'] or '综合'
        by_topic[t].append(a)

    created = 0
    for topic, arts in by_topic.items():
        if len(arts) < 10: continue
        if (perm_dir / f'Perm-{topic}.md').exists(): continue

        samples = sorted(arts, key=lambda a: len(a['title']))[-10:]
        titles = '\n'.join(f'- {a["title"][:50]}' for a in samples)

        prompt = f'''基于以下 {topic} 主题文章，写一篇 300 字的原子笔记，提炼该领域的核心知识。
格式：1个核心概念 → 3个关键洞察 → 1个行动建议。

文章样本：
{titles}

只返回笔记内容，用 Markdown 格式。'''

        try:
            text = call_deepseek(prompt, api_key, max_tokens=600)
        except Exception as e:
            print(f'  [WARN] {topic}: {e}')
            continue

        content = f'''---
title: "{topic} 核心知识"
created: {datetime.now().strftime('%Y-%m-%d')}
type: permanent-note
hasTopic: [[{topic}]]
tags: [permanent, {topic.lower()}, atomic]
sources: {len(arts)} articles
---

# {topic} 核心知识

{text}

---

## 来源文章
'''
        for a in arts[:15]:
            content += f'- [[{a["date"]}/{a["path"].name}|{a["title"][:50]}]]\n'

        (perm_dir / f'Perm-{topic}.md').write_text(content, encoding='utf-8')
        created += 1
        print(f'  [{topic}] → Perm-{topic}.md')
    return created


def generate_reference(vault_path, articles):
    """005_Reference: 自动提取工具和方法论。"""
    ref_dir = Path(vault_path) / '005_Reference'

    # 关键词扫描
    tool_keywords = ['工具', '开源', 'GitHub', 'Star', 'CLI', 'API', '框架', '平台', '模型',
                     '插件', 'Python', 'R包', '库', 'Agent', 'Skill', 'MCP', 'RAG']
    method_keywords = ['方法', '算法', '流程', '技术', '策略', '方案', '分析', '优化',
                       '评估', '验证', '建模', '预测', '训练', '调优', '部署']

    tools = defaultdict(list)
    methods = defaultdict(list)

    for a in articles:
        title = a['title']
        for kw in tool_keywords:
            if kw in title:
                tools[kw].append(a)
                break
        for kw in method_keywords:
            if kw in title:
                methods[kw].append(a)
                break

    created = 0

    # Tools index
    if tools:
        (ref_dir / 'Tools').mkdir(parents=True, exist_ok=True)
        content = f'''---
title: "工具索引"
created: {datetime.now().strftime('%Y-%m-%d')}
type: reference
tags: [reference, tools]
---

# 工具索引

> 自动从 {len(articles)} 篇文章中提取

'''
        for kw in sorted(tools, key=lambda k: len(tools[k]), reverse=True):
            if len(tools[kw]) < 3: continue
            content += f'\n## {kw} ({len(tools[kw])} 篇)\n'
            for a in tools[kw][:5]:
                content += f'- [[{a["date"]}/{a["path"].name}|{a["title"][:60]}]]\n'

        (ref_dir / 'Tools' / '工具索引.md').write_text(content, encoding='utf-8')
        created += 1
        print(f'  Tools: {len(tools)} categories')

    # Methods index
    if methods:
        (ref_dir / 'Methods').mkdir(parents=True, exist_ok=True)
        content = f'''---
title: "方法论索引"
created: {datetime.now().strftime('%Y-%m-%d')}
type: reference
tags: [reference, methods]
---

# 方法论索引

> 自动从 {len(articles)} 篇文章中提取

'''
        for kw in sorted(methods, key=lambda k: len(methods[k]), reverse=True):
            if len(methods[kw]) < 3: continue
            content += f'\n## {kw} ({len(methods[kw])} 篇)\n'
            for a in methods[kw][:5]:
                content += f'- [[{a["date"]}/{a["path"].name}|{a["title"][:60]}]]\n'

        (ref_dir / 'Methods' / '方法论索引.md').write_text(content, encoding='utf-8')
        created += 1
        print(f'  Methods: {len(methods)} categories')

    return created


def generate_projects(api_key, vault_path, articles):
    """006_Projects: AI 生成项目提案。"""
    proj_dir = Path(vault_path) / '006_Projects'
    proj_dir.mkdir(parents=True, exist_ok=True)

    # 取最大的主题生成项目
    by_topic = defaultdict(list)
    for a in articles:
        t = a['topic'] or '综合'
        by_topic[t].append(a)

    # 只对文章数 > 50 的主题生成项目
    created = 0
    for topic, arts in sorted(by_topic.items(), key=lambda x: len(x[1]), reverse=True):
        if len(arts) < 50: continue
        if (proj_dir / f'Proj-{topic}.md').exists(): continue

        samples = sorted(arts, key=lambda a: len(a['title']))[-8:]
        titles = '\n'.join(f'- {a["title"][:50]}' for a in samples)

        prompt = f'''为 {topic} 领域生成一个研究项目提案（150字），格式：
## 项目名称
## 目标
## 关键里程碑 (3个)
## 所需资源

基于文章样本：
{titles}

只返回提案内容。'''

        try:
            text = call_deepseek(prompt, api_key, max_tokens=400)
        except Exception as e:
            print(f'  [WARN] {topic}: {e}')
            continue

        content = f'''---
title: "{topic} 研究项目"
created: {datetime.now().strftime('%Y-%m-%d')}
type: project
hasTopic: [[{topic}]]
tags: [project, {topic.lower()}]
status: proposed
article_count: {len(arts)}
---

# {topic} 研究项目

{text}

---

## 📊 数据基础

- **文章数**: {len(arts)} 篇
- **覆盖日期**: {len(set(a["date"] for a in arts))} 天

## 📚 相关文献

'''
        for a in arts[:20]:
            content += f'- [[{a["date"]}/{a["path"].name}|{a["title"][:50]}]]\n'

        content += f'''

## 🔗 相关资源

- [[MOC-{topic}]] — 主题导航
- [[Perm-{topic}]] — 核心知识
- [[idea-{topic}-研究想法]] — 研究想法
'''

        (proj_dir / f'Proj-{topic}.md').write_text(content, encoding='utf-8')
        created += 1
        print(f'  [{topic}] → Proj-{topic}.md ({len(arts)} articles)')

    return created


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    p = argparse.ArgumentParser(description='全自动知识升级管道')
    p.add_argument('--vault', default=DEFAULT_VAULT)
    p.add_argument('--api-key', default='')
    p.add_argument('--skip-ai', action='store_true')
    args = p.parse_args()

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')

    print('📊 收集数据...')
    articles = collect_articles(args.vault)
    print(f'  {len(articles)} 篇文章')

    # 004: Permanent Notes (AI)
    if not args.skip_ai and api_key:
        print('\n📝 生成 004_Permanent 原子笔记...')
        p_count = generate_permanent(api_key, args.vault, articles)
    else:
        p_count = 0

    # 005: Reference (keyword-based, no AI needed)
    print('\n📚 生成 005_Reference 工具/方法论索引...')
    r_count = generate_reference(args.vault, articles)

    # 006: Projects (AI)
    if not args.skip_ai and api_key:
        print('\n🎯 生成 006_Projects 项目提案...')
        j_count = generate_projects(api_key, args.vault, articles)
    else:
        j_count = 0

    total = p_count + r_count + j_count
    print(f'\n✓ 全自动完成: {total} 个文件 (004:{p_count} + 005:{r_count} + 006:{j_count})')


if __name__ == '__main__':
    main()
