#!/usr/bin/env python3
"""定标：把"不知道准不准"变成一条曲线。

这一整轮工作里所有概率都缺同一件东西——**金标准**。没有它，"一致率 88.6%" 只是两个
都不完美的仪器在互相比，而阈值（收录 0.5、相关度 0.5/1.5）全是我拍的。

这个脚本不替你判断，它把"你要花多久"从"手工整理 50 篇"压到"照着编号填 50 行"：

    # 1. 抽样：现场用**生产路径**给每篇打分，同时导出给人看的清单
    python scripts/quality_eval.py sample --n 50

    # 2. 打开 ~/.weflow-cli/labels/<时间戳>.json，把每条的 label 填上：
    #    {"topic": "学术", "include": true}    —— 两栏，一分钟能填十几条
    #    没把握的留 null，它会被算作"未标注"而不是"标错"

    # 3. 算账：准确率、**校准曲线**、以及阈值应该定在哪
    python scripts/quality_eval.py score ~/.weflow-cli/labels/<时间戳>.json

**为什么现场打分而不是用存档标签**：磁盘上的文章绝大多数生成于概率字段引入之前，
没有 includeScore 可对；而且存档的 topic 本身就是要被检验的那个东西（实测过它一天之内
把 364 篇全判成"学术"）。所以样本必须现场用生产路径打分，才有 (概率, 人工标签) 配对。

**它不写进仓库**：标签文件落在 `~/.weflow-cli/labels/`，因为里面是文章标题与你的判断。
"""
import argparse
import glob
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jev_client import create_client  # noqa: E402

TZ = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY_ROOT = os.path.join(ROOT, 'output', 'biz-daily')
LABEL_DIR = os.path.join(os.path.expanduser('~'), '.weflow-cli', 'labels')
TOPICS = ['AI', '学术', '新闻', '文学', '投资', '政治']
WORKERS = 6
EXCERPT = 400
# 校准时按概率分桶。桶要窄到能看出斜率，又不能窄到每桶只剩两三条。
BUCKETS = [(0.0, 0.2), (0.2, 0.35), (0.35, 0.5), (0.5, 0.65), (0.65, 0.8), (0.8, 1.01)]


def read_article(path):
    """-> (frontmatter, body) 或 None（不是一篇真文章）。"""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    if not text.startswith('---'):
        return None
    parts = text.split('---', 2)
    if len(parts) < 3:
        return None
    meta = {}
    for line in parts[1].splitlines():
        if ':' in line:
            key, _, value = line.partition(':')
            meta[key.strip()] = value.strip().strip('"')
    # 日报目录里还躺着 README.md、行动建议.md 这类产物，它们没有 url。
    if not meta.get('url'):
        return None
    return meta, parts[2].strip()


def collect_pool(days):
    """最近 `days` 天里的真文章，按主题分桶（好让样本不被某一类刷屏）。"""
    cutoff = (datetime.now(TZ) - timedelta(days=days)).strftime('%Y-%m-%d')
    buckets = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(DAILY_ROOT, '*', '*', '*.md'))):
        day = path.replace('\\', '/').split('/')[-3]
        if day < cutoff:
            continue
        parsed = read_article(path)
        if not parsed:
            continue
        meta, body = parsed
        if len(body) < 200:      # 太短的正文判不出什么
            continue
        topic = meta.get('topic') if meta.get('topic') in TOPICS else '未分类'
        buckets[topic].append({
            'id': os.path.relpath(path, DAILY_ROOT).replace('\\', '/'),
            'day': day,
            'title': meta.get('title', ''),
            'storedTopic': meta.get('topic', ''),
            'storedRelevance': meta.get('relevance', ''),
            'body': body,
        })
    return buckets


def draw_sample(buckets, n, seed):
    """轮转取样：逐轮从每个主题各取一条，样本不被某一天某一类刷屏。

    固定 seed，所以同一条命令两次跑抽到的是同一批文章——否则"我标了一半"之后
    重跑一次就全错位了。
    """
    rng = random.Random(seed)
    pools = {topic: rng.sample(items, len(items)) for topic, items in buckets.items()}
    picked, index = [], 0
    while len(picked) < n:
        took = False
        for topic in sorted(pools):
            if index < len(pools[topic]):
                picked.append(pools[topic][index])
                took = True
                if len(picked) >= n:
                    break
        if not took:
            break
        index += 1
    return picked


def cmd_sample(args):
    client = create_client()
    if client is None:
        print(json.dumps({'success': False, 'code': 'NO_KEY',
                          'error': '缺少 TypeSafe key：weflow-cli config set typesafeApiKey "..."'},
                         ensure_ascii=False))
        return 2
    buckets = collect_pool(args.days)
    if not buckets:
        print(json.dumps({'success': False, 'code': 'NO_ARTICLES',
                          'error': '最近 %d 天没有可抽样的文章（先跑日报）' % args.days},
                         ensure_ascii=False))
        return 2
    picked = draw_sample(buckets, args.n, args.seed)
    print('抽样 %d 篇（seed=%d），用生产路径逐篇打分…' % (len(picked), args.seed))

    started = time.time()
    scored, failed = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(client.decide_article, item['title'], item['body'], TOPICS): item
                   for item in picked}
        for future in as_completed(futures):
            item = futures[future]
            try:
                verdict = future.result()
            except Exception as error:
                failed += 1
                print('  [WARN] %s 打分失败（%s）' % (item['title'][:24], type(error).__name__))
                continue
            item['jev'] = {key: verdict.get(key) for key in
                           ('topic', 'topicConfidence', 'relevance',
                            'relevanceScore', 'includeScore')}
            item['excerpt'] = item.pop('body')[:EXCERPT]
            item['label'] = {'topic': None, 'include': None}
            scored.append(item)
    print('打分完成 %d/%d，耗时 %.1fs' % (len(scored), len(picked), time.time() - started))

    os.makedirs(LABEL_DIR, exist_ok=True)
    stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S')
    target = os.path.join(LABEL_DIR, 'labels-%s.json' % stamp)
    payload = {
        'meta': {'createdAt': datetime.now(TZ).isoformat(timespec='seconds'),
                 'seed': args.seed, 'days': args.days, 'count': len(scored),
                 'howTo': ('把每条的 label 填上：{"topic": "AI|学术|新闻|文学|投资|政治",'
                           ' "include": true|false}；没把握的留 null（算未标注，不算标错）。'
                           '填完运行：python scripts/quality_eval.py score <本文件>')},
        'items': scored,
    }
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)

    print('\n' + '=' * 64)
    for index, item in enumerate(scored, 1):
        print('%2d. [%s] %s' % (index, item['jev']['topic'] or '?', item['title'][:52]))
        print('    历史标注 %s/%s · Jev 收录分 %s · %s'
              % (item['storedTopic'] or '?', item['storedRelevance'] or '-',
                 _fmt(item['jev']['includeScore']), item['day']))
    print('=' * 64)
    print('\n标签文件：%s' % target)
    print('把每条的 label 填成 {"topic": "...", "include": true/false}，然后：')
    print('  python scripts/quality_eval.py score "%s"' % target)
    return 0


def _fmt(value):
    return '%.2f' % value if isinstance(value, (int, float)) else '—'


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_labels(items):
    """→ 一份可打印的报告结构。纯函数，好测。"""
    labelled = [i for i in items
                if (i.get('label') or {}).get('include') is not None]
    topic_labelled = [i for i in items
                      if (i.get('label') or {}).get('topic')]
    unscored = [i for i in labelled if _num((i.get('jev') or {}).get('includeScore')) is None]

    report = {'total': len(items), 'labelled': len(labelled),
              'topicLabelled': len(topic_labelled),
              'unscored': len(unscored), 'buckets': [], 'sweep': [], 'best': None,
              'topicAgreement': None, 'storedAgreement': None}

    # 主题：Jev 与人工的吻合度，以及存档标签与人工的吻合度（作为对照）
    if topic_labelled:
        jev_ok = sum(1 for i in topic_labelled
                     if (i.get('jev') or {}).get('topic') == i['label']['topic'])
        stored_ok = sum(1 for i in topic_labelled
                        if i.get('storedTopic') == i['label']['topic'])
        report['topicAgreement'] = round(jev_ok / len(topic_labelled), 3)
        report['storedAgreement'] = round(stored_ok / len(topic_labelled), 3)

    scored = [(i, _num(i['jev']['includeScore'])) for i in labelled]
    scored = [(i, s) for i, s in scored if s is not None]
    if not scored:
        return report

    # 校准：每个概率桶里，人工说"该收"的比例。**这就是"概率有没有意义"的答案**——
    # 如果 0.5~0.65 桶里也是 90% 该收，那这个分数就没有分辨力。
    for low, high in BUCKETS:
        inside = [i for i, s in scored if low <= s < high]
        if not inside:
            continue
        yes = sum(1 for i in inside if i['label']['include'])
        report['buckets'].append({
            'range': [low, high], 'n': len(inside),
            'observed': round(yes / len(inside), 3),
            'mid': round((low + min(high, 1.0)) / 2, 3),
        })

    # 阈值扫描：找出让"按分数收录"与人工判断最一致的那个切点
    for threshold in [round(0.05 * k, 2) for k in range(1, 20)]:
        ok = sum(1 for i, s in scored if (s >= threshold) == bool(i['label']['include']))
        report['sweep'].append({'threshold': threshold, 'agreement': round(ok / len(scored), 3)})
    best = max(report['sweep'], key=lambda row: (row['agreement'], -row['threshold']))
    report['best'] = best
    return report


def cmd_score(args):
    path = args.file
    if path != '-' and not os.path.isabs(path):
        path = os.path.abspath(os.path.expanduser(path))
    try:
        with open(path, encoding='utf-8') as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({'success': False, 'code': 'BAD_FILE', 'error': str(error)},
                         ensure_ascii=False))
        return 2

    items = payload.get('items') or []
    report = score_labels(items)
    if args.json:
        print(json.dumps({'success': True, 'report': report}, ensure_ascii=False))
        return 0

    print('=' * 64)
    print('样本 %d 篇 · 已标注 %d 篇 · 参与计算 %d 篇'
          % (report['total'], report['labelled'],
             sum(b['n'] for b in report['buckets'])))
    if report['unscored']:
        print('（其中 %d 篇 Jev 没给收录分，已排除）' % report['unscored'])
    if report['labelled'] < 20:
        print('\n⚠️  标注少于 20 条，下面这些数字还不稳——**它们只是参考，不是结论**。')

    if report['topicAgreement'] is not None:
        print('\n主题判断（对着你标的 %d 条）：' % report['topicLabelled'])
        print('  Jev        %.1f%%' % (report['topicAgreement'] * 100))
        print('  历史存档   %.1f%%   ← 对照：这就是它一直在当"基准"的那个东西'
              % (report['storedAgreement'] * 100))

    if report['buckets']:
        print('\n收录分的校准（概率说的是不是真的）：')
        print('  %-14s %5s %10s' % ('概率区间', '条数', '人工说该收'))
        for bucket in report['buckets']:
            bar = '█' * int(round(bucket['observed'] * 20))
            print('  %.2f–%.2f     %5d %9.0f%%  %s'
                  % (bucket['range'][0], bucket['range'][1], bucket['n'],
                     bucket['observed'] * 100, bar))
        print('  斜着往上走 = 分数有意义；一条平线 = 它只是在乱猜。')

    if report['best']:
        print('\n阈值扫描（按分数收录 vs 你的判断）：')
        for row in report['sweep']:
            if abs(row['threshold'] - report['best']['threshold']) < 1e-9:
                print('  %.2f  %.1f%%   ← 最一致' % (row['threshold'], row['agreement'] * 100))
        print('  当前用的是 0.50。上面的最优点如果离它很远，说明该改的是那个常数，'
              '而不是模型。')

    print('\n' + '=' * 64)
    print('这些数字只代表你标的这些文章，且标注本身也有主观性。')
    print('但它比"与另一个不完美的基准比"结实——那一个从来不是准确率。')
    return 0


def main():
    parser = argparse.ArgumentParser(description='定标：抽样、人工标注、算校准曲线与最优阈值')
    sub = parser.add_subparsers(dest='command')

    sample = sub.add_parser('sample', help='抽样并用生产路径打分，导出待标注清单')
    sample.add_argument('--n', type=int, default=50)
    sample.add_argument('--days', type=int, default=90, help='只从最近多少天的文章里抽')
    sample.add_argument('--seed', type=int, default=20260921, help='固定种子：两次抽样结果一致')
    sample.add_argument('--json', action='store_true')
    sample.set_defaults(func=cmd_sample)

    score = sub.add_parser('score', help='读回标注文件，算准确率、校准与最优阈值')
    score.add_argument('file')
    score.add_argument('--json', action='store_true')
    score.set_defaults(func=cmd_score)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
