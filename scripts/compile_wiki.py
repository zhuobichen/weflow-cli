#!/usr/bin/env python3
"""
概念图谱编译 — 扫描文章的 [[Wikilinks]] → 聚合 → DeepSeek 生成概念页。

用法:
  python scripts/compile_wiki.py --api-key <key> [--limit 20] [--source output/biz-daily]
"""
import sys, os, json, re, time, hashlib
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import call_deepseek, parse_frontmatter, write_with_frontmatter

# Default paths
SOURCE_ROOT = 'output/biz-daily'
OUTPUT_ROOT = 'output/wechat-vault/Wiki/Concepts'

CONCEPT_PROMPT = """为概念生成 Wiki 知识页。

概念名：{name}

参考来源（来自公众号文章）：
{references}

请按格式返回：
【定义】
（1-2句话定义这个概念）

【关键要点】
- 要点1
- 要点2
- 要点3

【标签】
tag1, tag2, tag3

【相关概念】
概念A, 概念B, 概念C

要求：定义精准，要点简洁（每条≤30字），标签2-3个，相关概念2-4个。"""


def scan_articles(source_dir: str) -> list[dict]:
    """Scan all .md files, extract frontmatter + wikilinks."""
    articles = []
    for md_file in Path(source_dir).rglob('*.md'):
        if md_file.name == 'README.md':
            continue
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            continue

        fm, body = parse_frontmatter(content)

        # Extract [[wikilinks]] with optional descriptions
        wiki_pattern = re.findall(r'\[\[([^\]]+)\]\](?:\s*—?\s*([^\n]+))?', body)
        wikilinks = [(name.strip(), desc.strip()) for name, desc in wiki_pattern]

        if not wikilinks:
            continue

        articles.append({
            'file': str(md_file.relative_to(source_dir)),
            'title': fm.get('title', md_file.stem),
            'source': fm.get('source', ''),
            'topic': fm.get('topic', ''),
            'tags': fm.get('tags', []),
            'summary': _extract_summary(body),
            'wikilinks': wikilinks,
        })
    return articles


def _extract_summary(body: str) -> str:
    """Extract the AI summary section from article body."""
    m = re.search(r'## (?:AI 摘要|深度解析)\n\n(.+?)(?=\n\n##|\n\n---|\Z)', body, re.DOTALL)
    if m:
        return m.group(1).strip()[:500]
    # Fallback: first paragraph after metadata
    lines = body.strip().split('\n')
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('>') and not line.startswith('-'):
            return line[:200]
    return ''


def aggregate_concepts(articles: list[dict]) -> dict[str, list[dict]]:
    """Aggregate wikilinks: concept_name -> [articles that reference it]."""
    concept_map = defaultdict(list)
    for art in articles:
        seen = set()
        for name, desc in art['wikilinks']:
            if name in seen:
                continue
            seen.add(name)
            concept_map[name].append({
                'title': art['title'],
                'source': art['source'],
                'summary': art['summary'],
                'desc': desc,
                'file': art['file'],
            })
    return dict(concept_map)


def generate_concept(name: str, refs: list[dict], api_key: str) -> str | None:
    """Call DeepSeek to generate a concept Wiki page."""
    # Build references section
    ref_lines = []
    for r in refs[:5]:  # max 5 references
        ref_lines.append(f'- [{r["title"]}]（{r["source"]}）：{r["summary"][:150]}')
    ref_text = '\n'.join(ref_lines) if ref_lines else '(无详细信息)'

    prompt = CONCEPT_PROMPT.format(name=name, references=ref_text)
    try:
        response = call_deepseek(prompt, api_key, max_tokens=800)
    except Exception as e:
        print(f'  [ERR] {name}: {e}')
        return None

    # Parse response
    definition_m = re.search(r'【定义】\s*(.+?)(?=\n【|$)', response, re.DOTALL)
    points_m = re.search(r'【关键要点】\s*(.+?)(?=\n【|$)', response, re.DOTALL)
    tags_m = re.search(r'【标签】\s*(.+)', response)
    related_m = re.search(r'【相关概念】\s*(.+)', response)

    definition = definition_m.group(1).strip() if definition_m else ''
    points = points_m.group(1).strip() if points_m else ''
    tags = [t.strip() for t in tags_m.group(1).split(',')] if tags_m else []
    related = [r.strip() for r in related_m.group(1).split(',')] if related_m else []

    # Build markdown body
    body_parts = [f'# {name}\n']
    if definition:
        body_parts.append(f'{definition}\n\n')
    if points:
        body_parts.append('## 关键要点\n\n')
        body_parts.append(points + '\n\n')
    if related:
        body_parts.append('## 相关概念\n\n')
        for rc in related:
            body_parts.append(f'- [[{rc}]]\n')
        body_parts.append('\n')
    body_parts.append('## 来源\n\n')
    for r in refs[:5]:
        body_parts.append(f'- [[{r["file"]}]] — {r["title"]}\n')

    # Frontmatter
    source_files = [r['file'] for r in refs[:5]]
    today = time.strftime('%Y-%m-%d')
    fm = {
        'title': f'"{name}"',
        'type': 'concept',
        'tags': tags,
        'created': today,
        'sources': source_files,
    }

    return fm, ''.join(body_parts)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='概念图谱编译')
    parser.add_argument('--api-key', help='DeepSeek API key (或环境变量 DEEPSEEK_API_KEY)')
    parser.add_argument('--limit', type=int, default=20, help='最多生成概念数 (默认20)')
    parser.add_argument('--source', default=SOURCE_ROOT, help='文章目录')
    parser.add_argument('--output', default=OUTPUT_ROOT, help='概念页输出目录')
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print('[ERROR] 需要 DeepSeek API key')
        sys.exit(1)

    source_dir = args.source
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Scan
    print(f'=== Step 1: 扫描文章 ===')
    articles = scan_articles(source_dir)
    print(f'  找到 {len(articles)} 篇带 wikilinks 的文章')

    if not articles:
        print('No articles with wikilinks found. Exiting.')
        return

    # Step 2: Aggregate
    print(f'\n=== Step 2: 聚合概念 ===')
    concept_map = aggregate_concepts(articles)
    ranked = sorted(concept_map.items(), key=lambda x: len(x[1]), reverse=True)
    print(f'  共 {len(ranked)} 个概念（限制 TOP {args.limit}）')
    for i, (name, refs) in enumerate(ranked[:10]):
        print(f'  {i+1}. [[{name}]] — {len(refs)} 篇文章引用')

    # Step 3: Generate
    print(f'\n=== Step 3: AI 生成概念页 ===')
    top_concepts = ranked[:args.limit]
    generated = 0

    # Load existing concepts to skip
    skipped = 0
    for name, refs in top_concepts:
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', name)[:60]
        out_file = out_dir / f'{safe_name}.md'

        if out_file.exists():
            skipped += 1
            print(f'  [SKIP] {name} (已存在)')
            continue

        print(f'  [{generated+1}/{args.limit}] {name} ({len(refs)} 引用)...')
        result = generate_concept(name, refs, api_key)
        if result:
            fm, body = result
            write_with_frontmatter(str(out_file), fm, body)
            generated += 1
            time.sleep(0.5)

    if skipped:
        print(f'  跳过 {skipped} 个已有概念')
    print(f'  生成 {generated} 个新概念')

    # Step 4: Index
    print(f'\n=== Step 4: 生成索引 ===')
    concept_files = sorted(out_dir.glob('*.md'))
    index_lines = [
        '# 概念索引',
        '',
        f'共 {len(concept_files)} 个概念 | 生成时间：{time.strftime("%Y-%m-%d %H:%M")}',
        '',
        '| # | 概念 | 引用数 |',
        '|---|------|--------|',
    ]
    for i, cf in enumerate(concept_files):
        with open(cf, 'r', encoding='utf-8') as f:
            content = f.read()
        fm, _ = parse_frontmatter(content)
        title = fm.get('title', cf.stem)
        count = len(concept_map.get(title, []))
        index_lines.append(f'| {i+1} | [[{title}]] | {count} |')

    index_path = out_dir.parent / '00-Overview.md'
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_lines) + '\n')

    print(f'  索引: {index_path}')
    print(f'\n✓ 完成！共 {len(concept_files)} 个概念页')


if __name__ == '__main__':
    main()
