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
from _utils import call_deepseek


def collect_context(vault: str, biz_daily: str, question: str, top_k: int) -> list[dict]:
    """从 Vault + biz-daily 检索相关上下文。"""
    results = []
    search_terms = question.lower().split()

    # 1. 概念页（权重最高）
    concepts_dir = Path(vault) / 'Wiki' / 'Concepts'
    if concepts_dir.is_dir():
        for md in concepts_dir.glob('*.md'):
            try:
                text = md.read_text(encoding='utf-8')[:2000]
            except Exception:
                continue
            score = sum(text.lower().count(t) for t in search_terms)
            if score > 0:
                results.append({'source': 'concept', 'title': md.stem, 'content': text, 'score': score})

    # 2. 阅读笔记
    notes_dir = Path(vault) / 'Notes'
    if notes_dir.is_dir():
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

    # 3. 最新文章
    biz_path = Path(biz_daily)
    for date_dir in sorted(biz_path.glob('20*'), reverse=True)[:7]:  # 最近 7 天
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


def build_prompt(question: str, context: list[dict]) -> str:
    sources_text = ''
    for i, ctx in enumerate(context):
        label = {'concept': '概念', 'note': '笔记', 'article': '文章'}.get(ctx['source'], ctx['source'])
        sources_text += f'\n[{i+1}] ({label}) {ctx["title"]}\n{ctx["content"][:600]}\n'

    return f'''你是个人知识助手，基于用户笔记库回答问题。

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
    parser.add_argument('--json', action='store_true', help='JSON 输出')
    args = parser.parse_args()

    if not args.question:
        print('请提供问题')
        sys.exit(1)

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print('[ERROR] 缺少 API Key')
        sys.exit(1)

    top_k = int(args.top_k)
    context = collect_context(args.vault, args.biz_daily, args.question, top_k)

    if args.json:
        output = {'question': args.question, 'sources': [{'source': c['source'], 'title': c['title'], 'score': c['score']} for c in context]}
        if context:
            prompt = build_prompt(args.question, context)
            output['answer'] = call_deepseek(prompt, api_key, max_tokens=800)
        else:
            output['answer'] = '知识库中没有相关信息'
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    print(f'🔍 "{args.question}"\n')
    if not context:
        print('未找到相关内容')
        return

    print(f'检索到 {len(context)} 条相关内容:\n')
    for i, c in enumerate(context):
        icon = {'concept': '🧠', 'note': '📝', 'article': '📄'}.get(c['source'], '📎')
        print(f'  {i+1}. {icon} [{c["source"]}] {c["title"]}')

    prompt = build_prompt(args.question, context)
    print(f'\n{"="*50}')
    answer = call_deepseek(prompt, api_key, max_tokens=800)
    print(answer)
    print(f'{"="*50}')


if __name__ == '__main__':
    main()
