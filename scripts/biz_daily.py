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
                    TOPICS, TOPIC_CRITERIA)

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
CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.weflow-cli', 'config.json')
DB_PATH = None  # auto-detect from config
OUTPUT_ROOT = os.path.join(SCRIPT_DIR, 'output', 'biz-daily')
MAX_ARTICLES = 50  # 最多抓取篇数
FETCH_TIMEOUT = 15
DEEPSEEK_TIMEOUT = 60
FETCH_DELAY_MIN = 8   # 最小抓取间隔 (秒)
FETCH_DELAY_MAX = 12  # 最大抓取间隔 (秒)

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
        'topic': article.get('topic', ''),
        'relevance': article.get('relevance', '中'),
        'tags': article.get('tags', []),
        'summary': article.get('summary', article.get('digest', '')),
        'url': article.get('url', ''),
    }
    for key in ('relevanceScore', 'topicConfidence', 'includeScore'):
        if article.get(key) is not None:
            entry[key] = round(float(article[key]), 3)
    return entry


def _classify_with_jev(client, title, body, topics):
    """让 Jev 判断这一篇；**问不出来就返回 None**，由调用方逐篇退回老路。

    这里刻意吞掉异常（包括 `JevError` 之外的意外），因为"分类这一篇失败"
    不该让整天的日报挂掉。但异常类型会打出来——一个真的写错了的行号不该
    在日志里长得跟"网络抖了一下"一样。
    """
    if client is None:
        return None
    try:
        return client.decide_article(title, body, topics)
    except Exception as error:
        print(f'    [WARN] Jev 分类失败（{type(error).__name__}），本篇退回 LLM 解析：{error}')
        return None


# 分类是纯网络等待（实测 0.97s/篇），而且对摘要没有任何依赖，所以没有理由
# 把它排在那条串行的 LLM 循环里一篇一篇等。并发数克制一些：服务 2026-09-15 才上线，
# 打满了会返回 529（已实测遇到过），并发拉高只会换来一堆重试。
JEV_WORKERS = 6


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
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_classify_with_jev, client, title, body, topics): index
            for index, title, body in todo
        }
        for future in as_completed(futures):
            decisions[futures[future]] = future.result()

    ok = sum(1 for value in decisions.values() if value)
    # 先报个数：并发之后逐篇日志没有意义了，但这三件事必须看得见。
    print(f'  分类完成 {ok}/{len(todo)} 篇，耗时 {time.time() - started:.1f}s'
          f'（{workers} 并发；串行约需 {len(todo) * 0.97:.0f}s）')
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

    # biz_message_0.db (每库独立密钥)
    biz_db = os.path.join(msg_dir, 'biz_message_0.db')
    biz_key_enc = config.get('bizKey', '')
    biz_salt = config.get('bizSalt', '')
    pass_enc = config.get('favPassphrase', '')
    passphrase = decrypt_lock(pass_enc) if pass_enc else ''
    if passphrase and os.path.exists(biz_db):
        with open(biz_db, 'rb') as fh:
            biz_salt = fh.read(16).hex()
        biz_key_enc = ''
    if biz_key_enc and biz_salt:
        biz_key = decrypt_lock(biz_key_enc)
    else:
        # 从全库共用 passphrase 派生 (微信 4.x: PBKDF2-HMAC-SHA512, 256000 轮, 32 字节)
        pass_enc = config.get('favPassphrase', '')
        passphrase = decrypt_lock(pass_enc) if pass_enc else ''
        biz_key = ''
        if passphrase and os.path.exists(biz_db):
            with open(biz_db, 'rb') as fh:
                biz_salt = fh.read(16).hex()
            biz_key = hashlib.pbkdf2_hmac(
                'sha512', bytes.fromhex(passphrase),
                bytes.fromhex(biz_salt), 256000, dklen=32
            ).hex()
        if not biz_key:
            # 只指向 passphrase 一条路: 早先还建议 `config set bizKey`, 但该键不在
            # CLI 的可写白名单里, 照做必然报 INVALID_CONFIG_KEY。
            raise SystemExit(
                '缺少公众号数据库密钥: 请运行 weflow-cli fav set-key --passphrase <64位hex> '
                '配置全库 passphrase (自动派生各库密钥)'
            )

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
            })
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                html = resp.read().decode('utf-8', errors='ignore')

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


def download_images_to_local(markdown: str, images_dir: Path) -> tuple[str, dict]:
    """Download mmbiz images to local directory. Returns (markdown, url_mapping)."""
    url_mapping = {}  # remote_url -> local_rel_path

    if not markdown:
        return markdown, url_mapping

    # Create images directory
    images_dir.mkdir(parents=True, exist_ok=True)

    # Find all image URLs in markdown
    img_pattern = re.compile(r'!\[(.*?)\]\((https?://[^)]+)\)')
    img_matches = img_pattern.findall(markdown)

    if not img_matches:
        return markdown, url_mapping

    downloaded_count = 0
    for alt, url in img_matches:
        # Process any qpic.cn (mmbiz/mmecoa) images
        if '.qpic.cn' not in url:
            continue

        # Generate filename from URL hash
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        ext = '.jpg'  # default
        if '.png' in url or 'wx_fmt=png' in url:
            ext = '.png'
        elif '.gif' in url or 'wx_fmt=gif' in url:
            ext = '.gif'
        elif '.webp' in url or 'wx_fmt=webp' in url:
            ext = '.webp'

        local_filename = f'{url_hash}{ext}'
        local_path = images_dir / local_filename
        rel_path = f'images/{local_filename}'

        # Store mapping
        url_mapping[url] = rel_path

        # Skip if already downloaded
        if local_path.exists() and local_path.stat().st_size > 0:
            downloaded_count += 1
            continue

        # Download image
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://mp.weixin.qq.com/',
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read(10 * 1024 * 1024)  # max 10MB
                if len(data) > 0:
                    local_path.write_bytes(data)
                    downloaded_count += 1
        except Exception as e:
            # Keep original URL if download fails
            pass

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
    parser.add_argument('--api-key', help='AI API key (或设环境变量 DEEPSEEK_API_KEY)')
    parser.add_argument('--engine', default='deepseek', help='AI 引擎: local/deepseek/claude/ollama')
    parser.add_argument('--no-ai', action='store_true', help='关闭摘要、分类和日报简报的 AI 调用')
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
    if not args.dry_run and engine in ('deepseek', 'claude') and not api_key:
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

    # ====== Phase 2: AI summary + topic classification ======
    if not args.no_ai and (api_key or engine != 'deepseek'):
        print(f'\n=== Phase 2: AI 摘要 + 主题分类 (engine={engine}) ===\n')
        from _utils import call_ai
        from jev_client import create_client
        # 判断交给 Jev，生成留给 LLM。没配 key 就整条走老路（auto 的语义）。
        jev_client = create_client(config=config) if args.classifier in ('auto', 'jev') else None
        if jev_client is not None:
            print(f'  分类：Jev（model={jev_client.model}，生成摘要仍用 {engine}）')
        elif args.classifier == 'jev':
            print('  [WARN] --classifier jev 但没找到 TypeSafe key，本次退回 LLM 解析路径')
        # 先把分类并发跑完，再进串行的摘要循环。分类对摘要没有任何依赖，
        # 串行做就是把 N 次网络等待排在 N 次 LLM 调用后面。
        decisions = _classify_articles_parallel(articles, jev_client, TOPICS)
        for i, a in enumerate(articles):
            t, n, ti = a['time'], a['account_name'], a['title']
            content = a.get('fetched_md') or a.get('local_text', '')
            if content and len(content.strip()) > 50:
                try:
                    category_hint = a.get('source_category', '')
                    if category_hint:
                        summary_prompt = f'''请只为下面这篇公众号文章生成一段 50-300 字的中文摘要。
来源类别已经确定为「{category_hint}」，不要重新判断或改写文章分类，不要输出主题、标签、相关度或概念字段。

标题：{a["title"]}
来源：{a["account_name"]}

正文：
{content[:4000]}'''
                        response = call_ai(summary_prompt, engine, api_key, max_tokens=1000)
                        a['topic'] = category_hint
                        a['tags'] = [category_hint]
                        a['summary'] = response.strip()[:1000]
                        # 主题是人工配的强约束，不动；但相关度以前是硬编码的「中」，
                        # 而那正是"全库 relevance 都是中"的成因之一，交给 Jev。
                        _apply_decision(a, decisions.get(i), set_topic=False)
                        a['concepts'] = []
                        print(f'[{i+1}/{len(articles)}] [{t}] {n} - [{category_hint}] 固定来源类别，仅生成摘要')
                        time.sleep(0.3)
                        continue
                    prompt = TOPIC_PROMPT + f'\n\n标题：{a["title"]}\n来源：{a["account_name"]}'
                    prompt += f'\n\n内容：\n{content[:4000]}'
                    response = call_ai(prompt, engine, api_key, max_tokens=2000)

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
                        if raw_rel in ['高', '中', '低']:
                            a['relevance'] = raw_rel
                        elif '高' in raw_rel:
                            a['relevance'] = '高'
                        elif '中' in raw_rel:
                            a['relevance'] = '中'
                        elif '低' in raw_rel:
                            a['relevance'] = '低'
                        else:
                            a['relevance'] = '中'
                    else:
                        a['relevance'] = '中'

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
                    time.sleep(0.3)
                except Exception as e:
                    a['summary'] = a.get('digest', '') or content[:300]
                    a['topic'] = a.get('source_category') or '学术'
                    a['tags'] = [a['topic']]
                    a['concepts'] = []
                    # 这三条兜底路径以前完全不设 relevance，靠落盘时的默认值兜成「中」。
                    # 显式写出来，并保留 Jev 已经给出的那一份（setdefault 而不是赋值）。
                    a.setdefault('relevance', '中')
                    print(f'[{i+1}/{len(articles)}] [{t}] {n} - ERR: {e}')
            elif content:
                a['summary'] = content[:400]
                a['topic'] = a.get('source_category') or '学术'
                a.setdefault('relevance', '中')
            else:
                a['summary'] = a.get('digest', '(无内容)')
                a['topic'] = a.get('source_category') or '学术'
                a.setdefault('relevance', '中')

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

    # --- 写入结构化 JSON：一次提取，多次复用（供 AI 报告等下游使用） ---
    serializable = [_serializable_article(a, date_str) for a in articles]

    json_path = out_dir / '.articles.json'
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({
                'generated_at': datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),
                'date': date_str,
                'articles': serializable,
            }, f, ensure_ascii=False, indent=2)
        print(f'  ✓ 结构化数据: {json_path} ({len(serializable)} 篇)')
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
            tags = a.get('tags', [topic])
            concepts = a.get('concepts', [])

            fm = {
                'title': f'"{a["title"]}"',
                'source': f'"{a["account_name"]}"',
                'date': date_str,
                'topic': topic,
                'relevance': a.get('relevance', '中'),
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
            body_parts.append(f'## AI 摘要\n\n{summary}\n\n')

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
                        except:
                            pass
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
        if highlight_text and not args.no_ai:
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


if __name__ == '__main__':
    main()
