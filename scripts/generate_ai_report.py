#!/usr/bin/env python3
"""
AI 阅读日报 — 信息密集但精炼版本。

核心目标：没时间看公众号时，用这份报告快速扫一眼即可。
- AI 一次调用搞定：总评 + 趋势 + 逐篇摘要 + 行动指南
- 按主题分组，AI 主题最详细
- 默认优先走本地推理（Ollama/LM Studio/Claude Code 本地服务）

用法:
  python scripts/generate_ai_report.py                         # 今天（默认走本地推理）
  python scripts/generate_ai_report.py --date 2026-06-09
  python scripts/generate_ai_report.py --range 7               # 最近 7 天
  python scripts/generate_ai_report.py --engine local|deepseek|claude|ollama
  python scripts/generate_ai_report.py --dry-run                # 仅文章清单，不调用 AI

输出:
  output/ai-reports/ai-report-YYYY-MM-DD.md
"""
import sys, os, json, re, time, argparse
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import (create_engine, parse_frontmatter, TOPICS, TOPIC_CRITERIA,
                    load_config, excluded_topics)

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
SOURCE_ROOT = os.path.join(ROOT_DIR, 'output', 'biz-daily')
OUTPUT_DIR = os.path.join(ROOT_DIR, 'output', 'ai-reports')

TZ = timezone(timedelta(hours=8))

FOCUS_TOPIC = 'AI'


# ======================================================================
# PROMPT（信息密集精炼版，一次调用搞定）
# ======================================================================

REPORT_PROMPT = """你是一位为忙碌研究生写作的「深度阅读策展人」。你的任务是用**信息密集但精炼**的方式，把今天的公众号文章整理成一份有深度、有观点的日报。读者读完能清晰了解「今天发生了什么、核心观点是什么、对我有什么用」。

【读者】环境科学研究生，关注 AI 前沿 + 环境科学交叉方向（大气模拟、遥感反演、环境大数据等）。

【文章列表】
{articles_block}

---

## 写作要求（核心）

**精炼不是简短，是信息密度高。** 每句话都要有信息量：
- **覆盖要全面**：尽量覆盖所有文章，至少 20 篇以上要被提及或概括
- **重点要突出**：对最值得关注的 10-15 篇文章展开讲清楚「是什么、为什么重要、怎么用」
- **其余也要有存在感**：剩下的文章用主题分类 + 一句话概括，让读者知道今天还有什么
- 用**段落叙述**串联信息，而不是 bullet list 罗列标题
- 每个观点回答**「所以呢？」**——对读者有什么启发

## 结构（Markdown，遵守标题层级）

### 今日焦点（3-5 段叙述）
用叙事段落讲清最重要的事。每段聚焦一个主题，把 2-4 篇相关文章串成故事线。用 **《标题》**(*来源*) 格式。深入解释核心观点、数据和意义。

### AI 工具与趋势（2-3 段）
今天 AI 领域的新动态。对有价值的工具/项目，讲清楚：它是什么、能做什么、和环境科学研究有什么关系、怎么入手。

### 学术前沿（2-3 段）
与环境科学相关的研究进展。每篇讲清：研究了什么问题、用什么方法、得出什么结论、有什么启发。

### 其他值得关注
用表格或分组列出今天其他值得知道的文章，每条至少写清楚核心观点。不要只写标题。

### 值得做的事（具体、可执行）
5-8 个具体行动。不要泛泛而谈，要说「打开 XX 试 YY」或「搜索 XX 论文看第 N 节」。

## 写作规则
1. 中文输出，语气专业但不僵硬
2. 不引入文章列表中未出现的信息
3. 段落为主，可适当用表格对比
4. Markdown，**粗体**强调关键，*斜体*标注来源
5. 全文 3000-5000 字，确保信息量和深度
6. 每个章节都要有实质内容，不要用「今天内容较少，跳过」敷衍

只返回 Markdown 正文。"""


# ======================================================================
# DATA LOADING
# ======================================================================

# 「该不该进今天的日报」的切点。**暂定且未经校准**：Jev 问的是
# `worth_including`（含可直接用上的具体内容吗），返回 0~1 的概率，
# 原始值存在 frontmatter 的 `includeScore` 里，改这个数不用重跑日报。
INCLUDE_THRESHOLD = 0.5


# 「拿不太准」的区间。收录分落在中间，意味着这次收还是不收都说得过去——
# 报告不该把这种条目当成确定的事报出去。
UNCERTAIN_BAND = (0.35, 0.65)
TOPIC_CONFIDENT = 0.7
UNCERTAIN_LIST_LIMIT = 8


def _num(value):
    """把 includeScore 之类的概率读成 float；读不出返回 None。

    要认字符串：`.articles.json` 里是数字，md 兜底路径读出来是 YAML 标量文本。
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score(article):
    """读 includeScore。`.articles.json` 里是数字，md 兜底路径读出来是字符串。"""
    value = article.get('includeScore')
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_uncertainty_section(included, everything, include_all=False, exclude=()) -> str:
    """报告末尾那一段「我拿不准的」。**本地算，不经 LLM** —— 让模型去描述自己的不确定，
    等于又要它生成一段可能被编圆的散文。

    两种不确定分开说，因为处理方式不同：
      * 收录卡在阈值上 —— 收与不收都说得过去，你看到的是这次掷硬币的结果；
      * 主题置信度低 —— 它可能被分到了错误的那一栏，位置都可能是错的。

    **没有概率字段时不能沉默。** 早期生成的文章没有 includeScore，那一段的缺席会被
    读成"这份报告哪里都很确定"，而事实是"它没告诉你"。所以那种情况要明说。
    """
    scored = [a for a in everything if _score(a) is not None]
    if not scored:
        return (chr(10) + '---' + chr(10) + chr(10) + '## 我拿不准的' + chr(10) + chr(10)
                + '本期文章没有概率字段（生成早于该字段引入），所以**这份报告无法告诉你'
                  '它哪里不确定**——不能把上面的结论读成"都已经过确认"。' + chr(10))

    low, high = UNCERTAIN_BAND
    in_ids = {id(a) for a in included}

    def band(article):
        value = _score(article)
        return value is not None and low <= value <= high

    # 这一段三份清单都不提被排除的主题：否则"不想看新闻"的同一份报告，
    # 会在这一栏把新闻的标题和分数原样列出来。
    borderline_in = [a for a in included
                     if band(a) and not is_excluded_topic(a, exclude)]
    # 注意：这一栏装的是**没收**的文章，被排除的天然满足"没收"——
    # 把 exclude 交给 admits 反而会让它们更容易被列进来（我第一版就是这么错的）。
    # 这里要的是"因为分数而没收"，所以按排除集滤掉。
    borderline_out = [] if include_all else [a for a in everything
                                            if not admits(a, include_all) and band(a)
                                            and not is_excluded_topic(a, exclude)]
    low_confidence = [a for a in included
                      if isinstance(a.get('topicConfidence'), (int, float))
                      and a['topicConfidence'] < TOPIC_CONFIDENT
                      and not is_excluded_topic(a, exclude)]

    lines = [chr(10) + '---' + chr(10), '## 我拿不准的' + chr(10),
             '这两处不是漏掉，是**判断本身不确定**。列出来，免得被当成定论。' + chr(10)]

    total_borderline = len(borderline_in) + len(borderline_out)
    if total_borderline:
        if include_all:
            # 收录判据没启用，"收与不收"这个区分根本不存在——照抄阈值两侧的措辞
            # 会让人以为这里做过一次筛选。
            lines.append('**收录分落在边界上的 %d 篇**（本次未启用收录判据，全部收录）'
                         '——这些分数本来就说不准：%s' % (total_borderline, chr(10)))
        else:
            lines.append('**收录卡在阈值上的 %d 篇**（收了 %d 篇、没收 %d 篇）——'
                         '收与不收都说得过去：%s'
                         % (total_borderline, len(borderline_in),
                            len(borderline_out), chr(10)))
        listed = borderline_in + borderline_out
        for article in listed[:UNCERTAIN_LIST_LIMIT]:
            kept = '' if include_all else (' · 收' if id(article) in in_ids else ' · 没收')
            lines.append('- %s（收录分 %.2f%s · 主题 %s）'
                         % (article.get('title', '(无标题)'), _score(article), kept,
                            article.get('topic', '?')))
        if total_borderline > UNCERTAIN_LIST_LIMIT:
            lines.append('- 另有 %d 篇同样卡在边界，未列。' % (total_borderline - UNCERTAIN_LIST_LIMIT))
        lines.append('')

    if low_confidence:
        lines.append('**主题可能归错的 %d 篇**（主题置信度低于 %.1f，分栏位置可能不对）：%s'
                     % (len(low_confidence), TOPIC_CONFIDENT, chr(10)))
        for article in low_confidence[:UNCERTAIN_LIST_LIMIT]:
            lines.append('- %s（标为 %s · 置信 %.2f）'
                         % (article.get('title', '(无标题)'), article.get('topic', '?'),
                            article['topicConfidence']))
        if len(low_confidence) > UNCERTAIN_LIST_LIMIT:
            lines.append('- 另有 %d 篇同样置信偏低，未列。' % (len(low_confidence) - UNCERTAIN_LIST_LIMIT))
        lines.append('')

    if not total_borderline and not low_confidence:
        lines.append('本期没有落在边界上的条目——收录分与主题置信度都不在含糊区间。' + chr(10))

    lines.append('> 这些数字来自决策模型，**没有金标准校准过**；概率在阈值附近还会抖'
                 '（同一批数据两次跑会有出入），别细究 0.48 与 0.52 的区别。')
    lines.append('')
    return chr(10).join(lines)


def is_excluded_topic(article: dict, exclude=()) -> bool:
    """用户是不是明说了不要这个主题。

    单独抽出来是因为它有**两个方向相反**的用处：
    `admits` 靠它判"不要"，而「我拿不准的」那一栏靠它判"别列出来"——
    那一栏装的恰好是**没收**的文章，被排除的天然满足"没收"。
    两处各写一遍早晚会写反，事实上第一版就写反了。
    """
    return (article.get('topic') or '') in (exclude or ())


def admits(article: dict, include_all: bool = False, exclude=()) -> bool:
    """这篇文章该不该收录。

    焦点主题永远收。其余看 `includeScore`——那是**专门为这个问题**问出来的概率，
    比拿 `relevance != '高'` 顶替准：相关度量的是"对读者的实用价值"，回答不了
    "今天该不该收它"，阈值怎么调都是在调一个问错了的题。

    没有 `includeScore` 的是这个字段出现之前的产物，退回旧规则，行为不变。

    这里要认字符串：`.articles.json` 里的值是数字，但 md 兜底路径读出来的是
    YAML 标量文本（`'0.8'`）。只认 `int/float` 的话，那条路径上这个新字段
    就会被静默忽略——测出来正是如此。
    """
    # 排除是**正交**的一维，所以放在 include_all 前面：用户明说不要的主题，
    # 连 --include-all 都不该让它冒出来。
    if is_excluded_topic(article, exclude):
        return False
    if include_all or article.get('topic') == FOCUS_TOPIC:
        return True
    score = article.get('includeScore')
    if score is not None:
        try:
            return float(score) >= INCLUDE_THRESHOLD
        except (TypeError, ValueError):
            # 值不是数字（乱改过的产物）。退回旧规则，别让报告生成在这里炸掉。
            pass
    return article.get('relevance') == '高'

def load_articles_from_date(date_str: str, include_all: bool = False,
                            exclude=()) -> list[dict]:
    json_path = Path(SOURCE_ROOT) / date_str / '.articles.json'
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            arts = data.get('articles', data) if isinstance(data, dict) else data
            for a in arts:
                a.setdefault('date', date_str)
            # 这里原先直接 return，不筛。于是「非焦点主题只收相关度高的文章」
            # 这条规则**只在下面的 MD 兜底路径里存在**，而 .articles.json 才是
            # 正常走的那条路 —— 规则因此从未真正生效过。
            kept = [a for a in arts if admits(a, include_all, exclude)]
            if len(kept) != len(arts):
                # 说清**实际**用的是哪条规则：有收录分的按概率筛，早于该字段的产物
                # 退回旧规则。无脑写"阈值 includeScore >= x"，在后一种情况下是撒谎。
                parsed = [_num(a.get('includeScore')) for a in arts]
                scored = sum(1 for v in parsed if v is not None)
                if scored == 0:
                    print(f'  收录筛选: {len(arts)} 篇 -> {len(kept)} 篇'
                          f'（这些产物没有收录分，退回旧的 relevance == 高 规则）')
                elif scored < len(arts):
                    print(f'  收录筛选: {len(arts)} 篇 -> {len(kept)} 篇'
                          f'（{scored} 篇按 includeScore >= {INCLUDE_THRESHOLD}，'
                          f'其余 {len(arts) - scored} 篇按旧规则）')
                else:
                    print(f'  收录筛选: {len(arts)} 篇 -> {len(kept)} 篇'
                          f'（阈值 includeScore >= {INCLUDE_THRESHOLD}）')
            return kept
        except Exception:
            pass
    # 回退：扫描 MD 文件
    date_dir = Path(SOURCE_ROOT) / date_str
    if not date_dir.exists():
        return []
    articles = []
    for topic in TOPICS:
        topic_dir = date_dir / topic
        if not topic_dir.exists():
            continue
        for md_file in sorted(topic_dir.glob('*.md')):
            try:
                content = md_file.read_text(encoding='utf-8')
            except Exception:
                continue
            fm, body = parse_frontmatter(content)
            if not fm:
                continue
            relevance = fm.get('relevance', '中')
            if not admits({**fm, 'topic': topic}, include_all, exclude):
                continue
            # AI 主题读取完整正文（最多 5000 字），其他主题读取摘要
            if topic == FOCUS_TOPIC:
                # 提取正文部分供 AI 精读
                body_sections = body.split('## 正文')
                if len(body_sections) > 1:
                    full_text = body_sections[1].strip()  # 不限制字数，完整正文
                else:
                    m = re.search(r'## (?:AI 摘要|深度解析)\s*\n\s*(.+?)(?=\n##|\Z)', body, re.DOTALL)
                    full_text = m.group(1).strip()[:2000] if m else body[:2000].strip()
                summary = full_text
            else:
                m = re.search(r'## (?:AI 摘要|深度解析)\s*\n\s*(.+?)(?=\n##|\Z)', body, re.DOTALL)
                summary = m.group(1).strip()[:800] if m else body[:500].strip()
            articles.append({
                'date': date_str,
                'title': fm.get('title', md_file.stem).strip('"\''),
                'source': fm.get('source', '').strip('"\''),
                'topic': topic,
                'relevance': relevance,
                'tags': fm.get('tags', []),
                'url': fm.get('url', '').strip('"\''),
                'summary': summary,
            })
    return articles


def load_articles_range(from_date: date, to_date: date, include_all: bool = False,
                        exclude=()) -> list[dict]:
    all_articles = []
    cur = from_date
    while cur <= to_date:
        all_articles.extend(load_articles_from_date(cur.strftime('%Y-%m-%d'), include_all,
                                                   exclude))
        cur += timedelta(days=1)
    return all_articles


# ======================================================================
# PROMPT 准备
# ======================================================================

def build_articles_block(articles: list[dict]) -> str:
    lines = []
    ordered = [FOCUS_TOPIC] + [t for t in TOPICS if t != FOCUS_TOPIC]
    remaining = list(articles)
    for topic in ordered:
        group = [a for a in remaining if a.get('topic') == topic]
        if not group:
            continue
        lines.append(f'【主题：{topic}】共 {len(group)} 篇')
        for i, a in enumerate(group):
            tags = a.get('tags') or []
            tags_str = '、'.join(tags[:5]) if isinstance(tags, list) else str(tags)[:50]
            summary = (a.get('summary') or '(无摘要)').strip()
            if topic == FOCUS_TOPIC:
                # AI 文章：给完整的正文内容，不截断
                lines.append(
                    f'  [{i+1}] 《{a.get("title","")}》'
                    f'（{a.get("source","")}，相关度:{a.get("relevance","中")}）'
                )
                if tags_str:
                    lines.append(f'      标签：{tags_str}')
                lines.append(f'      正文内容：\n{summary}')
                lines.append('')
            else:
                # 非 AI 文章：摘要截断到 300 字
                summary = re.sub(r'\n+', ' / ', summary)[:300]
                lines.append(
                    f'  [{i+1}] 《{a.get("title","")}》'
                    f'（{a.get("source","")}，相关度:{a.get("relevance","中")}）'
                )
                if tags_str:
                    lines.append(f'      标签：{tags_str}')
                lines.append(f'      摘要：{summary}')
        lines.append('')
    extra = [a for a in remaining if a.get('topic') not in ordered]
    if extra:
        lines.append(f'【主题：其他】共 {len(extra)} 篇')
        for a in extra:
            summary = re.sub(r'\n+', ' / ', a.get('summary') or '')[:120]
            lines.append(f'  • 《{a.get("title","")}》（{a.get("source","")}）')
            lines.append(f'      摘要：{summary}')
    return '\n'.join(lines)


# ======================================================================
# REPORT 生成
# ======================================================================

def generate_report(articles: list[dict], engine, date_label: str,
                    dry_run: bool = False) -> str:
    topic_counts = Counter(a.get('topic', '其他') for a in articles)
    source_counts = Counter(a.get('source', '') for a in articles)
    top_sources = '、'.join(f'{s}({c})' for s, c in source_counts.most_common(5))
    ai_count = sum(1 for a in articles if a.get('topic') == FOCUS_TOPIC)
    other_count = len(articles) - ai_count

    header_lines = [
        f'# 🤖 AI 阅读日报 — {date_label}',
        '',
        f'> 📖 共 **{len(articles)}** 篇 · AI **{ai_count}** · 其他 **{other_count}**  ',
        f'> 📰 来源：{top_sources}  ',
        f'> 🎯 主题：{" / ".join(f"{t}{c}" for t, c in topic_counts.most_common())}',
        '',
        '---',
        '',
    ]

    if not articles:
        return '\n'.join(header_lines) + '> 暂无文章数据。请先运行 `biz_daily.py` 抓取公众号内容。\n'

    # dry-run：直接输出统计骨架
    if dry_run or engine is None:
        body_lines = ['## 📋 文章清单（dry-run，未调用 AI 提炼）', '']
        ordered = [FOCUS_TOPIC] + [t for t in TOPICS if t != FOCUS_TOPIC]
        for topic in ordered:
            group = [a for a in articles if a.get('topic') == topic]
            if not group:
                continue
            body_lines.append(f'### {topic} ({len(group)} 篇)')
            body_lines.append('')
            for a in group:
                s = re.sub(r'\n+', ' / ', a.get('summary') or '(无摘要)')[:160]
                body_lines.append(
                    f'- **《{a.get("title", "")}》** — *{a.get("source", "")}*  '
                    f'({a.get("date", "")})'
                )
                body_lines.append(f'  - {s}')
            body_lines.append('')
        ai_body = '\n'.join(body_lines)
    else:
        articles_block = build_articles_block(articles)
        print(f'  🤖 AI 提炼中（{len(articles)} 篇文章，本地/云端推理中）...')
        try:
            ai_body = engine.chat(
                REPORT_PROMPT.format(articles_block=articles_block),
                max_tokens=8192,
            )
        except Exception as e:
            ai_body = f'> ⚠️ AI 调用失败：{e}\n\n已输出文章骨架，请检查模型连接或使用 --dry-run。\n'

    footer_lines = [
        '',
        '---',
        '',
        f'*由 weflow-cli · generate_ai_report 自动生成 · '
        f'{datetime.now(TZ).strftime("%Y-%m-%d %H:%M")}*',
        '',
    ]

    return '\n'.join(header_lines) + ai_body.strip() + '\n' + '\n'.join(footer_lines)


# ======================================================================
# MAIN
# ======================================================================

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description='AI 阅读日报（信息密集精炼版）')
    parser.add_argument('--api-key', help='API key（local/ollama 不需要）')
    parser.add_argument('--engine', default='local',
                        help='AI 引擎: local(自动检测·默认) / deepseek / claude / ollama')
    parser.add_argument('--date', help='单日期 YYYY-MM-DD，默认今天')
    parser.add_argument('--range', type=int, help='最近 N 天合并报告，如 --range 7')
    parser.add_argument('--from', dest='from_date', help='起始日期 YYYY-MM-DD')
    parser.add_argument('--to', dest='to_date', help='结束日期 YYYY-MM-DD，默认今天')
    parser.add_argument('--output', help='输出文件路径（默认 output/ai-reports/）')
    parser.add_argument('--dry-run', action='store_true', help='不调用 AI，仅输出统计骨架')
    parser.add_argument('--exclude-topics', default='',
                        help='不在报告里出现的主题，逗号分隔（如 新闻,投资）；'
                             '展示层开关：不重抓、不改产物，只影响这份报告收谁')
    parser.add_argument('--include-all', action='store_true',
                        help='不筛选，收录当天全部文章（回退到引入收录判据之前的行为）')
    args = parser.parse_args()

    # 日期解析
    today = datetime.now(TZ).date()
    if args.date:
        d = datetime.strptime(args.date, '%Y-%m-%d').date()
        from_date, to_date = d, d
    elif args.range:
        to_date = today
        from_date = to_date - timedelta(days=args.range - 1)
    elif args.from_date:
        from_date = datetime.strptime(args.from_date, '%Y-%m-%d').date()
        to_date = datetime.strptime(args.to_date, '%Y-%m-%d').date() if args.to_date else today
    else:
        from_date = to_date = today

    date_label = from_date.strftime('%Y-%m-%d') if from_date == to_date else \
        f'{from_date.strftime("%Y-%m-%d")} ~ {to_date.strftime("%Y-%m-%d")}'

    print(f'=== AI 阅读日报 ===')
    print(f'📅 范围: {date_label}')
    print(f'🔧 引擎: {args.engine}' + (' (dry-run)' if args.dry_run else ''))

    # 加载文章
    exclude = excluded_topics(load_config(), args.exclude_topics,
                                  protected=(FOCUS_TOPIC,))
    if exclude:
        print('🚫 已排除主题: ' + '/'.join(t for t in TOPICS if t in exclude))
    articles = load_articles_range(from_date, to_date, args.include_all, exclude)
    if not articles:
        print(f'\n❌ 未找到 {date_label} 的文章。请先运行：')
        print(f'   python scripts/biz_daily.py')
        sys.exit(1)

    ai_count = sum(1 for a in articles if a.get('topic') == FOCUS_TOPIC)
    topic_counts = Counter(a.get('topic', '其他') for a in articles)
    print(f'📖 文章: {len(articles)} 篇（AI {ai_count} · 其他 {len(articles) - ai_count}）')
    print(f'🎯 主题: {dict(topic_counts)}')
    print(f'📰 来源: {len(set(a.get("source","") for a in articles))} 个公众号')

    # 输出路径
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = Path(ROOT_DIR) / out_path
    else:
        out_name = f'ai-report-{from_date.strftime("%Y-%m-%d")}.md' if from_date == to_date else \
            f'ai-report-{from_date.strftime("%Y%m%d")}-{to_date.strftime("%Y%m%d")}.md'
        out_path = Path(OUTPUT_DIR) / out_name

    # AI 引擎初始化
    engine = None
    if not args.dry_run:
        api_key = args.api_key or ''
        if args.engine not in ('local', 'ollama') and not api_key:
            api_key = os.environ.get('DEEPSEEK_API_KEY', '')
        try:
            engine = create_engine(args.engine, api_key)
        except (ValueError, RuntimeError) as e:
            print(f'\n[ERROR] {e}')
            print('       或者使用 --dry-run 直接输出文章清单。')
            sys.exit(1)

    # 生成报告
    t0 = time.time()
    report = generate_report(articles, engine, date_label, dry_run=args.dry_run)
    # 不确定那一段在本地算：它必须与"模型有没有写、写得漂不漂亮"无关。
    # 另外单独加载一次全集——被收录判据筛掉的边界条目也要能看见，
    # 否则"我拿不准"只报告了收进来的那一半。
    try:
        everything = load_articles_range(from_date, to_date, include_all=True,
                                         exclude=exclude)
    except Exception:
        everything = articles
    section = build_uncertainty_section(articles, everything, include_all=args.include_all,
                                        exclude=exclude)
    report += section
    uncertain_count = section.count(chr(10) + '- ')
    if uncertain_count:
        print(f'   不确定条目: {uncertain_count} 条已列入报告末尾')
    elapsed = time.time() - t0

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    size_kb = len(report.encode('utf-8')) / 1024

    print(f'\n✅ 报告已生成: {out_path}')
    print(f'   大小: {size_kb:.0f} KB · 耗时: {elapsed:.0f}s')


if __name__ == '__main__':
    main()
