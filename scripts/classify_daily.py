#!/usr/bin/env python3
"""
后处理：广告清洗 → DeepSeek主题分类 → 兴趣深度摘要 → 分文件夹输出。

用法: python scripts/classify_daily.py [date_dir] --api-key <key> [--interest AI]
"""

import sys, os, json, re, time, urllib.request, shutil
from collections import Counter
from pathlib import Path

OUTPUT_ROOT = 'output/biz-daily'
TOPICS = ['AI', '学术', '新闻', '文学']

# 微信文章广告/垃圾行
AD_PATTERNS = [
    re.compile(r'在小说阅读器读本章\s*'),
    re.compile(r'在小说阅读器中沉浸阅读\s*'),
    re.compile(r'去阅读\s*'),
    re.compile(r'Scan to Follow\s*'),
    re.compile(r'轻触阅读原文\s*'),
    re.compile(r'预览时标签不可点\s*'),
    re.compile(r'继续滑动看下一个\s*'),
    re.compile(r'\[.*?\]\(javascript:void\(0\);\)'),
    re.compile(r'!\[.*?\]\(https?://mmbiz[^)]+\)\s*'),
    re.compile(r'\n{4,}'),
]

CLASSIFY_PROMPT = """归类以下文章到: AI / 学术 / 新闻 / 文学

AI: AI产品测评、大模型、Agent、Claude/GPT/DeepSeek、编程开发、GitHub开源、科技教程
学术: 科研论文、Nature/Science期刊、环境科学、实验室、学术会议
新闻: 时事政策、社会热点、企业通知、招聘、促销
文学: 散文随笔、生活记录、美食旅游、历史人文

只输出分类名（两个字）。"""

INTEREST_PROMPT = """对以下AI领域文章生成深度解析：

标题：{title}
来源：{source}

正文：
{content}

请用markdown返回：
### 核心观点
（1-2句）

### 关键细节
- 点1
- 点2
- 点3

### 启示
（1句话）"""


def call_deepseek(prompt: str, api_key: str, max_tokens=800) -> str:
    payload = json.dumps({
        'model': 'deepseek-v4-pro',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
    }).encode('utf-8')
    req = urllib.request.Request(
        'https://api.deepseek.com/v1/chat/completions',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data['choices'][0]['message']['content']


def clean_ads(text: str) -> str:
    for pat in AD_PATTERNS:
        text = pat.sub('', text)
    text = re.sub(r'\n来源：[^\n]+\n编辑：[^\n]+\n校对：[^\n]+\n校审：[^\n]+', '', text)
    text = re.sub(r'\n>/ [^\n]+', '', text)
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip()


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('date_dir', nargs='?', help='日期目录')
    parser.add_argument('--api-key', help='DeepSeek API key')
    parser.add_argument('--interest', default='AI', help='兴趣主题，默认 AI')
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print('[ERROR] 需要 DeepSeek API key'); sys.exit(1)

    if args.date_dir:
        base = Path(OUTPUT_ROOT) / args.date_dir
    else:
        dirs = sorted(Path(OUTPUT_ROOT).glob('202*'), reverse=True)
        if not dirs: print('[ERROR] 未找到日报目录'); sys.exit(1)
        base = dirs[0]

    # Gather all md files (may be in subdirs from previous runs)
    md_files = sorted([f for f in base.rglob('*.md') if f.name != 'README.md'])
    print(f'目录: {base}')
    print(f'文章: {len(md_files)} 篇')
    print(f'兴趣: {args.interest}\n')

    # ====== Step 1: Clean ads ======
    print('=== Step 1: 广告清洗 ===')
    cleaned = 0
    for fpath in md_files:
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = clean_ads(content)
        if len(new_content) != len(content):
            cleaned += 1
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(new_content)
    print(f'  清洗 {cleaned} 篇\n')

    # ====== Step 2: Classify with DeepSeek ======
    print('=== Step 2: DeepSeek 主题分类 ===')
    topic_map = {}
    for i, fpath in enumerate(md_files):
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        title = re.search(r'^# (.+)', content, re.MULTILINE)
        summary = re.search(r'## AI 摘要\n\n(.+?)(?:\n\n---|\Z)', content, re.DOTALL)
        title = title.group(1) if title else fpath.stem
        summary = summary.group(1)[:300] if summary else content[200:500]

        topic = '学术'
        try:
            prompt = CLASSIFY_PROMPT + f'\n\n标题：{title}\n摘要：{summary}'
            result = call_deepseek(prompt, api_key, max_tokens=500).strip()
            for t in TOPICS:
                if t in result:
                    topic = t; break
        except Exception as e:
            print(f'  [{i+1}] ERR: {e}')

        topic_map[fpath] = topic
        print(f'  [{i+1}/{len(md_files)}] [{topic}] {title[:50]}')
        time.sleep(0.15)

    dist = Counter(topic_map.values())
    print(f'  分布: {dict(dist)}\n')

    # ====== Step 3: Deep summary for interest ======
    interest_files = [f for f, t in topic_map.items() if t == args.interest]
    if interest_files:
        print(f'=== Step 3: [{args.interest}] 深度摘要 ({len(interest_files)}篇) ===')
        for i, fpath in enumerate(interest_files):
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
            title = re.search(r'^# (.+)', content, re.MULTILINE)
            source = re.search(r'来源：(.+)', content)
            title = title.group(1) if title else ''
            source = source.group(1).strip() if source else ''

            body_match = re.search(r'## 正文\n\n(.+)', content, re.DOTALL)
            body = body_match.group(1)[:5000] if body_match else content[500:5500]

            try:
                prompt = INTEREST_PROMPT.format(title=title, source=source, content=body)
                deep = call_deepseek(prompt, api_key, max_tokens=2000)
                content = re.sub(
                    r'## AI 摘要\n\n.+?(?=\n\n---|\Z)',
                    f'## 深度解析\n\n{deep}',
                    content, flags=re.DOTALL,
                )
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f'  [{i+1}/{len(interest_files)}] {title[:50]}')
                time.sleep(0.3)
            except Exception as e:
                print(f'  [{i+1}] ERR: {e}')

    # ====== Step 4: Move to topic folders ======
    print(f'\n=== Step 4: 重建目录 ===')
    for topic in TOPICS:
        (base / topic).mkdir(exist_ok=True)
    for fpath, topic in topic_map.items():
        try:
            dest = base / topic / fpath.name
            if fpath.parent != dest.parent:
                shutil.move(str(fpath), str(dest))
        except:
            pass

    # ====== Step 5: README ======
    lines = [f'# 公众号日报 — {base.name}', '', f'共 {len(md_files)} 篇 | 兴趣: {args.interest}', '']
    for topic in TOPICS:
        files = sorted((base / topic).glob('*.md'))
        if not files: continue
        fire = ' 🔥' if topic == args.interest else ''
        lines.append(f'## {topic}{fire} ({len(files)}篇)')
        lines.append('')
        for j, f in enumerate(files):
            with open(f, 'r', encoding='utf-8') as fh:
                t = fh.readline().strip('#').strip()
            lines.append(f'{j+1}. [{t}](./{topic}/{f.name})')
        lines.append('')

    with open(base / 'README.md', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'\n✓ 完成！')
    for topic in TOPICS:
        n = len(list((base / topic).glob('*.md')))
        if n: print(f'  {topic}: {n} 篇')


if __name__ == '__main__':
    main()
