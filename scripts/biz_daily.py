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
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 公共工具
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import (call_deepseek, load_config, decrypt_lock, get_api_key,
                    write_with_frontmatter, format_wikilinks,
                    TOPICS, TOPIC_CRITERIA, DEFAULT_TOPIC,
                    RELEVANCE_NAMES, DEFAULT_RELEVANCE,
                    excluded_topics, record_source_topics, load_source_topics,
                    source_prior_candidates, source_topics_path)

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

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 配置一律走 _utils.load_config()（它自己解析路径）。这里原先还有一份 CONFIG_PATH，
# 定义了但没人用过。
DB_PATH = None  # auto-detect from config
OUTPUT_ROOT = os.path.join(SCRIPT_DIR, 'output', 'biz-daily')
# 这里曾有一个 MAX_ARTICLES = 50，注释写着"最多抓取篇数"——**它从来没有被读过**。
# 真正生效的是 --limit（默认 0 = 不限），所以一天 150–270 篇是常态。一个写着上限
# 却不生效的常量，比没有它更误导人。
FETCH_TIMEOUT = 15
DEEPSEEK_TIMEOUT = 60
FETCH_DELAY_MIN = 8   # 最小抓取间隔 (秒)
FETCH_DELAY_MAX = 12  # 最大抓取间隔 (秒)
PREFETCH_WORKERS = 8  # `--prefetch-bodies` 不带数字时的并发数（预热正文缓存，见 prefetch_bodies）

# TOPICS 现在从 _utils 导入（见文件头的 import）——分类法只有一份定义，
# 因为"散文写的枚举"已经漂过一次：这里曾硬编码五类，而 TOPICS 是六类。


def load_source_categories(config: dict) -> dict[str, str]:
    """Load the explicit official-account category map from CLI config."""
    raw = config.get('dailySourceCategories', '')
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        print('[WARN] dailySourceCategories 不是有效 JSON，忽略来源类别配置')
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(name).strip().casefold(): str(category).strip()
        for name, category in data.items()
        if str(name).strip() and str(category).strip() in TOPICS
    }


def get_source_category(categories: dict[str, str], username: str, display_name: str) -> str:
    """Match a category by official-account ID first, then display name."""
    return categories.get(username.casefold()) or categories.get(display_name.casefold()) or ''
def _guess_topic(article: dict) -> str:
    """Keyword-based topic guess when AI classification fails."""
    title = article.get('title', '')
    account = article.get('account_name', '')
    text = (title + ' ' + account).lower()

    # Strong AI signals
    ai_keywords = ['ai', 'agent', 'llm', 'gpt', 'claude', 'codex', 'cursor',
                   '大模型', '编程', '开源', 'skill', 'prompt', 'deepseek',
                   'copilot', 'vibe coding', 'rag', 'embedding', 'token']
    if any(kw in text for kw in ai_keywords):
        return 'AI'

    # Investment signals
    invest_keywords = ['股票', '基金', '融资', 'ipo', '上市', '财报', 'a股', '港股']
    if any(kw in text for kw in invest_keywords):
        return '投资'

    # Academic signals
    academic_keywords = ['nature', 'science', 'cell', 'est', '论文', '研究', '实验室',
                        'doi', 'et al', 'abstract', 'method', 'result', 'conclusion']
    if any(kw in text for kw in academic_keywords):
        return '学术'

    # Literature signals
    lit_keywords = ['小说', '散文', '诗词', '美食', '旅游', '随笔', '历史', '读书']
    if any(kw in text for kw in lit_keywords):
        return '文学'

    # Default: news
    return '新闻'


def _tags_for_write(article, topic):
    """两个写入者共用的 tags 取值。

    md 与 json 原本各写各的默认：md 是 `a.get('tags', [topic])`，json 是
    `article.get('tags', [])`。**键不存在时**（`--no-ai`、或没配 API key，
    Phase 2 整段不跑，就不会有任何分类路径去设 `tags`）同一篇文章于是变成
    md `tags: [学术]`、json `tags: []`。2026-09-04 的 177 篇就是这个样子。

    回落成 `[主题]` 而不是 `[]`，是沿用本仓库既有的约定：分类路径自己就是
    `a['tags'] = [a['topic']]`（三处）。这里只是让 json 那个写入者跟上。

    注意用的是 `.get(k, 默认)` 而不是 `... or [topic]`：键**存在但为空表**
    （模型一个标签都没抽出来）是另一种情况，两条路都保留空表，不要在这里合流。
    """
    return article.get('tags', [topic])


def recent_article_counts(output_root, exclude_date, days=7):
    """最近若干天每天的文章数（跳过目标日与空日期）。用于判断"今天是不是异常地少"。"""
    counts = []
    if not output_root.is_dir():
        return counts
    for entry in sorted(output_root.iterdir(), reverse=True):
        if not entry.is_dir() or entry.name == exclude_date:
            continue
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', entry.name):
            continue
        target = entry / '.articles.json'
        if not target.exists():
            continue
        try:
            data = json.loads(target.read_text(encoding='utf-8'))
        except Exception:
            continue
        count = len(data.get('articles') or [])
        if count > 0:
            counts.append(count)
        if len(counts) >= days:
            break
    return counts


#: 低于中位数的这个比例才算"异常偏少"。定 30% 而不是 50%：公众号本来就有明显的工作日/周末
#: 起伏（实测 84 ~ 526 篇），阈值太松会让正常的清淡日天天报警，那样这个提示很快就没人看了。
LOW_VOLUME_RATIO = 0.3
#: 样本中位数低于这个数就不下结论：样本本身太小的时候，"少"没有可比性。
LOW_VOLUME_MIN_MEDIAN = 20


def detect_low_volume(output_root, date_str, today_count):
    """今天是不是比最近几天少得离谱。返回 None 表示正常（或样本不足以判断）。

    存在的理由见调用处：这不是统计趣味，是**唯一能把"没人推送"和"客户端没开"分开的信号**。
    """
    counts = recent_article_counts(output_root, date_str)
    if len(counts) < 3:
        return None
    ordered = sorted(counts)
    median = ordered[len(ordered) // 2]
    if median < LOW_VOLUME_MIN_MEDIAN:
        return None
    if today_count >= median * LOW_VOLUME_RATIO:
        return None
    return {
        'today': today_count,
        'median': median,
        'ratio': round(today_count / median, 2),
        'sample': len(counts),
    }


def session_activity(config, biz_db_path, days=3):
    """客户端**自己**认为每天有多少个公众号会话有更新。

    为什么要单独读会话表：消息表和会话表是两份独立记录。两者都少，说明推送压根没到本机；
    只有消息表少，才可能是我们读漏了。2026-10-29 实测两者一致（今天 14 / 昨天 148），
    而订阅号界面里能看到两百篇——那些是**联网现拉**的，从未落盘。

    读不到就返回 `{}`：这只是诊断信息，不该让日报失败。
    """
    from _utils import decrypt_lock
    import os
    try:
        pass_enc = str(config.get('favPassphrase', '') or config.get('decryptKey', ''))
        if not pass_enc:
            return {}
        passphrase = bytes.fromhex(decrypt_lock(pass_enc))
        root = os.path.dirname(os.path.dirname(os.path.abspath(biz_db_path)))
        sdb = os.path.join(root, 'session', 'session.db')
        if not os.path.exists(sdb):
            return {}
        with open(sdb, 'rb') as fh:
            salt = fh.read(16)
        key = hashlib.pbkdf2_hmac('sha512', passphrase, salt, 256000, dklen=32).hex()
        conn = sqlcipher.connect(sdb)
        cur = conn.cursor()
        cur.execute(f'PRAGMA key = "x\'{key}{salt.hex()}\'"')
        cur.execute("SELECT username, last_timestamp FROM SessionTable WHERE username LIKE 'gh_%'")
        result = {}
        tz = timezone(timedelta(hours=8))
        for _user, ts in cur.fetchall():
            if not ts:
                continue
            day = datetime.fromtimestamp(ts, tz=tz).strftime('%Y-%m-%d')
            result[day] = result.get(day, 0) + 1
        conn.close()
        return result
    except Exception:
        return {}


def browser_history_articles(date_str, tz, limit=300):
    """从**内嵌浏览器**的浏览历史里补读公众号文章。

    为什么需要：微信 4.x 的订阅号是网页渲染的（`radium` 里的 Chromium）。用户在客户端里
    点开的文章**不写** `biz_message_0.db`，只在这份 Chromium `History` 里留下
    `(url, title, last_visit_time)`。2026-10-29 实测：那天消息表只有 29 行，而历史库里
    有用户实际打开过的公众号文章，包括一篇在消息表里完全找不到的。

    边界：
      - **只读**、只取 `mp.weixin.qq.com` 的 URL，不碰其它浏览记录；
      - 这是"你打开过的"，不是"推送过的"，所以它补的是缺口，不能替代推送；
      - 任何失败都返回空列表——诊断/补读功能不该让日报挂掉。
    """
    import os
    import sqlite3
    try:
        appdata = os.environ.get('APPDATA') or ''
        if not appdata:
            return []
        base = Path(appdata) / 'Tencent' / 'xwechat' / 'radium' / 'web' / 'profiles'
        if not base.is_dir():
            return []
        target = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=tz)
        start = int((target - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1_000_000)
        end = start + 86400 * 1_000_000
        found = {}
        for profile in base.iterdir():
            hist = profile / 'History'
            if not hist.exists():
                continue
            try:
                conn = sqlite3.connect(f'file:{hist}?mode=ro&immutable=1', uri=True)
                cur = conn.cursor()
                cur.execute(
                    "SELECT url, title, last_visit_time FROM urls "
                    "WHERE url LIKE '%mp.weixin.qq.com%' AND last_visit_time >= ? AND last_visit_time < ?",
                    (start, end))
                for url, title, visit in cur.fetchall():
                    if url and url not in found:
                        found[url] = (title or '', visit)
                conn.close()
            except Exception:
                continue
        out = []
        for url, (title, visit) in list(found.items())[:limit]:
            when = (datetime(1601, 1, 1, tzinfo=timezone.utc)
                    + timedelta(microseconds=visit)).astimezone(tz)
            out.append({
                'account': '(browser-history)',
                'account_name': '浏览记录',
                'source_category': '',
                'title': title or url,
                'digest': '',
                'url': url,
                'cover': '',
                'local_text': '',
                'time': when.strftime('%H:%M'),
                'timestamp': int(when.timestamp()),
            })
        return out
    except Exception:
        return []


def _article_key(entry):
    """同一篇文章的合并键：url 优先（微信文章 url 唯一），没有 url 时退回标题+时间。"""
    url = str(entry.get('url') or '').strip()
    if url:
        return url
    title = str(entry.get('title') or '').strip()
    if not title:
        return ''
    return 'no-url:%s|%s' % (title, entry.get('time') or '')


def merge_articles_json(json_path, date_str, serializable, decision_model=None, session_days=None):
    """把这一批**并进** `.articles.json`，返回落盘的全部条目（按时间排序）。

    为什么必须合并：同一天可能跑两次（午报 13 点 + 日终 24 点）。第二次进来时
    `.run_state.json` 已经把上午处理过的文章跳过了，所以这批只有下午新到达的那些；
    若覆盖写，上午那批的标题/摘要/主题/relevance 就从这个文件里消失。而 README 与
    `index.html` 是**从磁盘上的 md 重建**的 —— 于是"页面是全天、结构化数据只有半天"，
    下游（AI 报告、daily-stats、知识库统计）静默少一半，没有任何地方会报错。
    `.image_map.json` 早就是合并写的（`existing_map.update(...)`），这里只是把它缺的那半补上。
    """
    merged = {}
    session_days = session_days or {}
    previous_model = None
    if json_path.exists():
        try:
            old = json.loads(json_path.read_text(encoding='utf-8'))
            previous_model = old.get('decisionModel')
            for entry in old.get('articles') or []:
                key = _article_key(entry)
                if key:
                    merged[key] = entry
        except Exception:
            # 旧文件坏了不该让今天失败：从空开始（与 `.image_map.json` 同一态度）
            print('  [WARN] 旧的 .articles.json 读不了，这次从空开始（旧条目不参与合并）')
    for entry in serializable:
        key = _article_key(entry)
        if key:
            merged[key] = entry
    articles = sorted(merged.values(), key=lambda e: (e.get('time') or '', e.get('title') or ''))
    # **安静的一天和"客户端没开"必须能分辨。**
    # 公众号推送只在微信客户端运行时才会写进本地库，所以客户端没开的那几个小时是**永久缺失**的
    # （上游不补发）。2026-10-29 实测：本地库整天只有 21 行、14 个账号，而前一天是 248 行、
    # 151 个账号，且当天 00:00–10:00 一行都没有——日报却安静地发了一封 6 篇的邮件。
    # 没人报错，因为"少"和"正常但冷清"在数据上长得一样。这里把它变成一句话。
    low_volume = detect_low_volume(json_path.parent.parent, date_str, len(articles))
    payload = {
        'generated_at': datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),
        'date': date_str,
        # 本次没判断（`--no-ai`）时不要用 None 盖掉上一次记下的模型名：那会让
        # "这天是谁判的"在重跑后凭空消失。
        'decisionModel': decision_model if decision_model is not None else previous_model,
        'articles': articles,
    }
    if low_volume:
        payload['lowVolume'] = low_volume
        # 会话表是**客户端自己的**记录：它也少，就说明推送没到本机，而不是我们读漏了。
        yesterday = (datetime.strptime(date_str, '%Y-%m-%d')
                     - timedelta(days=1)).strftime('%Y-%m-%d')
        sess_today = session_days.get(date_str)
        sess_prev = session_days.get(yesterday)
        payload['lowVolume']['sessionToday'] = sess_today
        payload['lowVolume']['sessionPrevDay'] = sess_prev
        detail = ''
        if sess_today is not None:
            detail = (f' 客户端会话表同日也只有 {sess_today} 个公众号有更新'
                      + (f'（前一天 {sess_prev}）' if sess_prev is not None else '')
                      + '——推送没到本机，不是读漏了。')
        print(f'  [WARN] 本日只采到 {low_volume["today"]} 篇，最近 {low_volume["sample"]} 天的中位数是 '
              f'{low_volume["median"]} 篇。{detail}'
              f'订阅号界面里的内容可能是联网现拉的，从未落盘。')
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return articles


def _serializable_article(article, date_str):
    """一篇文章 -> `.articles.json` 里的那条记录。

    **判断的原始概率必须一起写进去。** 报告优先读这个文件，只把概率写进 md 的
    frontmatter 的话，它们在报告那条主路径上等于不存在——真的发生过：日报末尾的
    "我拿不准的"永远只输出一句"没有概率字段"，而 frontmatter 里明明有。
    """
    entry = {
        'title': article.get('title', ''),
        'source': article.get('account_name', ''),
        'date': date_str,
        'time': article.get('time', ''),
        # 默认值与分组/md 那条路**必须是同一个**。这里曾经是 `''`，而分组那边是
        # `'学术'`，于是同一次运行里 md 写 `topic: 学术`、json 写 `topic: ""`。
        'topic': article.get('topic') or DEFAULT_TOPIC,
        'relevance': article.get('relevance', DEFAULT_RELEVANCE),
        'tags': _tags_for_write(article, article.get('topic') or DEFAULT_TOPIC),
        'summary': article.get('summary', article.get('digest', '')),
        'url': article.get('url', ''),
    }
    for key in ('relevanceScore', 'topicConfidence', 'includeScore'):
        if article.get(key) is not None:
            entry[key] = round(float(article[key]), 3)
    return entry


def _normalize_topics(articles):
    """把每篇的主题定稿到 `TOPICS` 之内，**就地**改，返回兜底的篇数。

    在写入之前只跑这一次，分组、md frontmatter、json 三条路都读它的结果。

    以前没有这一步，主题在写入路径上有三个来源，而且都不报错：

    * 分组键：`a.get('topic','学术')`，越界再折成 `'学术'` —— md 写的是它
    * json：`a.get('topic','')` —— 空字符串
    * 原始字段：可能是空的，也可能不在分类法里

    2026-09-04 的产出就是这么分叉的：178 篇全落在 `学术/`、frontmatter 写
    `topic: 学术`（其中两篇是「OpenAI 深夜发布 GPT-6」「专为高管准备的 AI 助手」），
    而同一批的 json 全是 `topic: ""` —— 报告读 json，整批被静默排除。

    返回值不为 0 就是"这一批有文章没拿到主题"的信号，调用方要把它说出来：
    整批兜底（`--no-ai` 就是）不该看起来像一次正常分类。
    """
    fallbacks = 0
    for a in articles:
        if a.get('topic') not in TOPICS:
            a['topic'] = DEFAULT_TOPIC
            fallbacks += 1
    return fallbacks


def _group_by_topic(articles):
    """归一化主题并按主题分桶，返回 `(分组, 兜底篇数)`。

    **分组键就是 md 写进 frontmatter 的那个主题**（见 Phase 3 的 `fm['topic']`），
    所以这张表和 `.articles.json` 里的 `topic` 必须指的是同一个值——md 与 json
    对同一篇文章给出不同主题，就是从这里分叉出去的。

    归一化放在函数内部而不是让调用方分两步做：两步就能被写颠倒（先分组再归一化，
    键就落在归一化之前的旧值上），而这类颠倒不会报错。合成一个入口之后，
    "分组用的主题"和"落盘用的主题"在结构上不可能不是同一个。
    """
    fallbacks = _normalize_topics(articles)
    groups = {t: [] for t in TOPICS}
    for a in articles:
        # 直取不兜底：归一化刚保证过成员资格，这里再兜一次就等于又有第二个默认值。
        groups[a['topic']].append(a)
    return groups, fallbacks


def _classify_with_jev(client, title, body, topics, noise=None):
    """让 Jev 判断这一篇；**问不出来就返回 None**，由调用方逐篇退回老路。

    这里刻意吞掉异常（包括 `JevError` 之外的意外），因为"分类这一篇失败"
    不该让整天的日报挂掉。但异常类型会打出来——一个真的写错了的行号不该
    在日志里长得跟"网络抖了一下"一样。

    `noise` 可选（见 `_classify_articles_parallel`）：把**同一条**失败折叠成一次输出。
    一天上百篇全因同一个原因失败时（实测：判断层按地区不可用，HTTP 451），打上百行
    等于没说；而**没见过的异常类型或没见过的消息仍然每次照打**——折叠的是重复，不是未知。
    """
    if client is None:
        return None
    try:
        return client.decide_article(title, body, topics)
    except Exception as error:
        signature = (type(error).__name__, str(error)[:200])
        if noise is not None and signature in noise['seen']:
            noise['repeat'] += 1
        else:
            if noise is not None:
                noise['seen'].add(signature)
            print(f'    [WARN] Jev 分类失败（{type(error).__name__}），本篇退回 LLM 解析：{error}')
        return None


# 分类是纯网络等待（实测 0.97s/篇），而且对摘要没有任何依赖，所以没有理由
# 把它排在那条串行的 LLM 循环里一篇一篇等。并发数克制一些：服务 2026-09-15 才上线，
# 打满了会返回 529（已实测遇到过），并发拉高只会换来一堆重试。
JEV_WORKERS = 6
# 图片并发。取的是 qpic.cn（微信 CDN），浏览器本来就并发取图；串行只是实现选择。
# 实测每张 0.37s、135 KB、一篇 14–31 张，串行下来 190 篇约 18 分钟。
IMAGE_WORKERS = 6


def classifier_plan(no_ai, classifier, deepseek_key, engine, needs_llm=True):
    """这次日报用哪条判断路径。返回 `'skip' | 'jev' | 'llm'`。

    这是 D-031 那句"`--classifier` 是一条命令回滚"的**全部实现**——三条分支此前
    没有任何测试，而回滚承诺就压在它们上面：

    * `'llm'` 必须**不碰 Jev**（一键回退，也是"同一天两条路各跑一遍"的做法）；
    * `'jev'` 只是**意图**，不是结果——没有 key、或 key 解不开时，建客户端会失败，
      由调用方按 `classifier == 'jev'` 决定要不要告警，然后安静退回老路（`auto` 的语义）；
    * `'skip'` 让**整块 Phase 2 都不执行**：`--no-ai`，或 `dailyAiEnabled=false`
      （那个配置项在更早处被折进 `no_ai`）。

    `needs_llm=False`（`--no-summary`：只要判断、不要生成）时，**不再要求 LLM key**——
    那一层的 key 校验是为了调 LLM 生成摘要，而这条路上根本不生成。少了这一句，
    "没有 DeepSeek key 也能只用 Jev 分类"这个诉求会静默变成"整段不跑"。

    单独成函数是为了能**离线枚举**这几种组合——它们决定"有没有数据出境"，
    不该只靠读代码确认。
    """
    if no_ai:
        return 'skip'
    if needs_llm and not (deepseek_key or engine != 'deepseek'):
        return 'skip'
    return 'jev' if classifier in ('auto', 'jev') else 'llm'


def _classify_articles_parallel(articles, client, topics, workers=JEV_WORKERS):
    """把能分类的篇目一次性并发问完，返回 {下标: decision}。

    分类对摘要没有任何依赖，两者串在一起只是历史顺序。单独跑这一步之后，
    串行的摘要循环里就只剩它真正必须串行的那部分。

    eligibility 与主循环保持一致（正文存在且够长）——不一致的话，某些文章会在这
    一步被跳过、然后在循环里又被问一次，等于白花一次调用。
    """
    if client is None:
        return {}
    todo = []
    for index, article in enumerate(articles):
        content = article.get('fetched_md') or article.get('local_text', '')
        if content and len(content.strip()) > 50:
            todo.append((index, article.get('title', ''), content))
    if not todo:
        return {}

    started = time.time()
    decisions = {}
    # 同一条失败折叠成一次输出（见 `_classify_with_jev`）：一天上百篇同一个原因失败时，
    # 逐篇刷同一行等于没说；但没见过的异常/消息仍然照打。
    noise = {'seen': set(), 'repeat': 0}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_classify_with_jev, client, title, body, topics, noise): index
            for index, title, body in todo
        }
        for future in as_completed(futures):
            decisions[futures[future]] = future.result()

    ok = sum(1 for value in decisions.values() if value)
    # 先报个数：并发之后逐篇日志没有意义了，但这三件事必须看得见。
    print(f'  分类完成 {ok}/{len(todo)} 篇，耗时 {time.time() - started:.1f}s'
          f'（{workers} 并发；串行约需 {len(todo) * 0.97:.0f}s）')
    if noise['repeat']:
        print(f'  [WARN] 另有 {noise["repeat"]} 篇因**同一条**失败被折叠（原因已在上面打过一次）')
    return decisions


def _apply_decision(article, decision, set_topic=True):
    """把 Jev 的判断写进 article。`set_topic=False` 用于主题已被人工配置钉住的场景。"""
    if not decision:
        return False
    if set_topic:
        article['topic'] = decision['topic']
    article['relevance'] = decision['relevance']
    # 原始分与置信度一起落盘：相关度那三档的切点是暂定的，留着原始值，
    # 将来校准不用重跑整天的日报。
    article['relevanceScore'] = decision.get('relevanceScore')
    if decision.get('topicConfidence') is not None:
        article['topicConfidence'] = decision['topicConfidence']
    # 收录判断与 relatedness 不是一回事，单独存一个原始概率给日报那道门用。
    if decision.get('includeScore') is not None:
        article['includeScore'] = decision['includeScore']
    return True


SUMMARY_WORKERS = 6
# 单篇摘要的实测耗时（3 篇串行 2.27/2.92/2.45s，含网络）。只用来报「串行约需多少秒」。
SUMMARY_SECONDS_PER_ARTICLE = 2.5


def summary_prompt_for(article, category_hint, judged=False):
    """这一篇要发的提示词与 `max_tokens`。

    三条分支：
    * 配了类别的来源 → 短提示词（只要摘要，`max_tokens=1000`）；
    * `judged=True`（Jev 已经判过这一篇）→ `SUMMARY_ONLY_PROMPT`，不再要它写主题/相关度；
    * 其余（`--classifier llm`，或单篇 Jev 失败退回来的）→ 完整的 `TOPIC_PROMPT`，
      **与旧行为逐字节一致**，回退路径不能换标准。

    抽出来是为了让"取摘要"能并发：搬动的是**调用位置**，不是内容。
    """
    content = article.get('fetched_md') or article.get('local_text', '')
    if category_hint:
        return (f'''请只为下面这篇公众号文章生成一段 50-300 字的中文摘要。
来源类别已经确定为「{category_hint}」，不要重新判断或改写文章分类，不要输出主题、标签、相关度或概念字段。

标题：{article["title"]}
来源：{article["account_name"]}

正文：
{content[:4000]}''', 1000)
    base = SUMMARY_ONLY_PROMPT if judged else TOPIC_PROMPT
    prompt = base + f'\n\n标题：{article["title"]}\n来源：{article["account_name"]}'
    prompt += f'\n\n内容：\n{content[:4000]}'
    return prompt, 2000


def _summarise_articles_parallel(articles, engine, api_key, workers=SUMMARY_WORKERS,
                                 decisions=None):
    """把每篇的 LLM 调用先并发跑完，返回与 `articles` 等长的 `(response, error)`。

    **只有网络等待是并发的**：调用方仍按原顺序串行地解析与落字段，所以每篇的写入
    顺序、以及失败时走哪条兜底分支，都与串行版本一致。这一层不碰 article dict。

    资格判断必须与调用方**逐字一致**（`content and len(content.strip()) > 50`），
    否则会出现"并发跑了、主循环却不认为该跑"的错位——那正是 `_classify_articles_parallel`
    里用下标对齐要防的东西，这里同样按**原下标**回填。

    实测（12 篇）：串行约 12s → 6 路约 3s。串行时每篇后睡 0.3s 的节奏挪到了
    worker 里（每个请求照样睡一次），所以对上游的请求速率没有变密。
    """
    results = [(None, None)] * len(articles)
    jobs = [i for i, a in enumerate(articles)
            if (a.get('fetched_md') or a.get('local_text', ''))
            and len((a.get('fetched_md') or a.get('local_text', '')).strip()) > 50]
    if not jobs:
        return results

    from _utils import call_ai

    def one(index):
        article = articles[index]
        # 逐篇判断，不是整批一刀切：Jev 判过的那篇用精简提示词，判失败的仍用完整提示词
        # （它要靠 LLM 的【主题】/【相关度】兜底）。
        judged = bool((decisions or {}).get(index))
        prompt, max_tokens = summary_prompt_for(article, article.get('source_category', ''),
                                               judged=judged)
        try:
            return index, call_ai(prompt, engine, api_key, max_tokens=max_tokens), None
        except Exception as exc:            # 交给调用方那条原有的 except 分支
            return index, None, exc
        finally:
            time.sleep(0.3)

    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for index, response, error in pool.map(one, jobs):
            results[index] = (response, error)

    ok = sum(1 for response, _error in results if response is not None)
    # 与分类那段同一个形状：并发之后逐篇日志不再说明什么，但三件事必须看得见——
    # 成了几篇、实际耗时、串行本来要多久。
    print(f'  摘要完成 {ok}/{len(jobs)} 篇，耗时 {time.time() - started:.1f}s'
          f'（{workers} 并发；串行约需 {len(jobs) * SUMMARY_SECONDS_PER_ARTICLE:.0f}s）')
    return results


TOPIC_PROMPT = f"""对文章分类、深度摘要、打标签，并评估与读者的相关度。

【读者定位】环境科学研究生，研究方向是计算机与环境的交叉领域（环境模型、大气污染模拟、遥感反演、环境大数据分析、LCA等），关注AI工具如何提升科研效率。

【主题】必须且只能是：{' / '.join(TOPICS)} 中的一个词。

判断规则（每一类都必须落到其中一条）：
{chr(10).join('- %s：%s' % (name, desc) for name, desc in TOPIC_CRITERIA.items())}

【相关度】对上述读者的实用价值：
- 高：可直接用于科研（新工具/新方法/数据源/代码库）
- 中：有启发性（思路/趋势/跨领域技术）
- 低：信息性阅读（纯新闻/娱乐/文学）

**分类关键**：
- 【主题】只写一个词（{'/'.join(TOPICS)}），不要写其他文字
- 【相关度】只写一个词（高/中/低）
- 科研论文、期刊文章优先归学术；AI技术/工具/产品归AI

**摘要要求**：
- 【摘要】写一段完整的深度摘要（150-300字），不要只写一两句
- 格式：【核心观点】一句话概括中心思想。【关键细节】列出3-5个具体要点（工具/方法/数据/结论/人物/事件等），每个要点一句话
- 摘要不需要包含分类/相关度/标签信息，那些由上面的字段处理

返回格式（严格）：
【主题】AI
【相关度】高
【标签】tag1, tag2, tag3
【摘要】【核心观点】一句话。【关键细节】1. 要点一；2. 要点二；3. 要点三
【概念】概念名|一句话说明, 概念名|一句话说明"""

# Jev 已经判过主题与相关度时，给 LLM 的提示词改成这一份：**不再要它输出那两个字段**。
#
# 原来无论谁判，提示词都把【主题】【相关度】连"六类判据 + 三档定义"一起塞进去，
# 而 Jev 那条路上这两个答案是白写的——每篇约 20 个输出 token，加上那几段判据约
# 300 个输入 token，一天 400 篇就是十来万 token 的纯浪费。
#
# **摘要要求那一段与 `TOPIC_PROMPT` 逐字相同**（有测试钉住）——只动分类那几段，
# 生成的摘要/标签/概念不受影响。`--classifier llm`、以及单篇 Jev 失败退回来的那些，
# 仍然用完整的 `TOPIC_PROMPT`，回退路径与旧行为逐字节一致（D-031 的约束）。
SUMMARY_ONLY_PROMPT = f"""为文章写深度摘要、打标签，并抽取概念。

【读者定位】环境科学研究生，研究方向是计算机与环境的交叉领域（环境模型、大气污染模拟、遥感反演、环境大数据分析、LCA等），关注AI工具如何提升科研效率。

**摘要要求**：
- 【摘要】写一段完整的深度摘要（150-300字），不要只写一两句
- 格式：【核心观点】一句话概括中心思想。【关键细节】列出3-5个具体要点（工具/方法/数据/结论/人物/事件等），每个要点一句话
- 摘要不需要包含分类/相关度/标签信息，那些由上面的字段处理

返回格式（严格）：
【标签】tag1, tag2, tag3
【摘要】【核心观点】一句话。【关键细节】1. 要点一；2. 要点二；3. 要点三
【概念】概念名|一句话说明, 概念名|一句话说明"""

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

    # biz_message_0.db（订阅号库）：钥匙派生统一在 `_utils.biz_message_db()`。
    # 这块原来自己算了两遍（先按 bizKey/bizSalt，又被下面按 passphrase 派生的一遍覆盖），
    # 两遍并存就是分叉留下的痕迹 —— 见那个函数的注释。
    from _utils import biz_message_db
    try:
        biz_db, biz_key, biz_salt = biz_message_db(config)
    except RuntimeError as exc:
        raise SystemExit(str(exc))

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

def _decode_body(raw: bytes, content_encoding: str) -> str:
    """按 `Content-Encoding` 解压响应体。认不出来就原样解码。

    **只认我们主动要求的两种**（`gzip`/`deflate`）——请求里声明的就是这两种，
    服务器不该回别的；真回了不认识的，退回原样解码而不是抛错：那就会走
    `fetch_article` 的重试与回退，把一次"少传了字节"的优化变成"抓不到文章"。
    """
    enc = (content_encoding or '').strip().lower()
    if enc == 'gzip':
        import gzip
        raw = gzip.decompress(raw)
    elif enc == 'deflate':
        import zlib
        # 裸 deflate（无 zlib 头）是这里的常见形态，先按裸的试，再退回带头的。
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            raw = zlib.decompress(raw)
    return raw.decode('utf-8', errors='ignore')


def summary_section(summary):
    """md 正文里的摘要段。**空就不写这一段**（`--no-summary` 就是这种情况）。

    为什么要单独一条规则：没有摘要时，把标题写上去会看起来像"摘要生成失败"，而用
    本地 digest 顶上又会看起来像 AI 写的摘要——两者都是把不存在的东西说成存在。
    判据用"去空白后非空"，顺带挡住空串与纯空白。
    """
    return f'## AI 摘要\n\n{summary}\n\n' if str(summary or '').strip() else ''


def fetch_article(url: str, max_retries: int = 3) -> str | None:
    """Fetch WeChat article with WeChat browser UA to bypass WAF.
    Retries up to max_retries times if content is too short."""
    last_result = None
    for attempt in range(max_retries):
        try:
            # L1: Try direct fetch with WeChat UA headers
            req = urllib.request.Request(url, headers={
                'User-Agent': WECHAT_UA,
                'Referer': 'https://mp.weixin.qq.com/',
                'Origin': 'https://mp.weixin.qq.com',
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'zh-CN,zh;q=0.9',
                # **`urllib` 默认不发这个头**，于是服务器只好把整页未压缩地传过来。
                # 微信文章页是 3–4 MB（大量内联 JS/CSS），实测同一篇：不发头 33–40s，
                # 发了 gzip 后 0.7–0.8 MB / 6–10s——正文一模一样（解压后同尺寸，
                # `js_content` 与 `rich_media_content` 都在）。传的字节更少，
                # 上游压力是**变小**的，与"按篇节流"同一个方向。
                'Accept-Encoding': 'gzip, deflate',
            })
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                raw = resp.read()
                html = _decode_body(raw, resp.headers.get('Content-Encoding', ''))

            # Validate: article content must include js_content div
            if 'js_content' not in html and 'rich_media_content' not in html:
                print(f'  [WARN] 内容验证失败 (无 js_content)')
                return None

            # 微信文章用 data-src 懒加载图片，先提取再转 markdown
            img_count_before = len(re.findall(r'<img\s', html, re.I))
            html = re.sub(r'data-src="(https?://[^"]+)"', r'src="\1"', html)
            img_count_after = len(re.findall(r'<img\s[^>]*src="https?://[^"]*mmbiz', html, re.I))

            # Convert to markdown
            import html2text
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = False
            h.body_width = 0
            h.protect_links = True
            h.wrap_links = False
            result = h.handle(html)

            # 检查内容是否充足（去掉图片链接和空白后至少有 100 字）
            body_check = re.sub(r'!\[.*?\]\(.*?\)', '', result)
            body_check = re.sub(r'\[.*?\]\(.*?\)', '', body_check)
            body_check = ''.join(c for c in body_check if c not in ' \n\r\t#*->|')
            if len(body_check) < 100:
                if attempt < max_retries - 1:
                    retry_delay = (attempt + 1) * 3
                    print(f'  内容过短({len(body_check)}字), 第{attempt+1}次重试 (等{retry_delay}s)...')
                    time.sleep(retry_delay)
                    continue
                else:
                    print(f'  内容过短({len(body_check)}字), 已达最大重试次数')
                    return None

            # 统计结果中的图片
            md_imgs = re.findall(r'!\[.*?\]\(https?://', result)
            retry_suffix = f' (重试{attempt}次)' if attempt > 0 else ''
            print(f'  HTML图片: {img_count_before}个, mmbiz图片: {img_count_after}个, MD图片: {len(md_imgs)}个{retry_suffix}')
            return result
        except Exception as e:
            last_result = e
            if attempt < max_retries - 1:
                retry_delay = (attempt + 1) * 3
                print(f'  抓取异常, 第{attempt+1}次重试 (等{retry_delay}s): {e}')
                time.sleep(retry_delay)
                continue

    # L2: Fallback to Scrapling with custom headers
    if _FETCHER_AVAILABLE:
        try:
            page = _Fetcher.get(url)
            import html2text
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.body_width = 0
            result = h.handle(page.html_content)
            body_check = re.sub(r'!\[.*?\]\(.*?\)', '', result)
            body_check = ''.join(c for c in body_check if c not in ' \n\r\t#*->|')
            if len(body_check) >= 100:
                return result
        except Exception:
            pass
    print(f'  [WARN] 抓取失败: {last_result} (scrapling 未安装，无 fallback)')
    return None


FETCH_CACHE_DIR = os.path.join(SCRIPT_DIR, 'output', '.cache', 'fetch')


def fetch_article_cached(url: str, use_cache: bool = True) -> tuple[str | None, bool]:
    """抓正文，带本地缓存。返回 `(markdown 或 None, 是否命中缓存)`。

    **为什么需要它**：日报是**全有全无**的——要把当天的文章**全部抓完**才进 Phase 3
    写盘，抓取结果只存在内存里。一天 400 篇光是抓取（10s/篇 + 8–12s 节流）就一个多
    小时，任何中断（被收走、网络断、手滑）都会把前面的抓取全部作废。2026-09-22 那天
    实测停在 105/403，什么都没写出来。
    按 URL 缓存正文之后：**重跑只抓缺的那些**，也顺带让"改代码 / 换分类器 /
    `--no-summary` 与正常模式之间切换"重跑同一天几乎免费。

    **只缓存成功**：失败的下次照旧重试（`None` 不落盘）。
    """
    path = os.path.join(FETCH_CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + '.md')
    if use_cache and os.path.isfile(path) and os.path.getsize(path) > 0:
        try:
            with open(path, encoding='utf-8') as fh:
                body = fh.read()
            if body.strip():
                return body, True
        except OSError:
            pass                      # 读不了就当没缓存，走网络
    body = fetch_article(url)
    if body and body.strip():
        try:
            os.makedirs(FETCH_CACHE_DIR, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(body)
        except OSError:
            pass                      # 缓存写不进去不该让这一天的日报失败
    return body, False


def prefetch_bodies(articles, workers=PREFETCH_WORKERS, log=None):
    """**并发**把这一批文章的正文抓进缓存（`output/.cache/fetch/`），只抓不写盘。

    为什么值得有：Phase 1 是**串行**的，而且未命中缓存时每篇要 `sleep` 8~12 秒（节流是为了
    少打扰上游）——450 篇就是约 75 分钟。而**命中缓存既不联网也不 sleep**（见
    `fetch_article_cached`），所以先把正文并发抓下来，Phase 1 就变成秒级。
    对上游的**请求总次数完全相同**，区别只是把同样多的请求压进更短的时间窗 ——
    这是一个显式取舍，所以默认**关**，由 `--prefetch-bodies` 打开。

    失败**不抛也不报错退出**：Phase 1 还会再试一次（那里才是"这篇到底有没有正文"的判据）。
    返回 `{'requested', 'ok'}`。
    """
    urls = []
    for a in articles:
        url = a.get('url')
        if url and url not in urls:
            urls.append(url)
    if not urls:
        return {'requested': 0, 'ok': 0}

    def _one(url):
        try:
            body, _cached = fetch_article_cached(url)
            return bool(body and body.strip())
        except Exception:                     # noqa: BLE001  单篇炸了不能拖掉整批
            return False

    done = ok = 0
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for hit in pool.map(_one, urls):
            done += 1
            ok += 1 if hit else 0
            if log and (done % 50 == 0 or done == len(urls)):
                log('  预热 %d/%d（成功 %d）' % (done, len(urls), ok))
    return {'requested': len(urls), 'ok': ok}


def _download_one_image(url: str, local_path: Path) -> bool:
    """取一张图。成功返回 True；失败**不抛**——一张图挂了不该影响这一篇的其它图。"""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://mp.weixin.qq.com/',
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read(10 * 1024 * 1024)  # max 10MB
        if data:
            local_path.write_bytes(data)
            return True
    except Exception:
        pass
    return False


def download_images_to_local(markdown: str, images_dir: Path,
                             workers: int = IMAGE_WORKERS) -> tuple[str, dict]:
    """Download mmbiz images to local directory. Returns (markdown, url_mapping).

    **并发取图**（`IMAGE_WORKERS`）：串行只是实现选择，实测 190 篇约 18 分钟。

    只登记**真的在本地存在**的文件。原先每张图在下载前就写进映射，于是下载失败的
    也留一条——而映射会被注入阅读器（`window._IMG_MAP`，见 `generate_html.py`），
    让页面去找一个不存在的文件。保留远程链接才是对的：阅读器按远程取照样能看。
    """
    url_mapping = {}  # remote_url -> local_rel_path

    if not markdown:
        return markdown, url_mapping

    images_dir.mkdir(parents=True, exist_ok=True)

    img_matches = re.compile(r'!\[(.*?)\]\((https?://[^)]+)\)').findall(markdown)
    if not img_matches:
        return markdown, url_mapping

    targets, seen = [], set()
    for _alt, url in img_matches:
        # Process any qpic.cn (mmbiz/mmecoa) images
        if '.qpic.cn' not in url or url in seen:
            continue
        seen.add(url)                     # 同一张图在页面上可能出现多次，只取一次
        ext = '.jpg'  # default
        if '.png' in url or 'wx_fmt=png' in url:
            ext = '.png'
        elif '.gif' in url or 'wx_fmt=gif' in url:
            ext = '.gif'
        elif '.webp' in url or 'wx_fmt=webp' in url:
            ext = '.webp'
        local_filename = f'{hashlib.md5(url.encode()).hexdigest()[:12]}{ext}'
        targets.append((url, images_dir / local_filename, f'images/{local_filename}'))

    downloaded_count = 0
    todo = []
    for url, local_path, rel_path in targets:
        # Skip if already downloaded——重跑同一天时这一步就把它们全跳过了
        if local_path.exists() and local_path.stat().st_size > 0:
            url_mapping[url] = rel_path
            downloaded_count += 1
        else:
            todo.append((url, local_path, rel_path))

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_download_one_image, url, path): (url, rel_path)
                       for url, path, rel_path in todo}
            for future in as_completed(futures):
                url, rel_path = futures[future]
                if future.result():
                    url_mapping[url] = rel_path
                    downloaded_count += 1

    if downloaded_count > 0:
        print(f'  图片下载: {downloaded_count}张')

    return markdown, url_mapping


def extract_article_info(content_bytes: bytes) -> dict:
    """Extract title, digest, URL, full text from protobuf message_content."""
    import zstandard as zstd
    if isinstance(content_bytes, str):
        content_bytes = content_bytes.encode('utf-8', errors='ignore')
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


def parse_source_filters(values) -> list[str]:
    """Normalize repeated or comma-separated official-account filters."""
    filters = []
    seen = set()
    for value in values or []:
        for item in re.split(r'[,;\n]+', str(value or '')):
            normalized = item.strip().strip('"\'')
            if normalized and normalized.casefold() not in seen:
                seen.add(normalized.casefold())
                filters.append(normalized)
    return filters


# ====== Main ======

def main():
    # Fix Windows console encoding
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='公众号日报')
    parser.add_argument('--date', help='日期 YYYY-MM-DD, 默认今天')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不抓取')
    parser.add_argument('--limit', type=int, default=0, help='最多处理篇数 (0=不限制)')
    parser.add_argument('--browser-history', action='store_true',
                        help='把客户端内嵌浏览器里当天打开过的公众号文章也纳入（4.x 订阅号是网页渲染的，'
                             '这些文章不写消息库）')
    parser.add_argument('--rss-feed', action='append', default=None, metavar='URL',
                        help='外部 RSS/Atom 订阅源（可重复）。微信 4.2.0.11 起绝大多数公众号推送不再落库，'
                             '这一项用标准订阅源补回缺口；只取 mp.weixin.qq.com 且按当天过滤')
    parser.add_argument('--rss-feeds-file', default='output/rss-feeds.json', metavar='PATH',
                        help='订阅源配置文件（默认 output/rss-feeds.json，不存在就忽略）。'
                             '把常订阅的源写在这里，定时任务就不用带一长串 --rss-feed')
    parser.add_argument('--rss-opml', default=None, metavar='PATH',
                        help='OPML 订阅文件：从任意 RSS 阅读器导出的订阅列表，一次导入几十上百个源，'
                             '不用手抄地址')
    parser.add_argument('--articles-json', action='append', default=None, metavar='PATH',
                        help='外部文章列表 JSON（可重复）：一个数组，每项含 title/url/account/'
                             'date/time/timestamp。用于接入任何自行取得的来源——本工具既不关心'
                             '也不实现那个来源怎么拿数据')
    parser.add_argument('--api-key', help='AI API key (或设环境变量 DEEPSEEK_API_KEY)')
    parser.add_argument('--engine', default='deepseek', help='AI 引擎: local/deepseek/claude/ollama')
    parser.add_argument('--no-ai', action='store_true', help='关闭摘要、分类和日报简报的 AI 调用')
    parser.add_argument('--no-fetch-cache', action='store_true',
                        help='忽略正文抓取缓存，强制重新抓取（默认命中缓存，用于续跑与重跑）')
    parser.add_argument('--prefetch-bodies', nargs='?', type=int, const=PREFETCH_WORKERS, default=0,
                        metavar='N',
                        help='Phase 1 之前先**并发**把这一天的正文抓进缓存（N=并发数，默认 %d）——'
                             'Phase 1 于是全部命中缓存、不再逐篇 sleep（450 篇约 75 分钟 → 秒级）。'
                             '对上游的请求次数不变，只是压进更短的时间窗，所以默认关（见 prefetch_bodies）'
                             % PREFETCH_WORKERS)
    parser.add_argument('--no-summary', action='store_true',
                        help='只要判断、不要生成：完全跳过 LLM 调用（摘要/标签/概念/简报），'
                             '主题与相关度仍由 Jev 判断；因此**不需要 DeepSeek key**')
    parser.add_argument('--classifier', choices=['auto', 'llm', 'jev'], default='auto',
                        help='主题/相关度由谁判断：auto=配了 TypeSafe key 就用 Jev（默认），'
                             'llm=沿用 LLM 解析路径，jev=强制 Jev')
    parser.add_argument('--source', action='append', default=[], metavar='NAME',
                        help='仅处理指定公众号，可重复或用逗号分隔；默认读取配置 dailySources')
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

    # Load config & DB keys
    config = load_config()
    if str(config.get('dailyAiEnabled', 'true')).strip().lower() == 'false':
        args.no_ai = True
    keys = get_db_keys(config)
    source_filters = parse_source_filters(args.source or [config.get('dailySources', '')])
    source_categories = load_source_categories(config)

    # API key — only required for cloud engines
    engine = args.engine or 'deepseek'
    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '') or get_api_key(config)
    # `--no-summary` 下不生成任何文字，也就不需要 LLM key——这条校验是"要调 LLM"的前提，
    # 不是"要跑 Phase 2"的前提。不改这一句，"没有 DeepSeek 也能只用 Jev 分类"会被它挡死。
    if not args.dry_run and not args.no_summary and engine in ('deepseek', 'claude') and not api_key:
        print(f'[ERROR] --engine {engine} 需要 API key。请通过 --api-key、环境变量或配置文件提供')
        sys.exit(1)
    # Auto-detect local engine if no api_key and engine is deepseek
    if not api_key and engine == 'deepseek':
        engine = 'local'
        print(f'  无 API key，切换到本地引擎')

    print(f'=== 公众号日报 {date_str} ===')
    print(f'Biz DB: {keys["biz_db"]}')
    if source_filters:
        print(f'公众号筛选: {", ".join(source_filters)}')

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
    if source_filters:
        filter_keys = {item.casefold() for item in source_filters}
        biz_users = [
            user for user in biz_users
            if user.casefold() in filter_keys
            or str(name_map.get(user, user)).casefold() in filter_keys
        ]
        print(f'匹配公众号: {len(biz_users)} 个')
        if not biz_users:
            print('[WARN] 没有匹配的公众号；请检查昵称或 gh_ ID')

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
                if not info['title']:
                    continue
                # 过滤支付/服务通知（非真实文章）
                t = info['title']
                if any(t.startswith(p) for p in ('已支付', '已扣费', '支付成功', '扣费预通知', '你已关闭', '下单成功')):
                    continue
                if '自动续费' in t and '微信支付' in name_map.get(user, user):
                    continue
                articles.append({
                        'account': user,
                        'account_name': name_map.get(user, user),
                        'source_category': get_source_category(
                            source_categories, user, name_map.get(user, user)
                        ) if source_filters else '',
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

    # 订阅号是网页渲染的：**你点开过的**文章只留在内嵌浏览器的历史里（见
    # `browser_history_articles` 的说明）。默认关——它补的是"读过但没推送落库"的那部分，
    # 打开它意味着把浏览记录也纳入日报，这该由用户显式决定。
    if getattr(args, 'browser_history', False):
        try:
            extra = browser_history_articles(date_str, tz)
        except Exception:
            extra = []
        if extra:
            seen = {_article_key(item) for item in articles}
            added = [item for item in extra if _article_key(item) not in seen]
            articles.extend(added)
            print(f'  浏览记录补入 {len(added)} 篇（客户端里打开过、但推送没落库的）')

    # 外部 RSS 订阅源：微信 4.2.0.11 起，绝大多数公众号推送不再写入消息库
    # （实测同一账号 10-26/27 每天 480+ 行，升级后掉到 11 行），界面里的列表改为联网现拉。
    # 这一项让日报能接标准订阅源（RSS/Atom）把缺口补回来，不涉及任何非官方协议。
    rss_feeds = list(getattr(args, 'rss_feed', None) or [])
    try:
        from rss_feed import load_rss_feeds, load_opml_feeds, rss_feed_articles
        feeds_file = getattr(args, 'rss_feeds_file', None)
        if feeds_file and not os.path.isabs(feeds_file):
            # 锚到仓库根而不是进程工作目录：定时任务由 Task Scheduler 拉起，
            # 工作目录可能是 C:\Windows\System32，相对路径会静默读不到配置。
            feeds_file = os.path.join(SCRIPT_DIR, feeds_file)
        for url in load_rss_feeds(feeds_file):
            if url not in rss_feeds:
                rss_feeds.append(url)
        # OPML：用户从任意 RSS 阅读器导出的订阅列表，一次导入几十上百个源。
        opml_file = getattr(args, 'rss_opml', None)
        if opml_file and not os.path.isabs(opml_file):
            opml_file = os.path.join(SCRIPT_DIR, opml_file)
        for url in load_opml_feeds(opml_file):
            if url not in rss_feeds:
                rss_feeds.append(url)
    except Exception:
        pass
    if rss_feeds:
        try:
            extra = rss_feed_articles(date_str, tz, rss_feeds)
        except Exception as exc:
            extra = []
            print(f'  RSS 读取失败（不影响其它数据源）: {type(exc).__name__}')
        if extra:
            seen = {_article_key(item) for item in articles}
            added = [item for item in extra if _article_key(item) not in seen]
            articles.extend(added)
            print(f'  RSS 补入 {len(added)} 篇（来自 {len(rss_feeds)} 个订阅源）')

    # 外部文章列表 JSON：协议中立的接入口。谁用什么办法拿到这批文章与本工具无关，
    # 这里只负责"按日期过滤 + 去重 + 合并进当天"。
    extra_files = getattr(args, 'articles_json', None)
    if extra_files:
        merged_total = 0
        for path in extra_files:
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    payload = json.load(fh)
                items = payload.get('articles', []) if isinstance(payload, dict) else payload
                if not isinstance(items, list):
                    continue
                picked = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    when = str(item.get('date') or '')
                    if when and when != date_str:
                        continue
                    url = str(item.get('url') or '').strip()
                    title = str(item.get('title') or '').strip()
                    if not url and not title:
                        continue
                    ts = item.get('timestamp')
                    if not isinstance(ts, (int, float)) or ts <= 0:
                        ts = 0
                    picked.append({
                        'account': item.get('account') or '(external)',
                        'account_name': item.get('account_name') or item.get('account') or '(external)',
                        'source_category': item.get('source_category') or '',
                        'title': title or url,
                        'url': url,
                        'digest': item.get('digest') or '',
                        'cover': item.get('cover') or '',
                        'local_text': '',
                        'time': item.get('time') or '',
                        'timestamp': int(ts),
                    })
                seen = {_article_key(it) for it in articles}
                added = [it for it in picked if _article_key(it) not in seen]
                articles.extend(added)
                merged_total += len(added)
            except Exception as exc:
                print(f'  外部文章文件读取失败（跳过）: {type(exc).__name__}')
        if merged_total:
            print(f'  外部文章补入 {merged_total} 篇（来自 {len(extra_files)} 个文件）')

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

    # ====== Phase 0: 并发预热正文缓存（可选，`--prefetch-bodies`）======
    if args.prefetch_bodies and not args.no_fetch_cache:
        print(f'=== Phase 0: 并发预热正文缓存（{args.prefetch_bodies} 并发）===\n')
        prefetch = prefetch_bodies(articles, args.prefetch_bodies, log=print)
        print(f'预热完成：{prefetch["ok"]}/{prefetch["requested"]} 篇进了缓存'
              f'（没进的 Phase 1 会再试一次）\n')
    elif args.prefetch_bodies:
        # 说清楚为什么没做：`--no-fetch-cache` 让 Phase 1 绕过缓存，预热等于白抓一遍。
        print('[WARN] 同时给了 --no-fetch-cache 与 --prefetch-bodies：'
              '前者让 Phase 1 绕过缓存，预热没有意义，已跳过\n')

    # ====== Phase 1: Fetch all articles ======
    print(f'=== Phase 1: 抓取 {len(articles)} 篇文章 ===\n')
    cache_hits = 0
    for i, a in enumerate(articles):
        t, n, ti = a['time'], a['account_name'], a['title']
        print(f'[{i+1}/{len(articles)}] [{t}] {n} - {ti[:50]}')

        if a['url']:
            delay = FETCH_DELAY_MIN + random.random() * (FETCH_DELAY_MAX - FETCH_DELAY_MIN)
            md, cached = fetch_article_cached(a['url'], use_cache=not args.no_fetch_cache)
            if md:
                a['fetched_md'] = md
                if cached:
                    cache_hits += 1
                    print(f'  OK ({len(md)}字, 缓存)')
                else:
                    print(f'  OK ({len(md)}字, {delay:.1f}s)')
            else:
                print(f'  FAIL, 回退本地缓存')
            # **命中缓存就不睡**：节流是为了少打扰上游，而缓存命中根本没有请求。
            # 这也正是"重跑快"的来源——续跑那部分几乎是瞬时的，且与节流同向。
            if not cached:
                time.sleep(delay)
        elif a.get('local_text'):
            print(f'  无URL, 使用本地缓存')

    if cache_hits:
        print(f'  抓取完成：{len(articles) - cache_hits} 篇走网络，'
              f'{cache_hits} 篇命中本地缓存（少发 {cache_hits} 次请求）')

    # ====== Phase 2: AI summary + topic classification ======
    # **实际服务我们的判断模型**（如 `jev-1.13.0`），不是请求的别名（`jev-latest`）。
    # 别名会漂：今天跑的和测过的可能不是同一个东西，而结果看起来一切正常。落盘它，
    # 是为了让这件事**可见**。（没走 Jev 时保持 None，如实表示"没用判断模型"。）
    decision_model = None
    plan = classifier_plan(args.no_ai, args.classifier, api_key, engine,
                           needs_llm=not args.no_summary)
    if plan != 'skip':
        stage = '只分类（--no-summary）' if args.no_summary else 'AI 摘要 + 主题分类'
        print(f'\n=== Phase 2: {stage} (engine={engine}) ===\n')
        from _utils import call_ai
        from jev_client import create_client
        # 判断交给 Jev，生成留给 LLM。没配 key 就整条走老路（auto 的语义）。
        jev_client = create_client(config=config) if plan == 'jev' else None
        if jev_client is not None:
            tail = '本次不生成摘要' if args.no_summary else f'生成摘要仍用 {engine}'
            print(f'  分类：Jev（model={jev_client.model}，{tail}）')
        elif args.classifier == 'jev':
            print('  [WARN] --classifier jev 但没找到 TypeSafe key，本次退回 LLM 解析路径')
        # 先把分类并发跑完，再进串行的摘要循环。分类对摘要没有任何依赖，
        # 串行做就是把 N 次网络等待排在 N 次 LLM 调用后面。
        decisions = _classify_articles_parallel(articles, jev_client, TOPICS)
        if jev_client is not None:
            decision_model = jev_client.last_model
        if args.no_summary:
            # **只要判断、不要生成**：不调 LLM，也不留摘要。`prefetched = None` 让下面
            # 那个串行循环整段跳过——它是围绕 LLM 响应写的，没有响应就没有它的事。
            judged = 0
            for i, a in enumerate(articles):
                if _apply_decision(a, decisions.get(i)):
                    judged += 1
                # **明确写空**而不是留着不写：md 那条路的缺省是本地 digest、json 那条路
                # 另有缺省，写空才能让两边都如实表示"没有摘要"，而不是拿平台摘要冒充。
                a['summary'] = ''
            print(f'  判出 {judged}/{len(articles)} 篇；未生成：摘要、标签、概念')
            if jev_client is None:
                print('  [WARN] 没有 TypeSafe key，本次**没有任何判断**——只抓取与归档，'
                      '主题与相关度保持默认值')
            prefetched = None
        else:
            # 先把每篇的 LLM 调用并发跑完，再进下面这个串行循环做解析与落字段。
            # 解析是纯本地操作，并发的价值全在网络等待上；这样循环体本身不用重写，
            # 每条兜底分支的行为也就与串行版本一致。
            prefetched = _summarise_articles_parallel(articles, engine, api_key,
                                                      decisions=decisions)
        # `--no-summary` 时 prefetched 是 None，这个循环整段不跑（循环体是围绕 LLM
        # 响应写的）。用变量而不是把循环体缩进进 if，是为了让 diff 只碰这一行。
        pending = list(enumerate(articles)) if prefetched is not None else []
        for i, a in pending:
            t, n, ti = a['time'], a['account_name'], a['title']
            content = a.get('fetched_md') or a.get('local_text', '')
            if content and len(content.strip()) > 50:
                try:
                    category_hint = a.get('source_category', '')
                    response, call_error = prefetched[i]
                    if call_error is not None:
                        # 调用是并发阶段做的，异常在那里被捕获了。这里重新抛出，
                        # 让下面原有的 `except` 接管——兜底行为与串行版本一致。
                        raise call_error
                    if category_hint:
                        a['topic'] = category_hint
                        a['tags'] = [category_hint]
                        a['summary'] = response.strip()[:1000]
                        # 主题是人工配的强约束，不动；但相关度以前是硬编码的「中」，
                        # 而那正是"全库 relevance 都是中"的成因之一，交给 Jev。
                        _apply_decision(a, decisions.get(i), set_topic=False)
                        a['concepts'] = []
                        print(f'[{i+1}/{len(articles)}] [{t}] {n} - [{category_hint}] 固定来源类别，仅生成摘要')
                        continue
                    # （提示词与调用已移到 `_summarise_articles_parallel`，响应在上面取。）
                    # 判断来自开头那轮并发分类；没拿到才回落到下面的解析。
                    decision = decisions.get(i)

                    # Parse response: 【主题】xxx 【相关度】xxx 【标签】xxx 【摘要】xxx 【概念】xxx
                    topic_match = re.search(r'【主题】\s*(.+)', response)
                    relevance_match = re.search(r'【相关度】\s*(.+)', response)
                    tags_match = re.search(r'【标签】\s*(.+)', response)
                    # Capture summary - stop at next top-level 【tag (主题/相关度/标签/概念) or end
                    summary_match = re.search(r'【摘要】\s*(.+?)(?=\n【(?:主题|相关度|标签|概念)】|$)', response, re.DOTALL)
                    concepts_match = re.search(r'【概念】\s*(.+)', response, re.DOTALL)

                    if decision:
                        _apply_decision(a, decision)
                    elif topic_match:
                        raw_topic = topic_match.group(1).strip()
                        if raw_topic in TOPICS:
                            a['topic'] = raw_topic
                        else:
                            # Fuzzy match: try each known topic
                            matched = False
                            for t in TOPICS:
                                if t in raw_topic:
                                    a['topic'] = t
                                    matched = True
                                    break
                            if not matched:
                                a['topic'] = _guess_topic(a)
                    else:
                        a['topic'] = _guess_topic(a)

                    # Parse relevance: 高/中/低
                    if decision:
                        pass  # 已由 _apply_decision 写入
                    elif relevance_match:
                        raw_rel = relevance_match.group(1).strip()
                        if raw_rel in RELEVANCE_NAMES:
                            a['relevance'] = raw_rel
                        elif '高' in raw_rel:
                            a['relevance'] = '高'
                        elif '中' in raw_rel:
                            a['relevance'] = '中'
                        elif '低' in raw_rel:
                            a['relevance'] = '低'
                        else:
                            a['relevance'] = DEFAULT_RELEVANCE
                    else:
                        a['relevance'] = DEFAULT_RELEVANCE

                    # Parse tags: comma-separated, clean up
                    if category_hint:
                        a['tags'] = [category_hint]
                    elif tags_match:
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
                except Exception as e:
                    a['summary'] = a.get('digest', '') or content[:300]
                    a['topic'] = a.get('source_category') or DEFAULT_TOPIC
                    a['tags'] = [a['topic']]
                    a['concepts'] = []
                    # 这三条兜底路径以前完全不设 relevance，靠落盘时的默认值兜成「中」。
                    # 显式写出来，并保留 Jev 已经给出的那一份（setdefault 而不是赋值）。
                    a.setdefault('relevance', DEFAULT_RELEVANCE)
                    print(f'[{i+1}/{len(articles)}] [{t}] {n} - ERR: {e}')
            elif content:
                a['summary'] = content[:400]
                a['topic'] = a.get('source_category') or DEFAULT_TOPIC
                a.setdefault('relevance', DEFAULT_RELEVANCE)
            else:
                a['summary'] = a.get('digest', '(无内容)')
                a['topic'] = a.get('source_category') or DEFAULT_TOPIC
                a.setdefault('relevance', DEFAULT_RELEVANCE)

        # Print topic distribution
        from collections import Counter
        topic_counts = Counter(a.get('topic') or DEFAULT_TOPIC for a in articles)
        print(f'\n  主题分布: {dict(topic_counts)}')

    # ====== Phase 3: Write files (by topic folders) ======
    print(f'\n=== Phase 3: 写入文件 (按主题) ===\n')

    # Create topic subdirs
    for topic in TOPICS:
        (out_dir / topic).mkdir(parents=True, exist_ok=True)

    # --- 主题在这里定稿：**分组、md frontmatter、json 三条路都只读这一次的结果** ---
    topic_groups, fallback_count = _group_by_topic(articles)
    if fallback_count:
        # 说出来，别让它看起来像一次正常分类：`--no-ai`、没配 key、或者来源配置里
        # 写了个不在分类法里的词，都会整批落到兜底类。
        print(f'  [WARN] {fallback_count}/{len(articles)} 篇没有可用主题，'
              f'已归入兜底类「{DEFAULT_TOPIC}」（不是判断结果）')

    # --- 写入结构化 JSON：一次提取，多次复用（供 AI 报告等下游使用） ---
    # **合并写**：同一天跑两次（午报 + 日终）时，这一批只有新到达的文章，
    # 覆盖写会把上一批从文件里抹掉（详解见 `merge_articles_json` 的 docstring）。
    serializable = [_serializable_article(a, date_str) for a in articles]

    json_path = out_dir / '.articles.json'
    try:
        merged_articles = merge_articles_json(
            json_path, date_str, serializable, decision_model,
            session_activity(config, keys['biz_db']))
        print(f'  ✓ 结构化数据: {json_path} ({len(merged_articles)} 篇，本次新增 {len(serializable)} 篇)')
    except Exception as e:
        print(f'  [WARN] JSON 写入失败: {e}')

    # Write new article files
    for topic in TOPICS:
        group = topic_groups[topic]
        if not group:
            continue
        for a in group:
            safe_name = sanitize_filename(f'{a["account_name"]}-{a["title"]}')
            file_name = f'{safe_name}.md'
            file_path = out_dir / topic / file_name

            markdown = a.get('fetched_md') or a.get('local_text', '')
            # Skip articles with empty body content
            if markdown:
                body_text = markdown.strip()
                body_text = ''.join(body_text.split('\n'))  # remove newlines
                body_text = re.sub(r'!\[.*?\]\(.*?\)', '', body_text)  # remove images
                body_text = re.sub(r'\[.*?\]\(.*?\)', '', body_text)    # remove links
                body_text = body_text.strip()
                if len(body_text) < 100:
                    print(f'  [SKIP] 内容过短 ({len(body_text)}字): {a["title"]}')
                    continue
            summary = a.get('summary', a.get('digest', ''))
            tags = _tags_for_write(a, topic)
            concepts = a.get('concepts', [])

            fm = {
                'title': f'"{a["title"]}"',
                'source': f'"{a["account_name"]}"',
                'date': date_str,
                'topic': topic,
                'relevance': a.get('relevance', DEFAULT_RELEVANCE),
                'tags': tags,
                'created': date_str,
            }
            # 只增字段：下游只做 fm.get('relevance') 的字面量比较，不认识的键会被忽略。
            # 不留这两个值，Jev 带来的概率信息就在落盘这一步丢掉了。
            if a.get('relevanceScore') is not None:
                fm['relevanceScore'] = round(float(a['relevanceScore']), 3)
            if a.get('topicConfidence') is not None:
                fm['topicConfidence'] = round(float(a['topicConfidence']), 3)
            if a.get('includeScore') is not None:
                fm['includeScore'] = round(float(a['includeScore']), 3)
            if a['url']:
                fm['url'] = f'"{a["url"]}"'

            body_parts = [f'# {a["title"]}\n']
            body_parts.append(f'> 来源：{a["account_name"]}  \n')
            body_parts.append(f'> 时间：{date_str} {a["time"]}  \n')
            if a['url']:
                body_parts.append(f'> 原文：[阅读原文]({a["url"]})\n')
            body_parts.append('\n---\n\n')
            body_parts.append(summary_section(summary))

            if concepts:
                body_parts.append(format_wikilinks(concepts))
                body_parts.append('\n')

            if markdown:
                # Download images to local directory
                images_dir = out_dir / 'images'
                markdown, url_mapping = download_images_to_local(markdown, images_dir)

                # Save image mapping for HTML fallback
                if url_mapping:
                    map_file = out_dir / '.image_map.json'
                    existing_map = {}
                    if map_file.exists():
                        try:
                            existing_map = json.loads(map_file.read_text(encoding='utf-8'))
                        except Exception as exc:
                            # 读不出来的旧映射表会让**之前几天的图片**从 HTML 里消失，
                            # 而这一篇看起来还是成功的。宁可丢这一批映射，也要说清是什么原因。
                            print('  [WARN] 旧的 .image_map.json 读不了（%s: %s）—— 这次从空表开始，'
                                  '旧的图片映射会丢（HTML 里那些图可能显示不出来）'
                                  % (type(exc).__name__, exc), file=sys.stderr)
                            existing_map = {}
                    existing_map.update(url_mapping)
                    map_file.write_text(json.dumps(existing_map, ensure_ascii=False, indent=2), encoding='utf-8')

                body_parts.append('---\n\n')
                body_parts.append('## 正文\n\n')
                body_parts.append(markdown)

            write_with_frontmatter(str(file_path), fm, ''.join(body_parts))

    # Rebuild README from ALL existing md files (not just this batch)
    index_path = out_dir / 'README.md'
    all_articles = []
    for topic in TOPICS:
        topic_dir = out_dir / topic
        if not topic_dir.is_dir():
            continue
        for md_file in sorted(topic_dir.glob('*.md')):
            try:
                content = md_file.read_text(encoding='utf-8')
            except:
                continue
            # Extract title and time from frontmatter
            title = md_file.stem
            source = ''
            article_time = ''
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('title:'):
                    title = line.split(':', 1)[1].strip().strip('"').strip("'")
                elif line.startswith('source:'):
                    source = line.split(':', 1)[1].strip().strip('"').strip("'")
                elif line.startswith('date:') and source:
                    # Find time after frontmatter
                    break
            # Extract time from markdown body
            m = re.search(r'时间：\d{4}-\d{2}-\d{2} (\d{2}:\d{2})', content)
            if m:
                article_time = m.group(1)
            all_articles.append({
                'title': title,
                'source': source,
                'time': article_time,
                'topic': topic,
                'file': md_file.name,
            })

    # Generate AI briefing from all article files on disk
    briefing = ''
    if all_articles:
        topic_counts = {}
        for a in all_articles:
            t = a['topic']
            topic_counts[t] = topic_counts.get(t, 0) + 1
        topic_summary = '、'.join(f'{t}{c}篇' for t, c in sorted(topic_counts.items()))

        # Read summaries from existing md files
        highlights = []
        for topic in TOPICS:
            topic_dir = out_dir / topic
            if not topic_dir.is_dir():
                continue
            for md_file in sorted(topic_dir.glob('*.md'), key=lambda f: f.stat().st_mtime, reverse=True)[:15]:
                try:
                    content = md_file.read_text(encoding='utf-8')
                    # Extract title
                    title = md_file.stem
                    summary = ''
                    in_summary = False
                    for line in content.split('\n'):
                        if line.startswith('title:'):
                            title = line.split(':', 1)[1].strip().strip('"').strip("'")
                        if '## AI 摘要' in line or '## 深度解析' in line:
                            in_summary = True
                            continue
                        if in_summary and line.startswith('##'):
                            break
                        if in_summary and line.strip():
                            summary += line.strip()[:100]
                            if len(summary) > 80:
                                break
                    if summary:
                        highlights.append(f"[{topic}] {title} | {summary}")
                except Exception:
                    pass
            if len(highlights) >= 25:
                break

        highlight_text = '\n'.join(highlights[:25])
        # `--no-summary` 同样跳过简报的 LLM 调用（它是"生成"）：落到下面那条由标题拼的
        # 确定性兜底上。兜底路径本来就在（AI 失败时走它），所以这里不新增一种产物形状。
        if highlight_text and not args.no_ai and not args.no_summary:
            try:
                briefing_prompt = f"""你是公众号日报助手。基于今天 {len(all_articles)} 篇文章（{topic_summary}），生成一段200字以内的今日简报。

要求：
1. 按主题分组，每个主题1-2句话概括重点
2. 突出3-5篇最值得关注的文章及其核心观点
3. 语气简洁专业

文章概览：
{highlight_text}

请直接输出简报内容（不要标题）。"""
                # Use call_ai to support local/deepseek/claude engines
                from _utils import call_ai
                engine_type = 'deepseek' if api_key else 'local'
                briefing = call_ai(briefing_prompt, engine_type, api_key, max_tokens=500).strip()
                if briefing:
                    print(f'\n  ✓ 简报生成完成')
                else:
                    print(f'\n  [WARN] 简报 API 返回空，使用摘要拼接')
                    briefing = ''
            except Exception as e:
                print(f'\n  [WARN] 简报生成失败: {e}，使用摘要拼接')
                briefing = ''

        # Fallback: build briefing from highlights if AI failed
        if not briefing and highlights:
            topic_groups = {}
            for h in highlights[:15]:
                parts = h.split(' | ', 1)
                title_part = parts[0].replace('[', '').replace(']', ' - ')
                summary_part = parts[1] if len(parts) > 1 else ''
                topic = h.split(']')[0].replace('[', '')
                if topic not in topic_groups:
                    topic_groups[topic] = []
                if len(topic_groups[topic]) < 3:
                    topic_groups[topic].append(f"{title_part.split(' - ', 1)[-1]}：{summary_part[:60]}")
            parts = []
            for topic in TOPICS:
                if topic in topic_groups:
                    items = topic_groups[topic]
                    parts.append(f"{topic}：{'；'.join(items)}")
            briefing = '今日共收录' + str(len(all_articles)) + '篇文章。' + '。'.join(parts) if parts else f'今日共收录{len(all_articles)}篇文章，涵盖{topic_summary}。'

    index_lines = [
        f'# 公众号日报 — {date_str}',
        '',
    ]
    if briefing:
        index_lines.append(f'> 📋 **今日简报**\n> \n> {briefing}\n')
    index_lines.append(f'共 {len(all_articles)} 篇推送，按主题分类')
    index_lines.append('')
    for topic in TOPICS:
        group = [a for a in all_articles if a['topic'] == topic]
        if not group:
            continue
        index_lines.append(f'## {topic} ({len(group)}篇)')
        index_lines.append('')
        index_lines.append('| # | 时间 | 公众号 | 标题 |')
        index_lines.append('|---|------|--------|------|')
        for j, a in enumerate(group):
            index_lines.append(
                f'| {j+1} | {a["time"]} | {a["source"]} | [{a["title"]}](./{topic}/{a["file"]}) |'
            )
        index_lines.append('')

    with open(index_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_lines))
        f.write('\n---\n\n*由 weflow-cli 公众号日报自动生成*\n')

    print(f'\n✓ 完成！输出到 {out_dir}')
    print(f'  总索引: {index_path}')
    print(f'  文章数: {len(all_articles)}')
    for t in TOPICS:
        count = len([a for a in all_articles if a['topic'] == t])
        if count:
            print(f'  {t}/: {count} 篇')

    # Save run state for incremental dedup (atomically)
    # Only mark articles that were actually written to disk
    written_urls = set()
    for topic in TOPICS:
        topic_dir = out_dir / topic
        if not topic_dir.is_dir():
            continue
        for md_file in topic_dir.glob('*.md'):
            try:
                content = md_file.read_text(encoding='utf-8')
                for line in content.split('\n'):
                    if line.strip().startswith('url:'):
                        url = line.split(':', 1)[1].strip().strip('"').strip("'")
                        if url:
                            written_urls.add(url)
                        break
            except Exception:
                pass
    for a in topic_groups.values():
        if isinstance(a, list):
            for art in a:
                if isinstance(art, dict) and art.get('url') and art['url'] in written_urls:
                    fp = hashlib.sha256(art['url'].encode()).hexdigest()[:16]
                    processed[fp] = art.get('title', '')[:50]
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)

    # === 来源级先验：把今天**真正归档了的**判断累加进本地表 ===
    #
    # 记"盘上真有的"（按 written_urls 对回来）而不是"抓过的"：这样这张表和 output/
    # 里的语料一一对应，哪天想重算也重算得出来。
    #
    # 它**只记账，不跳过任何东西**。跳过是不可逆的，而这张表还在长；等某个来源
    # 攒够了样本（见 _utils.SOURCE_PRIOR_MIN_SAMPLES），再决定要不要拿它做拉取前的筛。
    pairs = [(a.get('account_name', ''), a.get('topic', '')) for a in articles
             if a.get('url') and a['url'] in written_urls]
    if pairs:
        summary = record_source_topics(pairs)
        if summary['error']:
            # 说清楚"没写进去"：静默失败会让人以为表在长，实际一直没动。
            print(f'  [WARN] 来源先验没写进去（{summary["error"]}）—— 不影响今天的日报')
        else:
            print(f'  来源先验: 今天记了 {summary["added"]} 篇，表里共 {summary["sources"]} 个来源'
                  f'（{source_topics_path()}）')
        exclude = excluded_topics(config)
        rows = source_prior_candidates(load_source_topics(), exclude)
        if rows:
            print('  来源先验: 这些来源已经稳到能判，且正落在你配的排除主题里'
                  '（只报数，不跳过）:')
            for source, topic, ratio, total in rows[:5]:
                print(f'    {source} → {topic}（{total} 篇里 {round(ratio * total)} 篇'
                      f' = {ratio:.0%}）')


if __name__ == '__main__':
    main()
