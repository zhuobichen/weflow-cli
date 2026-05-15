#!/usr/bin/env python3
"""
AI 日报生成 — 基于 git diff 分析今日知识变更，生成结构化学习日报。

用法:
  python scripts/generate_review.py --api-key <key> [--date 2026-05-15] [--output Reviews]
"""
import sys, os, json, re, time, subprocess
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import create_engine, parse_frontmatter

DAILY_PROMPT = """你是知识管理助手，基于今日文章内容生成学习日报。

今日新增文章（标题 + 来源 + 摘要）：
{articles_text}

请按格式返回：
【今日焦点】
（2-3句话，今日最重要的知识收获）

【按主题】
AI:
- 要点1
- 要点2

学术:
- 要点1
- 要点2

投资:
- 要点1

【关键收获】
（1句话，今日最值得记住的一点）

要求：只基于提供的文章内容，不要编造。无对应主题则标注"（无）"。"""


def scan_today_articles(target_date: str, source='output/biz-daily') -> list[dict]:
    """扫描指定日期的文章目录，提取 frontmatter + 摘要。"""
    date_dir = Path(source) / target_date
    if not date_dir.exists():
        return []

    articles = []
    for md_file in sorted(date_dir.rglob('*.md')):
        if md_file.name == 'README.md' or md_file.name.startswith('.'):
            continue
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            continue

        fm, body = parse_frontmatter(content)
        if not fm:
            continue

        # Extract summary
        summary = ''
        m = re.search(r'## (?:AI 摘要|深度解析)\n\n(.+?)(?=\n\n(?:##|---)|\Z)', body, re.DOTALL)
        if m:
            summary = m.group(1).strip()[:300]

        articles.append({
            'title': fm.get('title', md_file.stem),
            'source': fm.get('source', ''),
            'topic': fm.get('topic', '学术'),
            'tags': fm.get('tags', []),
            'summary': summary,
        })
    return articles


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='AI 学习日报生成')
    parser.add_argument('--api-key', help='DeepSeek API key（或环境变量 DEEPSEEK_API_KEY）')
    parser.add_argument('--date', help='日期 YYYY-MM-DD')
    parser.add_argument('--engine', default='deepseek', help='AI 引擎（deepseek/claude/ollama）')
    parser.add_argument('--source', default='output/biz-daily', help='文章目录')
    parser.add_argument('--output', default='output/reviews', help='输出目录')
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print('[ERROR] 需要 API key')
        sys.exit(1)

    # Date
    from datetime import datetime, timezone, timedelta
    if args.date:
        target_date = args.date
    else:
        tz = timezone(timedelta(hours=8))
        target_date = datetime.now(tz).strftime('%Y-%m-%d')

    print(f'=== AI 日报 — {target_date} ===\n')

    # Scan articles
    articles = scan_today_articles(target_date, args.source)
    if not articles:
        print(f'未找到 {target_date} 的文章，请先运行 biz_daily.py')
        return

    print(f'找到 {len(articles)} 篇文章')

    # Topic distribution
    from collections import Counter
    topics = Counter(a['topic'] for a in articles)
    print(f'  主题: {dict(topics)}')

    # Format articles for prompt
    article_texts = []
    for a in articles:
        article_texts.append(f'- [{a["topic"]}] {a["title"]}（{a["source"]}）：{a["summary"][:150]}')
    articles_text = '\n'.join(article_texts[: 30])  # max 30 articles

    # Call AI
    print(f'\n生成日报中...')
    engine = create_engine(args.engine, api_key)
    try:
        response = engine.chat(DAILY_PROMPT.format(articles_text=articles_text), max_tokens=1000)
    except Exception as e:
        print(f'[ERR] AI 调用失败: {e}')
        sys.exit(1)

    # Parse response
    focus_m = re.search(r'【今日焦点】\s*(.+?)(?=\n【|$)', response, re.DOTALL)
    takeaway_m = re.search(r'【关键收获】\s*(.+?)(?=\n【|$)', response, re.DOTALL)

    focus = focus_m.group(1).strip() if focus_m else ''
    takeaway = takeaway_m.group(1).strip() if takeaway_m else ''

    # Output
    out_dir = Path(args.output) / 'Daily'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f'Daily-{target_date}.md'

    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(f'---\ntitle: "学习日报 — {target_date}"\ntype: daily-review\ndate: {target_date}\ncreated: {target_date}\n---\n\n')
        f.write(f'# 学习日报 — {target_date}\n\n')
        f.write(f'> 共 {len(articles)} 篇文章 | 主题: {", ".join(f"{t}({c})" for t, c in topics.most_common())}\n\n')
        f.write(f'---\n\n')
        if focus:
            f.write(f'## 今日焦点\n\n{focus}\n\n')
        # Topic breakdown
        f.write(f'## 知识输入\n\n')
        for topic, count in topics.most_common():
            topic_articles = [a for a in articles if a['topic'] == topic]
            f.write(f'### {topic}（{count}篇）\n\n')
            for a in topic_articles[:8]:
                f.write(f'- **{a["title"]}** — {a["source"]}\n')
                if a['summary']:
                    f.write(f'  {a["summary"][:200]}\n')
            f.write('\n')
        f.write(f'---\n\n')
        if takeaway:
            f.write(f'## 关键收获\n\n> {takeaway}\n\n')
        f.write(f'*由 weflow-cli generate-review 自动生成*\n')

    print(f'\n✓ 日报: {out_file}')
    print(f'  焦点: {focus[:100] if focus else "(无)"}...')


if __name__ == '__main__':
    main()
