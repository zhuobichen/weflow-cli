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
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_VAULT = os.path.join(PROJECT_ROOT, 'output', 'wechat-vault')
DEFAULT_BIZ = os.path.join(PROJECT_ROOT, 'output', 'biz-daily')


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


def collect_files(vault: str, biz_daily: str, search_type: str, days: int) -> list[tuple]:
    """收集可搜索文件。"""
    files = []
    cutoff = datetime.now() - timedelta(days=days)

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

    if search_type in ('all', 'article'):
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
        vault_path = Path(vault)
        concepts_dir = vault_path / 'Wiki' / 'Concepts'
        if concepts_dir.is_dir():
            for md in concepts_dir.glob('*.md'):
                try:
                    fm = parse_fm(md.read_text(encoding='utf-8'))
                except Exception:
                    fm = {}
                files.append((md, 'concept', fm))

    if search_type in ('all', 'note'):
        vault_path = Path(vault)
        notes_dir = vault_path / 'Notes'
        if notes_dir.is_dir():
            for md in notes_dir.rglob('*.md'):
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
    parser.add_argument('--vault', default=DEFAULT_VAULT, help='Vault 路径')
    parser.add_argument('--biz-daily', default=DEFAULT_BIZ, help='biz-daily 路径')
    parser.add_argument('--json', action='store_true', help='JSON 输出')
    args = parser.parse_args()

    if not args.query:
        print('请提供搜索关键词')
        sys.exit(1)

    files = collect_files(args.vault, args.biz_daily, args.type, args.days)
    results = search_text(args.query, files, int(args.top_k))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print(f'🔍 搜索 "{args.query}" 找到 {len(results)} 条 (类型: {args.type}, {args.days}天内)\n')
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
