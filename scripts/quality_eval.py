#!/usr/bin/env python3
"""定标：把"不知道准不准"变成一条曲线。

这一整轮工作里所有概率都缺同一件东西——**金标准**。没有它，"一致率 88.6%" 只是两个
都不完美的仪器在互相比，而阈值（收录 0.5、相关度 0.5/1.5）全是我拍的。

这个脚本不替你判断，它把"你要花多久"从"手工整理 50 篇"压到"照着编号填 50 行"：

    # 1. 抽样：现场用**生产路径**给每篇打分，同时导出给人看的清单
    python scripts/quality_eval.py sample --n 50

    # 2. 打开 ~/.weflow-cli/labels/<时间戳>.json，把每条的 label 填上：
    #    {"topic": "学术", "relevance": "中", "include": true}   —— 三栏，一分钟能填十几条
    #    没把握的留 null，它会被算作"未标注"而不是"标错"
    #    （打印出来的那张清单是**盲的**：不显示模型的答案，否则一致率会被锚高）

    # 3. 算账：准确率、**校准曲线**、以及阈值应该定在哪
    python scripts/quality_eval.py score ~/.weflow-cli/labels/<时间戳>.json

**为什么现场打分而不是用存档标签**：磁盘上的文章绝大多数生成于概率字段引入之前，
没有 includeScore 可对；而且存档的 topic 本身就是要被检验的那个东西（实测过它一天之内
把 364 篇全判成"学术"）。所以样本必须现场用生产路径打分，才有 (概率, 人工标签) 配对。

**分数会按文章路径缓存**（`~/.weflow-cli/labels/.jev-scores.json`）：模型每次给的分数会有
微小差异，跨过一个档位边界就换掉整批样本（实测同一条命令连跑两次，50 条里 7 个位置不同）。
缓存之后 `--seed` 才真的定住样本，顺带重跑不再花额度；模型换了或判据改了用 `--refresh`。

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
from _utils import TOPICS, RELEVANCE_NAMES  # noqa: E402
from jev_client import create_client  # noqa: E402

TZ = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY_ROOT = os.path.join(ROOT, 'output', 'biz-daily')
LABEL_DIR = os.path.join(os.path.expanduser('~'), '.weflow-cli', 'labels')
# 按文章路径（`id`）缓存的判断结果。见 `load_score_cache`：没有它，`--seed` 定不住样本。
SCORE_CACHE = os.path.join(LABEL_DIR, '.jev-scores.json')

WORKERS = 6
EXCERPT = 400
# 校准时按概率分桶。桶要窄到能看出斜率，又不能窄到每桶只剩两三条。
BUCKETS = [(0.0, 0.2), (0.2, 0.35), (0.35, 0.5), (0.5, 0.65), (0.65, 0.8), (0.8, 1.01)]

# 「相关度有多高」这两题的切点。**现在的值**不是真理：`jev_client.score_to_relevance`
# 用 `int(score + 0.5)` 取最近档，等价于 0.5 / 1.5 两条线，而当时写下它们时没有任何金标准
# （那句注释自己写着"阈值是暂定的"）。下面的扫描就是来回答"它们该在哪"的。
CURRENT_RELEVANCE_CUTS = (0.5, 1.5)

# 扫描的候选切点。步长 0.25：原始分实测落在 0~2 之间，再细就没有足够的样本支撑。
CUT_CANDIDATES = [round(0.25 * step, 2) for step in range(1, 8)]

# 两题互相打架的判据（jev-chat-jarvis 的题目书里那条"题目之间不许互相矛盾"）。
# 我们问了两道本该一致的问题：`relevance`（对读者有多大用）与 `worth_including`
# （有没有今天就能用上的具体内容）。**高相关却说没内容、低相关却说很有内容**，
# 就是同一件事被答成了两种。
CONTRADICTION_HIGH_USELESS = (1.5, 0.5)   # 相关度落到「高」而收录分 < 0.5
CONTRADICTION_LOW_USEFUL = (0.5, 0.8)     # 相关度落到「低」而收录分 ≥ 0.8
# 主题置信度低于这个值：不是矛盾，但"它其实也没把握"这件事要单独看见。
LOW_TOPIC_CONFIDENCE = 0.5


def load_score_cache():
    """<-> 文章路径 → 那份判断。**为什么要缓存**：`--seed` 只能定住"抽哪几篇"，定不住分数——
    分数由模型现场给出，同一篇两次可能 0.49 / 0.52 地跨过一个档位边界，于是**分档抽样
    抽出来的样本又变了**（实测：同一条命令连跑两次，50 条里有 7 个位置不一样）。把分数
    按文章路径缓存住，样本才真的可复现；顺带重跑不再花额度。

    代价是分数会旧：模型换了、判据改了都要 `--refresh`。
    """
    try:
        with open(SCORE_CACHE, encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_score_cache(cache):
    try:
        os.makedirs(LABEL_DIR, exist_ok=True)
        tmp = SCORE_CACHE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(cache, handle, ensure_ascii=False)
        os.replace(tmp, SCORE_CACHE)
    except OSError:
        pass          # 缓存写不进去不该让抽样失败


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


# 正文里残留的清洗痕迹。抓回来的原文在标题下有这几句固定的话，且**变体不止一种**
# （实测见过「在小说阅读器读本章」「在小说阅读器中沉浸阅读」「在公众号小说中沉浸阅读」），
# 所以按不变量匹配，别按整句。
AD_NOISE = ('在小说阅读器', '沉浸阅读')


def _after_boilerplate(lines):
    """跳过开头的 `# 标题` / `> 来源…` / `---` / 空行。"""
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith('#') or line.startswith('>') or line.startswith('---'):
            index += 1
            continue
        break
    return lines[index:]


def _section(lines, heading):
    """某个 `## 标题` 之下、下一个 `## ` 之前的那几行；没有这一段回 None。"""
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index + 1
            break
    if start is None:
        return None
    body = []
    for line in lines[start:]:
        if line.strip().startswith('## '):
            break
        body.append(line)
    return _after_boilerplate(body) or None


def excerpt_of(body, limit=EXCERPT):
    """取标注时唯一看得到的那 400 字。

    日报正文的固定形状是：`# 标题` + `> 来源 / > 时间 / > 原文：[阅读原文](…)` 样板 +
    `## AI 摘要` + 那两三句摘要 + `## 正文` + 抓回来的原文（原文里**又**带一份标题和样板，
    还夹着"在小说阅读器读本章"这类残留）。直接取前 400 字的话，实测 50 条里多数只露出
    标题加来源，标注者（我自己试过一遍）只能靠标题猜。

    所以取**摘要段**：那两三句是判断主题与相关度最密集的信号。没有摘要（`--no-summary`
    的产物）就退回 `## 正文` 之后，再没有就退回跳过样板的第一段。
    """
    lines = (body or '').splitlines()
    picked = (_section(lines, '## AI 摘要')
              or _section(lines, '## 正文')
              or _after_boilerplate(lines))
    text = ' '.join(' '.join(picked).split())
    for noise in AD_NOISE:
        text = text.replace(noise, ' ')
    return ' '.join(text.split())[:limit]


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


def band_of(score):
    """收录分落在哪个档。没有分数返回 None。"""
    if score is None:
        return None
    for low, high in BUCKETS:
        if low <= score < high:
            return (low, high)
    return None


def draw_by_band(items, n, seed):
    """按**概率档位**分层取样。

    只按主题取样的样本会出现这种形状：49 篇里 45 篇的收录分都在 0.2 以下，真正处在
    决策边界上的只有 4 篇。拿这样的样本去标，校准曲线只有 4 个点，阈值扫描被一堆
    容易的负例主导——**标了 49 条，买到的信息量等于标了 4 条**。

    所以先按概率分档，再在档内轮转取。代价是样本不再是自然分布，所以**档位在全池里
    的占比要单独报出来**（`prevalence`）——它回答另一个问题："这个阈值每天会影响
    多少篇文章"。
    """
    rng = random.Random(seed)
    # **先按稳定键排序**。`items` 是按并发**完成**顺序追加的（`as_completed`），完成顺序每次
    # 网络抖动都不一样，而不排序的话 `rng.shuffle` 吃到的就是那个顺序——同一个 seed 抽出来的
    # 50 篇每次都不是同一批，而 CLI 上写着"两次抽样结果一致"。实测就是这么发现的：两次生成，
    # 第 20 条一次是这篇、一次是那篇。排序之后 seed 才真的定住样本。
    # 三级键：`id`（文章路径）唯一，正常情况下它就定了序；缺 `id` 的对象退到 day+title，
    # 仍然是确定的——只按 `id` 排的话，没有这个字段的输入会让排序退化成空操作、确定性
    # 又悄悄丢了（写这条时被自己的测试抓到过一次）。
    ordered = sorted(items, key=lambda item: (str(item.get('id') or ''),
                                              str(item.get('day') or ''),
                                              str(item.get('title') or '')))
    groups = defaultdict(list)
    for item in ordered:
        groups[band_of(_num((item.get('jev') or {}).get('includeScore')))].append(item)
    for group in groups.values():
        rng.shuffle(group)

    picked, index = [], 0
    # 每轮从每个档位各取一条。空档位（分数从不落在那里）自然跳过。
    while len(picked) < n:
        took = False
        for band in sorted(groups, key=lambda b: (b is None, b)):
            group = groups[band]
            if index < len(group):
                picked.append(group[index])
                took = True
                if len(picked) >= n:
                    break
        if not took:
            break
        index += 1
    return picked


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
    # 先给一个**比最终样本大得多**的候选池打分：分档取样需要每个档位都有候选，
    # 而低分档占绝大多数，只打 n 篇的话边界档位往往只有个位数。
    picked = draw_sample(buckets, args.pool, args.seed)
    cache = {} if args.refresh else load_score_cache()
    cached_hits = sum(1 for item in picked if item.get('id') in cache)
    print('候选池 %d 篇（seed=%d），用生产路径逐篇打分…（%d 篇命中分数缓存）'
          % (len(picked), args.seed, cached_hits))

    started = time.time()
    scored, failed = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {}
        for item in picked:
            hit = cache.get(item.get('id') or '')
            if hit:
                futures[pool.submit(lambda verdict=hit: verdict)] = item
            else:
                futures[pool.submit(client.decide_article, item['title'], item['body'], TOPICS)] = item
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
            if item.get('id'):
                cache[item['id']] = item['jev']
            item['excerpt'] = excerpt_of(item.pop('body'))
            # 三栏。`relevance` 之前没在标注范围内，于是"相关度阈值 0.5/1.5 对不对"这个
            # 问题一直没有数据可答——而它现在会决定一篇文章进不进日报。
            item['label'] = {'topic': None, 'relevance': None, 'include': None}
            scored.append(item)
    save_score_cache(cache)
    print('打分完成 %d/%d，耗时 %.1fs' % (len(scored), len(picked), time.time() - started))

    # 全池的档位占比：这是**另一个问题**的答案——"阈值一动，每天会多收或少收多少篇"。
    prevalence = defaultdict(int)
    for item in scored:
        prevalence[band_of(_num(item['jev']['includeScore']))] += 1
    total_pool = max(1, len(scored))

    picked = draw_by_band(scored, args.n, args.seed)
    by_band = defaultdict(int)
    for item in picked:
        by_band[band_of(_num(item['jev']['includeScore']))] += 1
    print('按档位取样 %d 篇：%s' % (
        len(picked), ' · '.join('%.2f-%.2f 取 %d 篇' % (b[0], b[1], c) for b, c in
                                sorted(by_band.items(), key=lambda kv: (kv[0] is None, kv[0])))))

    os.makedirs(LABEL_DIR, exist_ok=True)
    stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S')
    target = os.path.join(LABEL_DIR, 'labels-%s.json' % stamp)
    payload = {
        'meta': {'createdAt': datetime.now(TZ).isoformat(timespec='seconds'),
                 'seed': args.seed, 'days': args.days, 'count': len(scored),
                 'poolSize': total_pool,
                 # 样本按档位分层，所以它不是自然分布；档位占比必须单独给，
                 # 否则"样本里 20% 该收"会被误读成"每天有 20% 的文章该收"。
                 'prevalence': {'%.2f-%.2f' % band: round(count / total_pool, 3)
                                for band, count in prevalence.items() if band},
                 'howTo': ('把每条的 label 填上：{"topic": "AI|学术|新闻|文学|投资|政治",'
                           ' "relevance": "低|中|高", "include": true|false}；'
                           '没把握的留 null（算未标注，不算标错）。'
                           '填完运行：python scripts/quality_eval.py score <本文件>；'
                           '题目之间有没有互相打架：python scripts/quality_eval.py '
                           'consistency <本文件>')},
        'items': picked,
    }
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)

    print('\n' + '=' * 64)
    # **盲标**：这张清单不印 Jev 的答案。先看见它，人就会往它靠——测出来的"一致率"会
    # 偏高，而偏高的量无法估计。（`topicConfidence`/`relevanceScore`/`includeScore`
    # 仍然存在 JSON 里，供算账用；标注时别翻 JSON。）
    for index, item in enumerate(picked, 1):
        print('%2d. %s' % (index, item['title'][:60]))
        print('    %s · 历史标注 %s/%s'
              % (item['day'], item['storedTopic'] or '?', item['storedRelevance'] or '-'))
    print('=' * 64)
    print('\n标签文件：%s' % target)
    print('把每条的 label 填成 {"topic": "...", "relevance": "低|中|高", "include": true|false}，然后：')
    print('  python scripts/quality_eval.py score "%s"' % target)
    print('  python scripts/quality_eval.py consistency "%s"   # 题目之间打不打架' % target)
    return 0


def _fmt(value):
    return '%.2f' % value if isinstance(value, (int, float)) else '—'


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _level_for(score, low, high):
    """原始分 → 三个字的档位。

    与 `jev_client.score_to_relevance` 同一套规则，只是把那两条固定的线换成了变量，
    好让它们可被扫描。
    """
    if score < low:
        return RELEVANCE_NAMES[0]
    if score < high:
        return RELEVANCE_NAMES[1]
    return RELEVANCE_NAMES[-1]


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

    # 相关度：三个字的一致率 **和** 原始分的偏差。
    # 只比三个字的话，"整体偏高一点点"与"基本准确"看起来一模一样——而阈值该往哪挪，
    # 恰恰要看的是后者的方向与幅度。
    rel_labelled = [i for i in items
                    if (i.get('label') or {}).get('relevance') in RELEVANCE_NAMES]
    report['relevanceAgreement'] = None
    report['relevanceMae'] = None
    report['relevanceCuts'] = None
    report['bestRelevanceCuts'] = None
    report['relevanceLabelled'] = len(rel_labelled)
    if rel_labelled:
        jev_ok = sum(1 for i in rel_labelled
                     if (i.get('jev') or {}).get('relevance') == i['label']['relevance'])
        report['relevanceAgreement'] = round(jev_ok / len(rel_labelled), 3)

        pairs = []
        for i in rel_labelled:
            score = _num((i.get('jev') or {}).get('relevanceScore'))
            if score is not None:
                pairs.append((i, score, RELEVANCE_NAMES.index(i['label']['relevance'])))
        if pairs:
            # 平均差几档。0.3 以内说明分数与人的档位对得上；上了 0.7 就是系统性错位。
            report['relevanceMae'] = round(
                sum(abs(score - index) for _, score, index in pairs) / len(pairs), 2)

        # 切点扫描：0.5/1.5 是**当时拍的**（`jev_client` 里那句注释自己写着"暂定"）。
        # 这里在所有候选切点上算"按分数分档"与"你标的三档"的一致率，看它该在哪。
        if len(pairs) >= 5:
            sweep = []
            for low in CUT_CANDIDATES:
                for high in CUT_CANDIDATES:
                    if high - low < 0.5:      # 至少要留得下一整档
                        continue
                    ok = sum(1 for i, score, _ in pairs
                             if _level_for(score, low, high) == i['label']['relevance'])
                    sweep.append({'low': low, 'high': high,
                                  'agreement': round(ok / len(pairs), 3)})
            sweep.sort(key=lambda row: (-row['agreement'], row['low'], row['high']))
            report['relevanceSweep'] = sweep[:5]
            current_low, current_high = CURRENT_RELEVANCE_CUTS
            report['relevanceCuts'] = {
                'low': current_low, 'high': current_high,
                'agreement': next((row['agreement'] for row in sweep
                                   if row['low'] == current_low and row['high'] == current_high),
                                  None),
            }
            report['bestRelevanceCuts'] = sweep[0] if sweep else None

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


def consistency(items):
    """题目之间有没有互相打架 —— **不需要人工标签**。

    我们问了两个本该一致的问题：`relevance`（对读者有多大用）与 `worth_including`
    （有没有今天就能用上的具体内容）。同一篇答成"高相关 / 没内容"或"低相关 / 很有内容"，
    就是同一件事被答成了两种。jev-chat-jarvis 的题目书把这条写成硬要求（"题目之间不许
    互相矛盾"），他们靠标注集去压；**这一项不用**——矛盾是自证的，不需要金标准。

    只在两个分数都在场时判定：缺任何一个算"判不出来"，单独计数，既不算矛盾也不算一致
    （把"没答"当"没矛盾"是这个仓库反复踩过的坑）。
    """
    usable, unscored = [], 0
    for item in items:
        jev = item.get('jev') or {}
        score = _num(jev.get('relevanceScore'))
        include = _num(jev.get('includeScore'))
        if score is None or include is None:
            unscored += 1
            continue
        usable.append((item, score, include))

    high_cut, high_need = CONTRADICTION_HIGH_USELESS
    low_cut, low_need = CONTRADICTION_LOW_USEFUL
    conflicts, low_confidence = [], 0
    for item, score, include in usable:
        if score >= high_cut and include < high_need:
            conflicts.append({'kind': '高相关却无内容', 'relevanceScore': score,
                              'includeScore': include, 'title': item.get('title', '')})
        elif score < low_cut and include >= low_need:
            conflicts.append({'kind': '低相关却很有内容', 'relevanceScore': score,
                              'includeScore': include, 'title': item.get('title', '')})
        confidence = _num((item.get('jev') or {}).get('topicConfidence'))
        if confidence is not None and confidence < LOW_TOPIC_CONFIDENCE:
            low_confidence += 1

    return {
        'total': len(items), 'usable': len(usable), 'unscored': unscored,
        'conflicts': len(conflicts), 'conflictRate': (round(len(conflicts) / len(usable), 3)
                                                      if usable else None),
        'highButUseless': sum(1 for c in conflicts if c['kind'] == '高相关却无内容'),
        'lowButUseful': sum(1 for c in conflicts if c['kind'] == '低相关却很有内容'),
        'lowTopicConfidence': low_confidence,
        'examples': conflicts[:10],
    }


def cmd_consistency(args):
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

    report = consistency(payload.get('items') or [])
    if args.json:
        print(json.dumps({'success': True, 'report': report}, ensure_ascii=False))
        return 0

    print('=' * 64)
    print('题目之间的一致性（不需要人工标签）：')
    print('  参与判定 %d 篇（另有 %d 篇有一题没给分，既不算矛盾也不算一致）'
          % (report['usable'], report['unscored']))
    if not report['usable']:
        print('\n没有可判定的数据。先跑 sample 生成一份带 jev 打分的文件。')
        return 0
    rate = report['conflictRate']
    print('  互相打架 %d 篇（%.1f%%）' % (report['conflicts'], (rate or 0) * 100))
    print('    · 相关度说「高」、收录分却说没内容：%d' % report['highButUseless'])
    print('    · 相关度说「低」、收录分却说很有内容：%d' % report['lowButUseful'])
    print('  主题置信度 < %.1f 的：%d 篇（不是矛盾，是"它其实也没把握"）'
          % (LOW_TOPIC_CONFIDENCE, report['lowTopicConfidence']))
    if report['examples']:
        print('\n  前几条（完整清单用 --json 取）：')
        for row in report['examples']:
            print('    %.2f / %.2f  %s  %s'
                  % (row['relevanceScore'], row['includeScore'], row['kind'],
                     row['title'][:44]))
    print('\n' + '=' * 64)
    print('矛盾率高说明**题目措辞或切点**有问题，不是模型有问题：同一件事被问成了两件。')
    print('这一项不需要金标准，所以它现在就能给你一个数；要判断"哪个答案对"才需要标注。')
    return 0


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

    if report['relevanceAgreement'] is not None:
        print('\n相关度（对着你标的 %d 条）：' % report['relevanceLabelled'])
        print('  低/中/高 一致率  %.1f%%' % (report['relevanceAgreement'] * 100))
        if report['relevanceMae'] is not None:
            print('  平均差 %.2f 档   ← 0.3 内说明分数与你的档位对得上，'
                  '0.7 以上就是系统性错位' % report['relevanceMae'])

    if report.get('bestRelevanceCuts') and report.get('relevanceCuts'):
        best, current = report['bestRelevanceCuts'], report['relevanceCuts']
        print('\n相关度切点扫描（现在是 %.2f / %.2f）：' % (current['low'], current['high']))
        if current['agreement'] is not None:
            print('  当前切点   %.2f / %.2f  →  %.1f%%'
                  % (current['low'], current['high'], current['agreement'] * 100))
        print('  最一致     %.2f / %.2f  →  %.1f%%'
              % (best['low'], best['high'], best['agreement'] * 100))
        print('  切点只是拟合你标的这些文章；它没在说"哪个档位更对"，'
              '改它之前先看样本量。')

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
    sample.add_argument('--n', type=int, default=50, help='最终要标注多少篇')
    sample.add_argument('--pool', type=int, default=150,
                        help='先给多大的候选池打分，再按概率档位取 n 篇（默认 150）')
    sample.add_argument('--days', type=int, default=90, help='只从最近多少天的文章里抽')
    sample.add_argument('--seed', type=int, default=20260921, help='固定种子：两次抽样结果一致')
    sample.add_argument('--refresh', action='store_true',
                        help='忽略分数缓存重新打分（模型换了或判据改了才需要）')
    sample.add_argument('--json', action='store_true')
    sample.set_defaults(func=cmd_sample)

    score = sub.add_parser('score', help='读回标注文件，算准确率、校准与最优阈值')
    score.add_argument('file')
    score.add_argument('--json', action='store_true')
    score.set_defaults(func=cmd_score)

    check = sub.add_parser('consistency', help='题目之间有没有互相打架（不需要人工标签）')
    check.add_argument('file', help='sample 生成的标注文件（用它的 jev 打分，不再调模型）')
    check.add_argument('--json', action='store_true')
    check.set_defaults(func=cmd_consistency)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
