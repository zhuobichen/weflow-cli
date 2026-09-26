#!/usr/bin/env python3
"""
我的笔记 → 知识卡 — 让 AI 读你自己写的东西，再长出知识页。

用法:
  python scripts/user_notes.py --dry-run --json      # 看代价（只读本地、零出境）
  python scripts/user_notes.py --yes                 # 读你的笔记，每条一次调用
  python scripts/compile_wiki.py --source output/user-notes

## 这条线为什么与另外三条不同

另外三条（文章/对话/收藏）读的都是**素材**——别人写的文章、你聊过的话。这一条读的是
**你自己写的东西**，所以有两处必须守死：

1. **你的笔记只读，绝不修改、绝不移动**。AI 产出的是**另一份东西**（卡片），写在
   `output/user-notes/` 里。要改要删你的笔记，只有你自己来。
2. **卡上的摘要标 `summary_by: model`**：那是**模型读你的笔记的理解**，不是你的原话。
   混起来最危险——一段模型的理解被当成"我自己写的"，而它可能理解偏了。

一处刻意的设计：**你笔记里自己写的 `[[链接]]` 优先**。你自己起的概念名比模型起的好，
所以两者合并时以你的为准（模型只补你没提到的那部分）。

## 落在哪个目录

默认读你的"人写层"：`000_Inbox` / `003_Ideas` / `004_Permanent` / `005_Reference` /
`006_Projects` / `008_MOC` / `999_Archive`，以及**库根目录**散着的 .md（就是你在 Obsidian
里直接新建的那种）。绝不读 `Wiki/`（那是生成的）、`001_Daily`/`002_Literature`（管线产出）
和 `Sources/`（原始素材）。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import (SEPARATOR, call_deepseek, get_api_key, load_config,  # noqa: E402
                    note_path, parse_frontmatter, plain, strip_wx_ads, write_with_frontmatter)

TZ = timezone(timedelta(hours=8))
VAULT = 'output/wechat-vault'
OUTPUT_ROOT = 'output/user-notes'
DEFAULT_LIMIT = 20
MIN_NOTE_CHARS = 120          # 比另外两条低：自己写的一段话往往不长，但句句是信号
MAX_NOTE_CHARS = 6000

# 你的"人写层"。不在这里面的目录一律不读（生成的、管线的、素材的都不动）
HUMAN_LAYERS = ('000_Inbox', '003_Ideas', '004_Permanent', '005_Reference',
                '006_Projects', '008_MOC', '999_Archive')

NOTE_PROMPT = """下面是**用户自己写的一篇笔记**。你是知识库的编译者。

请只依据这篇笔记，返回一个 JSON 对象（不要解释、不要代码块围栏）：
{{"summary": "这篇笔记在讲什么（2-3 句，只写笔记里有的，用你自己的话复述）",
  "concepts": [{{"name": "概念名（2-12 字，不要带书名号引号方括号）", "desc": "这篇笔记关于它说了什么（一句话）"}}]}}

要求：
1. **只写笔记里有的**。笔记没展开的地方不许替他补；拿不准的宁可不写。
   **尤其不要替他把结论补完整**——他不确定的地方，你也不许装作确定。
2. `concepts` 2-6 条、按重要度排序；概念要具体（"MCP 协议"可以，"技术"不行），
   也不是分类词（"AI"、"工作"这种不算概念）。
3. 每条 desc 是"这篇笔记关于它的说法"，不是百科定义。
4. 全部中文，不要 markdown 语法（不要 #、*、-）。

笔记标题：{title}

正文：
{body}
"""


def list_human_notes(layers=HUMAN_LAYERS, vault=VAULT) -> list:
    """你的笔记：人写层里的 .md + 库根目录散着的 .md。**生成的东西一概不读。**"""
    root = Path(vault)
    if not root.exists():
        return []
    found = []
    for path in sorted(root.glob('*.md')):            # 库根目录（你在 Obsidian 里新建的）
        found.append(path)
    for layer in layers:
        directory = root / layer
        if directory.is_dir():
            found.extend(sorted(directory.rglob('*.md')))
    notes = []
    for path in found:
        if path.name == '00-Overview.md' or '.obsidian' in path.parts:
            continue
        try:
            frontmatter, body = parse_frontmatter(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        notes.append({
            'path': path,
            'rel': str(path.relative_to(root)).replace('\\', '/'),
            'title': str(frontmatter.get('title') or path.stem),
            'body': strip_wx_ads(body),
            'links': own_links(body),
        })
    return notes


_LINK = None


def own_links(body: str) -> list:
    """笔记里**你自己写的** `[[链接]]`（连同破折号后的说明）。"""
    global _LINK
    if _LINK is None:
        import re
        _LINK = re.compile(r'\[\[([^\]]+)\]\](?:\s*—?\s*([^\n]+))?')
    out = []
    for name, desc in _LINK.findall(body or ''):
        name = plain(name).strip().strip('[]')
        if name and name not in [item['name'] for item in out]:
            out.append({'name': name, 'desc': plain(desc).strip()})
    return out


def merge_concepts(own: list, mined: list, limit: int = 6) -> list:
    """**你自己写的链接优先**，模型补你没提到的部分。

    你自己起的概念名比模型起的好用（你以后还会用它搜），所以同名的以你的为准、
    且排在前面；模型只在没提到的地方补。
    """
    merged = []
    seen = set()
    for item in list(own) + list(mined):
        name = str(item.get('name') or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        desc = str(item.get('desc') or '').strip() or '(笔记里提到，未展开)'
        merged.append({'name': name, 'desc': desc})
        if len(merged) >= limit:
            break
    return merged


def parse_note(raw: str):
    """解出 `{summary, concepts}`。解不出来返回 None（**不猜**）。"""
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
    if not isinstance(data, dict):
        return None
    summary = plain(data.get('summary')).strip()
    if not summary:
        return None
    # `concepts` **必须是列表**（与另外三条线同一个契约）：字段在但类型不对，说明这次返回
    # 没按形状来——那就说没解出来，别拿它的一部分当结果（尤其是别把 dict 的键当概念名）
    if not isinstance(data.get('concepts'), list):
        return None
    mined = []
    for item in data['concepts']:
        if isinstance(item, dict):
            name = plain(item.get('name')).strip().strip('[]')
            desc = plain(item.get('desc')).strip()
            if name and desc:
                mined.append({'name': name, 'desc': desc})
    return {'summary': summary, 'mined': mined}


def build_card(note: dict, summary: str, concepts: list, updated: str):
    """卡片 → (frontmatter, body)。**摘要标明是模型的理解**，不是你的原话。"""
    frontmatter = {
        'title': note['title'],
        'type': 'user-card',
        'source': '我的笔记',
        'topic': '我的笔记',
        'tags': ['我的笔记'],
        'from': note['rel'],
        'summary_by': 'model',        # 模型读你的笔记后的理解，不是抄你的原话
        'noteChars': len(note['body'].strip()),
        'updated': updated,
    }
    parts = ['# %s\n' % note['title'], '## AI 摘要\n',
             '（以下是模型读你笔记后的复述，不是你的原话）\n', plain(summary) + '\n']
    if concepts:
        parts.append('## 概念\n')
        for concept in concepts:
            parts.append('- [[%s]] %s %s' % (concept['name'], SEPARATOR, concept['desc']))
        parts.append('')
    return frontmatter, '\n'.join(parts)


def already_carded(note: dict, out_dir: str) -> bool:
    card = note_path(out_dir, note['title'])
    if not card.exists():
        return False
    try:
        return card.stat().st_mtime >= note['path'].stat().st_mtime
    except OSError:
        return False


def exit_code(written: int) -> int:
    return 0 if written else 1


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='读你自己的笔记，长成知识卡（只读你的笔记，绝不修改）')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT,
                        help='最多处理几篇笔记（默认 %d）' % DEFAULT_LIMIT)
    # `--vault` 主要是给测试与演示用的：默认只读你自己的 Vault，
    # 但"能换一个库跑"让整条链路可以在临时目录里被验证（不必往你的库里塞东西）
    parser.add_argument('--vault', default=VAULT, help='库目录（默认 %s）' % VAULT)
    parser.add_argument('--out', default=OUTPUT_ROOT, help='卡片输出目录')
    parser.add_argument('--refresh', action='store_true', help='已有卡的也重做')
    parser.add_argument('--dry-run', action='store_true', help='只报要发多少给模型，不调用')
    parser.add_argument('--yes', action='store_true', help='确认把笔记发给生成模型')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    if args.limit < 1:
        print(json.dumps({'success': False, 'error': '--limit 要 ≥ 1'}))
        return 1

    notes = list_human_notes(vault=args.vault)
    runnable, empty, existing = [], [], []
    for note in notes[:args.limit]:
        if len(note['body'].strip()) < MIN_NOTE_CHARS:
            empty.append({'title': note['title'][:30], 'chars': len(note['body'].strip())})
            continue
        (runnable if args.refresh or not already_carded(note, args.out) else existing).append(note)

    if args.dry_run:
        preview = {
            'success': True, 'dryRun': True, 'action': 'user-notes',
            'notes': len(notes), 'calls': len(runnable),
            'empty': len(empty), 'alreadyCarded': len(existing),
            'chars': sum(len(n['body']) for n in runnable),
            'emptyTitles': [item['title'] for item in empty][:10],
            'model': 'DeepSeek（生成）',
            'readsLocalData': True, 'invokesAI': False, 'modifiesYourNotes': False,
            'note': '每条笔记一次调用；你的笔记**只读**，产出写到 %s' % args.out,
        }
        if args.json:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        else:
            print('你的笔记 %d 篇：会调用模型 %d 次（空/太短跳过 %d 篇，已有卡 %d 篇）'
                  % (len(notes), len(runnable), len(empty), len(existing)))
            if empty:
                print('  跳过的：%s' % '、'.join(item['title'] for item in empty[:8]))
            if not notes:
                print('  还没有笔记——在人写层（000_Inbox / 003_Ideas / 004_Permanent …）')
                print('  或在库根目录新建一篇 .md，写几句话再来')
        return 0

    if not runnable:
        message = '没有要处理的笔记（%d 篇空或太短）' % len(empty) if notes else '你的笔记层还是空的'
        print(json.dumps({'success': False, 'error': message}, ensure_ascii=False)
              if args.json else message, file=sys.stdout if args.json else sys.stderr)
        return 1

    if not args.yes:
        print('会把 %d 篇你自己的笔记发给 DeepSeek（每篇一次调用）。加 --yes 确认。' % len(runnable),
              file=sys.stderr)
        return 1

    config = load_config()
    api_key = get_api_key(config)
    if not api_key:
        print('没有配置 deepseekApiKey（weflow-cli config set deepseekApiKey "..."）', file=sys.stderr)
        return 1

    updated = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
    written, failed = [], []
    for note in runnable:
        prompt = NOTE_PROMPT.format(title=note['title'], body=note['body'][:MAX_NOTE_CHARS])
        try:
            raw = call_deepseek(prompt, api_key, max_tokens=2000, timeout=120)
        except Exception as error:
            failed.append({'title': note['title'][:30], 'reason': '调用失败：%s' % error})
            continue
        parsed = parse_note(raw)
        if parsed is None:
            failed.append({'title': note['title'][:30], 'reason': '模型没返回可用 JSON',
                           'rawTail': str(raw)[-200:] if raw else '(空)'})
            continue
        concepts = merge_concepts(note['links'], parsed['mined'])
        target = note_path(args.out, note['title'])
        target.parent.mkdir(parents=True, exist_ok=True)
        frontmatter, body = build_card(note, parsed['summary'], concepts, updated)
        write_with_frontmatter(str(target), frontmatter, body)
        written.append({'title': note['title'][:30], 'file': str(target),
                        'concepts': len(concepts),
                        'ownLinks': len(note['links']), 'from': note['rel']})

    result = {'success': True, 'action': 'user-notes', 'model': 'DeepSeek（生成）',
              'sendsNothing': True, 'modifiesYourNotes': False,
              'written': written, 'failed': failed, 'empty': empty,
              'alreadyCarded': len(existing), 'notes': len(notes), 'calls': len(runnable)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in written:
            print('✓ %s（%d 个概念，其中你自己链的 %d 个）' % (item['title'], item['concepts'], item['ownLinks']))
        for item in failed:
            print('✗ %s：%s' % (item['title'], item['reason']), file=sys.stderr)
        print('\n共 %d 张卡；接着跑 python scripts/compile_wiki.py --source %s'
              % (len(written), args.out))
    return exit_code(len(written))


if __name__ == '__main__':
    sys.exit(main())
