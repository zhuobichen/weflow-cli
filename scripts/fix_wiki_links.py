#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 wiki 概念页里"差一点"的断链：只差空格/下划线/连字符/大小写的链接名。

背景：从另一台机器搬来的概念页链接名（如双空格 "Precision Chemistry  铜催化"）
与本机卡片名（单空格）对不上，lint 报几千条断链。名字其实是同一个，只是写法差异。

做法：用 _utils.normalize_concept_name 建"卡片规范形 → 真实卡片名"映射；
遍历概念页的 [[链接]]，仅当：①原名在真实名字集合里不存在（确实断链），
②规范形在映射里**唯一命中**一个真实名（不会误改）时，替换成真实名。

**只改能确定的**；ambiguous 或没命中的一律不动，留给人看。本地、免费、幂等。
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _utils import normalize_concept_name  # noqa: E402

WIKILINK = re.compile(r'\[\[([^\]\|]+)(?:\|[^\]]*)?\]\]')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pages', required=True, help='概念页目录')
    ap.add_argument('--cards', action='append', required=True, help='卡片目录（可重复）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    real = set()
    for d in args.cards:
        p = Path(d)
        if p.is_dir():
            real.update(f.stem for f in p.glob('*.md'))
    norm = defaultdict(set)
    for name in real:
        norm[normalize_concept_name(name)].add(name)

    changed_pages, changed_links, skipped_ambiguous = 0, 0, 0
    samples = []
    for page in sorted(Path(args.pages).glob('*.md')):
        text = page.read_text(encoding='utf-8')
        hits = [0]

        def repl(m):
            nonlocal skipped_ambiguous
            name = m.group(1).strip()
            if name in real:
                return m.group(0)  # 本来就是好的
            key = normalize_concept_name(name)
            targets = norm.get(key, set())
            if len(targets) != 1:
                if len(targets) > 1:
                    skipped_ambiguous += 1
                return m.group(0)  # 没命中/多义：不动
            fixed = next(iter(targets))
            if fixed == name:
                return m.group(0)
            hits[0] += 1
            if len(samples) < 10:
                samples.append(f'{page.name}: [[{name}]] → [[{fixed}]]')
            return m.group(0).replace(name, fixed, 1)

        updated = WIKILINK.sub(repl, text)
        # frontmatter 的 `sources: [名字]` 也修（同一批断链的另一处写法）
        def fm_repl(m):
            nonlocal skipped_ambiguous
            name = m.group(1).strip()
            if name in real:
                return m.group(0)
            targets = norm.get(normalize_concept_name(name), set())
            if len(targets) != 1:
                return m.group(0)
            fixed = next(iter(targets))
            if fixed != name:
                hits[0] += 1
            return m.group(0).replace(name, fixed, 1)
        updated = re.sub(r'sources:\s*\[([^\]]+)\]', fm_repl, updated)

        if hits[0]:
            changed_pages += 1
            changed_links += hits[0]
            if not args.dry_run:
                page.write_text(updated, encoding='utf-8')

    out = {'realNames': len(real), 'changedPages': changed_pages,
           'changedLinks': changed_links, 'ambiguousSkipped': skipped_ambiguous,
           'samples': samples}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f'真实卡片名 {len(real)} 个；改了 {changed_links} 条链接（{changed_pages} 张页）；'
              f'多义跳过 {skipped_ambiguous} 条')
        for s in samples:
            print('  ' + s)
        if args.dry_run:
            print('（dry-run，没写文件）')


if __name__ == '__main__':
    main()
