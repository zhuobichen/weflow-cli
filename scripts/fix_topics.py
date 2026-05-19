#!/usr/bin/env python3
"""修复已生成文章的主题分类（关键词 fallback）。用法: python scripts/fix_topics.py 2026-05-19"""
import sys, os, re
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from biz_daily import _guess_topic

TOPICS = ['AI', '学术', '新闻', '文学', '投资']

def fix_date(date_str: str):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    base = Path('output/biz-daily') / date_str
    if not base.is_dir():
        print(f'[ERROR] 目录不存在: {base}')
        sys.exit(1)

    fixed = 0
    for topic in TOPICS:
        topic_dir = base / topic
        if not topic_dir.is_dir():
            continue
        for md_file in topic_dir.glob('*.md'):
            try:
                content = md_file.read_text(encoding='utf-8')
            except:
                continue

            # Extract current topic from frontmatter
            m = re.search(r'^topic:\s*(.+)', content, re.MULTILINE)
            old_topic = m.group(1).strip() if m else ''

            # Extract title
            title = md_file.stem
            m2 = re.search(r'^title:\s*"?(.+?)"?\s*$', content, re.MULTILINE)
            if m2:
                title = m2.group(1).strip().strip('"').strip("'")

            # Guess topic
            article = {'title': title, 'account_name': ''}
            m3 = re.search(r'^source:\s*"?(.+?)"?\s*$', content, re.MULTILINE)
            if m3:
                article['account_name'] = m3.group(1).strip().strip('"').strip("'")

            guessed = _guess_topic(article)

            if guessed != old_topic:
                # Update frontmatter
                new_content = re.sub(
                    r'^topic:\s*.+', f'topic: {guessed}',
                    content, flags=re.MULTILINE
                )
                # Update topic folder if needed - move file
                if guessed != topic:
                    new_dir = base / guessed
                    new_dir.mkdir(exist_ok=True)
                    new_path = new_dir / md_file.name
                    md_file.rename(new_path)
                    # Also update the file at new location
                    new_path.write_text(new_content, encoding='utf-8')
                    print(f'  [FIX] {title[:50]} -> {guessed} (was {topic})')
                else:
                    md_file.write_text(new_content, encoding='utf-8')
                fixed += 1

    print(f'\n✓ 修复 {fixed} 篇，未变动 {sum(1 for _ in base.rglob("*.md") if _.name != "README.md") - fixed} 篇')

    # Rebuild README
    from biz_daily import sanitize_filename
    all_articles = []
    for topic in TOPICS:
        topic_dir = base / topic
        if not topic_dir.is_dir():
            continue
        for md_file in sorted(topic_dir.glob('*.md')):
            content = md_file.read_text(encoding='utf-8')
            title = md_file.stem
            source = ''
            article_time = ''
            for line in content.split('\n'):
                if line.startswith('title:'):
                    title = line.split(':', 1)[1].strip().strip('"').strip("'")
                elif line.startswith('source:'):
                    source = line.split(':', 1)[1].strip().strip('"').strip("'")
            mm = re.search(r'时间：\d{4}-\d{2}-\d{2} (\d{2}:\d{2})', content)
            if mm: article_time = mm.group(1)
            all_articles.append({'title': title, 'source': source, 'time': article_time, 'topic': topic, 'file': md_file.name})

    index_lines = [f'# 公众号日报 — {date_str}', '', f'共 {len(all_articles)} 篇推送，按主题分类', '']
    for topic in TOPICS:
        group = [a for a in all_articles if a['topic'] == topic]
        if not group: continue
        index_lines.append(f'## {topic} ({len(group)}篇)')
        index_lines.append('')
        index_lines.append('| # | 时间 | 公众号 | 标题 |')
        index_lines.append('|---|------|--------|------|')
        for j, a in enumerate(group):
            index_lines.append(f'| {j+1} | {a["time"]} | {a["source"]} | [{a["title"]}](./{topic}/{a["file"]}) |')
        index_lines.append('')

    with open(base / 'README.md', 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_lines))

    print(f'  README 已重建')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python scripts/fix_topics.py 2026-05-19')
        sys.exit(1)
    fix_date(sys.argv[1])
