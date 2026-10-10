#!/usr/bin/env python3
"""
Vault RAG 对话 — 基于 Vault 知识库（文章+概念+笔记）的问答。

用法:
  python scripts/vault_rag.py "深度学习在遥感中的应用"
  python scripts/vault_rag.py "最近读了什么文章" --json
"""

import sys, os, json, argparse, re
from pathlib import Path

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_VAULT = os.path.join(PROJECT_ROOT, 'output', 'wechat-vault')
DEFAULT_BIZ = os.path.join(PROJECT_ROOT, 'output', 'biz-daily')

sys.path.insert(0, SCRIPTS_DIR)
from _utils import CONCEPT_DIRS, LINE_LABELS, call_deepseek, line_for_relative_concepts

LINE_CHOICES = ('all', 'wiki', 'chat')


def line_label(line: str) -> str:
    return '两条线' if line == 'all' else LINE_LABELS.get(line, line)


def collect_context(vault: str, biz_daily: str, question: str, top_k: int,
                    line: str = 'all') -> list[dict]:
    """从 Vault + biz-daily 检索相关上下文。

    `line` 把**材料也分开**：概念页按目录分线，而用户的笔记层与日报文章都属于**文章线**
    （`--line chat` 只给聊天线的概念页）。不这么分的话，"只看聊天知识库"仍然会从阅读笔记里
    捞结果进来——那正是"两条线分开"要避免的事。

    **函数默认 `all`（不缩小范围）**，用户看到的默认（文章线）在 CLI 那一层：忘了传 `line`
    的调用方会多搜一条线，而不是静默少搜一半。
    """
    results = []
    search_terms = question.lower().split()
    want = (lambda which: line == 'all' or line == which)

    # 1. 概念页（权重最高）——不传 `--line` 时两个目录都读：文章知识库与聊天知识库是分开的
    # （`_utils.CONCEPT_DIRS`），只读一个的话分出去的那一半会静默地搜不到。
    for relative in CONCEPT_DIRS:
        if not want(line_for_relative_concepts(relative)):
            continue
        concepts_dir = Path(vault) / relative
        if not concepts_dir.is_dir():
            continue
        for md in concepts_dir.glob('*.md'):
            try:
                text = md.read_text(encoding='utf-8')[:2000]
            except Exception:
                continue
            score = sum(text.lower().count(t) for t in search_terms)
            if score > 0:
                results.append({'source': 'concept', 'title': md.stem, 'content': text, 'score': score})

    # 2. 阅读笔记 —— **`Notes/` 这个目录在本仓库里从来不存**（2026-09-27 实测：
    # Vault 里没有它），所以这一段一直搜的是空气。笔记实际住在 `002_Literature`。
    # 这是**用户自己的**层（文章线），所以 `--line chat` 时整段跳过。
    for relative in (('002_Literature', '003_Ideas', '008_MOC') if want('wiki') else ()):
        notes_dir = Path(vault) / relative
        if not notes_dir.is_dir():
            continue
        for md in notes_dir.rglob('*.md'):
            try:
                text = md.read_text(encoding='utf-8')[:1500]
            except Exception:
                continue
            score = sum(text.lower().count(t) for t in search_terms)
            # 标题匹配加分
            if any(t in md.stem.lower() for t in search_terms):
                score += 15
            if score > 0:
                results.append({'source': 'note', 'title': md.stem[:60], 'content': text, 'score': score})

    # 3. 最新文章（日报 = 文章线，`--line chat` 时跳过）
    biz_path = Path(biz_daily) if want('wiki') else None
    for date_dir in (sorted(biz_path.glob('20*'), reverse=True)[:7] if biz_path else []):  # 最近 7 天
        for md in date_dir.rglob('*.md'):
            if md.name == 'README.md' or 'README' in md.name:
                continue
            try:
                text = md.read_text(encoding='utf-8')[:1000]
            except Exception:
                continue
            score = sum(text.lower().count(t) for t in search_terms)
            if score > 0:
                results.append({'source': 'article', 'title': md.stem[:60], 'content': text, 'score': score})

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]


def build_prompt(question: str, context: list[dict], line: str = 'all') -> str:
    sources_text = ''
    for i, ctx in enumerate(context):
        label = {'concept': '概念', 'note': '笔记', 'article': '文章'}.get(ctx['source'], ctx['source'])
        sources_text += f'\n[{i+1}] ({label}) {ctx["title"]}\n{ctx["content"][:600]}\n'

    return f'''你是个人知识助手，基于用户笔记库回答问题。

**这一趟只检索了：{line_label(line)}**（另外的范围里可能有相关内容）。没有段落时按
"**这个范围里**没有相关信息"说，别说成"知识库里没有"。

知识库内容：
{sources_text}

用户问题：{question}

请综合知识库内容回答。如果知识库中没有相关信息，如实说明。'''


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Vault RAG 对话')
    parser.add_argument('question', nargs='?', default='', help='问题')
    parser.add_argument('--top-k', default='8', help='检索条数')
    parser.add_argument('--vault', default=DEFAULT_VAULT, help='Vault 路径')
    parser.add_argument('--biz-daily', default=DEFAULT_BIZ, help='biz-daily 路径')
    parser.add_argument('--api-key', default='', help='DeepSeek API key')
    parser.add_argument('--line', choices=list(LINE_CHOICES), default='wiki',
                        help='只看一条知识库：wiki=文章线（默认）、chat=聊天线、all=两条都看')
    parser.add_argument('--json', action='store_true', help='JSON 输出')
    args = parser.parse_args()

    question = args.question or os.environ.get('WEFLOW_VAULT_QUESTION', '')
    if not question:
        print('请提供问题')
        sys.exit(1)

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print('[ERROR] 缺少 API Key')
        sys.exit(1)

    top_k = int(args.top_k)
    context = collect_context(args.vault, args.biz_daily, question, top_k, line=args.line)

    if args.json:
        output = {'question': question, 'line': args.line, 'lineLabel': line_label(args.line),
                  'sources': [{'source': c['source'], 'title': c['title'], 'score': c['score']} for c in context]}
        if context:
            prompt = build_prompt(question, context, args.line)
            output['answer'] = call_deepseek(prompt, api_key, max_tokens=800)
        else:
            # **说清是哪个范围里没有**：`知识库中没有相关信息` 会被读成"整库都没有"
            output['answer'] = '%s里没有相关信息' % line_label(args.line)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    print(f'🔍 "{question}"（范围：{line_label(args.line)}）\n')
    if not context:
        print('%s里没有相关信息（`--line all` 两条一起看）' % line_label(args.line))
        return

    print(f'检索到 {len(context)} 条相关内容:\n')
    for i, c in enumerate(context):
        icon = {'concept': '🧠', 'note': '📝', 'article': '📄'}.get(c['source'], '📎')
        print(f'  {i+1}. {icon} [{c["source"]}] {c["title"]}')

    prompt = build_prompt(question, context, args.line)
    print(f'\n{"="*50}')
    answer = call_deepseek(prompt, api_key, max_tokens=800)
    print(answer)
    print(f'{"="*50}')


if __name__ == '__main__':
    main()
