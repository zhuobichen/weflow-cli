#!/usr/bin/env python3
"""
概念图谱编译 — 扫描材料的 [[Wikilinks]] → 聚合 → DeepSeek 生成概念页。

用法:
  python scripts/compile_wiki.py --api-key <key> [--limit 20] [--source <材料目录>]

**源的硬要求：材料里必须有"概念形状"的链接**（`[[概念]] — 关于它说了什么`）。
没有它就聚合不出任何概念，而这个脚本会照实说 `No articles with wikilinks found` 然后退出。

两个目录都**不**满足这个要求，各自的坑不同（2026-09-26 实测）：
- `output/biz-daily`（老默认值）：2231 个 .md，抽样 300 个**一个 wikilink 都没有** ✗；
- `output/wechat-vault/002_Literature`（Vault 里的文章笔记）：1633 篇里正文链接只有两类——
  **1631 条与自己的主题同名**（`> - **主题**: [[AI]]`）和 `## 🔗 关联网络` 里的**路径互链**。
  "其它"候选概念 **0 条**：这批笔记**没有概念那一节**。

所以要文章线产出概念页，**还得有一道"从文章里提炼概念"的步骤**（对话线已经有：
`chat_notes.py` 产出的卡自带 `[[话题]]/[[人名]]`）。在那之前，本脚本只在对话线上有效。
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

参考来源（每条是"某篇材料 + 它关于这个概念说的那句话"）：
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


# 笔记里**不是概念**的那几节：它们是"笔记之间的关系"，而且链接的名字是**路径**
# （实测：`## 🔗 关联网络` 里写的是 `- [[AI/某篇标题.md]]`）。
RELATION_SECTIONS = ('## 🔗 关联网络', '## 关联网络', '## 📊 相关文章', '## 相关文章')

_SECTION_RE = r'^##\s+%s\s*$'


def concept_body(body: str) -> str:
    """去掉"关系"那几节之后的正文（只在里面找概念链接）。"""
    lines = body.split('\n')
    kept, dropping = [], False
    for line in lines:
        if line.strip().startswith('## '):
            dropping = any(line.strip().startswith(s) for s in RELATION_SECTIONS)
        if not dropping:
            kept.append(line)
    return '\n'.join(kept)


def concept_links(body: str, topic: str = '') -> list[tuple]:
    """正文里**真正算概念**的 `[[wikilink]]`。

    实测真库一篇笔记，正文里的 `[[…]]` 有三类，**只有第三类是概念**：
    - `> - **主题**: [[AI]]` —— 主题是**元数据**（就是 `hasTopic` 那份），不是概念；
    - `## 🔗 关联网络` 里的 `- [[AI/某篇标题.md]]` —— 笔记之间的引用，**名字是路径**；
    - `- [[MCP 协议]] — 它把时间线开放给 Agent 操作` —— 这才是概念，**破折号后那句才是原料**。

    不滤的代价是实测过的：1631 篇聚出来的引用榜首是「新闻(752) / 政治(388) / 学术(341)」
    外加两条 `.md` 路径。照那个跑 `--limit 20`，就是拿二十次模型调用去生成这种"概念页"，
    再写进你的 Vault。
    """
    body = concept_body(body)
    links = []
    for name, desc in re.findall(r'\[\[([^\]]+)\]\](?:\s*—?\s*([^\n]+))?', body):
        name, desc = name.strip(), desc.strip()
        # 路径形状的一律不要（`a/b.md`、`x.md`）：那是笔记引用，不是概念
        if not name or '/' in name or name.endswith('.md'):
            continue
        # 与自己的主题同名的一律不要：主题是分类，不是概念
        if topic and name == topic:
            continue
        links.append((name, desc))
    return links


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

        # Extract [[wikilinks]] with optional descriptions（过滤规则见 `concept_links`）
        wikilinks = concept_links(body, article_topic(fm))

        if not wikilinks:
            continue

        articles.append({
            'file': str(md_file.relative_to(source_dir)),
            'title': fm.get('title', md_file.stem),
            'source': fm.get('source', ''),
            'topic': article_topic(fm),
            'tags': fm.get('tags', []),
            'summary': _extract_summary(body),
            'wikilinks': wikilinks,
        })
    return articles


# 认哪些小节算"摘要"。
#
# **`📋 摘要` 是 Vault 里那 1633 篇笔记实际用的标题**（由 `create_reading_notes.py` 写），
# 而这里的正则原来只认 `AI 摘要` / `深度解析`——它一直靠下面那个"正文第一段"的兜底
# **碰巧**读到同一段。兜底能用，但那是运气：笔记前面多一行别的东西（一行来源、一句引用之外的
# 普通文本），摘要就会静默变成那一行，而概念页会照着它生成。
SUMMARY_HEADINGS = ('AI 摘要', '深度解析', '📋 摘要', '摘要')
_SUMMARY_RE = re.compile(
    r'## (?:%s)\s*\n+(.+?)(?=\n\n##|\n\n---|\Z)' % '|'.join(re.escape(h) for h in SUMMARY_HEADINGS),
    re.DOTALL)


def article_topic(frontmatter: dict) -> str:
    """这篇材料的主题。

    两个键都认：`topic`（新写的笔记用它，如 `chat_notes`）与 **`hasTopic`**（Vault 里那批用它，
    而且是**给你 Obsidian 的 dataview 查询用的**——`create_reading_notes.py:43` 写 `hasTopic: [[AI]]`，
    查询里 `WHERE contains(hasTopic, "AI")`）。所以**改读的、不改写的**：一改字段名，你库里的
    查询就全断了；而读 `hasTopic` 还能让现有 1633 篇**立刻**有主题。值可能是 `[[AI]]` / `AI`，
    两种都拆成 `AI`。
    """
    raw = frontmatter.get('topic') or frontmatter.get('hasTopic') or ''
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else ''
    return str(raw).strip().strip('[]').strip()


def _extract_summary(body: str) -> str:
    """Extract the AI summary section from article body."""
    m = _SUMMARY_RE.search(body)
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
                'topic': art.get('topic', ''),
                'summary': art['summary'],
                'desc': desc,
                'file': art['file'],
            })
    return dict(concept_map)


def build_ref_lines(refs: list[dict], limit: int = 5) -> list[str]:
    """每个概念喂给模型的参考行。

    **优先用 `desc`（这条 wikilink 关于该概念写的那句话），没有才退回 note 摘要。**
    这两者一直都被 `aggregate_concepts` 收着，但生成时只用了摘要——后果是一个被 20 篇提到的
    概念，拿到的却是 5 篇**泛泛的**摘要，关于它自己反倒没说什么。人物页尤其吃这个亏：
    每条的 `desc` 就是"那时候发生了什么"，那是时间线的原料。

    `limit=5`：参考条数多了会把提示词撑长，而模型对第 6 条以后的边际收益很小。
    """
    lines = []
    for ref in refs[:limit]:
        detail = ref.get('desc') or ref.get('summary') or ''
        # 主题也带上：**收集了就要用**。原来 `topic` 被收进文章字典之后一次都没被读过，
        # 于是"按主题分"这件事根本无从谈起；带上它，模型才知道这些来源属于同一个领域。
        where = f'{ref["source"]} · {ref["topic"]}' if ref.get('topic') else ref['source']
        lines.append(f'- [{ref["title"]}]（{where}）：{detail[:150]}')
    return lines


def generate_concept(name: str, refs: list[dict], api_key: str) -> str | None:
    """Call DeepSeek to generate a concept Wiki page."""
    # Build references section
    ref_lines = build_ref_lines(refs)
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
        # 这个概念的来源都来自哪些主题——按主题翻知识库时用得上
        'topics': sorted({r['topic'] for r in refs[:5] if r.get('topic')}),
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
