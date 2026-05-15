#!/usr/bin/env python3
"""
公众号日报 — 自动抓取今日推送文章，DeepSeek 摘要，输出到日期文件夹。

用法:
  python scripts/biz_daily.py                    # 今天
  python scripts/biz_daily.py --date 2026-05-12  # 指定日期
  python scripts/biz_daily.py --dry-run          # 预览不抓取

输出:
  output/biz-daily/YYYY-MM-DD/
    README.md          # 总索引
    <公众号>-<标题>.md  # 每篇文章
"""

import sys
import os
import json
import hashlib
import random
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 公共工具
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import call_deepseek, load_config, decrypt_lock, write_with_frontmatter, format_wikilinks

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print("请安装: pip install sqlcipher3")
    sys.exit(1)

# scrapling 按需导入（仅在 fetch_article fallback 时需要）
# 完整安装: pip install scrapling html2text playwright
_FETCHER_AVAILABLE = False
try:
    from scrapling.fetchers import Fetcher as _Fetcher
    _FETCHER_AVAILABLE = True
except ImportError:
    pass

# ====== Config ======

CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.weflow-cli', 'config.json')
DB_PATH = None  # auto-detect from config
OUTPUT_ROOT = 'output/biz-daily'
MAX_ARTICLES = 50  # 最多抓取篇数
FETCH_TIMEOUT = 15
DEEPSEEK_TIMEOUT = 60
FETCH_DELAY_MIN = 8   # 最小抓取间隔 (秒)
FETCH_DELAY_MAX = 12  # 最大抓取间隔 (秒)

TOPICS = ['AI', '学术', '新闻', '文学', '投资']
TOPIC_PROMPT = f"""完成三个任务：
1. 用2-4句话总结文章核心内容（中文）
2. 归类到：{' / '.join(TOPICS)}
3. 提取3-5个核心技术/主题标签，以及1-3个关键概念

分类标准：
- AI：AI产品/模型/Agent/工具、编程开发、GitHub开源、科技教程
- 学术：科研论文、Nature/Science期刊、环境科学、实验室
- 新闻：时事政策、社会热点、企业通知、招聘促销
- 文学：散文随笔、生活记录、美食旅游、历史人文
- 投资：股票基金、经济分析、市场趋势、理财策略、商业洞察

严格按格式返回：
【主题】主题名
【标签】tag1, tag2, tag3
【摘要】总结内容
【概念】概念名|简短说明, 概念名|简短说明"""

# ====== Helpers ======


def get_db_keys(config):
    """Extract all needed DB paths and keys from config."""
    nt_db = config.get('ntDbPath', '')
    msg_key_enc = config.get('ntKey', '')
    msg_salt = config.get('ntSalt', '')

    msg_key = decrypt_lock(msg_key_enc)

    # contact.db: derive path from ntDbPath
    msg_dir = os.path.dirname(nt_db.replace('\\', '/'))
    wxid_dir = os.path.dirname(os.path.dirname(msg_dir))  # up to xwechat_files/<wxid>
    contact_db = os.path.join(wxid_dir, 'db_storage', 'contact', 'contact.db')

    contact_key_enc = config.get('contactKey', '')
    contact_salt = config.get('contactSalt', '')
    contact_key = decrypt_lock(contact_key_enc) if contact_key_enc else ''

    # biz_message_0.db (separate key from message/contact db)
    biz_db = os.path.join(msg_dir, 'biz_message_0.db')
    # Try config first, then fallback to known key from scan
    biz_key_enc = config.get('bizKey', '')
    biz_salt = config.get('bizSalt', '')
    if biz_key_enc and biz_salt:
        biz_key = decrypt_lock(biz_key_enc)
    else:
        biz_key = '1c0451540217eb0373eb9242f61c173f8f7b3a4f29e922a9623ed59a3f90630e'
        biz_salt = '0478298c58563d07e3fa53b45f13d593'

    return {
        'biz_db': biz_db, 'biz_key': biz_key, 'biz_salt': biz_salt,
        'contact_db': contact_db, 'contact_key': contact_key, 'contact_salt': contact_salt,
        'msg_db': nt_db, 'msg_key': msg_key, 'msg_salt': msg_salt,
    }


WECHAT_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
    'MicroMessenger/8.0.38(0x18002633) NetType/WIFI Language/zh_CN'
)

def fetch_article(url: str) -> str | None:
    """Fetch WeChat article with WeChat browser UA to bypass WAF."""
    try:
        # L1: Try direct fetch with WeChat UA headers (most stable per wechat-article-exporter)
        req = urllib.request.Request(url, headers={
            'User-Agent': WECHAT_UA,
            'Referer': 'https://mp.weixin.qq.com/',
            'Origin': 'https://mp.weixin.qq.com',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        # Validate: article content must include js_content div
        if 'js_content' not in html and 'rich_media_content' not in html:
            print(f'  [WARN] 内容验证失败 (无 js_content)')
            return None

        # Convert to markdown
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        return h.handle(html)
    except Exception as e:
        # L2: Fallback to Scrapling with custom headers
        if _FETCHER_AVAILABLE:
            try:
                page = _Fetcher.get(url)
                import html2text
                h = html2text.HTML2Text()
                h.ignore_links = False
                h.body_width = 0
                return h.handle(page.html_content)
            except Exception as e2:
                print(f'  [WARN] 抓取失败 (L1+L2): {e}')
                return None
        else:
            print(f'  [WARN] 抓取失败: {e} (scrapling 未安装，无 fallback)')
            return None


def extract_article_info(content_bytes: bytes) -> dict:
    """Extract title, digest, URL, full text from protobuf message_content."""
    import zstandard as zstd
    dctx = zstd.ZstdDecompressor()
    try:
        text = dctx.decompress(content_bytes).decode('utf-8', errors='ignore')
    except:
        text = content_bytes.decode('utf-8', errors='ignore')

    info = {
        'title': '',
        'digest': '',
        'url': '',
        'cover': '',
        'local_text': '',  # locally cached body text
    }
    # Title
    titles = re.findall(r'<title[^>]*><!\[CDATA\[(.*?)\]\]></title>', text)
    if titles:
        info['title'] = titles[0]
    elif not titles:
        titles_plain = re.findall(r'<title[^>]*>(.*?)</title>', text)
        if titles_plain:
            info['title'] = titles_plain[0]

    # Full text from <des> field (some articles put full text here)
    descs = re.findall(r'<des[^>]*><!\[CDATA\[(.*?)\]\]></des>', text, re.DOTALL)
    if descs:
        info['local_text'] = descs[0].strip()

    # Digest (summary)
    digests = re.findall(r'<digest[^>]*><!\[CDATA\[(.*?)\]\]></digest>', text, re.DOTALL)
    if digests and digests[0].strip():
        d = digests[0].strip()
        if len(d) > len(info['local_text']):
            info['local_text'] = d
        elif not info['local_text']:
            info['local_text'] = d
        info['digest'] = d[:300]

    # Summary (some articles use this instead of digest)
    if not info['local_text']:
        summaries = re.findall(r'<summary[^>]*><!\[CDATA\[(.*?)\]\]></summary>', text, re.DOTALL)
        if summaries:
            info['local_text'] = summaries[0].strip()
            info['digest'] = summaries[0][:300]

    # Content desc
    if not info['local_text']:
        content_descs = re.findall(r'<contentDesc>(.*?)</contentDesc>', text)
        if content_descs:
            info['local_text'] = content_descs[0].strip()

    # URL
    urls = re.findall(r'<url[^>]*><!\[CDATA\[(https?://[^<\]]*)\]\]></url>', text)
    if urls:
        info['url'] = urls[0]
    # Cover
    covers = re.findall(r'<cover[^>]*><!\[CDATA\[(https?://[^<\]]*)\]\]></cover>', text)
    if covers:
        info['cover'] = covers[0]

    return info


def sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80]


# ====== Main ======

def main():
    # Fix Windows console encoding
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='公众号日报')
    parser.add_argument('--date', help='日期 YYYY-MM-DD, 默认今天')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不抓取')
    parser.add_argument('--limit', type=int, default=0, help='最多处理篇数 (0=不限制)')
    parser.add_argument('--api-key', help='DeepSeek API key (或设环境变量 DEEPSEEK_API_KEY)')
    args = parser.parse_args()

    # Date
    tz = timezone(timedelta(hours=8))
    if args.date:
        target_date = datetime.strptime(args.date, '%Y-%m-%d').replace(tzinfo=tz)
    else:
        target_date = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = int(target_date.timestamp())
    day_end = day_start + 86400
    date_str = target_date.strftime('%Y-%m-%d')

    # API key
    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not args.dry_run and not api_key:
        print('[ERROR] 缺少 DeepSeek API key。请通过 --api-key 或环境变量 DEEPSEEK_API_KEY 提供')
        sys.exit(1)

    # Load config & DB keys
    config = load_config()
    keys = get_db_keys(config)

    print(f'=== 公众号日报 {date_str} ===')
    print(f'Biz DB: {keys["biz_db"]}')

    # Connect biz db
    raw_key = f"x'{keys['biz_key']}{keys['biz_salt']}'"
    conn = sqlcipher.connect(keys['biz_db'])
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')

    # Load contact names
    name_map = {}
    if keys['contact_key'] and os.path.exists(keys['contact_db']):
        contact_raw = f"x'{keys['contact_key']}{keys['contact_salt']}'"
        try:
            conn2 = sqlcipher.connect(keys['contact_db'])
            c2 = conn2.cursor()
            c2.execute(f'PRAGMA key = "{contact_raw}";')
            c2.execute("SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username) FROM contact")
            name_map = dict(c2.fetchall())
            conn2.close()
        except:
            pass

    # Find today's articles
    c.execute("SELECT user_name FROM Name2Id WHERE user_name LIKE 'gh_%'")
    biz_users = [row[0] for row in c.fetchall()]

    articles = []
    for user in biz_users:
        tbl = 'Msg_' + hashlib.md5(user.encode()).hexdigest()
        try:
            c.execute(f'SELECT create_time, message_content FROM "{tbl}" WHERE create_time >= ? AND create_time < ? ORDER BY create_time',
                      (day_start, day_end))
            for create_time, content in c.fetchall():
                if not content:
                    continue
                info = extract_article_info(content)
                if info['title']:
                    articles.append({
                        'account': user,
                        'account_name': name_map.get(user, user),
                        'title': info['title'],
                        'digest': info['digest'],
                        'url': info['url'],
                        'cover': info['cover'],
                        'local_text': info['local_text'],
                        'time': datetime.fromtimestamp(create_time, tz=tz).strftime('%H:%M'),
                        'timestamp': create_time,
                    })
        except:
            pass

    # Sort by time
    articles.sort(key=lambda a: a['timestamp'])
    conn.close()

    # === Incremental dedup: load previous run state ===
    out_dir = Path(OUTPUT_ROOT) / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    state_file = out_dir / '.run_state.json'
    processed = {}
    if state_file.exists():
        with open(state_file, 'r', encoding='utf-8') as f:
            processed = json.load(f)

    new_articles = []
    skipped = 0
    for a in articles:
        fp = hashlib.sha256(a.get('url', '').encode()).hexdigest()[:16]
        if a.get('url') and fp in processed:
            skipped += 1
            print(f'  [SKIP] {a["title"][:40]}')
        else:
            new_articles.append(a)
    articles = new_articles
    if skipped:
        print(f'  （跳过 {skipped} 篇已处理）')

    # Apply limit after dedup
    if args.limit and args.limit > 0:
        articles = articles[:args.limit]

    print(f'找到 {len(articles)} 篇文章\n')

    if args.dry_run:
        for i, a in enumerate(articles):
            t, n, ti = a['time'], a['account_name'], a['title']
            print(f'{i+1}. [{t}] {n} - {ti}')
        print(f'\n共 {len(articles)} 篇 (预览模式)')
        return

    # Output folder already created above (with dedup state)
    print(f'输出目录: {out_dir}\n')

    # ====== Phase 1: Fetch all articles ======
    print(f'=== Phase 1: 抓取 {len(articles)} 篇文章 ===\n')
    for i, a in enumerate(articles):
        t, n, ti = a['time'], a['account_name'], a['title']
        print(f'[{i+1}/{len(articles)}] [{t}] {n} - {ti[:50]}')

        if a['url']:
            delay = FETCH_DELAY_MIN + random.random() * (FETCH_DELAY_MAX - FETCH_DELAY_MIN)
            md = fetch_article(a['url'])
            if md:
                a['fetched_md'] = md
                print(f'  OK ({len(md)}字, {delay:.1f}s)')
            else:
                print(f'  FAIL, 回退本地缓存')
            time.sleep(delay)
        elif a.get('local_text'):
            print(f'  无URL, 使用本地缓存')

    # ====== Phase 2: DeepSeek summary + topic classification ======
    if api_key:
        print(f'\n=== Phase 2: AI 摘要 + 主题分类 ===\n')
        for i, a in enumerate(articles):
            t, n, ti = a['time'], a['account_name'], a['title']
            content = a.get('fetched_md') or a.get('local_text', '')
            if content and len(content.strip()) > 50:
                try:
                    prompt = TOPIC_PROMPT + f'\n\n标题：{a["title"]}\n来源：{a["account_name"]}\n\n内容：\n{content[:4000]}'
                    response = call_deepseek(prompt, api_key, max_tokens=600)

                    # Parse response: 【主题】xxx 【标签】xxx 【摘要】xxx 【概念】xxx
                    topic_match = re.search(r'【主题】\s*(.+)', response)
                    tags_match = re.search(r'【标签】\s*(.+)', response)
                    # Stop summary at next 【tag or end
                    summary_match = re.search(r'【摘要】\s*(.+?)(?=\n【|$)', response, re.DOTALL)
                    concepts_match = re.search(r'【概念】\s*(.+)', response, re.DOTALL)

                    if topic_match:
                        a['topic'] = topic_match.group(1).strip()
                        if a['topic'] not in TOPICS:
                            a['topic'] = '学术'
                    else:
                        a['topic'] = '学术'

                    # Parse tags: comma-separated, clean up
                    if tags_match:
                        a['tags'] = [t.strip() for t in tags_match.group(1).split(',') if t.strip()]
                    else:
                        a['tags'] = [a['topic']]

                    # Parse concepts: name|desc, name|desc
                    if concepts_match:
                        concepts_raw = concepts_match.group(1).strip()
                        a['concepts'] = []
                        for pair in concepts_raw.split(','):
                            pair = pair.strip()
                            if '|' in pair:
                                name, desc = pair.split('|', 1)
                                a['concepts'].append((name.strip(), desc.strip()))
                            elif pair:
                                a['concepts'].append((pair.strip(), ''))
                    else:
                        a['concepts'] = []

                    if summary_match:
                        a['summary'] = summary_match.group(1).strip()
                    else:
                        a['summary'] = response[:300]

                    print(f'[{i+1}/{len(articles)}] [{t}] {n} - [{a.get("topic","?")}] tags={a.get("tags",[])} ({len(a.get("summary",""))}字)')
                    time.sleep(0.3)
                except Exception as e:
                    a['summary'] = a.get('digest', '') or content[:300]
                    a['topic'] = '学术'
                    a['tags'] = ['学术']
                    a['concepts'] = []
                    print(f'[{i+1}/{len(articles)}] [{t}] {n} - ERR: {e}')
            elif content:
                a['summary'] = content[:400]
                a['topic'] = '学术'
            else:
                a['summary'] = a.get('digest', '(无内容)')
                a['topic'] = '学术'

        # Print topic distribution
        from collections import Counter
        topic_counts = Counter(a.get('topic', '学术') for a in articles)
        print(f'\n  主题分布: {dict(topic_counts)}')

    # ====== Phase 3: Write files (by topic folders) ======
    print(f'\n=== Phase 3: 写入文件 (按主题) ===\n')

    # Create topic subdirs
    for topic in TOPICS:
        (out_dir / topic).mkdir(parents=True, exist_ok=True)

    # Group articles by topic
    topic_groups = {t: [] for t in TOPICS}
    for a in articles:
        t = a.get('topic', '学术')
        if t not in topic_groups:
            t = '学术'
        topic_groups[t].append(a)

    index_lines = [
        f'# 公众号日报 — {date_str}',
        '',
        f'共 {len(articles)} 篇推送，按主题分类',
        '',
    ]

    for topic in TOPICS:
        group = topic_groups[topic]
        if not group:
            continue
        index_lines.append(f'## {topic} ({len(group)}篇)')
        index_lines.append('')
        index_lines.append('| # | 时间 | 公众号 | 标题 |')
        index_lines.append('|---|------|--------|------|')
        for j, a in enumerate(group):
            safe_name = sanitize_filename(f'{a["account_name"]}-{a["title"]}')
            file_name = f'{safe_name}.md'
            file_path = out_dir / topic / file_name

            markdown = a.get('fetched_md') or a.get('local_text', '')
            summary = a.get('summary', a.get('digest', ''))
            tags = a.get('tags', [topic])
            concepts = a.get('concepts', [])

            # Frontmatter
            fm = {
                'title': f'"{a["title"]}"',
                'source': f'"{a["account_name"]}"',
                'date': date_str,
                'topic': topic,
                'tags': tags,
                'created': date_str,
            }
            if a['url']:
                fm['url'] = f'"{a["url"]}"'

            # Body
            body_parts = [f'# {a["title"]}\n']
            body_parts.append(f'> 来源：{a["account_name"]}  \n')
            body_parts.append(f'> 时间：{date_str} {a["time"]}  \n')
            if a['url']:
                body_parts.append(f'> 原文：[阅读原文]({a["url"]})\n')
            body_parts.append('\n---\n\n')
            body_parts.append(f'## AI 摘要\n\n{summary}\n\n')

            # Wiki Links
            if concepts:
                body_parts.append(format_wikilinks(concepts))
                body_parts.append('\n')

            if markdown:
                body_parts.append('---\n\n')
                body_parts.append('## 正文\n\n')
                body = markdown[:10000]
                if len(markdown) > 10000:
                    body += f'\n\n*(原文过长，截取前10000字，共{len(markdown)}字)*'
                body_parts.append(body)

            write_with_frontmatter(str(file_path), fm, ''.join(body_parts))

            index_lines.append(
                f'| {j+1} | {a["time"]} | {a["account_name"]} | [{a["title"]}](./{topic}/{file_name}) |'
            )
        index_lines.append('')

    # Write index
    index_path = out_dir / 'README.md'
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_lines))
        f.write('\n---\n\n*由 weflow-cli 公众号日报自动生成*\n')

    print(f'\n✓ 完成！输出到 {out_dir}')
    print(f'  总索引: {index_path}')
    print(f'  文章数: {len(articles)}')
    for t in TOPICS:
        if topic_groups[t]:
            print(f'  {t}/: {len(topic_groups[t])} 篇')

    # Save run state for incremental dedup
    for a in topic_groups.values():
        for art in a:
            fp = hashlib.sha256(art.get('url', '').encode()).hexdigest()[:16]
            if art.get('url'):
                processed[fp] = art['title'][:50]
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
