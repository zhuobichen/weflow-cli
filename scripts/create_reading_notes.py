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

TOPIC_ORDER = ['AI', '学术', '新闻', '文学', '投资', '政治']

# ====== V2 模板 ======

# 阅读笔记里"相关文章"那一段的查询。**单独抽出来，是因为它有两个使用方**：模板（写新笔记）
# 与 `refresh_dataview_blocks()`（重写老笔记）。写成两份就会漂移，而漂移的表现是
# "新笔记的查询是对的、老笔记的还是坏的"——不报错。
#
# 三处修正（2026-09-27，都是实测出来的坏查询）：
# - `SORT date DESC` → `SORT published DESC`：笔记里**没有 `date` 这个字段**（是 `published`），
#   原查询排不出来。
# - 加了 `file.name != this.file.name`：否则一篇笔记的"相关文章"里会列出它自己。
# - 首列从 `rating` 改成 `published`：`rating` 是留给用户打分的，全库默认空；
#   拿它当首列，表格十行全是空格。
# - `contains(hasTopic, "…")` → **`contains(string(hasTopic), "…")`**：笔记里写的是
#   `hasTopic: [[新闻]]`，而 YAML 把它解析成**嵌套列表** `[['新闻']]`——对它做
#   `contains(…, "新闻")` 是在拿字符串比一个子列表。外面套一层 `string()` 对形状不敏感
#   （字符串、列表、链接都成立），代价是万一有主题名互为子串会多匹配——本库的六个主题
#   （AI/学术/新闻/文学/投资/政治）之间没有这种关系。
RELATED_QUERY = '''```dataview
TABLE published AS 日期, source AS 来源, rating
FROM "002_Literature"
WHERE contains(string(hasTopic), "{topic}") AND file.name != this.file.name
SORT published DESC
LIMIT 10
```'''

# 日记里"概念连接"那一段的查询。**判日期必须用 `published`（文章日期），不能用 `created`**：
# `created` 是**生成这篇笔记的日期**，而一次回填会把几万篇的 `created` 全写成同一天
# （实测：25,676 篇全是 2026-09-26）。用 `created` 判，结果是 175 天的日记全空、
# 唯独生成那天把两万多篇一次性列出来。
DAILY_QUERY = '''```dataview
LIST
FROM "002_Literature"
WHERE published = date("{date}")
```'''

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

{related_query}

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

{daily_query}

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
    # **这里原先写的是 `007_Wiki/Concepts`，2026-09-27 改成顶层的 `Wiki/Concepts`。**
    # 概念页一直实际写在顶层（`compile_wiki.OUTPUT_ROOT`，另有 `vault_rag` / `vault_search` /
    # `wiki_lint` / 助手的知识检索 / CLI 两个选项共 6 处读那里），模板却声明在 007_Wiki，
    # 于是每次 init 都建出一个**永远空着**的 `007_Wiki/`——用户在 Obsidian 里看到它，
    # 得到的结论是"知识库没更新"。一条路径写在两处就会这样：不报错，只是一个目录永远空着。
    # `test/compile_wiki_test.py` 现在钉住两者必须一致。
    # **两个知识库各一个目录**（用户 2026-09-27 要求分开）：`Wiki/Concepts` 是公众号文章线的，
    # `Chat/Concepts` 是聊天线的。这里的清单要与 `_utils.CONCEPT_DIRS` 一致——有测试盯着。
    'Wiki/Concepts',
    'Chat/Concepts',
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
    # **界面残留要在这里清掉**：正文来自微信，带着阅读器自己的文字与文末的原创标记。
    # 实测 1633 篇里 330 篇的摘要在这一步之前就混进了它（写笔记这条路此前没清洗）。
    from _utils import strip_wx_ads
    lines = [strip_wx_ads(line) for line in lines]
    lines = [line for line in lines if line]
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
        # 查询要先自己 format 好再传进去：`str.format` **不会**回头再扫一遍替换进去的值，
        # 直接传带 `{topic}` 的原文，那对大括号会原样留在笔记里。
        related_query=RELATED_QUERY.format(topic=topic),
    )
    note_path.write_text(note_content, encoding='utf-8')
    return 'created', title


DV_BLOCK_RE = re.compile(r'```dataview\b.*?```', re.DOTALL)


def refresh_dataview_blocks(vault: str) -> dict:
    """把**已有**笔记里的 dataview 查询重写成上面那两份当前模板。**本地重算，不调用模型。**

    为什么非要有它：`create_reading_note()` 遇到已存在的笔记是 `skip`，所以改模板只影响
    **以后**生成的笔记——库里已有的那批会永远停在旧查询上。2026-09-27 修那两处坏查询时，
    库里已经有 25,852 个文件带着它们（阅读笔记 25,676 + 日记 176），光改模板一个都修不到。

    用哪一份按笔记的位置定：
    - `001_Daily/<日期>.md` → 日记那份，日期取自文件名；
    - `002_Literature/**` → 阅读笔记那份，主题取自 frontmatter 的 `topic`/`hasTopic`。

    **判不出来就不动**（没主题、没查询块），并分类计数报出来——不猜、不静默。
    """
    vault_path = Path(vault)
    targets = []
    daily_dir = vault_path / '001_Daily'
    if daily_dir.is_dir():
        targets += [('daily', p, p.stem) for p in sorted(daily_dir.glob('*.md'))]
    literature = vault_path / '002_Literature'
    if literature.is_dir():
        targets += [('note', p, None) for p in sorted(literature.rglob('*.md'))]

    rewritten, no_block, skipped, topicless = [], [], [], []
    for kind, path, value in targets:
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            # 读不出来就**不碰它**并计数。`UnicodeDecodeError` 要一起接住：它不是 OSError，
            # 漏了它就会让一个坏文件把整趟重写打断——而这一趟要处理两万多个文件。
            skipped.append(path.name)
            continue
        if kind == 'note':
            frontmatter, _ = parse_frontmatter(content)
            # **必须先取 `raw[0]`**。笔记里写的是 `hasTopic: [[新闻]]`，YAML 把它解析成
            # **嵌套列表** `[['新闻']]`；直接 `str()` 整个列表会得到 `"['[新闻]']"`，
            # 剥掉方括号就成了 `"'[新闻]'"`——一个带着引号和方括号的主题值被塞进查询，
            # 而且**不报错**，表格只是永远空着。2026-09-27 我第一版就是这么写的，
            # 25,669 篇被写坏。
            #
            # 这段与 `compile_wiki.article_topic` 是同一条规则。**没有直接调它**，是因为
            # `create_reading_notes` 刻意不引 `_utils`（本仓库记过：加了导入边会让命令能否
            # 运行取决于调用方的工作目录），而 `compile_wiki` 会传递地引入它。
            raw = frontmatter.get('topic') or frontmatter.get('hasTopic') or ''
            if isinstance(raw, (list, tuple)):
                raw = raw[0] if raw else ''
            value = str(raw).strip().strip('[]').strip()
            # **主题为空也照改，只是单独计数**：那 7 篇（实测）本来就是空主题，
            # 它们的查询里写着 `hasTopic, ""`。跳过它们等于让它们继续留着
            # `SORT date` 那个排不出来的写法——而"语法对、结果如实为空"比
            # "语法错、看不出为什么"要好。计数是给人看的：这 7 篇确实没有主题。
            if not value:
                topicless.append(path.name)
            fresh = RELATED_QUERY.format(topic=value)
        else:
            fresh = DAILY_QUERY.format(date=value)
        if not DV_BLOCK_RE.search(content):
            no_block.append(path.name)
            continue
        updated = DV_BLOCK_RE.sub(lambda _match: fresh, content, count=1)
        if updated != content:
            path.write_text(updated, encoding='utf-8')
            rewritten.append(path.name)
    return {'rewritten': rewritten, 'noBlock': no_block,
            'skipped': skipped, 'topicless': topicless}


SUMMARY_BLOCK_RE = re.compile(r'(##\s*[^\n]*摘要\s*\n)(.*?)(?=\n##\s|\Z)', re.DOTALL)


def clean_summaries(vault: str) -> dict:
    """把已有笔记**摘要段**里的微信界面残留洗掉。本地，不调模型。

    为什么需要单独一趟：清洗是**读时做**的（`create_reading_notes` 生成摘要时才调
    `strip_wx_ads`），所以**那条逻辑生效之前写好的笔记永远不会被清** —— 实测 25,676 篇
    里有 1,484 篇的摘要带着 `继续滑动看下一个`、`去阅读`、`轻触阅读原文` 这些**页面按钮
    文字**（不是文章内容）。改生成逻辑只影响以后写的笔记，与 `--refresh-queries` 同一个
    道理。好消息是不会有新的：新写的本来就干净。

    **只动摘要段，正文一个字不碰。** 正文是原文，清它等于改素材 —— 而残留对正文的害处
    只是"翻原文时看着乱"，不值这个代价。
    """
    from _utils import _AD_PHRASES, strip_wx_ads
    # **只扫阅读笔记那一层**（`002_Literature`），与 `refresh_dataview_blocks` 同一套目录。
    # 第一版写的是 `Path(vault).rglob('*.md')` —— 那会扫**整个库**，实测把 `Sources/` 的
    # 10,949 篇**原文**也洗了（2026-09-28）。那些原文是素材，清洗它们不是这条命令的职责：
    # 下游生成读的是笔记（`create_reading_notes` 已经读过原文并清过摘要），所以动素材
    # 只有"翻原文时看着干净"这一个好处，却要付"改素材"的代价。
    cleaned, untouched, skipped = [], [], []
    root = Path(vault) / '002_Literature'
    for path in sorted(root.rglob('*.md')):
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            skipped.append(path.name)
            continue
        match = SUMMARY_BLOCK_RE.search(text)
        if not match:
            continue
        segment = match.group(2)
        # **判据必须是"真的含界面短语"，不能是"文本变了"**：`strip_wx_ads` 末尾还有空白
        # 规整与 `.strip()`，任何首尾带空白的段落都会"变"，拿它当判据会全量重写（2026-09-28
        # 我在测量时正好踩过这一脚：判据恒真，于是报出 25,676 篇全部命中）。
        if not any(phrase in segment for phrase in _AD_PHRASES):
            untouched.append(path.name)
            continue
        fresh = strip_wx_ads(segment)
        if fresh == segment:
            untouched.append(path.name)
            continue
        path.write_text(text[:match.start(2)] + fresh + text[match.end(2):], encoding='utf-8')
        cleaned.append(path.name)
    return {'cleaned': cleaned, 'untouched': untouched, 'skipped': skipped}


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='创建 Obsidian 阅读笔记 V2')
    parser.add_argument('--date', help='日期 YYYY-MM-DD（--refresh-queries 时不需要）')
    parser.add_argument('--source', default=DEFAULT_SOURCE, help='文章目录')
    parser.add_argument('--vault', default=DEFAULT_VAULT, help='Vault 目录')
    parser.add_argument('--clean-summaries', action='store_true',
                        help='只把已有笔记摘要段里的微信界面残留洗掉（本地，不调模型）')
    parser.add_argument('--refresh-queries', action='store_true',
                        help='只做这件事：把已有笔记里的 dataview 查询重写成当前模板（本地，不调模型）')
    args = parser.parse_args()

    if args.clean_summaries:
        # 本地清洗：动的是本地就能算出来的东西，不必把两万多篇重问一遍
        result = clean_summaries(args.vault)
        print('清洗摘要：%d 篇；本来就干净、没动的 %d 篇；读不出来的 %d 篇'
              % (len(result['cleaned']), len(result['untouched']), len(result['skipped'])))
        for name in result['skipped'][:5]:
            print('   跳过:', name[:70])
        return

    if args.refresh_queries:
        result = refresh_dataview_blocks(args.vault)
        print('重写 %d 篇；没有查询块、没动的 %d 篇；读不出来的 %d 篇'
              % (len(result['rewritten']), len(result['noBlock']), len(result['skipped'])))
        if result['topicless']:
            print('   其中主题为空（照改，但表会是空的）: %d 篇' % len(result['topicless']))
        for name in result['skipped'][:5]:
            print('   跳过:', name[:70])
        return

    if not args.date:
        print('[ERROR] 要 --date（或用 --refresh-queries 只重写查询）')
        sys.exit(1)

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
            daily_query=DAILY_QUERY.format(date=args.date),
        )
        daily_path.write_text(daily_content, encoding='utf-8')
        print(f'  日记: {daily_path}')


if __name__ == '__main__':
    main()
