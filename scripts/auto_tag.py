#!/usr/bin/env python3
"""
自动标签 — AI 为缺少标签的文章生成标签，支持 Dataview 聚合。

用法:
  python scripts/auto_tag.py --date 2026-05-22
  python scripts/auto_tag.py --date 2026-05-22 --api-key <key>
"""

import sys, os, json, time
from pathlib import Path

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
DEFAULT_SOURCE = os.path.join(PROJECT_ROOT, 'output', 'biz-daily')

TOPIC_ORDER = ['AI', '学术', '新闻', '文学', '投资']

from _utils import call_deepseek, parse_frontmatter as _parse_fm


def parse_frontmatter(text: str):
    """解析 frontmatter，返回 (meta, body)。"""
    if not text.startswith('---'):
        return {}, text
    end = text.find('---', 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 3:].strip()
    meta = {}
    for line in fm_text.split('\n'):
        line = line.strip()
        if ':' in line:
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith('[') and val.endswith(']'):
                val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(',') if v.strip()]
            meta[key] = val
    return meta, body


def generate_tags(title: str, body: str, api_key: str) -> list[str]:
    """用 AI 生成 3-5 个标签。"""
    prompt = f"""为以下文章生成 3-5 个标签，用逗号分隔（英文）。
适合 Obsidian Dataview 查询，尽量使用通用概念词。

标题：{title}
正文摘要：{body[:500]}

只返回标签，不要其他内容。示例格式：machine-learning, remote-sensing, air-quality"""
    try:
        result = call_deepseek(prompt, api_key, max_tokens=60, temperature=0.3)
        tags = [t.strip().lower().replace(' ', '-') for t in result.split(',')]
        return [t for t in tags if t and len(t) < 30][:5]
    except Exception as e:
        print(f'    [WARN] AI 标签生成失败: {e}')
        return []


def write_frontmatter(filepath: Path, fm: dict, body: str):
    """写回带 frontmatter 的文件。"""
    lines = ['---']
    for k, v in fm.items():
        if v is None or v == '' or v == []:
            continue
        if isinstance(v, list):
            lines.append(f'{k}: [{", ".join(v)}]')
        else:
            lines.append(f'{k}: "{v}"')
    lines.append('---')
    lines.append('')
    lines.append(body)
    filepath.write_text('\n'.join(lines), encoding='utf-8')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='AI 自动标签 → 统一标签系统')
    parser.add_argument('--date', required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--source', default=DEFAULT_SOURCE, help='源目录')
    parser.add_argument('--api-key', default='', help='DeepSeek API key')
    parser.add_argument('--force', action='store_true', help='覆盖已有标签')
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print('[ERROR] 缺少 API Key（--api-key 或 DEEPSEEK_API_KEY 环境变量）')
        sys.exit(1)

    date_dir = os.path.join(args.source, args.date)
    if not os.path.isdir(date_dir):
        print(f'[ERROR] 目录不存在: {date_dir}')
        sys.exit(1)

    tagged = 0
    skipped = 0
    for topic in TOPIC_ORDER:
        topic_dir = Path(date_dir) / topic
        if not topic_dir.is_dir():
            continue
        for md_file in sorted(topic_dir.glob('*.md')):
            try:
                content = md_file.read_text(encoding='utf-8')
            except Exception:
                continue
            fm, body = parse_frontmatter(content)
            existing = fm.get('tags', [])
            if existing and not args.force:
                skipped += 1
                continue

            print(f'  {topic}/ {md_file.name[:50]}')
            new_tags = generate_tags(fm.get('title', md_file.stem), body, api_key)
            if new_tags:
                fm['tags'] = new_tags
                write_frontmatter(md_file, fm, body)
                tagged += 1
                print(f'    → {", ".join(new_tags)}')
                time.sleep(0.5)  # 避免限流

    print(f'\n✓ 完成: {tagged} 篇新增标签, {skipped} 篇已有标签')


if __name__ == '__main__':
    main()
