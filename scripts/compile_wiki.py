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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import (CHAT_CARD_PREFIX, CONCEPT_DIRS, call_deepseek,  # noqa: E402
                    get_api_key, load_config, parse_frontmatter, write_with_frontmatter)

# Default paths
SOURCE_ROOT = 'output/biz-daily'
OUTPUT_ROOT = 'output/wechat-vault/Wiki/Concepts'

# 并发数。与 `article_notes.CONCEPT_WORKERS`、`biz_daily` 那三个取同一个值：
# 量的都是同一个上游的同一个延迟。
#
# **必须定义在使用它的函数之前**：它被用作默认参数值，而默认参数在**定义时**求值——
# 放在后面就是一个 import 期就炸的 NameError（2026-09-27 踩过）。
CONCEPT_PAGES_WORKERS = 6

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

要求：定义精准，要点简洁（每条≤30字），标签2-3个，相关概念2-4个。

**若参考行标注为「人物」**：这一页写的是**人**——聊天里出现过的联系人、对话者或提到的人。
按材料写他与用户的关系和往来，**不要**把它当成同名的事物、作品、名人或典故。
（实测：一个昵称叫「白马非马」的联系人，曾被按字面写成公孙龙的那个哲学命题——
材料里明明写着"对话对方，准备面试"，所以这里必须说清。）"""


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
        # **纯符号/表情/单字不算概念。** 实测聊天线提出来过 `🐊`、`🤓`、`🥬 + 🔴`、`: D`、`D`
        # 这种——它们会各自长出一张页，在图上就是几个没有意义的孤立点。
        # 判据是"至少两个汉字或字母数字"：`AI`/`MCP` 这类两字符的照收。
        if len(re.findall(r'[一-鿿A-Za-z0-9]', name)) < 2:
            continue
        links.append((name, desc))
    return links


# 聊天卡把这两类**分开写**（`### 话题` 与 `### 人`）。聚合时丢掉这个区分是有代价的，
# 实测：`白马非马` 是个联系人的昵称，而模型拿到"对话对方，准备面试"这类材料之后，
# 仍然按名字把它写成了公孙龙那个典故——**先验盖过了材料**。带上"这是个人"，
# 那一页才会写成一个人。
KIND_SECTIONS = (('### 人', '人物'), ('### 话题', '话题'))


def link_kinds(body: str) -> dict:
    """`[[名字]]` → 它出现在哪个小节里（`人物` / `话题`）。不在这些小节的就不给。

    只认卡片实际用的那两个小节；别的来源线（文章卡）没有这个概念，返回空字典，
    于是"没标注"与"标为话题"分得开——后者是**知道**它是话题。
    """
    kinds = {}
    current = ''
    for line in body.split('\n'):
        stripped = line.strip()
        matched = False
        for marker, kind in KIND_SECTIONS:
            if stripped.startswith(marker):
                current = kind
                matched = True
                break
        if not matched and (stripped.startswith('## ') or stripped.startswith('### ')):
            current = ''
        if not current:
            continue
        for name in re.findall(r'\[\[([^\]\|]+)\]\]', line):
            kinds.setdefault(name, current)
    return kinds


def source_link_target(frontmatter: dict, card_path) -> str:
    """卡片 → 它在 Vault 里对应的**阅读笔记**的链接名。

    **为什么不能直接用卡片自己的文件名**：卡片叫 `{日期}-{完整标题}.md`，而阅读笔记叫
    `{日期}-{标题截到 50 字}.md`（`create_reading_notes.safe_filename`）。标题短的（实测
    98%）两者恰好相同，于是链接**碰巧**能解析；标题一长就对不上——实测 5,470 条来源链接里
    有 125 条因此指向一个不存在的名字。

    卡片里本来就记着来源笔记的相对路径（`from`，由 `article_notes.build_card` 写入），
    取它的文件名才是**正确的那一个**。没有 `from` 的老卡片退回卡片自己的名字（保持原行为）。
    """
    origin = str(frontmatter.get('from') or '').strip()
    if origin:
        return Path(origin).stem
    return Path(card_path).stem


def build_source_name_map(source_dir: str) -> dict:
    """卡片文件名 → 它对应的阅读笔记文件名。**只收两者不同的那些。**

    不同的原因只有一个：标题超过 50 字（`safe_filename` 会截断）。所以这张表很小，
    而它就是"已生成的概念页里那些断链"需要的全部信息。
    """
    mapping = {}
    for md_file in Path(source_dir).rglob('*.md'):
        if md_file.name == 'README.md':
            continue
        try:
            frontmatter, _ = parse_frontmatter(md_file.read_text(encoding='utf-8'))
        except Exception:
            continue
        # **聊天卡在 Vault 里带前缀**（`Sources/Chat/会话-<会话名>.md`）：卡名就是会话名，
        # 而概念页可能同名，两个同名文件会让 `[[名字]]` 二义。所以这里也要跟着改，
        # 否则概念页的"来源"链接永远指不到那张卡（实测 1,176 条）。
        if Path(source_dir).name == 'chat-notes':
            mapping[md_file.stem] = CHAT_CARD_PREFIX + md_file.stem
            continue
        target = source_link_target(frontmatter, md_file)
        if target != md_file.stem:
            mapping[md_file.stem] = target
    return mapping


# "来源"段那一行的形状：`- [[名字]] — 标题`。**破折号是判据**——`## 相关概念` 那一段
# 也是 `- [[名字]]`，但没有后面的 ` — `。
SOURCE_LINK_RE = re.compile(r'^- \[\[([^\]]+)\]\] — ', re.M)


def fix_source_links(pages_dir: str, source_dir: str) -> dict:
    """把已有概念页"来源"段里对不上的链接改成正确的那一个。**本地，不调模型。**

    为什么需要单独一趟：上面 `scan_articles` 的修只影响**以后**生成的概念页，而库里已经有
    1,963 张带着旧链接——实测 125 条指不到任何文件。重跑 `wiki compile` 也能修，但那要
    重花一遍模型的钱、还会把已经写好的页面随机重写一遍；这里只改那几行链接。
    """
    mapping = build_source_name_map(source_dir)
    pages, links = [], 0
    for page in sorted(Path(pages_dir).glob('*.md')):
        text = page.read_text(encoding='utf-8')
        hits = [0]

        def replace(match):
            raw = match.group(1)
            # **后缀要去掉**：库里其他所有链接（笔记互链、日记、MOC）都是不带 `.md` 的写法，
            # 而这里原来带。带后缀能不能解析我不确定，不带是确定的写法——所以统一成不带。
            name = raw[:-3] if raw.lower().endswith('.md') else raw
            # 卡片名与阅读笔记名在标题超长时不同，查表换成对的那个（见 `source_link_target`）
            fixed = mapping.get(name, name)
            if fixed != raw:
                hits[0] += 1
            return '- [[%s]] — ' % fixed

        updated = SOURCE_LINK_RE.sub(replace, text)
        if hits[0]:
            page.write_text(updated, encoding='utf-8')
            pages.append(page.name)
            links += hits[0]
    return {'pages': pages, 'links': links, 'names': len(mapping)}


def collect_dangling_targets(pages_dir: str) -> Counter:
    """现有概念页提到、但**自己没有页**的概念 → 被多少张页提到。

    这就是图谱里那些"悬空节点"的来源，也是 `## 相关概念` 那些线指向的另一端。
    2026-09-27 实测：3,680 个名字、4,633 条链接，其中被 ≥2 张页提到的有 516 个。

    **它们大多建不出来**：那些名字是模型写页面时自己引入的，卡片语料里根本没有
    （`提示工程` 被 27 张页指向，而卡片语料里 0 条；`深度学习` 只有 1 条，低于
    `--min-refs 2`）。所以降 `--min-refs` 解决不了——降到 1 会建 20,957 页，
    而这批 0 引用的一个都还建不到。参考材料只能取自**提到它的那些页面**。
    """
    pages = Path(pages_dir)
    # **大小写不敏感**：Windows 上 `[[Claude Code]]` 能解析到 `claude code.md`，
    # 用大小写敏感的集合去判就会把 129 张页的 `Claude Code` 误报成"悬空"
    # （2026-09-27 实测：那一版的第一名就是它，而它其实有页）。
    have = {p.stem.lower() for p in pages.glob('*.md')}
    counts = Counter()
    for page in pages.glob('*.md'):
        text = page.read_text(encoding='utf-8')
        start = text.find('## 相关概念')
        if start < 0:
            continue
        body = text[start:]
        end = body.find('\n## ', 1)
        if end > 0:
            body = body[:end]
        for target in re.findall(r'\[\[([^\]\|]+)\]', body):
            name = target[:-3] if target.endswith('.md') else target
            if name and name.lower() not in have:
                counts[name] += 1
    return counts


def load_card_texts(card_dirs=None) -> list[dict]:
    """把所有卡片读进内存：`[(名字, 主题, 标题, 正文)]`。

    **卡片不大**（实测 7,119 张共约 12 MB），一次读完再按字面找，比"一个概念扫一遍
    七千张卡"快得多。
    """
    import glob as _glob
    dirs = card_dirs or sorted(p for p in _glob.glob('output/*-notes') if os.path.isdir(p))
    cards = []
    for directory in dirs:
        for path in Path(directory).rglob('*.md'):
            try:
                frontmatter, body = parse_frontmatter(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            cards.append({'name': path.stem,
                          'topic': str(frontmatter.get('topic') or frontmatter.get('hasTopic') or ''),
                          'title': str(frontmatter.get('title') or path.stem),
                          # `file` 是"来源"段那条链接的目标：**阅读笔记的名字**，
                          # 不是卡片的（见 `source_link_target`）。`generate_concept`
                          # 会读 `r['file']` 与 `r['title']`——少了它们就是 KeyError。
                          'file': source_link_target(frontmatter, path),
                          'source': str(frontmatter.get('source') or ''),
                          'body': body})
    return cards


def mention_refs(cards: list, name: str, limit: int = 5, width: int = 160) -> list[dict]:
    """按**字面**在卡片正文里找提到这个概念的地方，取它所在的句子当参考材料。

    **为什么不能用"提到它的那些概念页的定义句"**：那些页面只写了 `- [[提示工程]]`
    一个光名字，关于这个概念一个字都没说；拿它们的自我定义去生成 `提示工程` 的页，
    等于让模型凭空编。2026-09-27 我第一版就是这么写的，写完发现材料完全对不上。

    真实材料在**卡片正文**里：实测 `提示工程` 被 2 张卡、`知识蒸馏` 被 3 张卡在正文里
    提到过——只是从没成为结构化的 `[[概念]] — desc` 引用，所以 `compile_wiki` 一直不
    知道它们。这里把那句话连同前后半个窗口取出来，那才是"某篇材料关于它说了什么"。
    """
    refs = []
    for card in cards:
        # **只在摘要里认，不在全文里认。** 实测差别很大：全文按字面找会命中广告词、
        # 图片 URL、结构碎片（`AI对齐` 取到"提升超多好礼！"，`大模型应用` 取到一段 URL），
        # 而摘要讲什么、文章才是在讲什么。
        summary = _extract_summary(card['body']) or ''
        index = summary.find(name)
        if index < 0:
            continue
        start = max(0, index - width // 2)
        snippet = summary[start:index + len(name) + width // 2]
        # 摘要本身是从微信正文抄来的，会夹着图片/链接的 markdown
        snippet = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', snippet)
        snippet = re.sub(r'https?://\S+', '', snippet)
        snippet = re.sub(r'\s+', ' ', snippet).strip()
        if len(snippet) < 10:
            continue
        # `file`/`title` 必须一起给：`generate_concept` 拿它们写"来源"段，
        # 缺了就是 KeyError，而那个异常会被并发包装层吞掉（表现为"生成 0 页、无报错"）。
        refs.append({'desc': snippet, 'summary': '', 'topic': card['topic'].strip('[]'),
                     'file': card['file'], 'title': card['title'],
                     'source': card['source']})
        if len(refs) >= limit:
            break
    return refs


def build_dangling_pages(pages_dir: str, api_key: str, top: int,
                         workers: int = CONCEPT_PAGES_WORKERS, origin: str = '') -> dict:
    """给"被页面提到最多、却自己没有页"的概念建页。**这条通道的参考材料来自页面本身。**

    为什么不能靠 `scan_articles`：那些名字是模型写页面时引入的，卡片语料里大多没有
    （见 `collect_dangling_targets`）。所以这里直接用**提到它的那些页面的定义句**当参考行
    ——与卡片那条 `[[概念]] — desc` 是同一个用途。
    """
    counts = collect_dangling_targets(pages_dir)
    cards = load_card_texts()
    jobs, skipped = [], 0
    for name, _times in counts.most_common(top):
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', name)[:60]
        out_file = Path(pages_dir) / f'{safe_name}.md'
        if out_file.exists():
            skipped += 1
            continue
        # **材料按字面从卡片正文里找**（见 `mention_refs`）；找不到就不建——
        # 没有材料还建页，就是让模型编。
        refs = mention_refs(cards, name)
        if not refs:
            skipped += 1
            continue
        jobs.append((name, refs, out_file))

    generated = 0
    for (name, refs, out_file), result in iter_concept_pages(jobs, api_key, workers, origin=origin):
        if not result:
            continue
        fm, body = result
        write_with_frontmatter(str(out_file), fm, body)
        generated += 1
    return {'dangling': len(counts), 'attempted': len(jobs),
            'generated': generated, 'skipped': skipped}


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
        kinds = link_kinds(body)
        wikilinks = [(name, desc, kinds.get(name, ''))
                     for name, desc in concept_links(body, article_topic(fm))]

        if not wikilinks:
            continue

        articles.append({
            # **用阅读笔记的名字**，不是卡片的（见 `source_link_target`）
            'file': source_link_target(fm, md_file),
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
        for item in art['wikilinks']:
            name, desc = item[0], item[1]
            # 兼容两种形状：老的两元组（没有 kind）与新的三元组
            kind = item[2] if len(item) > 2 else ''
            if name in seen:
                continue
            seen.add(name)
            concept_map[name].append({
                'title': art['title'],
                'source': art['source'],
                'topic': art.get('topic', ''),
                'summary': art['summary'],
                'desc': desc,
                'kind': kind,
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
        # **把"这是个人还是话题"带给模型**：不写的话，像 `白马非马` 这种与典故同名的联系人
        # 会被写成一个哲学命题（实测发生过——材料明明写着"对话对方，准备面试"）。
        if ref.get('kind'):
            where = f'{ref["kind"]} · {where}'
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


# 并发数。与 `article_notes.CONCEPT_WORKERS`、`biz_daily` 那三个取同一个值：
# 量的都是同一个上游的同一个延迟。
CONCEPT_PAGES_WORKERS = 6


def sibling_concept_dirs(out_dir: Path) -> list:
    """同一个库里**另一条线**的概念目录（有的话）。

    **一个概念名在库里只能有一张页。** 两张同名页会让 `[[DeepSeek]]` 在 Obsidian 里变成二义的
    ——它挑一张连上，另一张等同于断了，而**两边都不报错**。这正是聊天卡要加 `会话-` 前缀的原因；
    概念页没有前缀可用，所以只能用「另一条线已有同名页就不再建」来保证名字唯一。2026-09-27 实测：
    分开目录之后，聊天线立刻为 11 个文章线已有的概念各建了一张同名页，`wiki lint` 报"同名页 11 组"。

    只在这个输出目录**确实落在一条已知的知识库线上**时才去找：`--output` 指到库外的任意目录时，
    那里没有"另一条线"，也就没有什么可跳过的。
    """
    # **两级**：两条线都是 `<库>/<线>/Concepts`（`Wiki/Concepts`、`Chat/Concepts`），
    # 库根是 `out_dir.parent.parent`。写成一级的话候选目录恒不存在（`<库>/Chat/Wiki/Concepts`），
    # 于是这段静默失效、同名页照建 —— 2026-09-27 第一次就是这么写的，跑完那 11 张又回来了。
    root = out_dir.parent.parent
    mine = out_dir.resolve()
    found = []
    for relative in CONCEPT_DIRS:
        candidate = root / relative
        if candidate.resolve() == mine or not candidate.is_dir():
            continue
        found.append(candidate)
    return found


def skip_note(skipped: int, elsewhere: int) -> str:
    """跳过多少、分别为什么 —— **两类原因要分开说**。

    本目录已有 = 重跑（正常）；另一条线已有同名页 = 故意不重复建（`sibling_concept_dirs`）。
    合成一句"跳过 N 个"的话，看到的人会以为全是重跑，而这 11 个正是分开目录之后新增的那一类。
    写成函数是为了**能被测**：留在 `main()` 里的一句 f-string，改坏了不会有任何东西红。
    """
    if not skipped and not elsewhere:
        return ''
    where = f'（其中 {elsewhere} 个在另一条线已有同名页）' if elsewhere else ''
    return f'  跳过 {skipped + elsewhere} 个已有概念{where}'


def build_jobs(top_concepts: list, out_dir, other_dirs: list = ()) -> tuple:
    """滤掉**已经有页**的概念，返回 `([(name, refs, out_file)], 跳过数)`。

    这一步必须在**提交给线程池之前**做。原来那版是串行循环里 `if out_file.exists(): continue`，
    顺序上天然不会为已存在的页花钱；一旦改成并发，"先提交、拿到结果再丢"就变成了**为一个
    已存在的页付一次费**——而且不报错，只体现在账单上。
    """
    jobs, skipped, elsewhere = [], 0, 0
    for name, refs in top_concepts:
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', name)[:60]
        out_file = out_dir / f'{safe_name}.md'
        if out_file.exists():
            skipped += 1
            continue
        # 另一条线已经有同名页 —— 跳过，理由见 `sibling_concept_dirs`
        if any((other / f'{safe_name}.md').exists() for other in other_dirs):
            elsewhere += 1
            continue
        jobs.append((name, refs, out_file))
    return jobs, skipped, elsewhere


def all_card_dirs(card_dirs=None) -> list:
    """产卡目录。默认**所有** `output/*-notes` —— 与 `count_cards_per_concept` 同一口径。

    **一个都没有就报错退出，不静默通过**：它用的是仓库相对路径，换个工作目录跑就匹配不到，
    于是"每一页都没有目标" → 报告说"改了 0 页"，看起来像成功了。那个失败模式比不做更坏。
    """
    import glob as _glob
    dirs = [str(d) for d in card_dirs] if card_dirs else sorted(
        p for p in _glob.glob('output/*-notes') if os.path.isdir(p))
    if not dirs:
        raise SystemExit('没有找到任何产卡目录（output/*-notes）—— 请在仓库根目录下运行')
    return dirs


def iter_source_links(text: str) -> list:
    """读出「来源」段里每一行的名字。**宽容读取**，因为 `SOURCE_LINK_RE` 读不全。

    实测库里 7,061 条来源行里有 20 条它匹配不到，全是标题里带方括号的
    （`- [[2026-03-05-[TGRS]利用…]] — …`）：那个正则的 `[^\]]+` 在第一个 `]`
    就停下，再要求紧跟 `]] — ` 于是整条不匹配。拿它判"这一行已经有了"，结果是
    **每次重跑都再追加一遍**，幂等性当场就破。

    `- [[` 是**四个**字符：写成 `line[3:]` 会多读一个 `[`，于是每一条现有来源都
    "匹配不上" —— 实测那一个小错造出过 6,721 条假缺口，把整份报告带偏（2026-09-28 踩的）。
    """
    names = []
    for line in text.split('\n'):
        if not line.startswith('- [['):
            continue
        sep = line.find(']] — ')
        if sep != -1:
            names.append(line[4:sep])
    return names


def merged_concept_refs(card_dirs=None) -> tuple:
    """跨所有产卡目录合并出 `概念名 → refs`，以及 `卡片名 → 该写进链接的名字`。

    **合并而不是只看一条线**，理由与 `count_cards_per_concept` 相同：收藏线与"我的笔记"线
    同样往 `Wiki/Concepts` 写，只看一个目录会把它们的来源当成"找不到出处的旧行"。

    映射表是**必需**的：聊天卡没有 `from` 字段，聚合出来的是裸会话名，而库里是
    `Sources/Chat/会话-<名字>.md` —— 直接写 `r['file']` 会造出 129 条断链。
    """
    merged, mapping = {}, {}
    for d in all_card_dirs(card_dirs):
        mapping.update(build_source_name_map(d))
        for name, refs in aggregate_concepts(scan_articles(d)).items():
            merged.setdefault(name, []).extend(refs)
    return merged, mapping


SOURCE_SECTION = '## 来源'


def replace_source_section(text: str, lines: list) -> str:
    """只重写「## 来源」那一节，别的一个字不动。找不到那一节就原样返回。"""
    head = text.find(SOURCE_SECTION)
    if head == -1:
        return text
    start = text.find('\n''\n', head)
    if start == -1:
        return text
    nxt = text.find('\n' + '## ', start)
    tail = text[nxt:] if nxt != -1 else ''
    return text[:head] + SOURCE_SECTION + '\n\n' + '\n'.join(lines) + '\n' + tail


def refresh_sources(pages_dir: str, card_dirs=None, dry_run: bool = False) -> dict:
    """把已有概念页的「来源」段补齐成**当前的全部来源**。本地，不调模型。

    为什么要有这一趟：实测拉新文章后图谱只"长新节点"，**老页一个字节都不动** —— 新文章
    再讲到 `DeepSeek`，那一页的来源也不会多一条。而 `OPERATIONS.md` 里写着"概念页是累积的、
    卡是快照、概念是账本"。这一趟是让那句话成真。

    **只改正文，不动 frontmatter。** `sources:` 与正文是**故意不一样**的两套名字：
    正文要 Obsidian 能解析（聊天线写 `会话-<名字>`，指向 `Sources/Chat/` 里那张卡），
    而 `sources:` 要 `source_kinds_for` 能在 `output/<线>/<卡名>.md` 找到（聊天线是
    **不带**前缀的卡名）。同步任何一边都会让 `来源/聊天` 这类标签静默失效 —— 实测改
    frontmatter 会让聊天线的可推断性从 100% 掉到 0，而标签只增不减，**看起来仍然是对的**。

    只增不删：页里现有、而当前卡片里找不到出处的行照原样留着（可能是别的线来的），只报数。
    顺序用 `sorted()` 定成**规范形式**（与历史无关、幂等；实测这样只多动 29 张页）。
    """
    merged, mapping = merged_concept_refs(card_dirs)
    by_case = {}
    for name in merged:
        # 库里存在 `claude code.md` 而概念名是 `Claude Code`：精确匹配会让那 408 条无处可去，
        # 而且没人会知道。`collect_dangling_targets` 在同一个坑上栽过一次。
        by_case.setdefault(name.casefold(), name)

    changed, added, old_unknown, bracketed, dotmd, reordered = [], 0, 0, 0, 0, 0
    unmatched = []
    for page in sorted(Path(pages_dir).glob('*.md')):
        text = page.read_text(encoding='utf-8')
        concept = by_case.get(page.stem.casefold())
        refs = merged.get(concept) if concept else None
        if not refs:
            unmatched.append(page.name)
            continue

        # 现有行：**原样保留那一行的文本**（标题在内），只有新追加的才需要重新拼标题
        keep = {}
        for line in text.split('\n'):
            if line.startswith('- [[') and ']] — ' in line:
                keep.setdefault(line[4:line.find(']] — ')], line)

        want, want_set, by_name = [], set(), {}
        for r in refs:
            name = mapping.get(r['file'], r['file'])
            by_name.setdefault(name, r)
            if name not in want_set:
                want_set.add(name)
                want.append(name)
        old_unknown += sum(1 for n in keep if n not in want_set)

        fresh = {}
        for name in want:
            if name in keep:
                continue
            if '[' in name or ']' in name:
                # 连 Obsidian 都解析不了（名字里带方括号），写进去只会多一条断链
                bracketed += 1
                continue
            if name.endswith('.md'):
                # 标题**本身**以 `.md` 结尾（实测 1 条）。写进去会让 `wiki_lint.resolve` 把
                # 它当成"完整文件名"直接去查（`wiki_lint.py:123`），而库里那张卡的真名是
                # `….md.md` —— 于是体检从 0 断链变成 1 条。跳过它，只报数。
                dotmd += 1
                continue
            fresh[name] = '- [[%s]] — %s' % (name, by_name[name]['title'])

        final = [v for _, v in sorted({**keep, **fresh}.items())]
        if final == list(keep.values()):
            continue                                  # 已经是规范形态，一个字都不写
        if not fresh:
            reordered += 1
        changed.append(page.name)
        added += len(fresh)
        if not dry_run:
            page.write_text(replace_source_section(text, final), encoding='utf-8')
    return {'pages': len(changed), 'added': added, 'reordered': reordered,
            'oldUnknown': old_unknown, 'bracketed': bracketed, 'dotmd': dotmd,
            'unmatched': len(unmatched), 'names': len(mapping), 'sample': changed[:5]}


def iter_concept_pages(jobs: list, api_key: str, workers: int = CONCEPT_PAGES_WORKERS,
                       origin: str = ''):
    """并发生成概念页，**按输入顺序**逐个产出 `((name, refs, out_file), result)`。

    生成器而不是列表，理由同 `article_notes.iter_concepts`：整批跑完才返回的话，
    几千次调用期间磁盘上一页都没有，进程一死全部白花。调用方边收边写。

    串行那条路保留原来的 `time.sleep(0.5)`：那是**按篇节流**，只有一次只有一个请求时才
    有意义。并发那条路不再 sleep——同时有 `workers` 个请求在飞，本身就是另一种节奏，
    再加一层按篇等待只会把并发收益吃回去。
    """
    def one(job):
        name, refs, _ = job
        try:
            return generate_concept(name, refs, api_key, origin=origin)
        except Exception as error:
            # **不许静默**。原来这里是 `except Exception: return None`，于是
            # `reference_refs` 少给 `file`/`title` 造成的 KeyError 表现成
            # "尝试 158 个、生成 0 页、**无任何报错**"（2026-09-27 实测，查了一阵子）。
            # 一次调用失败不该拖垮整批，但必须留下痕迹。
            print('  [ERR] %s: %s: %s' % (name, type(error).__name__, error), file=sys.stderr)
            return None

    if workers <= 1:
        for job in jobs:
            yield job, one(job)
            time.sleep(0.5)
        return
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for job, result in zip(jobs, pool.map(one, jobs)):
            yield job, result


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
    parser.add_argument('--fix-source-links', action='store_true',
                        help='只修已有页面"来源"段里指不到文件的链接（本地，不调用模型）')
    parser.add_argument('--refresh-sources', action='store_true',
                        help='只把已有页面的"来源"段补齐成**当前全部来源**（本地，不调用模型）')
    parser.add_argument('--cards', action='append', default=None, metavar='DIR',
                        help='产卡目录，可重复；默认所有 output/*-notes（与索引的引用数同一口径）')
    parser.add_argument('--dry-run', action='store_true',
                        help='只报告会改什么，一个字都不写')
    parser.add_argument('--from-pages', type=int, default=0, metavar='N',
                        help='给"被页面提到最多却没有页"的前 N 个概念建页（参考材料取自页面本身）')
    parser.add_argument('--min-refs', type=int, default=1,
                        help='至少被几篇材料提到才建页（默认 1；2 能滤掉新闻里的一次性实体）')
    parser.add_argument('--workers', type=int, default=CONCEPT_PAGES_WORKERS,
                        help='并发生成几页（默认 %d）；1 = 串行且按篇节流' % CONCEPT_PAGES_WORKERS)
    parser.add_argument('--source', default=SOURCE_ROOT, help='文章目录')
    parser.add_argument('--output', default=OUTPUT_ROOT, help='概念页输出目录')
    args = parser.parse_args()

    if getattr(args, 'relabel', False):
        # 本地重贴标签：不必有 key、不必调模型
        changed = relabel_pages(args.output)
        print('重贴标签：%d 张页面' % changed)
        return

    if getattr(args, 'from_pages', 0):
        # 给图谱里那些"悬空节点"的另一端建页——它们不在卡片语料里，材料只能按字面从
        # 卡片摘要里找（见 `mention_refs`）。
        #
        # 这里**不能引用 `api_key`**：它要到下面才被赋值，而这是同一个函数作用域——
        # 引用一个尚未赋值的局部变量是 UnboundLocalError（2026-09-27 踩过）。
        key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '') or get_api_key(load_config())
        if not key:
            print('这条通道要调模型，需要 key（--api-key 或 DEEPSEEK_API_KEY）', file=sys.stderr)
            sys.exit(1)
        result = build_dangling_pages(args.output, key, args.from_pages, args.workers)
        print('悬空目标 %d 个；本次尝试 %d 个、生成 %d 页、跳过 %d 个'
              % (result['dangling'], result['attempted'], result['generated'], result['skipped']))
        return

    if getattr(args, 'fix_source_links', False):
        # 本地修链接：同上，动的是本地就能算出来的东西，不必把 1,963 页重问一遍
        result = fix_source_links(args.output, args.source)
        print('修来源链接：%d 张页面、%d 条链接（可对照的名字 %d 个）'
              % (len(result['pages']), result['links'], result['names']))
        return

    if getattr(args, 'refresh_sources', False):
        # 本地补来源：让**已有页**也能长出新边（新文章提到老概念时，那一页的来源要多一条）。
        # 与 `--fix-source-links` 同族：都不调模型，都只动来源段。区别是那个**只改行首**、
        # 这个**会追加行**（并且把顺序规范化）。
        result = refresh_sources(args.output, getattr(args, 'cards', None),
                                 dry_run=getattr(args, 'dry_run', False))
        head = '预览（一个字都没写）' if getattr(args, 'dry_run', False) else '已刷新'
        print('%s：%d 张页面有变化（新增 %d 条来源，其中 %d 张只是顺序变了）'
              % (head, result['pages'], result['added'], result['reordered']))
        print('  产卡目录映射表 %d 条；找不到出处的旧来源行 %d 条（**保留不动**）'
              % (result['names'], result['oldUnknown']))
        print('  名字写不得因此没追加的 %d 条（带方括号 %d + 以 .md 结尾 %d）；'
              '当前卡片里没有对应概念的页 %d 张'
              % (result['bracketed'] + result['dotmd'], result['bracketed'],
                 result['dotmd'], result['unmatched']))
        if result['sample']:
            print('  例:', '、'.join(result['sample'][:3]))
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

    # 已有页在**花钱之前**滤掉（见 `build_jobs`：先提交再丢弃 = 为已存在的页付一次费）
    # 另一条线的概念目录一起传进去：一个概念名在库里只能有一张页（见 `sibling_concept_dirs`）
    jobs, skipped, elsewhere = build_jobs(top_concepts, out_dir, sibling_concept_dirs(out_dir))
    done = 0
    # 边收边写：生成器一有结果就落盘，不等整批（见 `iter_concept_pages`）
    for (name, refs, out_file), result in iter_concept_pages(
            jobs, api_key, args.workers, origin=origin_tag(args.source)):
        done += 1
        if not result:
            print(f'  [ERR] {name} 没生成出来（{done}/{len(jobs)}）', file=sys.stderr)
            continue
        print(f'  [{done}/{len(jobs)}] {name} ({len(refs)} 引用)')
        fm, body = result
        write_with_frontmatter(str(out_file), fm, body)
        generated += 1

    note = skip_note(skipped, elsewhere)
    if note:
        print(note)
    print(f'  生成 {generated} 个新概念')

    # Step 4: Index
    print(f'\n=== Step 4: 生成索引 ===')
    concept_files = sorted(out_dir.glob('*.md'))
    # 这一页是**哪条线**的索引：写死 `Wiki/Concepts` 的话，聊天线生成的索引会把图谱筛选框指到文章线去
    vault_relative = '/'.join(out_dir.parts[-2:])
    index_lines = [
        f'# 概念索引（{vault_relative}）',
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
        f'只想看知识页，把图谱左上角的筛选框填成 `path:"{vault_relative}"`；'
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
