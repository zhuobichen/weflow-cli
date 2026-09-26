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


def rank_concepts(concept_map: dict, min_refs: int = 1) -> list:
    """按被引用的篇数排序，并滤掉"不够格"的。

    **为什么要有这个闸门**：只被一篇提到的概念，多半是那篇新闻里的人名/机构/单次事件——
    给它们逐条写页不是知识库，是剪报。实测 120 篇材料聚出 387 个概念，其中只有 **37 个**
    被 ≥2 篇提到（`--min-refs 2` 就是拿这条实测数据定的默认建议值）。
    """
    ranked = sorted(concept_map.items(), key=lambda item: len(item[1]), reverse=True)
    if min_refs > 1:
        ranked = [(name, refs) for name, refs in ranked if len(refs) >= min_refs]
    return ranked


# 一条来源（`--source` 目录名）→ 它派生出来的页该带什么来源标签。
# 借自那个考公库：它把私密内容单放 `private/`，README 里也点明"家庭/健康/情绪类内容注意隐私"。
# 我们的库里**公开文章与私人对话是混在一起的**——用嵌套标签分开，`tag:#来源/聊天`
# 就能一眼看出哪些页该当私密内容对待（要分享、要截图、要导出时用得上）。
SOURCE_TAGS = {
    'article-notes': '来源/文章',
    'chat-notes': '来源/聊天',
    'fav-notes': '来源/收藏',
    'user-notes': '来源/我的笔记',
}


def origin_tag(source_dir: str) -> str:
    """`--source` 指向哪个产卡目录 → 来源标签。认不出来就不加（不猜）。"""
    name = Path(str(source_dir)).name
    return SOURCE_TAGS.get(name, '')


def generate_concept(name: str, refs: list[dict], api_key: str, origin: str = '') -> str | None:
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
        # 嵌套标签（借自那个考公脑库的做法）：`tag:#知识/概念` 一键筛出所有知识页，
        # 而模型给的分类词继续当第二层用
        'tags': ['知识/概念'] + ([origin] if origin else []) + list(tags),
        # **未经人工核验**：这些页是模型生成的。写出来，免得它被当成定论
        # （与那个考公库 README 里"未核验的信息必须明确标注"是同一条纪律）
        'verified': False,
        'created': today,
        # 这个概念的来源都来自哪些主题——按主题翻知识库时用得上
        'topics': sorted({r['topic'] for r in refs[:5] if r.get('topic')}),
        'sources': source_files,
    }

    return fm, ''.join(body_parts)


def count_cards_per_concept(card_dirs=None) -> dict:
    """每个概念被**多少张卡片**提到——跨**所有**产卡目录数。

    为什么不能用本次运行的 `concept_map`：索引页是每次 compile 都重写的，而三条线（文章/对话/收藏）
    各跑一次 compile。用本次的 map 的话，索引会**轮流被覆盖成"只看到最后那条源"的视角**——
    实测到的症状是：收藏线产出的概念在索引里显示"引用数 0"，而它看着只是"没人引用"。
    """
    import glob as _glob
    counts = {}
    dirs = card_dirs or sorted(p for p in _glob.glob('output/*-notes') if os.path.isdir(p))
    for directory in dirs:
        for path in Path(directory).rglob('*.md'):
            try:
                _, body = parse_frontmatter(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            for name in {n.strip() for n in re.findall(r'\[\[([^\]]+)\]\]', body) if n.strip()}:
                counts[name] = counts.get(name, 0) + 1
    return counts


def source_kinds_for(page_sources, card_dirs=None) -> list:
    """这一页的来源卡片住在哪几类目录里 → 该带的来源标签（本地推断，不猜）。

    `sources` 里记的是**卡片文件名**（相对各自产卡目录），所以拿它在 `output/*-notes`
    里一找就知道这张页是文章派生的、聊天派生的，还是两者都有。
    """
    import glob as _glob
    dirs = [Path(p) for p in (card_dirs or sorted(p for p in _glob.glob('output/*-notes')
                                                  if os.path.isdir(p)))]
    kinds = set()
    for name in page_sources or []:
        for directory in dirs:
            if (directory / str(name)).exists():
                tag = SOURCE_TAGS.get(directory.name)
                if tag:
                    kinds.add(tag)
                break
    return sorted(kinds)


def relabel_pages(out_dir, card_dirs=None) -> int:
    """给**已经生成好的**页补上标签与"未核验"标记。**本地、不调用模型。**

    与 `article_notes --refresh-summaries` 同一个路数：frontmatter 是本地就能算出来的东西，
    规则一变不必把 51 个概念重问一遍。只在缺的时候补（幂等）。

    来源标签（`来源/聊天` 那类）也从**已有信息**推出来：`sources` 里记的是卡片文件名，
    去 `output/*-notes` 里一找就知道它住在哪一类目录。
    """
    changed = 0
    for path in sorted(Path(out_dir).glob('*.md')):
        if path.name == '00-Overview.md':
            continue
        content = path.read_text(encoding='utf-8')
        frontmatter, body = parse_frontmatter(content)
        tags = [t for t in (frontmatter.get('tags') or []) if t != '知识/概念']
        wanted = ['知识/概念'] + source_kinds_for(frontmatter.get('sources'), card_dirs) + tags
        if wanted == list(frontmatter.get('tags') or []) and 'verified' in frontmatter:
            continue
        frontmatter['tags'] = wanted
        frontmatter['verified'] = False
        write_with_frontmatter(str(path), frontmatter, body)
        changed += 1
    return changed


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='概念图谱编译')
    parser.add_argument('--api-key', help='DeepSeek API key (或环境变量 DEEPSEEK_API_KEY)')
    parser.add_argument('--limit', type=int, default=20, help='最多生成概念数 (默认20)')
    parser.add_argument('--relabel', action='store_true',
                        help='只给已有页面补标签与未核验标记（本地，不调用模型）')
    parser.add_argument('--min-refs', type=int, default=1,
                        help='至少被几篇材料提到才建页（默认 1；2 能滤掉新闻里的一次性实体）')
    parser.add_argument('--source', default=SOURCE_ROOT, help='文章目录')
    parser.add_argument('--output', default=OUTPUT_ROOT, help='概念页输出目录')
    args = parser.parse_args()

    if getattr(args, 'relabel', False):
        # 本地重贴标签：不必有 key、不必调模型
        changed = relabel_pages(args.output)
        print('重贴标签：%d 张页面' % changed)
        return

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
    before = len(concept_map)
    ranked = rank_concepts(concept_map, args.min_refs)
    if args.min_refs > 1:
        print('  按 --min-refs %d 过滤：%d → %d 个概念' % (args.min_refs, before, len(ranked)))
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
        result = generate_concept(name, refs, api_key, origin=origin_tag(args.source))
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
        '**这一页是生成的**：重跑 `weflow-cli wiki compile` 会覆盖它，也会覆盖每个概念页。'
        '要改内容，改上游的材料（卡片）再重跑，别在笔记里直接改——手改会在下次导出时消失。',
        '',
        '**这些页由模型生成、未经人工核验**（每个概念页的 frontmatter 里 `verified: false`）。'
        '它们可以当线索用，别当成定论。',
        '',
        '**看关系图谱请带筛选**：这个库里有上万条材料，直接开全局图谱会卡住。'
        '只想看知识页，把图谱左上角的筛选框填成 `path:"Wiki/Concepts"`；'
        '更顺手的日常用法是**局部图谱**（打开任意一页 → 右上角更多 →「局部图谱」），只加载邻接节点，秒开。',
        '',
        '| # | 概念 | 引用数 |',
        '|---|------|--------|',
    ]
    # 跨所有产卡目录数引用（见 `count_cards_per_concept` 的注释：不能用本次的 concept_map）
    all_counts = count_cards_per_concept()
    concept_files.sort(key=lambda p: str(parse_frontmatter(p.read_text(encoding='utf-8'))[0].get('title', p.stem)))
    for i, cf in enumerate(concept_files):
        fm, _ = parse_frontmatter(cf.read_text(encoding='utf-8'))
        # **标题要先去引号再当键**：写出去的是 `title: "甲"`（YAML 安全），而这里的键是裸的 `甲`
        title = str(fm.get('title', cf.stem)).strip().strip('"')
        index_lines.append(f'| {i+1} | [[{title}]] | {all_counts.get(title, 0)} |')

    index_path = out_dir.parent / '00-Overview.md'
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_lines) + '\n')

    print(f'  索引: {index_path}')
    print(f'\n✓ 完成！共 {len(concept_files)} 个概念页')


if __name__ == '__main__':
    main()
