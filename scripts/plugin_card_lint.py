"""卡片体检：把第三方插件产出的卡片交给**真正的消费端**读一遍。

存在的理由是一句话：**卡片是给 `compile_wiki.scan_articles` 读的，不是给人读的。**
插件作者最容易犯的错不是格式丑，而是写了一张"看起来像卡片、但消费端直接跳过"的文件
——最常见的一种是正文里没有 `[[wikilink]]`，而消费端第一条规则就是没有链接的卡片不参与建页。
那种失败是**静默的**：文件在、目录在、什么都没报错，概念页就是没有。

所以这里不重新实现一遍规则，而是调用 `compile_wiki` 自己的 `parse_frontmatter` /
`concept_links` / `article_topic`。规则改了，这个脚本跟着变，不会出现两份契约。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compile_wiki as cw  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description='检查卡片能否被知识库消费端读出概念')
    parser.add_argument('--cards', required=True, help='卡片目录')
    args = parser.parse_args()

    root = Path(args.cards)
    if not root.is_dir():
        print(json.dumps({'success': False, 'error': '目录不存在'}, ensure_ascii=False))
        return 1

    files = sorted(path for path in root.rglob('*.md') if path.name != 'README.md')
    skipped = []
    readable = 0
    links = 0

    for path in files:
        try:
            content = path.read_text(encoding='utf-8')
        except Exception as error:  # 读不了就报出来，不静默吞掉
            skipped.append({'file': path.name, 'reason': '读不了：%s' % error})
            continue

        frontmatter, body = cw.parse_frontmatter(content)
        found = cw.concept_links(body, cw.article_topic(frontmatter))
        if not found:
            skipped.append({
                'file': path.name,
                'reason': '正文里没有 [[wikilink]]：消费端会跳过这张卡，概念页不会有它的任何内容',
            })
            continue
        readable += 1
        links += len(found)

    print(json.dumps({
        'success': True,
        'cards': len(files),
        'readable': readable,
        'links': links,
        'skipped': skipped,
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
