#!/usr/bin/env python3
"""给「悬空概念」补页 —— **零模型**：只用卡片正文里的原句拼，不生成、不编造。

为什么不用模型：悬空目标里大量名字在卡片语料里**一条材料都没有**（`compile_wiki.py` 的
注释记过：`提示工程` 被 27 张页指向，卡片里 0 条）。对没有材料的目标，任何"生成"都是编，
而这批页会被当成知识长期引用。所以这里只做**抽取式**：

    定义      ← 第一条提到它的原文句子（带出处）
    关键要点  ← 前 3 条原文句子（带出处）
    相关概念  ← 不做（没有可靠来源时宁可不连）
    来源      ← 所有引用到的卡片（账本，不截断）

没有材料的名字**直接跳过**，并在统计里报出来 —— "补不了"本身是信息。

用法：
    python scripts/fill_dangling.py --limit 20 --dry-run      # 预览
    python scripts/fill_dangling.py --limit 200 --apply       # 真写
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import compile_wiki as cw  # noqa: E402


import re as _re

_SPLIT = _re.compile('(?<=[。！？；\\n])')


_DEF_HINT = _re.compile(r'^(?:是|指|叫|即|属于|是一款|是一种|是一个|指的是)')


def _squash_repeat(text: str) -> str:
    """把句内重复的片段压掉。

    实测出现过半句重复两次（卡片摘要是从正文抄的，正文自己就重复过）：
        "大家好，我是老章 DeepSeek-X 正式上线…  大家好，我是老章 DeepSeek-X 正式上线…"
    同一句读两遍比少一句更糟——读者会以为原文如此。
    """
    # 找最短的重复单元：至少 12 字的一段，紧随其后又出现一次
    m = _re.search(r'(.{12,}?)\1', text)
    if m:
        text = text.replace(m.group(1) + m.group(1), m.group(1))
    return text


def _norm(text: str) -> str:
    return _re.sub(r'[\s，。、；：！？,.;:!?（）()【】\[\]{}"\'「」『』·—～~*]', '', text)


def _is_definition(sentence: str, name: str) -> bool:
    """这句话像不像"在解释这个名词"？

    判据只用文本规则（零模型）：句子**以名字开头**，或名字后面紧跟"是/指/叫/即…"。
    只"提到名字"的句子（"…好多人还体验不了，这不 Workbuddy 来了"）不当定义——
    它们对"这是什么"没有信息量。
    """
    idx = sentence.find(name)
    if idx < 0:
        return False
    if sentence[:idx].strip(' ，。、；：') == '':
        return True
    tail = sentence[idx + len(name):idx + len(name) + 12].lstrip(' ，。、；：*"')
    return bool(_DEF_HINT.match(tail))


def best_sentences(cards: list[dict], name: str, limit: int = 5) -> list[dict]:
    """取**完整句子**里的原话，优先那些真的在解释这个名词的句子。

    为什么不用 `mention_refs` 的窗口：它按字符数截前后各半，句子会被切在半截
    （实测 `Skill` 的定义取到"将进一步改变我们定制"——话没说完）。定义段被截断比没有更糟。

    排序规则（全部文本规则，零模型）：
      1. 以名字开头的句子（最像定义）
      2. 名字后紧跟"是/指/叫/即…"的句子
      3. 其余提到名字的句子
    同类内部保持原文顺序；同一张卡只贡献一句；跨卡做归一化去重。
    """
    bucket = ([], [], [])
    seen = set()
    for card in cards:
        summary = cw._extract_summary(card.get('body') or '')
        if not summary or name not in summary:
            continue
        for sent in _SPLIT.split(summary):
            sent = sent.strip()
            if name not in sent or not (10 <= len(sent) <= 220):
                continue
            sent = _re.sub(r'!\[[^\]]*\]\([^)]*\)', '', sent)
            sent = _re.sub(r'https?://\S+', '', sent)
            sent = _squash_repeat(_re.sub(r'\s+', ' ', sent).strip())
            if len(sent) < 10:
                continue
            key = _norm(sent)
            if key in seen:
                continue
            seen.add(key)
            idx = sent.find(name)
            rank = 0 if sent[:idx].strip(' ，。、；：') == '' else (1 if _is_definition(sent, name) else 2)
            bucket[rank].append({'desc': sent, 'file': card.get('file'), 'title': card.get('title'),
                                 'topic': card.get('topic'), 'source': card.get('source')})
            break   # 一张卡只贡献一句
    ordered = bucket[0] + bucket[1] + bucket[2]
    return ordered[:limit]

def build_page(name: str, refs: list[dict]) -> str:
    """四段结构；每句都带来源，读的人能点回去核对。"""
    def link(r: dict) -> str:
        target = r.get('file') or r.get('title') or name
        return f'[[{target}]]'

    lines = [f'# {name}', '']
    lines.append(f'> 本页由脚本从卡片原文抽取，**未经模型改写**；每句后的链接是出处。')
    lines.append('')

    lines.append('## 定义')
    lines.append('')
    first = refs[0]
    lines.append(f'{first["desc"]} —— {link(first)}')
    lines.append('')

    if len(refs) > 1:
        lines.append('## 关键要点')
        lines.append('')
        for r in refs[1:4]:
            lines.append(f'- {r["desc"]} —— {link(r)}')
        lines.append('')

    lines.append('## 来源')
    lines.append('')
    seen = set()
    for r in refs:
        target = r.get('file') or r.get('title')
        if not target or target in seen:
            continue
        seen.add(target)
        lines.append(f'- [[{target}]]')
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description='给悬空概念补页（零模型，抽取式）')
    ap.add_argument('--pages', default='output/wechat-vault/Wiki/Concepts', help='概念页目录（读写都在这里）')
    ap.add_argument('--limit', type=int, default=20, help='最多补多少页')
    ap.add_argument('--min-pages', type=int, default=1, help='至少被几页提到才补（默认 1）')
    ap.add_argument('--dry-run', action='store_true', help='只预览，不写文件')
    ap.add_argument('--apply', action='store_true', help='真的写文件')
    ap.add_argument('--show', type=int, default=1, help='预览时打印几个完整页面')
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        print('要么 --dry-run 预览，要么 --apply 真写', file=sys.stderr)
        return 2

    pages_dir = Path(args.pages)
    targets = cw.collect_dangling_targets(str(pages_dir))
    ranked = [(n, c) for n, c in targets.most_common() if c >= args.min_pages]
    print(f'悬空目标 {len(targets)} 个（≥{args.min_pages} 页提到的 {len(ranked)} 个）')

    cards = cw.load_card_texts()
    print(f'卡片 {len(cards)} 张')

    written, skipped, shown = 0, 0, 0
    for name, count in ranked:
        if written >= args.limit:
            break
        target_file = pages_dir / f'{name}.md'
        if target_file.exists():
            continue
        refs = best_sentences(cards, name, limit=5)
        if not refs:
            skipped += 1
            continue
        page = build_page(name, refs)
        if args.apply:
            target_file.write_text(page, encoding='utf-8')
        written += 1
        if args.dry_run and shown < args.show:
            shown += 1
            print(f'\n{"=" * 60}\n[预览] {name}（被 {count} 页提到，{len(refs)} 条材料）\n{"=" * 60}')
            print(page)

    print(f'\n可补 {written} 页；无材料跳过 {skipped} 个'
          f'{"（预览模式，未写盘）" if args.dry_run else "（已写盘）"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
