#!/usr/bin/env python3
"""
文章知识卡 — 从 Vault 里的文章笔记里提炼「概念」，喂给 wiki compile。

用法:
  python scripts/article_notes.py --dry-run --json     # 看代价（只读本地、零出境）
  python scripts/article_notes.py --limit 30 --yes     # 先试水 30 篇
  python scripts/compile_wiki.py --source output/article-notes

## 为什么需要这一步（实测出来的）

`wiki compile` 要的是「概念形状」的链接（`[[概念]] — 关于它说了什么`）。把 Vault 里那 1633 篇
文章笔记**全量扫过**之后发现：正文里的 `[[…]]` 只有两类——与自己主题同名的那条
（`> - **主题**: [[AI]]`）和 `## 🔗 关联网络` 里的**路径互链**。"其它"候选概念 **0 条**：
**这批笔记没有概念那一节**。所以文章线此前无论怎么跑 compile 都产不出概念页。

这一步就是把缺的那节补上：每篇一次模型调用，**只问概念**。摘要不重新生成——原笔记里
已经有一段摘要了，照抄进卡片即可（少一次生成就少一次编造的机会）。

## 与对话线的关系

同一形状、同一消费者（`compile_wiki`）：`chat_notes.py` 产对话卡，本脚本产文章卡，
两者都进 `output/*-notes/`，都靠 `compile_wiki --source` 聚成概念页。共用的件
（`plain` / `note_path` / `SEPARATOR`）在 `_utils.py` 里一份。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import (SEPARATOR, call_deepseek, get_api_key, load_config,  # noqa: E402
                    note_path, plain, parse_frontmatter, write_with_frontmatter)
import compile_wiki  # noqa: E402  （用它的 concept_body / _extract_summary，同一份实现）

TZ = timezone(timedelta(hours=8))
SOURCE_ROOT = 'output/wechat-vault/002_Literature'
OUTPUT_ROOT = 'output/article-notes'
DEFAULT_LIMIT = 30
# 进提示词的字数上限。文章笔记本身已经是提炼过的一千多字，3000 足够，超出只是保险。
MAX_ARTICLE_CHARS = 3000
# 太短的不值得一次调用（与对话线那道闸门同一个理由，阈值按文章的量级定）
MIN_ARTICLE_CHARS = 200

CONCEPT_PROMPT = """你是知识库的**编译者，不是作者**。下面是一篇公众号文章笔记。

请只依据它，回报这篇文章涉及的**概念**——概念是可被反复引用的东西（某个方法、工具、事件、
现象、理论、人物），**不是分类词**（"AI"、"新闻"、"学术"这种主题标签不算概念，它们已经在
frontmatter 里了），也不是文章标题。

只返回一个 JSON 对象（不要解释、不要代码块围栏）：
{{"concepts": [{{"name": "概念名（2-12 字，不要带书名号引号方括号）", "desc": "这篇文章关于它说了什么（一句话，具体）"}}]}}

要求：
1. **只写文章里有的**。没讲到的不许补；拿不准的宁可不列。
2. 2-6 条，**按重要度排序**；概念名要具体（"MCP 协议"可以，"技术"不行）。
3. 每条 desc 是"这篇文章关于它的说法"，不是百科定义。
4. 全部中文，不要 markdown 语法（不要 #、*、-）。

文章：{title}（来源：{source}｜主题：{topic}）

正文：
{body}
"""


def worth_concepts(body: str, min_chars: int = MIN_ARTICLE_CHARS) -> bool:
    """这篇够不够提炼——不够就跳过，**并报出来**（不是静默丢）。

    太短的多半是"标题党残页"或抓取不完整：写出来的卡片只会是空壳，白占一次调用，
    还往概念页里灌一条没有信息量的来源。与对话线那道闸门同一个理由。
    """
    return len(body.strip()) >= min_chars


def list_articles(source_root: str, limit: int) -> list:
    """按发布时间倒序取前 N 篇（文件名前缀就是日期，frontmatter 里也有 published）。"""
    root = Path(source_root)
    if not root.exists():
        return []
    found = []
    for path in root.rglob('*.md'):
        if path.name == 'README.md':
            continue
        try:
            frontmatter, body = parse_frontmatter(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        found.append({
            'path': path,
            'rel': str(path.relative_to(root)).replace('\\', '/'),
            'title': str(frontmatter.get('title') or path.stem),
            'source': str(frontmatter.get('source') or ''),
            'topic': compile_wiki.article_topic(frontmatter),
            'tags': frontmatter.get('tags') or [],
            'published': str(frontmatter.get('published') or (path.name[:10] if re.match(r'\d{4}-\d{2}-\d{2}', path.name) else '')),
            'body': body,
        })
    found.sort(key=lambda item: item['published'], reverse=True)
    return found[:limit] if limit else found


def build_card(article: dict, concepts: list, updated: str):
    """卡片 → (frontmatter, body)。

    - `## AI 摘要` 那一段**照抄**原笔记的摘要（消费者 `compile_wiki` 就找这一节）；
    - `[[…]]` 只出现在 `## 概念` 那一节、**由这里渲染**（不由模型写，见 `_utils.plain` 的注释）。
    """
    frontmatter = {
        'title': article['title'],
        'type': 'article-card',
        'source': article['source'],
        'topic': article['topic'],
        'tags': list(article['tags'])[:4] or ['文章'],
        'published': article['published'],
        # 溯源：这张卡是从哪篇笔记里提炼的
        'from': article['rel'],
        'updated': updated,
    }
    parts = ['# %s\n' % article['title'], '## AI 摘要\n']
    parts.append(compile_wiki._extract_summary(article['body']) or '(原笔记没有摘要小节)')
    parts.append('')
    if concepts:
        parts.append('## 概念\n')
        for item in concepts:
            parts.append('- [[%s]] %s %s' % (item['name'], SEPARATOR, item['desc']))
        parts.append('')
    return frontmatter, '\n'.join(parts)


def parse_concepts(raw: str) -> list:
    """解出概念列表。解不出来返回 None（**不猜**），空列表表示"这篇确实没有概念"。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get('concepts'), list):
        return None
    out = []
    for item in data['concepts']:
        if not isinstance(item, dict):
            continue
        name = plain(item.get('name')).strip().strip('[]')
        desc = plain(item.get('desc')).strip()
        if name and desc:
            out.append({'name': name, 'desc': desc})
        if len(out) >= 6:
            break
    return out


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='文章知识卡（从文章笔记提炼概念，喂给 wiki compile）')
    parser.add_argument('--source', default=SOURCE_ROOT, help='文章笔记目录')
    parser.add_argument('--out', default=OUTPUT_ROOT, help='卡片输出目录')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT,
                        help='最多处理几篇（默认 %d，按发布时间倒序）' % DEFAULT_LIMIT)
    parser.add_argument('--dry-run', action='store_true', help='只报要发多少给模型，不调用')
    parser.add_argument('--yes', action='store_true', help='确认调用云端模型')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    if args.limit < 1:
        print(json.dumps({'success': False, 'error': '--limit 要 ≥ 1'}))
        return 1

    articles = list_articles(args.source, args.limit)
    if not articles:
        message = '在 %s 下没找到 .md 笔记' % args.source
        print(json.dumps({'success': False, 'error': message}) if args.json else message,
              file=sys.stdout if args.json else sys.stderr)
        return 1

    runnable, thin = [], []
    for article in articles:
        (runnable if worth_concepts(article['body']) else thin).append(article)

    if args.dry_run:
        preview = {
            'success': True, 'dryRun': True, 'action': 'article-notes',
            'source': args.source, 'articles': len(runnable),
            'chars': sum(len(a['body']) for a in runnable),
            'skipped': len(thin), 'skippedTitles': [a['title'][:24] for a in thin][:10],
            'model': 'DeepSeek（生成）', 'calls': len(runnable),
            'readsLocalData': True, 'invokesAI': False, 'writesFiles': True,
            'note': '每篇一次调用，**只问概念**（摘要照抄原笔记）；卡片写到 %s' % args.out,
        }
        if args.json:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        else:
            print('%d 篇、约 %d 字会发给 DeepSeek（每篇一次调用，只问概念）'
                  % (len(runnable), preview['chars']))
            if thin:
                print('另有 %d 篇太短（<%d 字）跳过' % (len(thin), MIN_ARTICLE_CHARS))
            print('卡片写到 %s；之后跑 python scripts/compile_wiki.py --source %s' % (args.out, args.out))
        return 0

    if not runnable:
        print('没有够得上提炼的笔记（%d 篇都太短）' % len(thin), file=sys.stderr)
        return 1

    if not args.yes:
        print('会把 %d 篇文章发给 DeepSeek（每篇一次调用）。加 --yes 确认。' % len(runnable),
              file=sys.stderr)
        return 1

    config = load_config()
    api_key = get_api_key(config)
    if not api_key:
        print('没有配置 deepseekApiKey（weflow-cli config set deepseekApiKey "..."）', file=sys.stderr)
        return 1

    updated = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
    written, failed = [], []
    for article in runnable:
        prompt = CONCEPT_PROMPT.format(
            title=article['title'], source=article['source'] or '未知',
            topic=article['topic'] or '未分类',
            body=compile_wiki.concept_body(article['body'])[:MAX_ARTICLE_CHARS])
        try:
            raw = call_deepseek(prompt, api_key, max_tokens=800, timeout=90)
        except Exception as error:
            failed.append({'title': article['title'][:30], 'reason': '调用失败：%s' % error})
            continue
        concepts = parse_concepts(raw)
        if concepts is None:
            failed.append({'title': article['title'][:30], 'reason': '模型没返回可用 JSON'})
            continue
        target = note_path(args.out, article['path'].stem)
        target.parent.mkdir(parents=True, exist_ok=True)
        frontmatter, body = build_card(article, concepts, updated)
        write_with_frontmatter(str(target), frontmatter, body)
        written.append({'title': article['title'][:30], 'file': str(target), 'concepts': len(concepts)})

    result = {
        'success': True, 'action': 'article-notes', 'model': 'DeepSeek（生成）',
        'sendsNothing': True, 'written': written, 'failed': failed,
        'skipped': [{'title': a['title'][:30], 'chars': len(a['body'])} for a in thin],
        'articles': len(runnable), 'calls': len(runnable),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in written:
            print('✓ %s（%d 个概念）→ %s' % (item['title'], item['concepts'], item['file']))
        for item in failed:
            print('✗ %s：%s' % (item['title'], item['reason']), file=sys.stderr)
        print('\n共 %d 张卡；接着跑 python scripts/compile_wiki.py --source %s'
              % (len(written), args.out))
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
