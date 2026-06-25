#!/usr/bin/env python3
"""
后处理：广告清洗 → 兴趣主题深度摘要 → 分文件夹 → 重建README。

前置: 已运行 biz_daily.py，md 文件中已含【主题】标签。

用法: python scripts/classify_daily.py [date_dir] --api-key <key> [--interest AI]
"""

import sys, os, json, re, time, urllib.request, shutil
from collections import Counter
from pathlib import Path

# 公共工具
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import (
    call_ai, parse_frontmatter, write_with_frontmatter,
    DEFAULT_USER_PROFILE, generate_action_suggestion, load_config
)

OUTPUT_ROOT = 'output/biz-daily'
TOPICS = ['AI', '学术', '新闻', '文学', '投资']

AD_PATTERNS = [
    re.compile(r'在小说阅读器读本章\s*'),
    re.compile(r'在小说阅读器中沉浸阅读\s*'),
    re.compile(r'去阅读\s*'),
    re.compile(r'Scan to Follow\s*'),
    re.compile(r'轻触阅读原文\s*'),
    re.compile(r'预览时标签不可点\s*'),
    re.compile(r'继续滑动看下一个\s*'),
    re.compile(r'\[.*?\]\(javascript:void\(0\);\)'),
    re.compile(r'\n{4,}'),
]

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


BATCH_ACTION_PROMPT = """你是读者的科研助手。基于以下文章列表，为读者筛选并生成可落地的行动建议。

【读者定位】
{profile}

【文章列表】
{articles}

请完成以下任务：

1. **筛选**：只保留对读者有实际落地价值的文章（排除纯新闻、娱乐、广告、无关内容）
2. **评估**：对每篇保留的文章，判断相关度（高/中）并给出理由
3. **生成建议**：为每篇保留的文章生成三级行动建议：
   - **立即可做**：今天就能执行的具体动作
   - **本周计划**：本周可以推进的中期动作
   - **长期关注**：值得持续跟踪的方向（仅高相关度时输出）

输出格式要求：

```
## 📋 今日行动清单（快速浏览）
- [ ] 动作1（来自：文章标题）
- [ ] 动作2（来自：文章标题）
...

---

## 详细建议

### 🔴 高相关度

#### 1. 《文章标题》
> 来源：公众号名 | 主题：AI/学术

**相关度**：高 — 一句话解释原因

**行动建议**
- **立即可做**：xxx
- **本周计划**：xxx
- **长期关注**：xxx

### 🟡 中相关度

#### 1. 《文章标题》
...
```

如果筛选后没有值得建议的文章，直接输出：「今日文章暂无直接可落地的行动建议，建议信息性阅读即可。」"""


def generate_batch_action_suggestions(articles_data: list[dict], api_key: str, profile: str = '',
                                      engine: str = 'deepseek') -> str:
    """批量生成行动建议。
    
    Args:
        articles_data: 文章数据列表，每项包含 title, source, topic, summary, content
        api_key: DeepSeek API key
        profile: 用户定位描述
        
    Returns:
        格式化的行动建议 markdown 文本
    """
    if not profile:
        profile = DEFAULT_USER_PROFILE
    
    # 构建文章列表文本
    articles_text = []
    for i, a in enumerate(articles_data, 1):
        articles_text.append(
            f"[{i}] 《{a['title']}》\n"
            f"    来源：{a['source']} | 主题：{a['topic']} | 相关度：{a.get('relevance', '中')}\n"
            f"    摘要：{a['summary'][:200]}...\n"
            f"    正文节选：{a['content'][:500]}...\n"
        )
    
    prompt = BATCH_ACTION_PROMPT.format(
        profile=profile,
        articles='\n'.join(articles_text)
    )
    
    return call_ai(prompt, engine, api_key, max_tokens=4000)


def clean_ads(text: str) -> str:
    for pat in AD_PATTERNS:
        text = pat.sub('', text)
    text = re.sub(r'\n来源：[^\n]+\n编辑：[^\n]+\n校对：[^\n]+\n校审：[^\n]+', '', text)
    text = re.sub(r'\n>/ [^\n]+', '', text)
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip()


def extract_topic_from_file(content: str) -> str:
    """Extract topic from YAML frontmatter, fallback to text parsing."""
    fm, _ = parse_frontmatter(content)
    if 'topic' in fm and fm['topic'] in TOPICS:
        return fm['topic']
    # Fallback: legacy text-based parsing
    m = re.search(r'> 主题：(\S+)', content)
    if m: return m.group(1)
    return '学术'


def extract_tags_from_file(content: str) -> list[str]:
    """Extract tags from YAML frontmatter."""
    fm, _ = parse_frontmatter(content)
    return fm.get('tags', [])


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('date_dir', nargs='?', help='日期目录')
    parser.add_argument('--api-key', help='AI API key（云端引擎需要）')
    parser.add_argument('--engine', default='deepseek', help='AI 引擎: local/deepseek/claude/ollama（默认 deepseek）')
    parser.add_argument('--interest', default='AI', help='兴趣主题，默认 AI')
    parser.add_argument('--profile', default='', help='用户定位描述（默认使用环境科学研究生画像）')
    parser.add_argument('--skip-action', action='store_true', help='跳过行动建议生成')
    args = parser.parse_args()

    config = load_config()
    engine = args.engine or 'deepseek'
    api_key = args.api_key or ''
    # 本地引擎不需要 api_key
    if engine in ('deepseek', 'claude') and not api_key:
        api_key = os.environ.get('DEEPSEEK_API_KEY', '') or config.get('deepseekApiKey', '')
        if not api_key:
            print(f'[ERROR] --engine {engine} 需要 API key。请通过 --api-key、环境变量或配置文件提供')
            sys.exit(1)
    
    user_profile = args.profile if args.profile else DEFAULT_USER_PROFILE

    if args.date_dir:
        base = Path(OUTPUT_ROOT) / args.date_dir
    else:
        dirs = sorted(Path(OUTPUT_ROOT).glob('202*'), reverse=True)
        if not dirs: print('[ERROR] 未找到日报目录'); sys.exit(1)
        base = dirs[0]

    md_files = sorted([f for f in base.rglob('*.md') if f.name != 'README.md'])
    print(f'目录: {base}')
    print(f'文章: {len(md_files)} 篇 | 兴趣: {args.interest}\n')

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

    # ====== Step 2: Read topics from md (already set by biz_daily) ======
    print('=== Step 2: 读取主题 ===')
    topic_map = {}
    for fpath in md_files:
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        topic = extract_topic_from_file(content)
        if topic not in TOPICS:
            for t in TOPICS:
                if t in topic: topic = t; break
            else: topic = '学术'
        topic_map[fpath] = topic

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
                deep = call_ai(prompt, engine, api_key, max_tokens=2000)
                content = re.sub(
                    r'## AI 摘要\n\n.+?(?=\n\n---|\Z)',
                    f'## 深度解析\n\n{deep}',
                    content, flags=re.DOTALL,
                )
                # Update frontmatter: mark as enhanced
                fm, body = parse_frontmatter(content)
                if fm:
                    fm['enhanced'] = 'true'
                    write_with_frontmatter(str(fpath), fm, body)
                else:
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(content)
                print(f'  [{i+1}/{len(interest_files)}] {title[:50]}')
                time.sleep(0.3)
            except Exception as e:
                print(f'  [{i+1}] ERR: {e}')

    # ====== Step 4: Generate action suggestions (batch) ======
    if not args.skip_action:
        print(f'\n=== Step 4: 生成行动建议 ===')
        # 只处理 AI 和 学术 主题的文章
        candidate_files = [f for f, t in topic_map.items() if t in ['AI', '学术']]
        if candidate_files:
            articles_data = []
            skipped_articles = []
            for fpath in candidate_files:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                fm, body = parse_frontmatter(content)
                title = fm.get('title', '') if fm else ''
                source = fm.get('source', '') if fm else ''
                topic = fm.get('topic', '') if fm else ''
                relevance = fm.get('relevance', '中') if fm else '中'
                
                # 提取摘要
                summary_match = re.search(r'## AI 摘要\n\n(.+?)(?=\n\n---|\n\n## |\Z)', content, re.DOTALL)
                summary = summary_match.group(1).strip() if summary_match else ''
                
                # 提取正文
                body_match = re.search(r'## 正文\n\n(.+)', content, re.DOTALL)
                body_text = body_match.group(1)[:3000] if body_match else content[:3000]
                
                articles_data.append({
                    'title': title,
                    'source': source,
                    'topic': topic,
                    'relevance': relevance,
                    'summary': summary,
                    'content': body_text,
                })
            
            # 收集被跳过的文章（新闻/文学/投资类）
            for fpath, topic in topic_map.items():
                if topic not in ['AI', '学术']:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    fm, _ = parse_frontmatter(content)
                    title = fm.get('title', '') if fm else ''
                    source = fm.get('source', '') if fm else ''
                    skipped_articles.append({'title': title, 'source': source, 'topic': topic})
            
            try:
                print(f'  候选文章: {len(articles_data)} 篇 (AI/学术)')
                print(f'  跳过文章: {len(skipped_articles)} 篇 (新闻/文学/投资)')
                action_content = generate_batch_action_suggestions(articles_data, api_key, user_profile, engine)
                
                # 添加被跳过的文章列表
                if skipped_articles:
                    action_content += '\n\n---\n\n## 被跳过的文章（主题不符）\n\n'
                    for a in skipped_articles:
                        action_content += f"- 《{a['title']}》| 来源：{a['source']} | 主题：{a['topic']}\n"
                
                # 写入行动建议文件
                action_file = base / '行动建议.md'
                with open(action_file, 'w', encoding='utf-8') as f:
                    f.write(f'# 行动建议 — {base.name}\n\n')
                    f.write(f'> 基于定位：{user_profile[:80]}...\n\n')
                    f.write(action_content)
                print(f'  ✓ 已生成: {action_file}')
            except Exception as e:
                print(f'  [WARN] 行动建议生成失败: {e}')
        else:
            print('  无 AI/学术 类文章，跳过行动建议')
    else:
        print(f'\n=== Step 4: 跳过行动建议 (--skip-action) ===')

    # ====== Step 5: Move to topic folders ======
    print(f'\n=== Step 5: 重建目录 ===')
    # 保护收藏文件夹和状态文件
    fav_dir = base / '收藏'
    fav_state = base / '.fav_state.json'
    fav_backup = None
    if fav_dir.exists():
        import tempfile as _tmp
        fav_backup = Path(_tmp.mkdtemp()) / '收藏'
        shutil.copytree(str(fav_dir), str(fav_backup))
        shutil.rmtree(str(fav_dir))
    for topic in TOPICS:
        (base / topic).mkdir(exist_ok=True)
    for fpath, topic in topic_map.items():
        try:
            dest = base / topic / fpath.name
            if fpath.parent != dest.parent:
                shutil.move(str(fpath), str(dest))
        except:
            pass
    # 恢复收藏
    if fav_backup and fav_backup.exists():
        shutil.copytree(str(fav_backup), str(fav_dir))
        shutil.rmtree(str(fav_backup.parent))

    # ====== Step 6: README ======
    # Preserve briefing block from biz_daily README
    briefing_block = []
    old_readme = base / 'README.md'
    if old_readme.exists():
        try:
            with open(old_readme, 'r', encoding='utf-8') as f:
                for line in f:
                    if '📋' in line and '简报' in line:
                        briefing_block.append(line.rstrip())
                        for line in f:
                            if line.startswith('>'):
                                stripped = line.rstrip()
                                if stripped != '>':  # skip empty blockquote lines
                                    briefing_block.append(stripped)
                            else:
                                break
                        break
        except:
            pass

    action_badge = ' ✅' if (base / '行动建议.md').exists() else ''
    lines = [
        f'# 公众号日报 — {base.name}',
        '',
    ]
    if briefing_block:
        lines.extend(briefing_block)
        lines.append('')
    lines.extend([
        f'共 {len(md_files)} 篇 | 兴趣: {args.interest}',
        '',
        f'**[📋 查看行动建议](./行动建议.md){action_badge}** — 基于你的定位生成的可落地建议',
        ''
    ])
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
