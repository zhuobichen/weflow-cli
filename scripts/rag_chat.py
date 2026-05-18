#!/usr/bin/env python3
"""
RAG 智能聊天助手 — 基于语义搜索 + AI 引擎的对话式知识检索。

用法:
  # 单次提问
  python scripts/rag_chat.py "上周和张三聊了什么"
  python scripts/rag_chat.py "帮我总结最近 AI 动态" --top-k 8
  python scripts/rag_chat.py "WRF 相关的讨论" --talker "项目群"

  # 交互模式（多轮对话）
  python scripts/rag_chat.py --interactive

配置:
  API key 从 ~/.weflow-cli/config.json 读取（deepseekApiKey / dashscopeApiKey）
"""

import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import load_config, create_engine
from semantic_search import search as semantic_search, build_index, INDEX_DIR, VECTORS_FILE

PROMPT_TEMPLATE = """你是一个个人知识助手，可以访问用户的微信聊天记录和公众号文章。

基于以下检索到的相关信息回答问题。如果信息不足以回答，请明确说明，不要编造。

## 相关信息

{context}

## 用户问题

{question}

请用中文简洁回答，在关键信息处引用来源编号（如 [1]、[2]）。"""


def format_context(results: list[dict]) -> str:
    """将搜索结果格式化为 RAG context。"""
    chunks = []
    for i, item in enumerate(results, 1):
        if item.get('type') == 'article':
            chunks.append(
                f"[{i}] 📄 公众号文章 | {item.get('date', '?')} | {item.get('topic', '')}\n"
                f"标题: {item.get('title', '')}\n"
                f"内容: {item.get('text', '')[:500]}"
            )
        elif item.get('type') == 'chat':
            chunks.append(
                f"[{i}] 💬 {item.get('talker', '?')} | {item.get('time', '?')}\n"
                f"内容: {item.get('text', '')[:500]}"
            )
        else:
            chunks.append(
                f"[{i}] 内容: {item.get('text', str(item)[:500])}"
            )
    return '\n\n'.join(chunks) if chunks else '（未找到相关信息）'


def build_prompt(question: str, results: list[dict], history: list[dict] = None) -> str:
    """构建 RAG prompt。"""
    context = format_context(results)

    if history and len(history) > 0:
        history_text = '\n'.join(
            f"用户: {h['question']}\n助手: {h['answer']}"
            for h in history[-6:]  # 最近 3 轮
        )
        context = f"## 对话历史\n\n{history_text}\n\n## 当前检索结果\n\n{context}"

    return PROMPT_TEMPLATE.format(context=context, question=question)


def query_rag(question: str, embed_key: str, chat_key: str, top_k: int = 10,
              talker: str = None, history: list[dict] = None) -> dict:
    """执行一次 RAG 查询。"""
    # 1. 语义检索
    results = semantic_search(question, embed_key, top_k=top_k)

    # 2. 过滤 talker（如果指定）
    if talker:
        results = [r for r in results if talker.lower() in (r.get('talker', '') + r.get('title', '')).lower()]
        if not results:
            return {
                'answer': f'未找到与 "{talker}" 相关的对话或文章。',
                'sources': [],
            }

    # 3. 构建 prompt
    prompt = build_prompt(question, results, history)

    # 4. 调用 AI
    config = load_config()
    engine_type = config.get('aiEngine', 'deepseek')
    engine = create_engine(engine_type, chat_key)
    answer = engine.chat(prompt, max_tokens=1500)

    # 5. 格式化来源
    sources = []
    for i, item in enumerate(results[:5], 1):
        if item.get('type') == 'chat':
            sources.append(f"[{i}] {item.get('talker', '?')} ({item.get('time', '?')})")
        else:
            sources.append(f"[{i}] {item.get('title', item.get('id', '?'))[:60]}")

    return {'answer': answer, 'sources': sources}


def interactive_mode(embed_key: str, chat_key: str):
    """交互式多轮对话。"""
    config = load_config()
    engine_type = config.get('aiEngine', 'deepseek')

    print(f'\n🤖 RAG 智能助手 ({engine_type})')
    print('  输入问题开始对话，输入 /help 查看帮助，输入 /exit 退出\n')

    history: list[dict] = []

    while True:
        try:
            question = input('🔍 你: ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\n👋 再见！')
            break

        if not question:
            continue

        if question == '/exit':
            print('👋 再见！')
            break
        if question == '/help':
            print('命令: /exit 退出, /clear 清除历史, /rebuild 重建索引\n')
            continue
        if question == '/clear':
            history = []
            print('✓ 对话历史已清除\n')
            continue
        if question == '/rebuild':
            print('重建索引中...')
            result = build_index(embed_key, full=False)
            print(f'✓ {result}\n')
            continue

        result = query_rag(question, embed_key, chat_key, top_k=10, history=history)
        history.append({'question': question, 'answer': result['answer']})

        print(f'\n🤖 助手:\n{result["answer"]}')
        if result.get('sources'):
            print(f"\n📎 来源: {' | '.join(result['sources'])}")
        print()


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='RAG 智能聊天助手')
    parser.add_argument('question', nargs='?', help='要问的问题')
    parser.add_argument('--interactive', '-i', action='store_true', help='交互模式')
    parser.add_argument('--top-k', type=int, default=10, help='检索数量（默认 10）')
    parser.add_argument('--talker', help='限定联系人/群聊')
    parser.add_argument('--api-key', help='DeepSeek API key（优先从 config 读取）')
    parser.add_argument('--json', action='store_true', help='JSON 输出')
    args = parser.parse_args()

    config = load_config()
    embed_key = os.environ.get('DASHSCOPE_API_KEY', '') or config.get('dashscopeApiKey', '')
    chat_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '') or config.get('deepseekApiKey', '')

    if not embed_key:
        print('[ERROR] 缺少 Embedding API key。请在 ~/.weflow-cli/config.json 中设置 dashscopeApiKey')
        sys.exit(1)
    if not chat_key:
        print('[ERROR] 缺少 AI API key。请在 ~/.weflow-cli/config.json 中设置 deepseekApiKey')
        sys.exit(1)

    if args.interactive or not args.question:
        interactive_mode(embed_key, chat_key)
    else:
        result = query_rag(args.question, embed_key, chat_key, top_k=args.top_k, talker=args.talker)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f'\n{result["answer"]}')
            if result.get('sources'):
                print(f"\n📎 来源: {' | '.join(result['sources'])}")


if __name__ == '__main__':
    main()
