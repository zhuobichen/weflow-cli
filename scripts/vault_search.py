#!/usr/bin/env python3
"""
Vault 全局搜索 — 关键词 + AI 排序，覆盖文章/概念/笔记。

用法:
  python scripts/vault_search.py "遥感反演" --top-k 10
  python scripts/vault_search.py "空气污染" --type article --days 30
"""

import sys, os, json, argparse, re
from pathlib import Path
from datetime import datetime, timedelta

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
from _utils import (CONCEPT_DIRS, LINE_LABELS, line_for_relative_concepts)  # noqa: E402
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_VAULT = os.path.join(PROJECT_ROOT, 'output', 'wechat-vault')
DEFAULT_BIZ = os.path.join(PROJECT_ROOT, 'output', 'biz-daily')

LINE_CHOICES = ('all', 'wiki', 'chat')

# 笔记层里**哪些属于聊天线**。其余笔记层是用户自己写的/读的（文章线）。
#
# 两个知识库分开之后，`--line` 要能真的把材料也分开，否则"只看聊天线"仍然会从用户的
# 阅读笔记里捞结果——那不是聊天知识库里的东西。`Sources/Chat` 是 `chat_notes --vault-copy`
# 放聊天卡的地方（`scripts/chat_notes.py`），只有它是聊天线的。
CHAT_NOTE_DIRS = ('Sources/Chat',)


def line_label(line: str) -> str:
    return '两条线' if line == 'all' else LINE_LABELS.get(line, line)


def search_text(query: str, files: list[tuple], top_k: int) -> list[dict]:
    """关键词搜索 + 简单排序。"""
    terms = query.lower().split()
    scored = []
    for filepath, category, meta in files:
        try:
            text = filepath.read_text(encoding='utf-8')[:2000]
        except Exception:
            continue
        text_lower = text.lower()
        # 计分：标题匹配 > 关键词频次
        score = 0
        title = meta.get('title', '')
        if any(t in title.lower() for t in terms):
            score += 10
        for t in terms:
            score += text_lower.count(t)
        if score > 0:
            scored.append({'file': str(filepath), 'category': category, 'title': title,
                           'score': score, 'snippet': text[:300]})
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:top_k]


def collect_files(vault: str, biz_daily: str, search_type: str, days: int,
                  line: str = 'all') -> list[tuple]:
    """收集可搜索文件。

    `line` 在**收集这一步**就过滤（而不是收完再筛）：三条来源各自属于哪条线是这里才知道的
    —— 日报文章与用户的笔记层是文章线、`Sources/Chat` 的聊天卡是聊天线、概念页按目录分。

    **函数默认 `all`（不缩小范围）**，用户看到的默认（文章线）在 CLI 那一层：忘了传 `line`
    的调用方会多搜一条线，而不是静默少搜一半。
    """
    files = []
    cutoff = datetime.now() - timedelta(days=days)
    want = (lambda which: line == 'all' or line == which)

    def parse_fm(text):
        if not text.startswith('---'):
            return {}
        end = text.find('---', 3)
        if end == -1:
            return {}
        fm = {}
        for line in text[3:end].strip().split('\n'):
            if ':' in line:
                k, _, v = line.partition(':')
                fm[k.strip()] = v.strip().strip('"').strip("'")
        return fm

    if search_type in ('all', 'article') and want('wiki'):
        # biz-daily 文章
        biz_dir = Path(biz_daily)
        for date_dir in sorted(biz_dir.glob('20*'), reverse=True):
            try:
                dir_date = datetime.strptime(date_dir.name, '%Y-%m-%d')
                if dir_date < cutoff:
                    continue
            except ValueError:
                continue
            for md in date_dir.rglob('*.md'):
                if md.name == 'README.md':
                    continue
                try:
                    fm = parse_fm(md.read_text(encoding='utf-8'))
                except Exception:
                    fm = {}
                files.append((md, 'article', fm))

    if search_type in ('all', 'concept'):
        # **两个目录都要读**（`_utils.CONCEPT_DIRS`）：文章知识库与聊天知识库是分开的
        # （用户 2026-09-27 明确要求），只读一个的话，分出去的那一半会**静默地搜不到**
        # ——命令照样返回结果，只是少了一半。
        vault_path = Path(vault)
        for relative in CONCEPT_DIRS:
            # 概念页**按目录分线**（清单里每条线一个概念目录），而不是按库根——
            # 测试的临时库、或 `--vault` 指到别处时，库根对不上但"这串相对路径属于哪条线"仍在
            if not want(line_for_relative_concepts(relative)):
                continue
            concepts_dir = vault_path / relative
            if not concepts_dir.is_dir():
                continue
            for md in concepts_dir.glob('*.md'):
                try:
                    fm = parse_fm(md.read_text(encoding='utf-8'))
                except Exception:
                    fm = {}
                files.append((md, 'concept', fm))

    if search_type in ('all', 'note') and (want('wiki') or want('chat')):
        # **`Notes/` 这个目录在本仓库里从来不存。** 2026-09-27 实测：Vault 里没有它，
        # 于是 `--type note` 一直搜的是空气——而它本该覆盖的阅读笔记有 25,676 篇，
        # 是最大的一层。笔记实际住在下面这几个目录里。
        vault_path = Path(vault)
        # `Sources/Chat` 是聊天知识卡（`chat_notes --vault-copy` 放的）。它跟
        # `Sources/WeChat`（两万六千篇原始文章，走 biz-daily 那条路）不是一回事：
        # 这里面是一百多张**已经提炼过的**卡——会话里聊了什么、有哪些人、欠着什么。
        for sub in ('002_Literature', '001_Daily', '003_Ideas', '008_MOC', 'Sources/Chat'):
            # 笔记层也分线：`Sources/Chat` 是聊天卡（聊天线），其余是用户自己的笔记（文章线）
            if not want('chat' if sub in CHAT_NOTE_DIRS else 'wiki'):
                continue
            notes_dir = vault_path / sub
            if not notes_dir.is_dir():
                continue
            for md in notes_dir.rglob('*.md'):
                if md.name == 'README.md':
                    continue
                try:
                    fm = parse_fm(md.read_text(encoding='utf-8'))
                except Exception:
                    fm = {}
                files.append((md, 'note', fm))

    return files


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Vault 全局搜索')
    parser.add_argument('query', nargs='?', default='', help='搜索关键词')
    parser.add_argument('--top-k', default='10', help='返回数量')
    parser.add_argument('--type', default='all', choices=['all', 'article', 'concept', 'note'])
    parser.add_argument('--days', type=int, default=90, help='搜索天数范围')
    parser.add_argument('--line', choices=list(LINE_CHOICES), default='wiki',
                        help='只看一条知识库：wiki=文章线（默认）、chat=聊天线、all=两条都看')
    parser.add_argument('--vault', default=DEFAULT_VAULT, help='Vault 路径')
    parser.add_argument('--biz-daily', default=DEFAULT_BIZ, help='biz-daily 路径')
    parser.add_argument('--json', action='store_true', help='JSON 输出')
    args = parser.parse_args()

    if not args.query:
        print('请提供搜索关键词')
        sys.exit(1)

    files = collect_files(args.vault, args.biz_daily, args.type, args.days, line=args.line)
    results = search_text(args.query, files, int(args.top_k))

    if args.json:
        # **带上范围**：`--line chat` 搜空了和"知识库里真没有"是两回事，而这一支的输出
        # 就是 JSON 本身（CLI 直接把它打给人看），没有别的地方能写这句。
        print(json.dumps({'line': args.line, 'lineLabel': line_label(args.line),
                          'type': args.type, 'results': results},
                         ensure_ascii=False, indent=2))
        return

    print(f'🔍 搜索 "{args.query}" 找到 {len(results)} 条 '
          f'(类型: {args.type}, 范围: {line_label(args.line)}, {args.days}天内)\n')
    for i, r in enumerate(results):
        icon = {'article': '📄', 'concept': '🧠', 'note': '📝'}.get(r['category'], '📎')
        num = str(i + 1).rjust(2)
        print(f'{num}. {icon} {r["title"] or r["file"].split("/")[-1]}')
        print(f'   {r["category"]} | 相关度: {r["score"]}')
        snippet = re.sub(r'\s+', ' ', r['snippet'][:120])
        print(f'   {snippet}')
        print()


if __name__ == '__main__':
    main()
