#!/usr/bin/env python3
"""
收藏知识卡 — 把微信收藏里的文章变成知识卡，喂给 wiki compile。

用法:
  python scripts/fav_notes.py --dry-run --json      # 看代价（只读本地；有哪些要联网抓也报出来）
  python scripts/fav_notes.py --limit 10 --yes      # 先试水 10 条
  python scripts/compile_wiki.py --source output/fav-notes

## 为什么收藏值得单独一条来源

日报那条线吃的是"你今天读了什么"，而**收藏是你明确留下的**——两者不是一回事：
收藏里常有日报窗口之外的文章，而且"我主动存过"本身就是兴趣的最强信号。
（另外这条线自带一个好处：收藏的文章多半日报已经抓过，`fetch_article_cached` 会命中缓存，
所以联网这一步常常是零成本。）

## 与另外两条线的关系

同一形状、同一消费者（`wiki_compile` 的 `--source`）：文章线 `article_notes.py`、
对话线 `chat_notes.py`、收藏线就是本脚本。共用的件在 `_utils.py`。

**一处不同，必须写清**：文章的卡里"摘要"是**照抄**原笔记的，而收藏记录里**没有摘要**
（只有标题/来源/链接）——所以这条线的摘要**是模型写的**。卡片里会标出来
（frontmatter 的 `summary_by: model`），免得它看起来像抄来的原文。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import (SEPARATOR, call_deepseek, decrypt_lock, get_api_key, load_config,  # noqa: E402
                    note_path, plain, strip_wx_ads, write_with_frontmatter)

TZ = timezone(timedelta(hours=8))
OUTPUT_ROOT = 'output/fav-notes'
DEFAULT_LIMIT = 10
MAX_ARTICLE_CHARS = 3000
MIN_ARTICLE_CHARS = 200
LINK_CHARS = 6 * 1024 * 1024      # 单篇正文上限（缓存来的 markdown 一般远小于它）

FAV_PROMPT = """你是知识库的**编译者，不是作者**。下面是一篇用户**收藏**的公众号文章。

请只依据它，返回一个 JSON 对象（不要解释、不要代码块围栏）：
{{"summary": "这篇文章在说什么（2-3 句，只写文章里有的）",
  "concepts": [{{"name": "概念名（2-12 字，不要带书名号引号方括号）", "desc": "这篇文章关于它说了什么（一句话，具体）"}}]}}

要求：
1. **只写文章里有的**。没讲到的不许补；拿不准的宁可不写。
2. `concepts` 2-6 条、按重要度排序；**概念要具体**（"MCP 协议"可以，"技术"不行），
   也不是分类词（"AI"、"新闻"这种主题标签不算概念）。
3. 每条 desc 是"这篇文章关于它的说法"，不是百科定义。
4. 全部中文，不要 markdown 语法（不要 #、*、-）。

文章：{title}（来源：{source}）

正文：
{body}
"""


def read_favorites(limit: int):
    """读收藏列表（本地、只读）。返回 (条目列表, 错误原因)。"""
    import nt_decrypt
    config = load_config()
    fav_db = config.get('favDbPath', '')
    if not fav_db:
        return [], '配置里没有 favDbPath（收藏库还没连上）'
    got = nt_decrypt.get_favorites(fav_db, decrypt_lock(config.get('favKey', '')), limit=limit)
    if isinstance(got, dict) and got.get('error'):
        return [], str(got['error'])
    items = got.get('favorites') if isinstance(got, dict) else got
    return list(items or []), None


def local_text(item: dict) -> str:
    """收藏**自带**的正文（笔记/文本类有，文章类没有）。**不碰网络。**

    与 `favorite_text` 分开，是为了让 `--dry-run` 能说清"哪几条要联网抓"而**不去抓**：
    预览是只读的（它的说明里写着"只读本地"），自己去打网络就名不副实了——我第一版
    就是这么写的，跑 dry-run 时看见它在抓文章才发现。
    """
    for key in ('desc', 'content', 'summary'):
        text = strip_wx_ads(str(item.get(key) or '')).strip()
        if len(text) >= MIN_ARTICLE_CHARS:
            return text
    return ''


def classify_source(item: dict) -> str:
    """这条收藏的正文从哪来（**只看本地信息**）：local / fetch / none。"""
    if local_text(item):
        return 'local'
    return 'fetch' if str(item.get('link') or '').strip() else 'none'


def favorite_text(item: dict, fetch=None):
    """一条收藏的正文：本地文本优先，没有（或太短）就抓链接。返回 (正文, 来源说明)。

    收藏分两种：笔记/文本类**自带内容**（`desc`/`content`），文章类只有 `link`。
    抓取复用日报那条线的 `fetch_article_cached`——不重写第二份（那套微信 UA 绕 WAF、
    gzip、重试是踩出来的），而且**带缓存**：日报抓过的文章这里零网络。

    `fetch` 是**可注入的口子**：默认走 `biz_daily.fetch_article_cached`，测试传桩进来——
    否则单元测试会真去联网（我第一版就是这么写的，跑测试时才发现）。
    """
    for key in ('desc', 'content', 'summary'):
        text = strip_wx_ads(str(item.get(key) or '')).strip()
        if len(text) >= MIN_ARTICLE_CHARS:
            return text, 'local'
    link = str(item.get('link') or '').strip()
    if not link:
        return '', 'no-text'
    if fetch is None:
        try:
            import biz_daily
            fetch = biz_daily.fetch_article_cached
        except Exception as error:
            return '', 'fetch-failed: %s' % type(error).__name__
    try:
        got = fetch(link)
    except Exception as error:
        return '', 'fetch-failed: %s' % type(error).__name__
    if not got:
        return '', 'fetch-failed'
    text, cached = got
    if not text:
        return '', 'fetch-failed'
    return strip_wx_ads(str(text))[:LINK_CHARS], ('cache' if cached else 'network')


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
    if not isinstance(data, dict) or not isinstance(data.get('concepts'), list):
        return None
    summary = plain(data.get('summary')).strip()
    if not summary:
        return None
    concepts = []
    for item in data['concepts']:
        if not isinstance(item, dict):
            continue
        name = plain(item.get('name')).strip().strip('[]')
        desc = plain(item.get('desc')).strip()
        if name and desc:
            concepts.append({'name': name, 'desc': desc})
        if len(concepts) >= 6:
            break
    return {'summary': summary, 'concepts': concepts}


def build_card(item: dict, note: dict, body_len: int, saved: str, updated: str):
    """卡片 → (frontmatter, body)。摘要这次**是模型写的**，所以标出来。"""
    title = str(item.get('title') or '无标题').strip()
    source = str(item.get('source_name') or '').strip()
    frontmatter = {
        'title': title,
        'type': 'fav-card',
        'source': source or '收藏',
        'topic': '收藏',
        'tags': ['收藏'],
        # **收藏时间**不是发布时间——别把它叫 published，那是另一回事
        'saved': saved,
        'url': str(item.get('link') or ''),
        'summary_by': 'model',      # 这条线的摘要不是抄的，标出来
        'bodyChars': body_len,
        'updated': updated,
    }
    parts = ['# %s\n' % title, '## AI 摘要\n', note['summary'] + '\n']
    if note['concepts']:
        parts.append('## 概念\n')
        for concept in note['concepts']:
            parts.append('- [[%s]] %s %s' % (concept['name'], SEPARATOR, concept['desc']))
        parts.append('')
    return frontmatter, '\n'.join(parts)


def already_carded(item: dict, out_dir: str) -> bool:
    """这条收藏已经有卡了吗（按标题落成的文件名）。"""
    title = str(item.get('title') or '').strip()
    return bool(title) and note_path(out_dir, title).exists()


def exit_code(written: int) -> int:
    """部分成功算成功——与另外两条线同一个约定。"""
    return 0 if written else 1


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='收藏知识卡（把收藏变成喂给 wiki compile 的卡）')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT,
                        help='最多处理几条收藏（默认 %d）' % DEFAULT_LIMIT)
    parser.add_argument('--out', default=OUTPUT_ROOT, help='卡片输出目录')
    parser.add_argument('--refresh', action='store_true', help='已有卡的也重做')
    parser.add_argument('--dry-run', action='store_true', help='只报要发多少给模型，不调用')
    parser.add_argument('--yes', action='store_true', help='确认调用云端模型')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    if args.limit < 1:
        print(json.dumps({'success': False, 'error': '--limit 要 ≥ 1'}))
        return 1

    favorites, error = read_favorites(args.limit)
    if error:
        message = '读收藏失败：%s' % error
        print(json.dumps({'success': False, 'error': message}, ensure_ascii=False)
              if args.json else message)
        return 1

    runnable, thin, existing = [], [], []
    for item in favorites:
        if args.refresh or not already_carded(item, args.out):
            runnable.append(item)
        else:
            existing.append(item)

    if args.dry_run:
        # **只看本地信息**：预览是只读的，不替用户去打网络（抓取留给真跑那一步）
        counts = {'local': 0, 'fetch': 0, 'none': 0}
        for item in runnable:
            counts[classify_source(item)] += 1
        preview = {
            'success': True, 'dryRun': True, 'action': 'fav-notes',
            'favorites': len(favorites), 'calls': len(runnable),
            'alreadyCarded': len(existing), 'noText': counts['none'],
            'textSource': {'local': counts['local'], 'needsFetch': counts['fetch']},
            'model': 'DeepSeek（生成）', 'readsLocalData': True, 'fetchArticleUrls': False,
            'invokesAI': False,
            'note': '每条一次调用；正文优先用收藏自带的文本，文章类走日报那条线的抓取（带缓存）'
                    '——抓取发生在真跑时，预览不联网',
        }
        if args.json:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        else:
            print('收藏 %d 条，会调用模型 %d 次（已有卡 %d 条跳过）'
                  % (len(favorites), len(runnable), len(existing)))
            print('正文：自带 %d 条、要联网抓 %d 条、取不到 %d 条（预览不联网，抓取在 --yes 之后）'
                  % (counts['local'], counts['fetch'], counts['none']))
        return 0

    if not runnable:
        message = '没有要处理的收藏（%d 条都已有卡）' % len(favorites)
        print(json.dumps({'success': False, 'error': message}, ensure_ascii=False)
              if args.json else message, file=sys.stdout if args.json else sys.stderr)
        return 1

    if not args.yes:
        print('会把 %d 条收藏的正文发给 DeepSeek（每条一次调用）。加 --yes 确认。' % len(runnable),
              file=sys.stderr)
        return 1

    config = load_config()
    api_key = get_api_key(config)
    if not api_key:
        print('没有配置 deepseekApiKey（weflow-cli config set deepseekApiKey "..."）', file=sys.stderr)
        return 1

    updated = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
    written, failed = [], []
    for item in runnable:
        text, how = favorite_text(item)
        title = str(item.get('title') or '无标题').strip()
        if not text:
            failed.append({'title': title[:30], 'reason': '取不到正文（%s）' % how})
            continue
        saved = ''
        if item.get('update_time'):
            saved = datetime.fromtimestamp(int(item['update_time']), tz=TZ).strftime('%Y-%m-%d')
        prompt = FAV_PROMPT.format(title=title, source=item.get('source_name') or '未知',
                                   body=text[:MAX_ARTICLE_CHARS])
        try:
            raw = call_deepseek(prompt, api_key, max_tokens=2000, timeout=120)
        except Exception as error:
            failed.append({'title': title[:30], 'reason': '调用失败：%s' % error})
            continue
        note = parse_note(raw)
        if note is None:
            failed.append({'title': title[:30], 'reason': '模型没返回可用 JSON',
                           'rawTail': str(raw)[-200:] if raw else '(空)'})
            continue
        target = note_path(args.out, title)
        target.parent.mkdir(parents=True, exist_ok=True)
        frontmatter, body = build_card(item, note, len(text), saved, updated)
        write_with_frontmatter(str(target), frontmatter, body)
        written.append({'title': title[:30], 'file': str(target),
                        'concepts': len(note['concepts']), 'textFrom': how})

    result = {'success': True, 'action': 'fav-notes', 'model': 'DeepSeek（生成）',
              'sendsNothing': True, 'written': written, 'failed': failed,
              'alreadyCarded': len(existing), 'favorites': len(favorites), 'calls': len(runnable)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in written:
            print('✓ %s（%d 个概念，正文来自 %s）' % (item['title'], item['concepts'], item['textFrom']))
        for item in failed:
            print('✗ %s：%s' % (item['title'], item['reason']), file=sys.stderr)
        print('\n共 %d 张卡；接着跑 python scripts/compile_wiki.py --source %s'
              % (len(written), args.out))
    return exit_code(len(written))


if __name__ == '__main__':
    sys.exit(main())
