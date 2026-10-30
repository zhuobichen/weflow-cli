"""把当天日报按主题整理成一封邮件（AI / 文学 / 学术），SMTP 发出。

为什么不发 HTML 阅读器：`output/biz-daily/<日期>/index.html` 的图片是**本地相对路径**，
发到邮箱里会全部裂掉；内联 base64 又会让邮件变成好几 MB。所以正文走「文本 + 原文链接」，
要带图的完整版仍在电脑上开阅读器。

"每篇都由 AI 读过"不是这里做的：逐篇摘要（`【核心观点】/【关键细节】`）是 `biz_daily.py`
在抓取阶段用决策模型生成的，本脚本只负责**按主题聚合 + 写每个主题的综述**（每个主题一次调用）。

用法：
    python scripts/daily_mail.py --date 2026-10-18
    python scripts/daily_mail.py --date 2026-10-18 --to someone@example.com --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import pathlib
import re
import smtplib
import sys
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from _utils import get_api_key, load_config  # noqa: E402

ROOT = SCRIPT_DIR.parent
#: **政治不进默认**：2026-10-26 实测，含政治题材的邮件被 QQ SMTP 直接拒收
#: （`550 The mail may contain inappropriate words or content.`），去掉后同一封信
#: 立刻发出。政治类仍然在本地阅读器里（`daily-server`），只是不走邮件。
DEFAULT_TOPICS = ('AI', '学术', '新闻', '文学', '投资')
#: 只有这两类生成 AI 综述 + 聚类。其余题材**照常发出**（标题 + 一句摘要），但不额外
#: 花钱：六个主题全做综述和聚类是 12 次调用，而新闻/文学/投资/政治通常只需扫一眼标题。
DIGEST_TOPICS = ('AI', '学术')
#: 非重点主题最多列几条。六个主题全量渲染是 10 万字符（2026-10-26 实测 356 篇），
#: 多数邮件客户端会截断；只留最前面这些，其余让读的人在本地阅读器里翻。
OTHER_TOPIC_LIMIT = 12
SMTP_CONFIG = pathlib.Path(os.path.expanduser('~/.weflow-issue-watch.json'))


def articles_path(date: str) -> pathlib.Path:
    return ROOT / 'output' / 'biz-daily' / date / '.articles.json'


def load_articles(date: str, topics):
    path = articles_path(date)
    if not path.is_file():
        raise SystemExit(f'没有这一天的数据：{path}')
    data = json.loads(path.read_text(encoding='utf-8'))
    arts = [a for a in (data.get('articles') or []) if a.get('topic') in topics and a.get('title')]
    return data, arts


def group_by_topic(articles, topics):
    groups = {t: [] for t in topics}
    for a in articles:
        groups.setdefault(a.get('topic'), []).append(a)
    for t in groups:
        groups[t].sort(key=lambda a: (a.get('time') or '', a.get('relevanceScore') or 0), reverse=True)
    return groups


def _canonical_article_url(url: str) -> str:
    """Remove tracking query values so URL changes do not resend the same article."""
    try:
        parts = urlsplit(url or '')
    except ValueError:
        return (url or '').strip()
    if not parts.scheme or not parts.netloc:
        return (url or '').strip()
    tracking_keys = {'ascene', 'chksm', 'clicktime', 'enterid', 'from', 'isappinstalled',
                     'scene', 'sessionid', 'subscene'}
    query = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                   if not key.lower().startswith('utm_') and key.lower() not in tracking_keys)
    path = parts.path.rstrip('/')
    normalized_query = urlencode(query)
    if not path and not normalized_query:
        return f'{parts.scheme.lower()}://{parts.netloc.lower()}'
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                       normalized_query, ''))


def article_fingerprint(article: dict) -> str:
    """Stable, content-free key for one official-account article."""
    url = _canonical_article_url(article.get('url', ''))
    identity = url or '\0'.join((article.get('source') or '', article.get('title') or '',
                                  article.get('time') or ''))
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def mail_state_path(date: str) -> pathlib.Path:
    return articles_path(date).with_name('.mail-sent.json')


def load_sent_fingerprints(path: pathlib.Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('mail state too large')
        state = json.loads(path.read_text(encoding='utf-8'))
        if state.get('version') != 1:
            raise ValueError('unsupported mail state version')
        fingerprints = state.get('fingerprints')
        if isinstance(fingerprints, list) and all(
                isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                for value in fingerprints):
            return set(fingerprints)
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        pass
    print('[WARN] 已发送邮件状态无效，按完整日报发送以避免漏报', file=sys.stderr)
    return set()


def save_sent_fingerprints(path: pathlib.Path, fingerprints: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({'version': 1, 'fingerprints': sorted(fingerprints)}, indent=2)
    fd, temp_name = tempfile.mkstemp(prefix='.mail-sent-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def deliver_mail(user, auth, to, subject, text_body, html_body, *,
                 incremental=False, state_path=None, sent=None, articles=()):
    send(user, auth, to, subject, text_body, html_body)
    if incremental:
        sent = set(sent or ())
        sent.update(article_fingerprint(article) for article in articles)
        save_sent_fingerprints(state_path, sent)


def only_unsent_articles(articles: list[dict], sent: set[str]) -> list[dict]:
    return [article for article in articles if article_fingerprint(article) not in sent]


def articles_in_email(groups: dict, clusters: dict | None = None) -> list[dict]:
    """Return only articles actually rendered, including compact-topic limits."""
    clusters = clusters or {}
    shown = []
    for topic, items in groups.items():
        section_clusters = clusters.get(topic) or [(topic, list(items))]
        for _name, cluster_items in section_clusters:
            shown.extend(cluster_items if topic in DIGEST_TOPICS
                         else cluster_items[:OTHER_TOPIC_LIMIT])
    return shown


GITHUB_RE = re.compile(r'https?://github\.com/[\w\-./]+', re.I)
#: 主题内分类的小圆点配色。同一条新闻流里给不同聚类换色，读起来不容易串行。
PALETTE = ('#6366f1', '#8b5cf6', '#0ea5e9', '#f59e0b', '#ec4899', '#14b8a6')
_TAG_RE = re.compile(r'【[^】]{1,12}】')


def clean_summary(summary: str, limit=170) -> str:
    """把 `【核心观点】…【关键细节】1. …；2. …` 压成**一句人话**。

    邮件里那串标签是给机器分段的，对读者只是噪音：谁在手机上读日报，都不会去分辨
    「核心观点」和「关键细节」的区别。所以只取【核心观点】那一段，标签一律去掉；
    没有标签的老格式就整段压平。
    """
    text = summary or ''
    m = re.search(r'【核心观点】(.+?)(?=【|$)', text, re.S)
    if m:
        text = m.group(1)
    text = _TAG_RE.sub(' ', text)
    text = ' '.join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip('，；、,. ') + '…'


def body_index(date: str) -> dict:
    """url -> 那篇文章的 md 全文（用来从正文里找 GitHub 链接）。

    按 frontmatter 的 `url` 建索引，而不是按文件名：文件名是 `sanitize_filename` 拼的，
    标题里有 `/`、`:` 或超长名字时会被改写，按名字匹配会静默丢文章。
    """
    index = {}
    for md in (ROOT / 'output' / 'biz-daily' / date).glob('*/*.md'):
        try:
            text = md.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        m = re.search(r'^url:\s*"?([^"\n]+?)"?\s*$', text, re.M)
        if m:
            index[m.group(1)] = text
    return index


def github_links(text: str, limit=3) -> list:
    if not text:
        return []
    seen, out = set(), []
    for url in GITHUB_RE.findall(text):
        url = url.rstrip('.,;)）')
        key = url.rstrip('/').lower()
        if key in seen or '/features' in key or '/pricing' in key:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def _cluster_once(topic, items, api_key, want, strict):
    """一次聚类调用。`strict=True` 用在"模型偷懒只给一组"的重试上。"""
    # 只喂**编号 + 标题**：把摘要也塞进去会让输入膨胀到六七千字，模型反而更容易
    # 直接吐一组（2026-10-21 那封：91 篇 AI 被分成单独一组「模型与产品」）。
    numbered = '\n'.join(f'{i}. {a.get("title")}' for i, a in enumerate(items, 1))
    demand = (
        '**必须分成至少 5 组**。只给一组视为无效答案——这些文章明显可以按话题再细分。\n'
        if strict else
        f'分成 {want - 1}-{want + 1} 组。\n'
    )
    prompt = (
        f'把下面 {len(items)} 条公众号文章按话题分组。\n'
        + demand +
        '组名用 4-8 个汉字，能概括这一组在讲什么（例如「模型与产品」「科研工具」「治理与风险」）。\n'
        '只输出 JSON 数组，不要解释、不要代码围栏：\n'
        '[{"name":"组名","items":[1,2,3]}, ...]\n'
        '要求：每个序号出现且只出现一次。\n\n'
        + numbered
    )
    try:
        raw = __import__('_utils').call_ai(prompt, 'deepseek', api_key, max_tokens=1500) or ''
    except Exception as exc:
        print(f'[WARN] {topic} 聚类调用失败（{type(exc).__name__}: {exc}）', file=sys.stderr)
        return None

    data = None
    try:
        data = json.loads(raw)
    except Exception:
        import re as _re
        m = _re.search(r'\[[\s\S]*\]', raw)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if not isinstance(data, list) or not data:
        print(f'[WARN] {topic} 聚类返回无法解析', file=sys.stderr)
        return None

    seen, clusters = set(), []
    for group in data:
        if not isinstance(group, dict):
            continue
        name = str(group.get('name') or '').strip()[:12] or '其他'
        picked = []
        for idx in group.get('items') or []:
            try:
                i = int(idx)
            except Exception:
                continue
            if 1 <= i <= len(items) and i not in seen:
                seen.add(i)
                picked.append(items[i - 1])
        if picked:
            clusters.append((name, picked))
    leftover = [a for i, a in enumerate(items, 1) if i not in seen]
    if leftover:
        if clusters and len(leftover) <= max(3, len(items) // 4):
            clusters[-1][1].extend(leftover)
        else:
            clusters.append(('其他', leftover))
    return clusters or None


def cluster_items(topic: str, items, api_key: str, want=5):
    """把同一主题下的文章再分一次类（让几十篇的段落不再是一根长肠子）。

    为什么不用现成的 `tags`：实测 68 篇 AI 文章里有 **382 个不同标签**，频次最高的才 6 次——
    按标签聚类会碎成一地。所以交给模型分一次。

    **退化要重试**：2026-10-21 那天模型把 91 篇 AI 全塞进一组「模型与产品」，
    版式当场退回上线前的样子（一根长肠子），而且完全不报错。现在"只给一组但篇数很多"
    会被当成失败、用更硬的指令再要一次。

    返回 `[(类名, [文章...])]`；任何一步失败都退回"一组=全部"，**绝不因此不发信**。
    """
    if len(items) < 8 or not api_key:
        return [(topic, list(items))]

    result = None
    for strict in (False, True):
        result = _cluster_once(topic, items, api_key, want, strict)
        degenerate = result and len(result) == 1 and len(items) > 20
        if result and not degenerate:
            return result
        if degenerate:
            print(f'[WARN] {topic} 聚类退化成 1 组（{len(items)} 篇），{"再要求一次" if not strict else "仍失败"}',
                  file=sys.stderr)
    return result or [(topic, list(items))]


def topic_digest(topic: str, items, api_key: str) -> str:
    """每个主题一次调用：让它读完全部标题+摘要，写一段综述。失败就返回空串（不影响发信）。"""
    if not api_key:
        return ''
    lines = [f'- {a.get("title")}｜{a.get("source")}｜{clean_summary(a.get("summary"), 90)}' for a in items]
    prompt = (
        f'下面是今天「{topic}」主题下 {len(items)} 篇公众号文章的标题与要点。\n'
        '请用中文写一段 150-250 字的综述：今天这个主题主要在讨论什么、有哪些值得注意的进展或分歧、'
        '其中哪一两篇值得优先精读（给出标题）。不要罗列全部篇目，不要空话，不要用列表。\n\n'
        + '\n'.join(lines)
    )
    try:
        return api_key and __import__('_utils').call_ai(prompt, 'deepseek', api_key, max_tokens=600) or ''
    except Exception as exc:
        print(f'[WARN] {topic} 综述生成失败（{type(exc).__name__}: {exc}），邮件里跳过这一段', file=sys.stderr)
        return ''


def render(date: str, groups, digests, bodies=None, clusters=None) -> tuple[str, str, int]:
    bodies = bodies or {}
    clusters = clusters or {}
    total = sum(len(v) for v in groups.values())
    # 主题配色：AI 靛蓝、学术 青绿。用 table + inline style —— 邮件客户端会剥离
    # `<style>` 与 class，class 选择器在 QQ / Gmail / 企业微信里表现不一致。
    # 六个主题各一套配色。同一封信里出现六种强调色不算花——它们是**分区标记**，
    # 换主题时颜色跟着换，扫读时不用先找回标题才知道换段了。
    accents = {
        'AI': ('#4f46e5', '#eef2ff'),
        '学术': ('#0d9488', '#ecfdf5'),
        '新闻': ('#2563eb', '#eff6ff'),
        '文学': ('#db2777', '#fdf2f8'),
        '投资': ('#ca8a04', '#fefce8'),
        '政治': ('#dc2626', '#fef2f2'),
    }

    text_lines = [f'公众号日报 {date}', f'共 {total} 篇', '']
    blocks: list[str] = []

    for topic, items in groups.items():
        if not items:
            continue
        accent, tint = accents.get(topic, ('#334155', '#f1f5f9'))
        text_lines.append(f'=== {topic}（{len(items)} 篇）===')
        digest = (digests or {}).get(topic) or ''
        if digest:
            text_lines.append(digest)
        text_lines.append('')

        # 主题分区：一条通栏色条 + 主题名，比一个小圆角标签更能"切开"版面。
        # 档位名取自 `_utils.RELEVANCE_NAMES`：写死 `('高','中','低')` 会与判据层漂移，
        # 而 `topic_taxonomy_test` 就是专门拦这件事的（它当场红过一次）。
        from _utils import RELEVANCE_NAMES as _REL
        hi, mid, lo = (sum(1 for a in items if a.get('relevance') == k) for k in (_REL[2], _REL[1], _REL[0]))
        def pct(n):
            return max(1, round(n * 100 / max(1, len(items))))
        blocks.append(
            '<tr><td style="padding:30px 28px 0;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td width="4" style="background:{accent};border-radius:2px;"></td>'
            '<td style="padding-left:10px;">'
            f'<span style="font-size:18px;font-weight:700;color:#111827;">{html.escape(topic)}</span>'
            f'<span style="font-size:12px;color:#9aa3af;margin-left:8px;">{len(items)} 篇</span>'
            '</td></tr></table>'
            # 相关度比例条：不用看数字就知道今天"值得读的占多少"
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px;">'
            '<tr>'
            f'<td width="{pct(hi)}%" height="7" style="background:{accent};border-radius:4px 0 0 4px;"></td>'
            f'<td width="{pct(mid)}%" height="7" style="background:{tint};"></td>'
            f'<td width="{pct(lo)}%" height="7" style="background:#eef1f4;border-radius:0 4px 4px 0;"></td>'
            '</tr></table>'
            f'<div style="font-size:11.5px;color:#9aa3af;margin-top:6px;">'
            f'<span style="color:{accent};font-weight:600;">高 {hi}</span> · 中 {mid} · 低 {lo}</div>'
            '</td></tr>'
        )
        if digest:
            blocks.append(
                '<tr><td style="padding:14px 28px 0;">'
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'style="background:#f8fafc;border-left:3px solid {accent};border-radius:8px;">'
                '<tr><td style="padding:14px 16px;font-size:14px;line-height:1.8;color:#3f4652;">'
                f'{html.escape(digest)}</td></tr></table></td></tr>'
            )

        # 主题内再分一次类：68 篇挤成一排没人看，分成 4-6 组后每一段都有名字。
        section_clusters = (clusters or {}).get(topic) or [(topic, list(items))]
        for ci, (cname, citems) in enumerate(section_clusters):
            dot = PALETTE[ci % len(PALETTE)]
            if len(section_clusters) > 1:
                blocks.append(
                    '<tr><td style="padding:22px 28px 0;">'
                    f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
                    f'background:{dot};vertical-align:middle;"></span>'
                    f'<span style="font-size:13.5px;font-weight:700;color:#374151;margin-left:7px;">'
                    f'{html.escape(cname)}</span>'
                    f'<span style="font-size:11.5px;color:#b3bac4;margin-left:6px;">{len(citems)}</span>'
                    '</td></tr>'
                )
                text_lines.append(f'-- {cname}（{len(citems)}）--')

            # **非重点主题走紧凑模式**：一行标题、不带摘要，且只列前 N 条。
            # 六个主题全量渲染是 10 万字符（2026-10-26 实测 356 篇），多数邮件客户端
            # 会截断，人也不会读完——而新闻/文学/投资/政治本来就是"扫一眼标题"的量。
            compact = topic not in DIGEST_TOPICS
            shown = citems[:OTHER_TOPIC_LIMIT] if compact else citems
            for a in shown:
                i = items.index(a) + 1
                title = a.get('title') or ''
                source = a.get('source') or ''
                when = (a.get('time') or '')[:5]
                rel = a.get('relevance') or ''
                url = a.get('url') or ''
                repos = github_links(bodies.get(url, ''))
                meta = ' · '.join(x for x in (source, when, rel) if x)
                num = f'{i:02d}'
                title_html = (f'<a href="{html.escape(url)}" style="color:#111827;text-decoration:none;">'
                              f'{html.escape(title)}</a>' if url else html.escape(title))

                if compact:
                    # 紧凑模式下不按相关度分档：它们整体就是"浏览用"的流水。
                    summary = ''
                    text_lines.append(f'{num}. {title} — {meta}')
                    blocks.append(
                        '<tr><td style="padding:6px 28px 0;">'
                        f'<span style="font-size:11px;color:#c0c7d0;">{num}</span> '
                        f'<span style="font-size:13px;color:#374151;">{title_html}</span>'
                        f'<span style="font-size:11px;color:#b3bac4;"> · {html.escape(source)}</span>'
                        '</td></tr>'
                    )
                    continue
                # **颜色必须自己说明自己。** 上一版把序号徽章染成主题色来表示"高相关"，
                # 但没有任何图例——用户看到截图的第一反应是"这边为什么有的是绿的"（2026-10-20）。
                # 现在徽章统一成灰色，相关度改成 meta 行里一个**写着"高"的小色块**：
                # 颜色解释自己，不需要图例。
                if rel == _REL[2]:
                    summary = clean_summary(a.get('summary'), 190)
                    text_lines.append(f'{num}. ★ {title} — {meta}')
                elif rel == _REL[1]:
                    summary = clean_summary(a.get('summary'), 120)
                    text_lines.append(f'{num}. {title} — {meta}')
                else:
                    summary = ''
                    text_lines.append(f'{num}. {title} — {meta}')
                if rel == _REL[2]:
                    rel_html = (f'<span style="display:inline-block;background:{accent};color:#ffffff;'
                                f'font-size:10.5px;font-weight:600;padding:1px 6px;border-radius:4px;">'
                                f'{html.escape(rel)}</span>')
                else:
                    rel_html = f'<span style="color:#b3bac4;">{html.escape(rel)}</span>' if rel else ''
                meta_html = ' · '.join(x for x in (
                    html.escape(source), html.escape(when), rel_html) if x)
                if summary:
                    text_lines.append(f'    {summary}')
                for repo in repos:
                    text_lines.append(f'    GitHub: {repo}')
                if url:
                    text_lines.append(f'    {url}')
                blocks.append(
                    '<tr><td style="padding:10px 28px 0;">'
                    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                    'style="background:#ffffff;border:1px solid #eef0f3;border-radius:12px;">'
                    '<tr><td style="padding:14px 16px;">'
                    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                    f'<td width="28" valign="top"><span style="display:inline-block;width:24px;height:24px;'
                    'line-height:24px;text-align:center;border-radius:7px;background:#f1f5f9;'
                    f'color:#94a3b8;font-size:11.5px;font-weight:700;">{num}</span></td>'
                    '<td valign="top">'
                    f'<div style="font-size:14.5px;font-weight:600;color:#111827;line-height:1.55;">{title_html}</div>'
                    f'<div style="font-size:11.5px;color:#9aa3af;margin-top:4px;">{meta_html}</div>'
                    + (f'<div style="font-size:13.5px;line-height:1.75;color:#4b5563;margin-top:8px;">'
                       f'{html.escape(summary)}</div>' if summary else '')
                    + ''.join(
                        f'<div style="margin-top:8px;font-size:12.5px;">'
                        f'<a href="{html.escape(repo)}" style="color:{accent};text-decoration:none;">'
                        f'&#9679; {html.escape(repo.replace("https://github.com/", "").rstrip("/"))}</a></div>'
                        for repo in repos
                    )
                    + '</td></tr></table></td></tr></table></td></tr>'
                )
        text_lines.append('')

    text_lines.append('完整版（含配图）在电脑上打开：')
    text_lines.append(f'  weflow-cli daily-server --date {date} --open')

    stats = ' · '.join(f'{t} {len(v)} 篇' for t, v in groups.items() if v)
    page = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>' + html.escape(f'公众号日报 {date}') + '</title></head>'
        '<body style="margin:0;padding:0;background:#f4f5f7;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f5f7;"><tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        'style="width:100%;max-width:640px;background:#ffffff;border-radius:14px;'
        'overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);">'
        '<tr><td style="background:#111827;padding:26px 28px;">'
        '<div style="font-size:12px;letter-spacing:.12em;color:#9ca3af;font-weight:600;">'
        'WEFLOW · 公众号日报</div>'
        f'<div style="font-size:26px;font-weight:700;color:#ffffff;margin-top:8px;">{html.escape(date)}</div>'
        f'<div style="font-size:13px;color:#c7cbd1;margin-top:8px;">{html.escape(stats)}</div>'
        '</td></tr>'
        + ''.join(blocks)
        + '<tr><td style="padding:22px 28px 28px;">'
        '<div style="font-size:12px;color:#9aa3af;line-height:1.8;">'
        '完整版（含配图）在电脑上打开：<br>'
        '<span style="font-family:ui-monospace,Consolas,Menlo,monospace;color:#6b7280;">'
        f'weflow-cli daily-server --date {html.escape(date)} --open</span>'
        '</div></td></tr>'
        '</table></td></tr></table></body></html>'
    )
    return '\n'.join(text_lines), page, total


def smtp_settings():
    if not SMTP_CONFIG.is_file():
        raise SystemExit(f'找不到发信凭据 {SMTP_CONFIG}（需要 smtp_user 与 smtp_auth）')
    cfg = json.loads(SMTP_CONFIG.read_text(encoding='utf-8'))
    if not cfg.get('smtp_user') or not cfg.get('smtp_auth'):
        raise SystemExit(f'{SMTP_CONFIG} 里缺 smtp_user / smtp_auth')
    return cfg['smtp_user'], cfg['smtp_auth']


def send(user, auth, to, subject, text_body, html_body):
    msg = MIMEMultipart('alternative')
    msg['From'] = formataddr((str(Header('WeFlow CLI 日报', 'utf-8')), user))
    msg['To'] = to
    msg['Subject'] = Header(subject, 'utf-8')
    msg['Date'] = formatdate(localtime=True)
    msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    with smtplib.SMTP_SSL('smtp.qq.com', 465, timeout=45) as smtp:
        smtp.login(user, auth)
        refused = smtp.sendmail(user, [to], msg.as_string())
    if refused:
        raise SystemExit(f'被拒收：{refused}')


def main():
    ap = argparse.ArgumentParser(description='把某天的日报按主题整理成邮件并发送')
    ap.add_argument('--date', required=True, help='YYYY-MM-DD')
    ap.add_argument('--to', default='', help='收件人；缺省用配置 dailyMailTo，再缺省发给自己')
    ap.add_argument('--topics', default=','.join(DEFAULT_TOPICS), help='逗号分隔的主题')
    ap.add_argument('--label', default='', help='标题后缀，如「截至中午」——区分半日速览与次日完整版')
    ap.add_argument('--incremental', action='store_true',
                    help='只发送该日期尚未成功发送的文章；发送成功后记录内容指纹')
    ap.add_argument('--no-digest', action='store_true', help='不生成每主题的 AI 综述（省钱、更快）')
    ap.add_argument('--dry-run', action='store_true', help='只渲染，不发送或更新已发送状态；配合 --incremental 时预览真实增量')
    args = ap.parse_args()

    topics = [t.strip() for t in args.topics.split(',') if t.strip()]
    data, all_articles = load_articles(args.date, set(topics))
    state_path = mail_state_path(args.date)
    sent = load_sent_fingerprints(state_path) if args.incremental else set()
    articles = only_unsent_articles(all_articles, sent)
    groups = group_by_topic(articles, topics)
    total = sum(len(v) for v in groups.values())
    print(f'{args.date}: 命中 {total} 篇（{", ".join(f"{t}={len(v)}" for t, v in groups.items())}）')
    if not total:
        if args.incremental:
            print('没有新文章需要发送')
            return
        raise SystemExit('没有可发送的文章')

    cfg = load_config()
    # `--no-digest` 只关"每主题一段综述"；聚类是版式结构，仍要做（两件事分开开关，
    # 否则想看新版式的人一加 `--no-digest` 就退回一根长肠子）。
    api_key = get_api_key(cfg)
    digests = {} if args.no_digest else {
        t: topic_digest(t, items, api_key)
        for t, items in groups.items() if items and t in DIGEST_TOPICS}
    # 聚类只给重点主题做。非重点主题分 4-6 组没有收益（新闻/文学本来就是几条标题的
    # 流水），却要为每个主题多付一次调用。
    clusters = {t: cluster_items(t, items, api_key, want=5)
                for t, items in groups.items()
                if len(items) >= 8 and t in DIGEST_TOPICS}
    for t, cs in clusters.items():
        print(f'  {t} 分了 {len(cs)} 组: ' + ' / '.join(f'{n}({len(v)})' for n, v in cs))

    bodies = body_index(args.date)
    with_repo = sum(1 for a in articles if github_links(bodies.get(a.get('url'), '')))
    print(f'正文含 GitHub 链接: {with_repo} 篇')

    mailed_articles = articles_in_email(groups, clusters)
    text_body, html_body, total = render(args.date, groups, digests, bodies, clusters)
    # **`--label` 支持 ASCII 代号**，中文在这里映射。原因：调用方 `daily_task.ps1` 是
    # 无 BOM 的 UTF-8，Windows PowerShell 5.1 会按 GBK 解码它，直接传中文会变成
    # `鎴嚦涓崍` 那种乱码进到邮件主题（2026-10-26 实际发生）。
    LABELS = {'noon': '截至中午'}
    label = (args.label or '').strip()
    label = LABELS.get(label, label)
    suffix = f'（{label}）' if label else ''
    subject = f'公众号日报 {args.date}{suffix}：{" / ".join(t for t in topics if groups.get(t))} {total} 篇'
    if label:
        text_body = f'【{label}】以下是到此刻为止收到的 {total} 篇。\n\n' + text_body
    if args.incremental:
        text_body = f'本次仅包含此前未成功发送的 {total} 篇新文章。\n\n' + text_body
    # **安静的一天和"客户端没开"必须能分辨**，见 biz_daily.detect_low_volume。
    # 这一行比它下面所有内容都重要：它是这封邮件唯一能告诉你"今天可能是数据缺了"的地方。
    low = data.get('lowVolume') if isinstance(data, dict) else None
    if isinstance(low, dict) and low.get('median'):
        note = (f'⚠ 本日只采到 {low.get("today")} 篇，最近 {low.get("sample")} 天的中位数是 '
                f'{low.get("median")} 篇。公众号推送只在微信客户端运行时才写进本地库，'
                f'客户端没开的时段补不回来——先确认它当时在运行。')
        text_body = note + '\n\n' + text_body
        html_body = (
            '<p style="margin:0 0 14px;padding:10px 12px;background:#fff4e5;'
            'border-left:3px solid #f59e0b;color:#7c4a03;font-size:14px">' + note + '</p>'
        ) + html_body
    user, auth = smtp_settings()
    to = args.to or str(cfg.get('dailyMailTo') or '') or user
    print(f'收件人: {to}  主题: {subject}  正文字符: {len(text_body)}')
    if args.dry_run:
        out = ROOT / 'output' / f'daily-mail-{args.date}.html'
        out.write_text(html_body, encoding='utf-8')
        print(f'[dry-run] 已渲染到 {out}')
        return
    deliver_mail(user, auth, to, subject, text_body, html_body,
                 incremental=args.incremental, state_path=state_path,
                 sent=sent, articles=mailed_articles)
    print(f'已发送到 {to}')


if __name__ == '__main__':
    main()
