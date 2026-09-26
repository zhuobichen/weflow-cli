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
from _utils import parse_frontmatter  # noqa: E402

DEFAULT_PAGES_DIR = 'output/wechat-vault/Wiki/Concepts'
# 卡片在哪（`## 来源` 那节的 `[[<相对路径>.md]]` 要从这些目录里找）
CARD_DIRS = ('output/article-notes', 'output/chat-notes')
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


def resolve(name: str, page_stems: set, card_dirs) -> bool:
    """这个链接指向的东西存在吗。

    - 带路径或以 `.md` 结尾的 → 是**来源卡片**，去卡片目录里找；
    - 其余 → 是**概念**，看有没有同名页面。
    """
    if name.endswith('.md') or '/' in name:
        target = name if name.endswith('.md') else name + '.md'
        return any((Path(d) / target).exists() for d in card_dirs)
    return name in page_stems


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
    """标题高度相似的页——**同一件事被多个公众号转发**的痕迹。

    实测那批里有一组四条：《中国建筑集团有限公司党组成员、副总经理陈勇被查》《中建集团副总经理陈勇被查》
    《打虎！中建集团副总经理陈勇，被查》《打虎！陈勇被查》——同一件事四个来源，于是相关概念的
    引用数被灌到榜首（`sources` 那栏能直接看到）。这只是**报告**，不自动去重：
    不同公众号对同一件事的写法可能确实各有信息，删哪张该由人定。
    """
    groups = []
    for i, left in enumerate(pages):
        for right in pages[i + 1:]:
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


def collect(pages_dir: str, card_dirs) -> list:
    pages = []
    for path in sorted(Path(pages_dir).glob('*.md')):
        if path.name == '00-Overview.md':      # 索引页不是概念，它引用的东西另算
            continue
        frontmatter, body = parse_frontmatter(path.read_text(encoding='utf-8'))
        pages.append({'stem': path.stem,
                      'topic': compile_frontmatter_topic(frontmatter),
                      'title': str(frontmatter.get('title') or '').strip('"'),
                      'links': extract_links(body),
                      'body': body})
    return pages


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='知识库体检（本地，不联网、不调用模型）')
    parser.add_argument('--dir', default=DEFAULT_PAGES_DIR, help='概念页目录')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    pages_dir = Path(args.dir)
    if not pages_dir.exists():
        message = '没有概念页（%s 不存在）——先跑 article-notes / chat-notes，再跑 wiki compile' % args.dir
        print(json.dumps({'success': False, 'error': message}, ensure_ascii=False)
              if args.json else message)
        return 1

    pages = collect(args.dir, CARD_DIRS)
    card_links = collect_cards(CARD_DIRS)
    report = inspect(pages, lambda name: resolve(name, {p['stem'] for p in pages}, CARD_DIRS), card_links)
    report['success'] = True
    report['pagesDir'] = args.dir
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
        print('\n标题高度相似的页 %d 组（**同一件事可能被转发多次**，会让概念排名灌水）：'
              % len(report['nearDuplicates']))
        for pair in report['nearDuplicates'][:4]:
            print('  %s ／ %s' % (pair[0][:28], pair[1][:28]))
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
