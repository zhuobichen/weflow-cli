#!/usr/bin/env python3
"""
知识库体检 — 概念页的死链、孤儿与空页。**全本地：不联网、不调用任何模型。**

用法:
  python scripts/wiki_lint.py              # 人看的报告
  python scripts/wiki_lint.py --json       # 机器读的

查四样（前两样就是 D-049 里记着"没做"的那两件事）：
- **死链**：页面里的 `[[X]]` 指向一个不存在的页面。概念页之间互链，以及 `## 来源` 那节
  指回卡片——卡片被删或改名，链接就断了，而**看页面本身看不出来**。
- **孤儿**：没有任何页面链接到它。多半意味着它聚出来的概念名跟别处对不上
  （同一个东西写成两个名字），是"该合并"的信号。
- **空页**：没有定义也没有要点（正文几乎为空）。模型那次返回不完整时会留下这种。
- **同名**：两页标题相同（文件名不同）——会把同一个概念劈成两份。

为什么值得有：生成那一步（`wiki compile`）**不会**告诉你这些。它按概念名写文件，
写完就算成功；而"写进去的东西连不连得起来"是另一件事。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import CONCEPT_DIRS, parse_frontmatter  # noqa: E402

DEFAULT_PAGES_DIR = 'output/wechat-vault/Wiki/Concepts'
VAULT_ROOT = 'output/wechat-vault'
# 体检要看的**全部**概念页目录（Vault 相对路径那份清单在 `_utils.CONCEPT_DIRS`）。
VAULT_CONCEPT_DIRS = tuple(str(Path(VAULT_ROOT) / d) for d in CONCEPT_DIRS)
# 链接可能指向的「材料」层（与卡片目录一起参与可解析性判断）
MATERIAL_DIRS = tuple(str(Path(VAULT_ROOT) / d) for d in
                      ('002_Literature', '001_Daily', 'Sources/Chat', 'Sources/WeChat'))
# 卡片在哪（`## 来源` 那节的 `[[<相对路径>.md]]` 要从这些目录里找）。
# **用通配而不是写死目录名**：今天是文章线与对话线，明天再加一条来源（收藏线就是这么加的），
# 写死的话体检会**静默漏算**那条线的入链——而漏算的表现是"每张新页都是孤儿"。
CARD_DIRS = tuple(str(path) for path in sorted(Path('output').glob('*-notes'))) or (
    'output/article-notes', 'output/chat-notes')
# 少于这个字数就算空页（概念页有定义+要点，正常几百字）
MIN_BODY_CHARS = 80

_LINK = re.compile(r'\[\[([^\]]+)\]\]')


def compile_frontmatter_topic(frontmatter: dict) -> str:
    """页面的 `topics`（生成时写进去的）取第一个——体检只关心它退不退化。"""
    raw = frontmatter.get('topics') or frontmatter.get('topic') or ''
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else ''
    return str(raw).strip().strip('[]').strip()


def extract_links(body: str) -> list:
    """页面里所有 `[[…]]` 的名字。

    **故意不过滤**（不像生成那侧会滤掉路径与主题词）：体检查的是"链接连不连得起来"，
    滤掉就等于把断掉的链接藏起来——那正好是它要找的东西。
    """
    return [name.strip() for name in _LINK.findall(body or '') if name.strip()]


def links_by_section(body: str) -> list:
    """每条链接连同它所在的 `## 小节` —— 因为**两种死链的意义完全不同**。

    `## 相关概念` 里的链接是模型提出的"这个概念还该跟谁连"，而只有被引用最多的那些
    才真的建了页。所以那一节里的"还没有页"是**扩张候选**，不是坏掉的东西；
    而 `## 来源` 里的 `[[<卡片>.md]]` 断了就是**真断了**（卡片被删或改名）。
    一视同仁地报"40 条死链"，看的人只会以为这里烂掉了。
    """
    section, out = '', []
    for line in (body or '').split('\n'):
        if line.strip().startswith('## '):
            section = line.strip()[3:].strip()
            continue
        for name in _LINK.findall(line):
            name = name.strip()
            if name:
                out.append((section, name))
    return out


def collect_card_stems(card_dirs) -> set:
    """卡片文件名（不带 `.md`）。`## 来源` 那节的链接就是这些名字。"""
    stems = set()
    for directory in card_dirs:
        root = Path(directory)
        if root.exists():
            stems |= {p.stem for p in root.rglob('*.md')}
    return stems


def collect_cards(card_dirs) -> list:
    """卡片里的链接——它们才是概念页**真正的**入链（每张卡都链着它提炼出的概念）。"""
    links = []
    for directory in card_dirs:
        root = Path(directory)
        if not root.exists():
            continue
        for path in root.rglob('*.md'):
            try:
                _, body = parse_frontmatter(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            links.extend(extract_links(body))
    return links


def resolve(name: str, page_stems: set, card_dirs, card_stems=None) -> bool:
    """这个链接指向的东西存在吗。

    - 带路径或以 `.md` 结尾的 → 是**来源卡片**，去卡片目录里找；
    - 否则 → ⚠️ **既可能是概念，也可能是卡片**。

    这里原来写的是"裸名字就是概念"，而 `## 来源` 那节的链接**就是裸的卡片名**
    （`compile_wiki --fix-source-links` 把 `.md` 去掉了，与全库其余链接统一）。
    于是那条判据把 7,872 条**在 Obsidian 里解析得好好的**链接全报成了断链。

    `card_stems` 为空时退回旧行为，好让调用方不必一次性全改。
    """
    if name.endswith('.md') or '/' in name:
        target = name if name.endswith('.md') else name + '.md'
        return any((Path(d) / target).exists() for d in card_dirs)
    if name in page_stems:
        return True
    return name in (card_stems or set())


RELATED_SECTIONS = ('相关概念', '相关主题', '相关')


def inspect(pages: list, exists, card_links=()) -> dict:
    """纯函数：给定页面、"链接是否存在"的判据、以及卡片里的入链，报出问题。

    - `broken`：**真断了的**链接——指向不存在的卡片（卡片删了/改名了）；
    - `aspirational`：`## 相关概念` 那节里指向"还没建页"的概念——**扩张候选，不是错误**；
    - `orphans`：连卡片都没提到过它的页面（卡片链接也算入链，否则每张页都会被误报成孤儿）；
    - `empty`：没有定义也没有要点；
    - `duplicateTitles`：同名页。
    """
    stems = {page['stem'] for page in pages}
    inbound = {stem: 0 for stem in stems}
    for name in card_links:                       # 卡片 → 概念，这是主要入链
        if not (name.endswith('.md') or '/' in name) and name in inbound:
            inbound[name] += 1
    broken, aspirational, empty = [], [], []
    for page in pages:
        for section, name in links_by_section(page['body']):
            if exists(name):
                if not (name.endswith('.md') or '/' in name) and name in inbound:
                    inbound[name] += 1
                continue
            entry = {'page': page['stem'], 'target': name, 'section': section}
            if any(section.startswith(prefix) for prefix in RELATED_SECTIONS):
                aspirational.append(entry)
            else:
                broken.append(entry)
        if len(page['body'].strip()) < MIN_BODY_CHARS:
            empty.append({'page': page['stem'], 'chars': len(page['body'].strip())})
    orphans = sorted(stem for stem, count in inbound.items() if count == 0)
    titles = {}
    for page in pages:
        titles.setdefault(page['title'] or page['stem'], []).append(page['stem'])
    duplicates = {title: stems for title, stems in titles.items() if len(stems) > 1}
    return {'pages': len(pages), 'broken': broken, 'aspirational': aspirational,
            'orphans': orphans, 'empty': empty, 'duplicateTitles': duplicates,
            'degenerateFields': degenerate_fields(pages),
            'nearDuplicates': near_duplicate_titles(pages)}


# 一个字段有 ≥ 这么多页、且某一个值占了 ≥ 这个比例，就当成"退化了"（等于没携带信息）
DEGENERATE_MIN_PAGES = 5
DEGENERATE_RATIO = 0.8


def longest_common_run(left: str, right: str) -> int:
    """两串的最长公共子串长度（中文里"同一件事被转发多次"的信号）。"""
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for i in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        for j in range(1, len(right) + 1):
            if left[i - 1] == right[j - 1]:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


DUP_RUN_CHARS = 5          # 公共子串至少这么长
DUP_COVER_RATIO = 0.4      # 而且占较短标题的这么一大部分


def near_duplicate_titles(pages: list) -> list:
    """标题高度相似的页——**同一件事被多个公众号转发**的痕迹。只报告，不自动去重。

    这条判据**有两处已知的失效，都是 2026-09-28 量出来的**，改判据前先看这里：

    1. **它抓不住它自己当初的动机用例**。写这个函数是因为《打虎！陈勇被查》与
       《中建集团副总经理陈勇被查》是同一件事 —— 但那是"同一人、换了标题"，两串的最长公共
       子串只有 4 字（"陈勇被查"），**任何子串判据都抓不到**。当年那段注释把这条写成了
       "实测那批里有一组四条"，是**不成立的**：实测那四个人两两之间只有 2 对满足判据。
    2. **它在今天的规模上退化成了噪音**。判据（公共子串 ≥5 字、且占较短标题 ≥40%）是
       5,062 张页、长中文新闻标题时定的；现在 22,374 张页里有很多**短名字**
       （`AI Agent开发框架` 与 `生物信息学LLM Agent综述` 共享 "Agent"），于是报出
       **424,697 组**，前几条全是这类巧合。这个数不是"转发灌水"，是判据失去了分辨力。

    抽 20 组复核判据本身都成立（没有假阳性），所以问题在**门槛**不在实现。要收紧的话，
    "规范化后一个标题包含另一个（短的 ≥8 字）"实测 1,234 组、看着都是真的相似
    （`世界人工智能大会` / `2026世界人工智能大会`），代价是**换一类漏检**（同样抓不到第 1 条
    那种换标题的写法）。哪个更合用于"关系图谱该不该合并"，得由人定 —— 所以这里不动，
    只把失效写在看得见的地方。
    """
    # **按 5-gram 分桶，而不是两两比。** 判据是"最长公共子串 ≥ DUP_RUN_CHARS"，那**等价于**
    # "两者共享一个这么长的子串" —— 所以只把共享某个 5-gram 的页放进同一个桶里两两比，
    # **结果与全量两两比完全一样**，只是不再做那 2.5 亿次注定失败的比较。
    # 实测：5,062 张页全量要 1-2 分钟；22,445 张是 20 倍的对数（几十分钟），分桶后几秒。
    index = {}
    for page in pages:
        name = page['title'] or page['stem']
        if len(name) < DUP_RUN_CHARS:
            # 比 5 字符还短的名字不可能满足"公共子串 ≥5"（子串不可能比它自己长），
            # 原判据对它们也从不命中
            continue
        for gram in {name[i:i + DUP_RUN_CHARS] for i in range(len(name) - DUP_RUN_CHARS + 1)}:
            index.setdefault(gram, []).append(page)
    groups, seen = [], set()
    for bucket in index.values():
        for i, left in enumerate(bucket):
            for right in bucket[i + 1:]:
                pair = (id(left), id(right))
                if pair in seen:
                    continue          # 一对可能共享多个 5-gram，会落进多个桶
                seen.add(pair)
                a, b = left['title'] or left['stem'], right['title'] or right['stem']
                run = longest_common_run(a, b)
                if run >= DUP_RUN_CHARS and run >= DUP_COVER_RATIO * min(len(a), len(b)):
                    groups.append([a, b])
    return groups


def degenerate_fields(pages: list) -> dict:
    """**退化字段**：取值几乎只有一种。

    实测：39 张概念页的 `topics` **全是「学术」**——上游那个主题分类器把最近这批语料
    全扔进一个桶（另一处独立测量：标题像新闻的 224 篇里 40 篇也被标成「学术」）。
    一个字段整列同一个值，看起来像"有元数据"，实际什么也没说；而**它会被照着信**。
    这条检查的作用不是修分类器（那是日报那条线的事），而是让这种退化**在页面上看得见**。
    """
    if len(pages) < DEGENERATE_MIN_PAGES:
        return {}
    out = {}
    # 只查**概念页上真有、而且"看起来像元数据"**的字段。别去查 `source`——概念页用的是复数
    # `sources`，查一个不存在的字段只会报出一句"(全空)"，那是拿噪音换注意力。
    for field in ('topic',):
        values = [str(page.get(field) or '').strip() for page in pages]
        values = [value for value in values if value]
        if not values:
            continue
        counts = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        top, count = max(counts.items(), key=lambda item: item[1])
        if count / len(pages) >= DEGENERATE_RATIO:
            out[field] = {'值': top, '页数': count, '总页数': len(pages)}
    return out


def resolvable_names(pages: list) -> set:
    """体检认为"指向它就算解析成功"的所有名字：页的 stem **加上别名**。

    `--merge-duplicates` 把 `GPT-5.6` 并进 `GPT 5.6` 时，就是靠 aliases 让旧链接不断的
    （实测库里 98 条），Obsidian 认它。体检要是不认，会把这些名字报成"还没建页、
    建议再跑 compile 提高 --limit" —— 而它们已经有页了，那是**误导**。
    """
    names = set()
    for page in pages:
        names.add(page['stem'])
        names |= set(page.get('aliases') or [])
    return names


def collect(pages_dir: str, card_dirs) -> list:
    pages = []
    for path in sorted(Path(pages_dir).glob('*.md')):
        if path.name == '00-Overview.md':      # 索引页不是概念，它引用的东西另算
            continue
        frontmatter, body = parse_frontmatter(path.read_text(encoding='utf-8'))
        raw_aliases = frontmatter.get('aliases') or []
        if isinstance(raw_aliases, str):
            raw_aliases = [x for x in raw_aliases.strip('[]').split(',')]
        pages.append({'stem': path.stem,
                      'topic': compile_frontmatter_topic(frontmatter),
                      'title': str(frontmatter.get('title') or '').strip('"'),
                      # **别名也算"指向这一页的名字"**：`--merge-duplicates` 把
                      # `GPT-5.6` 并进 `GPT 5.6` 时就是靠 aliases 让旧链接不断的，
                      # Obsidian 认它。体检不认的话，会把这些名字报成"还没建页、
                      # 建议再跑 compile" —— 而它们已经有页了。
                      'aliases': [str(a).strip().strip('"').strip("'") for a in raw_aliases],
                      'links': extract_links(body),
                      'body': body})
    return pages


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='知识库体检（本地，不联网、不调用模型）')
    # **默认体检全部概念页目录**（文章一个、聊天一个，见 `_utils.CONCEPT_DIRS`）。
    # 这条一旦只读一个，分出去的那一半就会"体检通过"——因为它根本没被看。
    parser.add_argument('--dir', action='append', default=[],
                        help='概念页目录（可重复；默认 %s）'
                             % '、'.join(VAULT_CONCEPT_DIRS))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    dirs = args.dir or VAULT_CONCEPT_DIRS
    existing = [d for d in dirs if Path(d).exists()]
    if not existing:
        message = ('没有概念页（%s 都不存在）——先跑 article-notes / chat-notes，'
                   '再跑 wiki compile' % '、'.join(dirs))
        print(json.dumps({'success': False, 'error': message}, ensure_ascii=False)
              if args.json else message)
        return 1

    pages = [page for d in existing for page in collect(d, CARD_DIRS)]
    card_links = collect_cards(CARD_DIRS)
    # **链接可能指向材料，而不是卡片。** `## 来源` 指的是「这一页是从哪来的」：
    # 文章线指向 `002_Literature` 里的阅读笔记，聊天线指向 `Sources/Chat` 里的卡。
    # 只查卡片目录会把这类全报成断链——实测 1,950 条，而它们在 Obsidian 里都是好的
    # （两边文件名本来就不同：卡片保留完整标题，阅读笔记截到 50 字）。
    card_stems = collect_card_stems(tuple(CARD_DIRS) + MATERIAL_DIRS)
    report = inspect(pages,
                     lambda name: resolve(name, resolvable_names(pages), CARD_DIRS, card_stems),
                     card_links)
    report['success'] = True
    report['pagesDir'] = existing
    report['cardLinks'] = len(card_links)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print('概念页 %d 张（卡片侧入链 %d 条）' % (report['pages'], report['cardLinks']))
    if report['broken']:
        print('\n**断链 %d 条**（指向不存在的卡片——卡片删了或改名了）：' % len(report['broken']))
        for item in report['broken'][:10]:
            print('  %s → %s' % (item['page'], item['target']))
        print('  改名字就统一，删掉了就把那行去掉')
    if report['orphans']:
        print('\n孤儿页 %d 张（连卡片都没提到过它）：' % len(report['orphans']))
        for name in report['orphans'][:10]:
            print('  %s' % name)
        print('  多半意味着它已经不被任何来源支持了')
    if report['empty']:
        print('\n空页 %d 张（没有定义或要点）：' % len(report['empty']))
        for item in report['empty'][:10]:
            print('  %s（%d 字）' % (item['page'], item['chars']))
        print('  重新跑一次 wiki compile 通常就好了（那次模型返回不完整）')
    if report['duplicateTitles']:
        print('\n同名页 %d 组：' % len(report['duplicateTitles']))
        for title, stems in list(report['duplicateTitles'].items())[:5]:
            print('  %s ← %s' % (title, '、'.join(stems)))
    if report['aspirational']:
        names = sorted({item['target'] for item in report['aspirational']})
        print('\n还没建页的相关概念 %d 个（**扩张候选，不是错误**）：%s'
              % (len(names), '、'.join(names[:12])))
        print('  想要它们就再跑 wiki compile（提高 --limit），或把它们当下一步的线索')
    if report.get('nearDuplicates'):
        count = len(report['nearDuplicates'])
        # 这行必须带劝阻：判据在今天的规模上几乎对谁都成立（2026-09-28 实测 424,697 组，
        # 前几条是 `AI Agent开发框架` / `生物信息学LLM Agent综述` 这种共享 "Agent" 的巧合）。
        # 不写清楚，这个数会被当成"有 42 万对重复该合并"。
        print('\n标题高度相似的页 %d 组（**判据偏松，规模一大就失去分辨力，当成线索别当结论**）：'
              % count)
        for pair in report['nearDuplicates'][:4]:
            print('  %s ／ %s' % (pair[0][:28], pair[1][:28]))
        if count > 10000:
            print('  判据是"公共子串 ≥5 字且占较短标题 ≥40%"，短名字一多就会互相命中；'
                  '真要按它去重，先把门槛收紧（见 wiki_lint.near_duplicate_titles 的说明）')
        else:
            print('  只报告不去重：不同公众号的写法可能各有信息，删哪张该由人定')
    if report.get('degenerateFields'):
        print('\n退化字段（整列几乎同一个值，等于没携带信息）：')
        for field, info in report['degenerateFields'].items():
            print('  %s: %s（%s/%s 页）' % (field, info['值'], info['页数'], info.get('总页数', report['pages'])))
        print('  多半是上游分类器把整批语料扔进了一个桶——不改分类器的话，别信这个字段')
    if not any((report['broken'], report['orphans'], report['empty'], report['duplicateTitles'])):
        print('\n没有需要修的问题（断链/孤儿/空页/同名）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
