#!/usr/bin/env python3
"""历史文章回填 — 把过去几个月的公众号文章抓成正文，喂给知识库。

用法:
  python scripts/backfill_articles.py --since 2026-03-01 --until 2026-08-31 --dry-run --json
  python scripts/backfill_articles.py --since 2026-03-01 --until 2026-08-31 --workers 12 --yes

## 为什么不是直接跑 `pipeline run --date`

三条实测出来的理由，都不是推测：

1. **`pipeline run` 有硬编码的 10 分钟超时**（`bin/weflow-cli.ts:3906` 的 `timeout: 600_000`）。
   那是给"当天日报"那条交互路径设的；一天 100+ 篇历史文章抓不完就被它掐死，产出为零。
2. **`biz_daily` 是逐篇串行抓的，而且每篇都下图片**。实测试点 25 分钟只处理了约 47 篇
   （≈30 秒/篇，大头在 13–21 张配图），29,667 篇要 150 小时以上。
3. **知识库用不到图片**。下游 `create_reading_notes` 只读 md 的 frontmatter（title/source/
   url/topic/date）与正文前 10 行；`compile_wiki` 只认 `## AI 摘要` 那一段。图片一张都不读。

所以本脚本只做三件与 `biz_daily` 不同的事：**并发抓**、**不下图片**、**不落 HTML 阅读器**。
取数、主题归一化、md 格式、`.articles.json` 的字段全部复用 `biz_daily` 里那几个函数，
不另写一份——两份写同一件事就会分叉，本仓库已经因为这类分叉吃过亏（见 `_normalize_topics`
的注释）。

## 一条必须说清的限制

**图片型文章抓不到正文。** 实测 3/4、7/4 两天：27/40、44/60 有正文，其余是图文消息
（内容全在图片里），清洗掉图片与链接后不足 100 字，与 `biz_daily` 一样被跳过。这不是
抓取失败，是那类文章本来就没有文字。所以"2 万篇"这个量级是有正文的部分，不是总量。
"""
import argparse
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import load_config  # noqa: E402
from biz_daily import (DEFAULT_RELEVANCE, DEFAULT_TOPIC, TOPICS,  # noqa: E402
                       _group_by_topic, _guess_topic, _serializable_article,
                       _tags_for_write, extract_article_info, fetch_article,
                       get_db_keys, sanitize_filename, summary_section)
from _utils import write_with_frontmatter  # noqa: E402

TZ = timezone(timedelta(hours=8))
OUTPUT_ROOT = 'output/biz-daily'
# 与 biz_daily 的 `内容过短` 闸门同一个阈值。换成别的数会让两条路对"哪些文章算有正文"
# 给出不同答案——那正是本仓库不许再出现的分叉。
MIN_BODY_CHARS = 100
# 支付/服务通知不是文章，biz_daily 在这张表上过滤过一次，这里沿用同一张表。
SKIP_TITLE_PREFIXES = ('已支付', '已扣费', '支付成功', '扣费预通知', '你已关闭', '下单成功')


def body_without_media(markdown: str) -> str:
    """去掉图片与链接后的正文——判"够不够长"用的是它，不是原文。

    与 `biz_daily` 的清洗口径一致（去图、去链、去换行后再数字数）。
    """
    text = ''.join(str(markdown or '').split('\n'))
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'\[.*?\]\(.*?\)', '', text)
    return text.strip()


def collect_articles(cursor, users, name_map, day):
    """某一天库里所有文章（还没抓正文）。字段与 biz_daily 的 Phase 1 一致。"""
    start = int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=TZ).timestamp())
    end = start + 86400
    out = []
    for user in users:
        table = 'Msg_' + hashlib.md5(user.encode()).hexdigest()
        try:
            rows = cursor.execute(
                'SELECT create_time, message_content FROM "%s" '
                'WHERE create_time >= ? AND create_time < ? ORDER BY create_time' % table,
                (start, end)).fetchall()
        except Exception:
            continue
        for create_time, content in rows:
            if not content:
                continue
            info = extract_article_info(content)
            if not info['title']:
                continue
            if any(info['title'].startswith(p) for p in SKIP_TITLE_PREFIXES):
                continue
            entry = {
                'account': user,
                'account_name': name_map.get(user, user),
                'title': info['title'],
                'digest': info['digest'],
                'url': info['url'],
                'cover': info['cover'],
                'local_text': info['local_text'],
                'time': datetime.fromtimestamp(create_time, tz=TZ).strftime('%H:%M'),
                'timestamp': create_time,
            }
            out.append(entry)
    out.sort(key=lambda a: a['timestamp'])
    return apply_topics(out)


def apply_topics(articles):
    """给每篇定主题（关键词），**就地**改并返回。

    主题必须在这里定，**不能留给下游兜底**：`_group_by_topic` 对没有 topic 的文章一律
    折成 `DEFAULT_TOPIC`，于是整批会静默落进同一个目录。这条踩过——第一次跑完 3/5，
    106 篇 100% 落在 `学术/`，而当时的输出里只写着一句"主题兜底 113 篇"。

    用 `_guess_topic`（关键词），这是 `biz_daily` 在 AI 分类失败时走的同一条退路；
    本脚本不调模型，所以它是这里唯一可用的那条。
    """
    for article in articles:
        if article.get('topic') not in TOPICS:
            article['topic'] = _guess_topic(article)
    return articles


def fetch_bodies(articles, workers: int, log=None):
    """并发把这批文章的正文抓回来，写进 `fetched_md`。

    **只抓正文，不碰图片**——`fetch_article` 返回的 markdown 里图片是链接，本脚本
    就此打住，不再像 `biz_daily` 那样逐张下到本地（那一步占了实测 30 秒/篇里的绝大部分）。
    """
    todo = [a for a in articles
            if len(body_without_media(a.get('local_text'))) < MIN_BODY_CHARS and a.get('url')]
    if not todo:
        return {'attempted': 0, 'ok': 0}

    def one(article):
        try:
            body = fetch_article(article['url'])
        except Exception:
            return False
        if body:
            article['fetched_md'] = body
            return True
        return False

    ok = 0
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        for result in pool.map(one, todo):
            ok += 1 if result else 0
    if log:
        log('    抓正文: 尝试 %d，成功 %d，并发 %d' % (len(todo), ok, workers))
    return {'attempted': len(todo), 'ok': ok}


def write_day(articles, day, out_root):
    """落盘：`<日期>/<主题>/<来源>-<标题>.md` + `.articles.json` + README。

    格式与 `biz_daily` 一致——`create_reading_notes` 与 `compile_wiki` 都按那个形状读。
    正文那段写 `## AI 摘要`（内容用文章自带的 digest）：**本脚本不调模型**，所以这里
    放的只能是原文已有的东西，不能假装是生成的摘要。
    """
    groups, fallbacks = _group_by_topic(articles)
    out_dir = Path(out_root) / day
    written, skipped = [], []
    for topic in TOPICS:
        for article in groups[topic]:
            markdown = article.get('fetched_md') or article.get('local_text') or ''
            if len(body_without_media(markdown)) < MIN_BODY_CHARS:
                skipped.append({'title': article['title'][:30], 'reason': '正文不足 %d 字' % MIN_BODY_CHARS})
                continue
            safe = sanitize_filename('%s-%s' % (article['account_name'], article['title']))
            target = out_dir / topic / (safe + '.md')
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = article.get('digest') or ''
            frontmatter = {
                'title': '"%s"' % article['title'],
                'source': '"%s"' % article['account_name'],
                'date': day,
                'topic': topic,
                'relevance': article.get('relevance', DEFAULT_RELEVANCE),
                'tags': _tags_for_write(article, topic),
                'created': day,
                # 来源标记：正文是回填脚本抓的，不是当天日报那条路
                'backfilled': 'true',
            }
            if article.get('url'):
                frontmatter['url'] = '"%s"' % article['url']
            body = [
                '# %s\n' % article['title'],
                '> 来源：%s  ' % article['account_name'],
                '> 时间：%s %s  ' % (day, article.get('time', '')),
                '> 原文：[阅读原文](%s)\n' % article.get('url', ''),
                '\n---\n',
                summary_section(digest).rstrip('\n'),
                '\n---\n',
                markdown,
            ]
            # 序列化**用 _utils 里那一份**，不另写：frontmatter 的引号规则（含 `:`/`#`/`"`
            # 才加引号、列表写成 `[a, b]`）在两边各写一遍就会分叉，而 `parse_frontmatter`
            # 只认其中一种。
            write_with_frontmatter(str(target), frontmatter, '\n'.join(body))
            written.append(article)
    out_dir.mkdir(parents=True, exist_ok=True)
    serializable = [_serializable_article(a, day) for a in written]
    (out_dir / '.articles.json').write_text(
        json.dumps({'date': day, 'generated_at': datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S'),
                    'backfilled': True, 'articles': serializable},
                   ensure_ascii=False, indent=2), encoding='utf-8')
    (out_dir / 'README.md').write_text(
        '# 公众号文章 — %s（历史回填）\n\n共 %d 篇。正文由 `backfill_articles.py` 抓取，'
        '**未下载图片、未调用模型**。\n' % (day, len(written)), encoding='utf-8')
    return {'written': len(written), 'skipped': len(skipped),
            'fallbackTopics': fallbacks, 'skipSample': skipped[:3]}


def day_done(out_root, day) -> bool:
    """这一天已经回填过了吗（增量：免得重跑把同样的文章再抓一遍）。"""
    path = Path(out_root) / day / '.articles.json'
    if not path.exists():
        return False
    try:
        return bool(json.loads(path.read_text(encoding='utf-8')).get('articles'))
    except Exception:
        return False


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='历史文章回填（只抓正文，不下图片、不调模型）')
    parser.add_argument('--since', required=True, help='起始日期 YYYY-MM-DD（含）')
    parser.add_argument('--until', required=True, help='结束日期 YYYY-MM-DD（含）')
    parser.add_argument('--out', default=OUTPUT_ROOT, help='输出根目录')
    parser.add_argument('--workers', type=int, default=8, help='抓正文的并发数（默认 8）')
    parser.add_argument('--limit-per-day', type=int, default=0, help='每天最多抓几篇（0=不限）')
    parser.add_argument('--refresh', action='store_true', help='已回填过的天也重做（默认跳过）')
    parser.add_argument('--max-days', type=int, default=0, help='最多处理几天（0=不限，用于试跑）')
    parser.add_argument('--dry-run', action='store_true', help='只报到要抓多少，不抓不写')
    parser.add_argument('--yes', action='store_true', help='确认抓取并写入文件')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    try:
        start = datetime.strptime(args.since, '%Y-%m-%d').date()
        end = datetime.strptime(args.until, '%Y-%m-%d').date()
    except ValueError as error:
        print(json.dumps({'success': False, 'error': '日期格式要 YYYY-MM-DD：%s' % error})
              if args.json else '日期格式要 YYYY-MM-DD：%s' % error)
        return 1
    if end < start:
        print('--until 不能早于 --since'); return 1
    if args.workers < 1:
        print('--workers 要 ≥ 1'); return 1

    days = []
    cursor_day = start
    while cursor_day <= end:
        days.append(cursor_day.isoformat())
        cursor_day += timedelta(days=1)
    if args.max_days:
        days = days[:args.max_days]

    keys = get_db_keys(load_config())
    import sqlcipher3.dbapi2 as sqlcipher
    conn = sqlcipher.connect(keys['biz_db'])
    conn.execute('PRAGMA key = "x\'%s%s\'";' % (keys['biz_key'], keys['biz_salt']))
    cursor = conn.cursor()
    users = [row[0] for row in cursor.execute(
        "SELECT user_name FROM Name2Id WHERE user_name LIKE 'gh_%'").fetchall()]
    name_map = {}
    if keys.get('contact_key') and os.path.exists(keys.get('contact_db', '')):
        try:
            contact = sqlcipher.connect(keys['contact_db'])
            contact.execute('PRAGMA key = "x\'%s%s\'";'
                            % (keys['contact_key'], keys['contact_salt']))
            name_map = dict(contact.execute(
                "SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username) "
                "FROM contact WHERE username LIKE 'gh_%'").fetchall())
            contact.close()
        except Exception:
            pass

    todo = []
    for day in days:
        if not args.refresh and day_done(args.out, day):
            continue
        articles = collect_articles(cursor, users, name_map, day)
        if args.limit_per_day:
            articles = articles[:args.limit_per_day]
        if articles:
            todo.append((day, articles))

    if args.dry_run:
        total = sum(len(items) for _, items in todo)
        need = sum(1 for _, items in todo for a in items
                   if len(body_without_media(a.get('local_text'))) < MIN_BODY_CHARS and a.get('url'))
        payload = {
            'success': True, 'dryRun': True, 'action': 'backfill-articles',
            'days': len(todo), 'articles': total, 'needFetch': need,
            'workers': args.workers, 'invokesAI': False, 'downloadsImages': False,
            'readsLocalData': True,
            'note': '只抓正文，不下图片、不调用模型；抓完还要跑 vault notes 与 article-notes',
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json
              else '%d 天、%d 篇（其中 %d 篇要联网抓正文），并发 %d'
                   % (len(todo), total, need, args.workers))
        conn.close()
        return 0

    if not todo:
        print('没有要处理的（都已回填；要重做加 --refresh）')
        conn.close()
        return 0
    if not args.yes:
        print('会抓 %d 天、%d 篇的正文（不调模型）。加 --yes 确认。'
              % (len(todo), sum(len(i) for _, i in todo)))
        conn.close()
        return 1

    report = []
    for index, (day, articles) in enumerate(todo, 1):
        print('\n[%d/%d] %s — 库里 %d 篇' % (index, len(todo), day, len(articles)))
        fetch = fetch_bodies(articles, args.workers, log=print)
        result = write_day(articles, day, args.out)
        print('    写入 %d 篇，跳过 %d 篇（正文太短），主题兜底 %d 篇'
              % (result['written'], result['skipped'], result['fallbackTopics']))
        report.append({'date': day, 'inDb': len(articles), 'fetched': fetch['ok'],
                       'written': result['written'], 'skippedThin': result['skipped']})
    conn.close()

    total_written = sum(r['written'] for r in report)
    if args.json:
        print(json.dumps({'success': True, 'action': 'backfill-articles',
                          'days': report, 'written': total_written},
                         ensure_ascii=False, indent=2))
    else:
        print('\n共写入 %d 篇，覆盖 %d 天' % (total_written, len(report)))
        print('接着跑: vault notes --date <日期>  然后  article-notes')
    return 0 if total_written else 1


if __name__ == '__main__':
    sys.exit(main())
