"""Batch add local_source links to existing V2 notes."""
from pathlib import Path
import re

vault = Path('output/wechat-vault')
lit_dir = vault / '002_Literature' / 'WeChat'
biz = Path('output/biz-daily')

updated = 0
for date_dir in sorted(lit_dir.iterdir()):
    if not date_dir.is_dir():
        continue
    date_str = date_dir.name
    for note_file in date_dir.glob('*.md'):
        content = note_file.read_text(encoding='utf-8')
        if 'local_source:' in content:
            continue

        title_match = re.search(r'title:\s*"(.+?)"', content)
        source_match = re.search(r'source:\s*"(.+?)"', content)
        if not title_match:
            continue

        title = title_match.group(1)
        source = source_match.group(1) if source_match else ''

        # Find matching biz-daily article
        found = False
        for topic_dir in biz.glob(f'{date_str}/*'):
            if not topic_dir.is_dir():
                continue
            for article in topic_dir.glob('*.md'):
                if article.name == 'README.md':
                    continue
                try:
                    ac = article.read_text(encoding='utf-8')
                except Exception:
                    continue
                if f'title: "{title}"' in ac or f"title: '{title}'" in ac:
                    local_path = 'file:///' + str(article.resolve()).replace('\\', '/')

                    # Add local_source in frontmatter
                    content = content.replace(
                        'source_type: wechat-article\n',
                        f'source_type: wechat-article\nlocal_source: "{local_path}"\n'
                    )

                    # Update callout: rename 链接→网页, add 本地
                    if '📂 打开源文件' not in content:
                        # Replace the link line in callout
                        content = content.replace(
                            '- **链接**: [',
                            '- **网页**: [微信原文]('
                        )
                        # Find where to insert local line (after 网页 line)
                        lines = content.split('\n')
                        for i, line in enumerate(lines):
                            if line.strip().startswith('- **网页**: ['):
                                # Insert local file line after this
                                lines.insert(i + 1, f'> - **本地**: [📂 打开源文件]({local_path})')
                                break
                        content = '\n'.join(lines)

                    note_file.write_text(content, encoding='utf-8')
                    updated += 1
                    found = True
                    break
            if found:
                break

    print(f'  {date_str}: {updated} total')

print(f'\nBatch done: {updated} notes updated')
