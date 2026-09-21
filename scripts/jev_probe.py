#!/usr/bin/env python3
"""Jev 对照实验：衡量"决策模型 vs 现有标签"，而不是接线（D-031 已把判断接进日报）。

日报现在用 `scripts/jev_client.py` 判断主题与相关度。**这个脚本是那个决定的证据来源，
不是第二套实现**：契约、客户端、判据文本全部从 `jev_client` import，这里只负责取样、
对照和统计。抄一份过来会漂——事实上已经漂过一次：产品版把判据写成了裸的「低/中/高」，
而实测用的是带说明的版本（说明文字就是模型判档的依据），两边对齐之前，"测过的"
和"上线的"根本不是同一个东西。

用法：

    # 只打印将要发送的内容，不联网、不需要 key
    python scripts/jev_probe.py --dry-run

    # 通路验证：3 个小样本，把原始 answers 原样打出来
    python scripts/jev_probe.py --smoke --yes

    # 对照实验：真实文章 vs 文件里记的标签
    python scripts/jev_probe.py --against-daily --limit 60 --yes

**它给出的是一致率，不是准确率**：比对基准是 DeepSeek 自己写进 frontmatter 的
`topic`，两边完全可能一起错。要准确率得人工定标——这是仍然欠着的一步。

**数据出境**：--against-daily 会把文章正文发给 api.typesafe.ai，所以要求显式
`--yes`，且默认只发正文前 N 字符。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Windows 控制台默认 GBK，中文日报正文里有下标之类 GBK 编不出的字符，直接
# print 会 UnicodeEncodeError 崩掉。改成 UTF-8 + replace，让脚本在任何 codepage
# 下都不会因为“打印”这件事失败。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

# 契约与客户端只有一份实现（scripts/jev_client.py，产品代码用的就是它）。
# 这里原先抄了一份，抄本的判据文本和产品版一旦漂开，"测过的"和"上线的"就不是同一个东西。
from jev_client import (  # noqa: E402  (scripts/ 已由 main 插进 sys.path)
    DEFAULT_MODEL,
    ENDPOINT,
    INPUT_USD_PER_TOKEN,
    JevError,
    TOPIC_CRITERIA,
    build_questions,
    create_client,
)

# 探针自己的取样词表（产品侧的枚举在 biz_daily.TOPICS，仓库里另有多份，本轮不合并）。
TOPICS = ['AI', '学术', '新闻', '文学', '投资', '政治']


def split_frontmatter(text):
    """把 md 的 YAML frontmatter 和正文分开。

    **这一步是实验成立的前提，不是整洁问题**：日报产物在 frontmatter 里已经写着
    `topic: AI` / `relevance: 中` / `tags: [AI]`，那是 DeepSeek 自己的答案。连它
    一起发过去，模型只要照着抄就"一致"了，一致率会漂亮地指向一个假的结论。
    """
    meta = {}
    body = text
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ':' in line:
                    key, _, value = line.partition(':')
                    meta[key.strip()] = value.strip().strip('"')
            body = text[end + 4:]
    return meta, body.strip()


def collect_samples(limit, max_chars):
    """从日报产物里取真实文章，**按主题分层轮取**。

    直接按目录顺序切前 N 篇会严重偏样：日报是逐日生成的，`days/topics` 的字典序
    决定了前面几十篇几乎全来自第一天和第一个主题（实际就是 AI）。那样算出来的
    一致率和置信度分桶，测的是"AI 类文章"，不是"中文文章"。

    所以：先全量读进来按标签主题分组，再轮转取，保证每个主题都被覆盖到。

    标签取 frontmatter（写进文件的那份）而不是目录名——两者理论上一致、实际不一定，
    不一致的会被单独数出来。
    """
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'output', 'biz-daily')
    if not os.path.isdir(root):
        raise JevError(f'找不到日报产物目录: {root}')

    buckets = {topic: [] for topic in TOPICS}
    for day in sorted(os.listdir(root)):
        day_dir = os.path.join(root, day)
        if not os.path.isdir(day_dir):
            continue
        for topic in TOPICS:
            topic_dir = os.path.join(day_dir, topic)
            if not os.path.isdir(topic_dir):
                continue
            for name in sorted(os.listdir(topic_dir)):
                if not name.endswith('.md'):
                    continue
                try:
                    with open(os.path.join(topic_dir, name), encoding='utf-8') as handle:
                        raw = handle.read()
                except OSError:
                    continue
                meta, body = split_frontmatter(raw)
                # 只收真文章：`行动建议.md` 这类报告产物也躺在主题目录里、也带
                # topic 字段，但它是前一阶段的输出，拿去"分类对照"是自问自答
                # （第一次跑就混进来 2 条）。真文章都有 url。
                if not meta.get('url'):
                    continue
                label = meta.get('topic') or topic
                buckets.setdefault(label, []).append({
                    'day': day,
                    'title': meta.get('title') or name[:-3],
                    'body': body[:max_chars],
                    'labelTopic': label,
                    'labelRelevance': meta.get('relevance') or '',
                    'dirTopic': topic,
                })

    # 轮转：每轮从每个主题各取一篇，直到取够或取空。
    samples = []
    index = 0
    while len(samples) < limit:
        took_any = False
        for topic in sorted(buckets):
            bucket = buckets[topic]
            if index < len(bucket):
                samples.append(bucket[index])
                took_any = True
                if len(samples) >= limit:
                    break
        if not took_any:
            break
        index += 1
    return samples


def strate(title, body):
    """state 只放生产环境真会给模型的东西：标题 + 正文。

    日期、目录名、frontmatter 里的一切都不放（都是有答案味道的东西）。
    """
    return f'标题：{title}\n\n正文：\n{body}'


def cmd_dry_run(samples, max_chars):
    questions = build_questions(TOPICS, TOPIC_CRITERIA)
    print(f'将要发送 {len(samples)} 篇文章到 {ENDPOINT}')
    print(f'questions 形状：{json.dumps(questions, ensure_ascii=False)[:300]}...')
    print(f'state（每篇正文截断到 {max_chars} 字，不含 frontmatter）：\n')
    for sample in samples[:3]:
        state = strate(sample['title'], sample['body'])
        print('  ' + '-' * 66)
        for line in state[:400].splitlines():
            print('  | ' + line)
        print(f'  |（标签只在本地用于比对：topic={sample["labelTopic"]} '
              f'relevance={sample["labelRelevance"]}，不发出去）')
    total = sum(len(strate(s['title'], s['body'])) for s in samples)
    print(f'\n合计约 {total} 字符。正文出境，所以真正发送要加 --yes。')


def cmd_smoke(client):
    """三个小样本，把原始返回打出来 —— 目的是确认契约，不是确认质量。"""
    print(f'model 别名：{client.models()}')
    cases = [
        ('英文 string + noul', 'The support agent issued a full refund of $49 to the customer.',
         {'is_refund': {'type': 'noul', 'instructions': 'Was a refund issued?'}}),
        ('中文 string + noul/choice/score',
         '客户在微信里说：发票 4411 被重复扣款了，三天没人回复，请今天退款。',
         {'wants_refund': {'type': 'noul', 'instructions': '对方明确要求退款吗？'},
          'urgency': {'type': 'score', 'instructions': '紧急程度？',
                      'criteria': ['不急', '一般', '紧急', '十万火急']},
          'dept': {'type': 'choice', 'instructions': '该转给哪个团队？',
                   'criteria': {'billing': '账单/退款/付款问题',
                                'tech': '程序报错/崩溃'}}}),
        ('中文 dict 形式的 state（试探 state 能否传对象）',
         {'标题': '大气污染模拟新方法', '正文': '本文提出一种基于神经网络的大气污染模拟降尺度方法。'},
         {'is_paper': {'type': 'noul', 'instructions': '这是一篇科研论文吗？'},
          'topic': {'type': 'choice', 'instructions': '主题？',
                    'criteria': TOPIC_CRITERIA}}),
    ]
    total_in = 0
    for label, state, questions in cases:
        print(f'\n### {label}')
        try:
            answers, usage = client.decide(state, questions)
        except JevError as error:
            print(f'  失败：{error}')
            continue
        total_in += usage.get('input_tokens', 0)
        print('  answers :', json.dumps(answers, ensure_ascii=False))
        print('  usage   :', usage,
              f"-> ${usage.get('input_tokens', 0) * INPUT_USD_PER_TOKEN:.6f}")
    print(f'\n合计约 ${total_in * INPUT_USD_PER_TOKEN:.6f}')


def cmd_against_daily(samples, client):
    """对照：Jev 的主题 vs 文件里记的 DeepSeek 主题。

    **这是「一致率」，不是「准确率」**：标签来自 DeepSeek 自己的输出，两边完全
    可能一起错。要真的拿到准确率，得人抽一批做金标准——那是下一步，不是这一步。
    """
    questions = build_questions(TOPICS, TOPIC_CRITERIA)
    agree = 0
    done = 0
    total_in = 0
    rows = []
    dirty_labels = [s for s in samples if s['labelTopic'] != s['dirTopic']]
    for index, sample in enumerate(samples, 1):
        state = strate(sample['title'], sample['body'])
        try:
            answers, usage = client.decide(state, questions)
        except JevError as error:
            print(f'[{index}] 失败：{error}')
            break
        total_in += usage.get('input_tokens', 0)
        topic = answers.get('topic', {})
        relevance = answers.get('relevance', {})
        picked = topic.get('choice')
        same = picked == sample['labelTopic']
        agree += 1 if same else 0
        done += 1
        rows.append({'day': sample['day'], 'title': sample['title'][:40],
                     'label': sample['labelTopic'], 'jev': picked, 'same': same,
                     'conf': topic.get('confidence'),
                     'probs': topic.get('probabilities'),
                     'relevance': relevance.get('score'),
                     'relLabel': sample['labelRelevance'],
                     'relConf': relevance.get('confidence'),
                     'isPaper': answers.get('is_research_paper', {}).get('noul')})
        print(f'[{index}/{len(samples)}] {sample["day"]} {sample["title"][:32]}')
        print(f'    DeepSeek={sample["labelTopic"]}  Jev={picked} '
              f'conf={topic.get("confidence")}  一致={same}  '
              f'相关度 Jev={relevance.get("score")} / 标签={sample["labelRelevance"]}')

    if done:
        print(f'\n一致率 {agree}/{done} = {agree / done:.1%}（不是准确率，见函数说明）')
    if dirty_labels:
        print(f'注意：有 {len(dirty_labels)} 篇的 frontmatter 主题与目录名不一致，'
              f'比对以 frontmatter 为准。')
    print(f'输入 token 合计 {total_in}，约 ${total_in * INPUT_USD_PER_TOKEN:.6f}')

    # 一致度与置信度是否相关，是「概率能不能拿来定阈值」的唯一直接证据。
    with_conf = [r for r in rows if isinstance(r['conf'], (int, float))]
    if with_conf:
        for threshold in (0.8, 0.6):
            hi = [r for r in with_conf if r['conf'] >= threshold]
            lo = [r for r in with_conf if r['conf'] < threshold]
            hi_rate = sum(r['same'] for r in hi) / len(hi) if hi else 0
            lo_rate = sum(r['same'] for r in lo) / len(lo) if lo else 0
            print(f'  阈值 {threshold}: 高置信 {len(hi)} 条一致率 {hi_rate:.1%} / '
                  f'低置信 {len(lo)} 条一致率 {lo_rate:.1%}')
        print('  （高置信组明显更准，才说明概率有区分度、阈值能定；两组接近则说明'
              '概率没有信息量，只当分类器用就好）')
    return rows


def main():
    parser = argparse.ArgumentParser(description='Jev 中文对照探针（不接线）')
    parser.add_argument('--api-key', default='', help='不传则读 TYPESAFE_API_KEY')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--smoke', action='store_true', help='3 个小样本验证通路')
    parser.add_argument('--against-daily', action='store_true', help='用日报文章做对照')
    parser.add_argument('--dry-run', action='store_true', help='只打印要发什么，不联网')
    parser.add_argument('--limit', type=int, default=40, help='对照取多少篇')
    parser.add_argument('--max-chars', type=int, default=2000, help='每篇正文截断长度')
    parser.add_argument('--yes', action='store_true', help='确认把这些正文发到第三方')
    parser.add_argument('--json', action='store_true', help='对照结果按 JSON 输出')
    args = parser.parse_args()

    try:
        samples = collect_samples(args.limit, args.max_chars) if (
            args.dry_run or args.against_daily) else []

        if args.dry_run:
            if not samples:
                print('没有取到样本')
                return 1
            cmd_dry_run(samples, args.max_chars)
            return 0

        if not (args.smoke or args.against_daily):
            parser.print_help()
            return 1
        if not args.yes:
            print('这会把你本机的文章正文发到 api.typesafe.ai。确认无误后加 --yes，\n'
                  '或先跑 --dry-run 看清楚要发什么。')
            return 1

        client = create_client(args.api_key, model=args.model)
        if client is None:
            raise JevError(
                '缺少 TypeSafe key。用 --api-key，或 environment TYPESAFE_API_KEY，'
                '或 weflow-cli config set typesafeApiKey')
        if args.smoke:
            cmd_smoke(client)
        else:
            if not samples:
                print('没有取到样本')
                return 1
            rows = cmd_against_daily(samples, client)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    except JevError as error:
        print(f'错误：{error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
