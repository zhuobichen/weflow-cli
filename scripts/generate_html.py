#!/usr/bin/env python3
"""
生成公众号日报 HTML 页面 — 分类展示、已读/未读标记（localStorage 持久化）。

用法:
  python scripts/generate_html.py --date 2026-05-18
  python scripts/generate_html.py --date 2026-05-18 --output custom.html
"""
import sys, os, json, re
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_ROOT = os.path.join(os.path.dirname(SCRIPTS_DIR), 'output', 'biz-daily')

TOPIC_LABELS = {
    'AI': ('AI', '#8b5cf6', '🤖'),
    '学术': ('学术', '#3b82f6', '📚'),
    '新闻': ('新闻', '#f59e0b', '📰'),
    '文学': ('文学', '#ec4899', '📝'),
    '投资': ('投资', '#10b981', '💰'),
}

TOPIC_ORDER = ['AI', '学术', '新闻', '文学', '投资']


def parse_frontmatter(text: str) -> dict:
    """解析 YAML frontmatter（简易版，兼容现有格式）。"""
    if not text.startswith('---'):
        return {}
    end = text.find('---', 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    meta = {}
    for line in fm_text.split('\n'):
        line = line.strip()
        if ':' in line:
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith('[') and val.endswith(']'):
                val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(',') if v.strip()]
            meta[key] = val
    return meta


def get_body_preview(text: str, max_chars=200) -> str:
    """提取正文预览（跳过 frontmatter 和标题行）。"""
    if text.startswith('---'):
        end = text.find('---', 3)
        text = text[end + 3:] if end != -1 else text
    lines = text.strip().split('\n')
    body_lines = []
    for line in lines:
        line = line.strip()
        if line.startswith('# ') or line.startswith('> ') or line.startswith('---'):
            continue
        if line:
            body_lines.append(line)
    preview = ' '.join(body_lines)
    if len(preview) > max_chars:
        preview = preview[:max_chars] + '...'
    return preview


def collect_articles(date_dir: str) -> dict:
    """收集所有文章，按主题分组。"""
    topics = {}
    for topic in TOPIC_ORDER:
        topic_dir = Path(date_dir) / topic
        if not topic_dir.is_dir():
            continue
        articles = []
        for md_file in sorted(topic_dir.glob('*.md')):
            try:
                content = md_file.read_text(encoding='utf-8')
            except Exception:
                continue
            meta = parse_frontmatter(content)
            preview = get_body_preview(content)
            articles.append({
                'filename': md_file.name,
                'rel_path': f'{topic}/{md_file.name}',
                'title': meta.get('title', md_file.stem),
                'source': meta.get('source', ''),
                'topic': topic,
                'tags': meta.get('tags', []),
                'relevance': meta.get('relevance', ''),
                'url': meta.get('url', ''),
                'preview': preview,
            })
        if articles:
            topics[topic] = articles
    return topics


def escape_html(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def generate_article_viewer(out_path: str):
    """生成 article.html — Markdown 渲染阅读器。"""
    html = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>文章阅读</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
    line-height: 1.8;
}
.container { max-width: 960px; margin: 0 auto; padding: 24px; }
.top-bar {
    display: flex; align-items: center; gap: 12px; padding: 12px 0;
    margin-bottom: 24px; border-bottom: 1px solid #1e293b;
    position: sticky; top: 0; background: #0f172a; z-index: 10; flex-wrap: wrap;
}
.btn-back {
    color: #94a3b8; text-decoration: none; font-size: 14px;
    padding: 6px 14px; border: 1px solid #334155; border-radius: 8px;
    transition: all .2s; white-space: nowrap;
}
.btn-back:hover { color: #e2e8f0; border-color: #475569; }
.article-source { color: #818cf8; font-size: 13px; font-weight: 600; }
.article-meta { color: #64748b; font-size: 12px; margin-left: auto; }
.mode-badge { font-size: 11px; padding: 2px 10px; border-radius: 10px; font-weight: 600; }
.mode-badge.html { background: #10b98120; color: #34d399; border: 1px solid #10b98140; }
.view-switch { display: inline-flex; background: #1e293b; border-radius: 8px; overflow: hidden; border: 1px solid #334155; }
.view-switch a { padding: 4px 12px; font-size: 12px; color: #64748b; text-decoration: none; transition: all .2s; }
.view-switch a.active { background: #334155; color: #e2e8f0; }
.view-switch a:hover:not(.active) { color: #94a3b8; }
.article-header { margin-bottom: 32px; padding-bottom: 20px; border-bottom: 1px solid #1e293b; }
.article-header h1 { font-size: 26px; font-weight: 700; line-height: 1.4; margin-bottom: 12px; color: #f1f5f9; }
.article-header .meta-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: 13px; color: #64748b; }
.article-header .source-tag { background: #312e81; color: #a5b4fc; padding: 2px 10px; border-radius: 6px; font-size: 12px; }
.tags-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.tag { font-size: 11px; padding: 2px 8px; background: #334155; border-radius: 6px; color: #94a3b8; }
.article-body { font-size: 16px; color: #cbd5e1; }
.article-body h2 { font-size: 22px; font-weight: 700; margin: 32px 0 16px; color: #f1f5f9; border-bottom: 1px solid #1e293b; padding-bottom: 8px; }
.article-body h3 { font-size: 18px; font-weight: 600; margin: 24px 0 12px; color: #e2e8f0; }
.article-body h4 { font-size: 16px; font-weight: 600; margin: 20px 0 8px; color: #cbd5e1; }
.article-body p { margin: 12px 0; }
.article-body strong { color: #f1f5f9; font-weight: 700; }
.article-body a { color: #818cf8; text-decoration: none; }
.article-body a:hover { text-decoration: underline; }
.article-body img { max-width: 100%; height: auto; border-radius: 8px; margin: 16px 0; border: 1px solid #334155; }
.article-body blockquote { border-left: 3px solid #818cf8; padding: 8px 16px; margin: 16px 0; background: #1a1040; border-radius: 0 8px 8px 0; color: #a5b4fc; }
.article-body blockquote p { margin: 4px 0; }
.article-body code { background: #1e293b; padding: 2px 6px; border-radius: 4px; font-size: 14px; color: #fbbf24; }
.article-body pre { background: #0c1222; border: 1px solid #1e293b; border-radius: 10px; padding: 16px; overflow-x: auto; margin: 16px 0; font-size: 13px; line-height: 1.7; }
.article-body pre code { background: none; padding: 0; color: #e2e8f0; }
.article-body ul, .article-body ol { margin: 12px 0; padding-left: 24px; }
.article-body li { margin: 6px 0; color: #cbd5e1; }
.article-body hr { border: none; border-top: 1px solid #1e293b; margin: 24px 0; }
.article-body table { width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }
.article-body th { background: #1e293b; padding: 10px 14px; text-align: left; color: #e2e8f0; font-weight: 600; border-bottom: 2px solid #334155; }
.article-body td { padding: 8px 14px; border-bottom: 1px solid #1e293b; color: #94a3b8; }
.loading { text-align: center; padding: 80px 20px; color: #64748b; font-size: 16px; }
.error-box { background: #dc262610; border: 1px solid #dc262630; border-radius: 12px; padding: 20px; text-align: center; color: #f87171; margin: 40px 0; }
@media (max-width: 640px) {
    .container { padding: 12px; }
    .article-header h1 { font-size: 20px; }
    .article-body { font-size: 15px; }
}
</style>
</head>
<body>
<div class="container" id="app">
  <div class="loading">&#x1f4d6; 加载中...</div>
</div>
<script>
(async function() {
  const params = new URLSearchParams(location.search);
  const file = params.get('file');
  if (!file) {
    document.getElementById('app').innerHTML = '<div class="error-box">\u274c \u7f3a\u5c11 file \u53c2\u6570</div>';
    return;
  }
  let mdUrl = file;
  if (!file.startsWith('http') && !file.startsWith('/')) { mdUrl = './' + file; }
  try {
    const res = await fetch(mdUrl);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const text = await res.text();
    let title = '', source = '', tags = [], body = text;
    if (text.startsWith('---')) {
      const endIdx = text.indexOf('---', 3);
      if (endIdx !== -1) {
        const fm = text.slice(3, endIdx).trim();
        body = text.slice(endIdx + 3).trim();
        fm.split('\n').forEach(line => {
          const ci = line.indexOf(':');
          if (ci === -1) return;
          const key = line.slice(0, ci).trim();
          let val = line.slice(ci + 1).trim().replace(/^["']|["']$/g, '');
          if (key === 'title') title = val;
          if (key === 'source') source = val;
          if (key === 'tags') {
            try { tags = JSON.parse(val); } catch(e) {
              tags = val.replace(/[\\[\\]]/g, '').split(',').map(s => s.trim().replace(/["']/g, ''));
            }
          }
        });
      }
    }
    if (!title) {
      const h1m = body.match(/^#\s+(.+)/m);
      if (h1m) { title = h1m[1]; body = body.replace(h1m[0], '').trim(); }
    }
    if (!title) title = file.split('/').pop().replace('.md', '');
    let htmlContent = '';
    if (typeof marked !== 'undefined') {
      marked.setOptions({ breaks: true, gfm: true });
      htmlContent = marked.parse(body);
    } else {
      htmlContent = body.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>');
      htmlContent = '<p>' + htmlContent + '</p>';
    }
    const tagsHtml = tags.length ? '<div class="tags-row">' + tags.map(t => '<span class="tag">' + t + '</span>').join('') + '</div>' : '';
    document.getElementById('app').innerHTML =
      '<div class="top-bar">' +
        '<a href="./" class="btn-back">\u2190 \u8fd4\u56de\u5217\u8868</a>' +
        '<span class="article-source">' + (source || '') + '</span>' +
        '<span class="article-meta">\u9605\u8bfb\u6a21\u5f0f</span>' +
        '<span class="mode-badge html">\ud83c\udfa8 HTML</span>' +
        '<div class="view-switch">' +
          '<a href="' + file + '">\ud83d\udcdd MD</a>' +
          '<a href="article.html?file=' + encodeURIComponent(file) + '" class="active">\ud83c\udfa8 HTML</a>' +
        '</div>' +
      '</div>' +
      '<div class="article-header">' +
        '<h1>' + title + '</h1>' +
        '<div class="meta-row">' + (source ? '<span class="source-tag">' + source + '</span>' : '') + '</div>' +
        tagsHtml +
      '</div>' +
      '<div class="article-body">' + htmlContent + '</div>' +
      '<div style="text-align:center;padding:40px;color:#475569;font-size:12px;">\u2014 END \u2014</div>';
  } catch(e) {
    document.getElementById('app').innerHTML =
      '<div class="error-box">\u274c \u52a0\u8f7d\u5931\u8d25: ' + e.message + '<br><br><a href="./" class="btn-back">\u2190 \u8fd4\u56de\u5217\u8868</a></div>';
  }
})();
</script>
</body>
</html>'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)


def generate_html(date_str: str, topics: dict, action_suggestions_exist: bool, briefing: str = '') -> str:
    """生成完整 HTML。"""
    total = sum(len(v) for v in topics.values())

    # 构建文章卡片 JSON
    articles_json = []
    for topic in TOPIC_ORDER:
        if topic not in topics:
            continue
        for art in topics[topic]:
            articles_json.append({
                'id': art['rel_path'],
                'topic': topic,
            })

    # 构建分类导航
    nav_items = []
    for topic in TOPIC_ORDER:
        if topic not in topics:
            continue
        count = len(topics[topic])
        _, color, icon = TOPIC_LABELS[topic]
        nav_items.append(f'''
            <button class="tab-btn active" data-topic="{topic}"
                    style="--accent: {color}" onclick="switchTab('{topic}')">
                {icon} {topic} <span class="count">{count}</span>
            </button>''')

    # 构建文章列表
    topic_sections = []
    for topic in TOPIC_ORDER:
        if topic not in topics:
            continue
        _, color, icon = TOPIC_LABELS[topic]
        cards = []
        for art in topics[topic]:
            all_tags = art['tags'] or []
            shown_tags = all_tags[:4]
            hidden_count = len(all_tags) - 4
            tags_html = ''.join(f'<span class="tag">{escape_html(t)}</span>' for t in shown_tags)
            if hidden_count > 0:
                tags_html += f'<span class="tag tag-more">+{hidden_count}</span>'
            relevance_badge = ''
            if art.get('relevance') == '高':
                relevance_badge = '<span class="relevance high">高相关</span>'
            elif art.get('relevance') == '中':
                relevance_badge = '<span class="relevance mid">中相关</span>'

            cards.append(f'''
                <article class="card" data-id="{escape_html(art['rel_path'])}" data-topic="{topic}">
                    <div class="card-header">
                        <div class="card-title-row">
                            <span class="unread-dot" id="dot-{escape_html(art['rel_path']).replace('/', '_').replace('.', '_')}"></span>
                            <h3 class="card-title">
                                <a href="{escape_html(art['rel_path'])}"
                                   onclick="markRead('{escape_html(art['rel_path'])}')">
                                    {escape_html(art['title'])}
                                </a>
                            </h3>
                            {relevance_badge}
                        </div>
                        <div class="card-meta">
                            <span class="source">{escape_html(art['source'])}</span>
                        </div>
                    </div>
                    <div class="card-tags">{tags_html}</div>
                    <p class="card-preview">{escape_html(art['preview'])}</p>
                    <div class="card-actions">
                        <a href="{escape_html(art['rel_path'])}"
                           class="btn-read" onclick="markRead('{escape_html(art['rel_path'])}')">
                           📖 阅读
                        </a>
                        <button class="btn-toggle" onclick="toggleRead('{escape_html(art['rel_path'])}')">
                           <span class="toggle-label">✓ 标记已读</span>
                        </button>
                        <button class="btn-fav" onclick="toggleFav('{escape_html(art['rel_path'])}')" title="收藏">
                           ☆
                        </button>
                    </div>
                </article>''')
        sections_html = '\n'.join(cards)

        topic_sections.append(f'''
            <section class="topic-section" id="section-{topic}" data-topic="{topic}">
                <h2 class="topic-heading" style="--accent: {color}">
                    {icon} {topic} <span class="count">{len(topics[topic])} 篇</span>
                </h2>
                <div class="cards-grid">
                    {sections_html}
                </div>
            </section>''')

    action_link = ''
    if action_suggestions_exist:
        action_link = '''
        <a href="./行动建议.md" target="_blank" class="action-link">📋 行动建议</a>
        '''

    briefing_html = ''
    if briefing:
        briefing_html = f'''
<div class="briefing">
  <div class="briefing-title">📋 今日简报</div>
  <div class="briefing-text">{escape_html(briefing)}</div>
</div>'''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>公众号日报 — {date_str}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f5f0e8;
    color: #3c3a38;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
}}
.container {{ max-width: 1400px; margin: 0 auto; padding: 28px 32px; }}

/* ---- Header ---- */
.header {{
    text-align: center;
    padding: 44px 20px 28px;
    margin-bottom: 24px;
    position: relative;
}}
.header::after {{
    content: '';
    position: absolute;
    bottom: 0; left: 50%; transform: translateX(-50%);
    width: 60px; height: 3px;
    background: linear-gradient(90deg, #8b6914, #c8963e, #d4a853);
    border-radius: 2px;
}}
.header h1 {{
    font-size: 28px; font-weight: 800; letter-spacing: -.3px;
    color: #4a3728;
}}
.header .meta {{
    margin-top: 8px; color: #8b7355; font-size: 14px;
    display: flex; align-items: center; justify-content: center; gap: 14px; flex-wrap: wrap;
}}
.action-link {{
    color: #8b6914; text-decoration: none; font-weight: 600;
    padding: 5px 14px; border: 1px solid #c8963e40; border-radius: 20px;
    font-size: 13px; transition: all .25s; background: #faf6ef;
}}
.action-link:hover {{ background: #c8963e15; border-color: #c8963e70; }}

/* ---- Briefing ---- */
.briefing {{
  background: linear-gradient(135deg, #faf6ef, #f7f0e4);
  border: 1px solid #d4c5a0; border-radius: 14px; padding: 22px 26px; margin-bottom: 20px;
}}
.briefing-title {{ font-size: 15px; font-weight: 700; color: #6b4c1e; margin-bottom: 10px; }}
.briefing-text {{ font-size: 14px; line-height: 1.9; color: #5c4a32; }}

/* ---- Stats Bar ---- */
.stats-bar {{
    display: flex; align-items: center; justify-content: center; gap: 10px;
    flex-wrap: wrap; margin-bottom: 22px; padding: 10px 22px;
    background: #faf6ef; border: 1px solid #d4c5a060; border-radius: 12px;
    font-size: 13px; color: #8b7355;
}}
.stats-bar strong {{ color: #3c3a38; font-weight: 700; }}
.stats-group {{
    display: flex; align-items: center; gap: 8px;
}}
.btn-clear {{
    background: #f0e8d5; color: #5c4a32; border: 1px solid #d4c5a0;
    padding: 5px 14px; border-radius: 8px; cursor: pointer;
    font-size: 12px; font-weight: 500; transition: all .2s;
}}
.btn-clear:hover {{ background: #e8dcc0; border-color: #c8963e; color: #3c3a38; }}
.btn-clear.active-mode {{
    background: #8b6914 !important; border-color: #8b6914 !important; color: #fff !important;
}}

/* ---- Tabs ---- */
.tabs {{
    display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 24px;
    position: sticky; top: 12px; z-index: 10; padding: 6px;
    background: #faf6eff2; backdrop-filter: blur(12px);
    border: 1px solid #d4c5a060; border-radius: 12px;
}}
.tab-btn {{
    background: transparent; color: #8b7355; border: none;
    padding: 9px 18px; border-radius: 10px; cursor: pointer;
    font-size: 14px; font-weight: 500; transition: all .2s;
}}
.tab-btn:hover {{ color: #3c3a38; background: #f0e8d5; }}
.tab-btn.active {{
    color: #2a2018; background: #dcc89a;
    box-shadow: 0 1px 4px #00000018, inset 0 1px 0 #fff8;
    font-weight: 700;
    border: 1px solid #c8963e30;
}}
.tab-btn .count {{
    background: #d4c5a040; padding: 2px 8px; border-radius: 8px;
    font-size: 12px; margin-left: 5px; font-weight: 600;
}}
.tab-btn.active .count {{ background: #c8963e20; color: #8b6914; }}

/* ---- Topic Sections ---- */
.topic-section {{ display: none; }}
.topic-section.active {{ display: block; }}
.topic-heading {{
    font-size: 18px; font-weight: 700; margin-bottom: 16px;
    display: flex; align-items: center; gap: 10px; color: #4a3728;
}}
.topic-heading::after {{
    content: ''; flex: 1; height: 1px; background: #d4c5a0; margin-left: 8px;
}}
.topic-heading .count {{ font-size: 13px; color: #8b7355; font-weight: 400; }}

/* ---- Cards ---- */
.cards-grid {{ display: flex; flex-direction: column; gap: 10px; }}
.card {{
    background: #fefcf8; border-radius: 12px; padding: 20px 24px;
    border: 1px solid #d4c5a0; transition: all .25s ease;
    position: relative; overflow: hidden;
}}
.card::before {{
    content: ''; position: absolute; top: 0; left: 0; width: 3px; height: 100%;
    background: transparent; transition: background .3s; border-radius: 12px 0 0 12px;
}}
.card:hover {{
    border-color: #c8963e50; background: #fffdf7;
    transform: translateY(-1px); box-shadow: 0 3px 16px #3c3a3810;
}}
.card:hover::before {{ background: #c8963e; }}
.card.read {{
    opacity: 0.55; background: #f5f0e8; border-color: #d4c5a030;
}}
.card.read:hover {{ opacity: 0.7; border-color: #d4c5a060; transform: none; box-shadow: none; }}
.card.read:hover::before {{ background: #b8a080; }}
.card.faved {{ border-color: #c8963e40; background: #fffcf5; }}

.card-header {{ margin-bottom: 8px; }}
.card-title-row {{ display: flex; align-items: flex-start; gap: 10px; }}
.unread-dot {{
    width: 9px; height: 9px; border-radius: 50%;
    background: #c8963e; flex-shrink: 0; margin-top: 5px; transition: all .3s;
    box-shadow: 0 0 6px #c8963e40;
}}
.card.read .unread-dot {{ background: #b8a080; box-shadow: none; }}
.card-title {{
    font-size: 18px; font-weight: 700; flex: 1; line-height: 1.4; letter-spacing: -.3px;
}}
.card-title a {{ color: #2a2018; text-decoration: none; transition: color .2s; }}
.card-title a:hover {{ color: #8b6914; text-decoration: underline; text-underline-offset: 3px; }}
.card.read .card-title a {{ color: #8b7355; }}

.card-meta {{ display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 13px; color: #8b7355; font-weight: 500; }}
.card-meta .source {{
    color: #6b4c1e; font-weight: 600;
}}

.relevance {{
    font-size: 10px; padding: 3px 10px; border-radius: 12px; font-weight: 700;
    flex-shrink: 0; letter-spacing: .3px;
}}
.relevance.high {{ background: #dc5b5b15; color: #b04444; border: 1px solid #dc5b5b30; }}
.relevance.mid {{ background: #c8963e15; color: #8b6914; border: 1px solid #c8963e40; }}

.card-tags {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }}
.tag {{
    font-size: 11px; padding: 3px 10px; background: #f0e8d5; border-radius: 6px;
    color: #8b7355; font-weight: 500; letter-spacing: .2px;
}}
.tag-more {{
    background: #ede0c8; color: #96876a; font-weight: 600; cursor: default;
}}

.card-preview {{
    font-size: 13px; color: #a0987a; line-height: 1.65; margin-bottom: 12px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}}

/* ---- Card Actions ---- */
.card-actions {{ display: flex; gap: 8px; align-items: center; justify-content: flex-end; }}
.btn-read {{
    font-size: 13px; color: #fff; text-decoration: none;
    padding: 6px 18px; border: none; border-radius: 7px;
    transition: all .2s; font-weight: 600;
    background: linear-gradient(135deg, #8b6914, #a07828);
    box-shadow: 0 1px 3px #8b691420;
}}
.btn-read:hover {{ background: linear-gradient(135deg, #6b4c1e, #8b6914); box-shadow: 0 2px 8px #8b691430; color: #fff; }}
.btn-toggle {{
    font-size: 12px; color: #8b7355; background: none; border: 1px solid #d4c5a0;
    padding: 5px 14px; border-radius: 7px; cursor: pointer;
    transition: all .2s; font-weight: 500;
}}
.btn-toggle:hover {{ color: #3c3a38; border-color: #b8a080; }}
.card.read .btn-toggle {{ color: #6b8f5e; border-color: #6b8f5e30; }}
.card.read .toggle-label::before {{ content: '↩ 标记未读'; }}
.toggle-label::before {{ content: '✓ 标记已读'; }}

/* ---- Favorite ---- */
.btn-fav {{
    font-size: 14px; background: none; border: 1px solid #d4c5a0;
    padding: 4px 10px; border-radius: 7px; cursor: pointer;
    transition: all .2s; color: #8b7355; line-height: 1;
}}
.btn-fav:hover {{ color: #c8963e; border-color: #c8963e60; }}
.btn-fav.faved {{ color: #c8963e; border-color: #c8963e50; background: #c8963e10; }}
.fav-empty {{
    text-align: center; padding: 60px 20px; color: #96876a;
    font-size: 15px; line-height: 2;
}}

/* ---- Search ---- */
.search-box {{
    width: 100%; max-width: 480px; margin: 0 auto 20px; display: block;
    padding: 11px 18px; border-radius: 12px; border: 1px solid #d4c5a0;
    background: #fdfaf5; color: #3c3a38; font-size: 14px; outline: none;
    transition: all .25s; font-family: inherit;
}}
.search-box:focus {{ border-color: #c8963e; box-shadow: 0 0 0 3px #c8963e15; }}
.search-box::placeholder {{ color: #b8a080; }}

/* ---- Footer ---- */
.footer {{
    text-align: center; padding: 32px 20px; color: #b8a080;
    font-size: 12px; margin-top: 24px; border-top: 1px solid #d4c5a050;
}}

/* ---- Responsive ---- */
@media (max-width: 640px) {{
    .container {{ padding: 14px; }}
    .header {{ padding: 28px 14px 22px; }}
    .header h1 {{ font-size: 22px; }}
    .card {{ padding: 14px 16px; }}
    .tabs {{ gap: 2px; padding: 4px; }}
    .tab-btn {{ padding: 7px 11px; font-size: 13px; }}
    .card-title {{ font-size: 14px; }}
}}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>📋 公众号日报 — {date_str}</h1>
    <div class="meta">
        <span>共 <strong>{total}</strong> 篇文章 {', '.join(f'{TOPIC_LABELS[t][2]} {len(topics[t])}' for t in TOPIC_ORDER if t in topics)}</span>
        {action_link}
        <span style="font-size:12px;color:#b8a080;display:flex;align-items:center;gap:6px">
          <button class="btn-clear" id="btn-mode-md" onclick="setViewMode('md')" style="font-size:11px;padding:3px 10px">📝</button>
          <button class="btn-clear" id="btn-mode-html" onclick="setViewMode('html')" style="font-size:11px;padding:3px 10px">🎨</button>
          <button class="btn-clear" onclick="exportFav()" id="btn-export-fav" style="font-size:11px;padding:3px 10px">📥</button>
        </span>
    </div>
</div>

    {briefing_html}

<div class="stats-bar">
    <span class="stats-group">
        <span>📊 <strong id="unread-count">-</strong> 篇未读 / {total} 篇</span>
        <button class="btn-clear" id="btn-filter-unread" onclick="toggleUnreadFilter()" style="font-weight:600">👁 仅看未读</button>
    </span>
    <span class="stats-group">
        <button class="btn-clear" onclick="markAllRead()">✅ 全部标为已读</button>
        <button class="btn-clear" onclick="clearAll()">🔄 重置</button>
    </span>
    <span class="stats-group">
        <span id="sync-indicator" style="display:none;font-size:12px;color:#6b8f5e;font-weight:600">🟢 已同步</span>
    </span>

<input type="text" class="search-box" placeholder="🔍 搜索文章标题、来源、标签..." oninput="doSearch(this.value)">

<nav class="tabs">
    {''.join(nav_items)}
            <button class="tab-btn" data-topic="收藏"
                    style="--accent: #fbbf24" onclick="switchTab('收藏')">
                ⭐ 收藏 <span class="count" id="fav-count">0</span>
            </button>
</nav>

            <section class="topic-section" id="section-收藏" data-topic="收藏">
                <h2 class="topic-heading" style="--accent: #fbbf24">
                    ⭐ 收藏 <span class="count" id="fav-section-count">0 篇</span>
                </h2>
                <div class="cards-grid" id="fav-cards">
                    <div class="fav-empty">⭐ 点击文章卡片上的 ☆ 按钮即可收藏<br>收藏的文章会显示在这里</div>
                </div>
            </section>

{''.join(topic_sections)}

<div class="footer">
    生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 阅读状态保存在浏览器中
</div>

</div>

<script>
const STORAGE_KEY = 'weflow_read_{date_str}';
const FAV_STORAGE_KEY = 'weflow_fav_{date_str}';
const ALL_IDS = {json.dumps([a['id'] for a in articles_json], ensure_ascii=False)};

function getState() {{
    try {{
        return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{{}}');
    }} catch(e) {{ return {{}}; }}
}}
function saveState(state) {{
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}}
function isRead(id) {{
    return !!getState()[id];
}}
function markRead(id) {{
    const state = getState();
    state[id] = true;
    saveState(state);
    applyState();
}}
function markUnread(id) {{
    const state = getState();
    delete state[id];
    saveState(state);
    applyState();
}}
function toggleRead(id) {{
    if (isRead(id)) markUnread(id);
    else markRead(id);
}}
function markAllRead() {{
    const state = {{}};
    ALL_IDS.forEach(id => state[id] = true);
    saveState(state);
    applyState();
}}
function clearAll() {{
    if (confirm('确定要重置所有阅读标记？')) {{
        localStorage.removeItem(STORAGE_KEY);
        applyState();
    }}
}}
function applyState() {{
    let unread = 0;
    ALL_IDS.forEach(id => {{
        const card = document.querySelector(`.card[data-id="${{id.replace(/"/g, '&quot;')}}"]`);
        if (!card) return;
        if (isRead(id)) {{
            card.classList.add('read');
        }} else {{
            card.classList.remove('read');
            unread++;
        }}
    }});
    const el = document.getElementById('unread-count');
    if (el) el.textContent = unread;
}}
function switchTab(topic) {{
    document.querySelectorAll('.topic-section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    if (topic === '收藏') {{
        renderFavSection();
    }}
    const section = document.getElementById('section-' + topic);
    if (section) section.classList.add('active');
    const btn = document.querySelector(`.tab-btn[data-topic="${{topic}}"]`);
    if (btn) btn.classList.add('active');
    window.location.hash = topic;
}}
function doSearch(query) {{
    const q = query.toLowerCase().trim();
    document.querySelectorAll('.card').forEach(card => {{
        if (!q) {{ card.style.display = ''; return; }}
        const text = (card.getAttribute('data-id') + ' ' + card.textContent).toLowerCase();
        card.style.display = text.includes(q) ? '' : 'none';
    }});
    if (q) {{
        document.querySelectorAll('.topic-section').forEach(s => s.classList.add('active'));
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    }} else {{
        switchTab(window.location.hash.slice(1) || 'AI');
    }}
}}

// --- Favorites ---
const IS_LOCALHOST = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
const API_BASE = IS_LOCALHOST ? '/api/fav' : null;

function getFavState() {{
    try {{
        return JSON.parse(localStorage.getItem(FAV_STORAGE_KEY) || '[]');
    }} catch(e) {{ return []; }}
}}
function saveFavState(arr) {{
    localStorage.setItem(FAV_STORAGE_KEY, JSON.stringify(arr));
}}
function isFaved(id) {{
    return getFavState().includes(id);
}}
async function toggleFav(id) {{
    if (API_BASE) {{
        // 实时模式：调 API 写入磁盘
        try {{
            const res = await fetch(API_BASE + '/toggle', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{id: id}})
            }});
            const data = await res.json();
            if (data.ok) {{
                await loadFavFromServer();
            }}
        }} catch(e) {{
            console.error('API 请求失败，回退到本地模式', e);
            toggleFavLocal(id);
        }}
    }} else {{
        toggleFavLocal(id);
    }}
}}
function toggleFavLocal(id) {{
    const arr = getFavState();
    const idx = arr.indexOf(id);
    if (idx >= 0) {{ arr.splice(idx, 1); }}
    else {{ arr.push(id); }}
    saveFavState(arr);
    applyFavState();
}}
async function loadFavFromServer() {{
    try {{
        const res = await fetch(API_BASE + '/list');
        const data = await res.json();
        saveFavState(data);
        applyFavState();
    }} catch(e) {{
        console.error('加载收藏列表失败', e);
    }}
}}
function applyFavState() {{
    const favs = getFavState();
    // Update fav buttons
    document.querySelectorAll('.btn-fav').forEach(btn => {{
        const card = btn.closest('.card');
        if (!card) return;
        const id = card.getAttribute('data-id');
        if (favs.includes(id)) {{
            btn.classList.add('faved');
            btn.innerHTML = '★';
            card.classList.add('faved');
        }} else {{
            btn.classList.remove('faved');
            btn.innerHTML = '☆';
            card.classList.remove('faved');
        }}
    }});
    // Update fav count
    const countEl = document.getElementById('fav-count');
    if (countEl) countEl.textContent = favs.length;
    const sectionCountEl = document.getElementById('fav-section-count');
    if (sectionCountEl) sectionCountEl.textContent = favs.length + ' 篇';
}}
function renderFavSection() {{
    const favs = getFavState();
    const container = document.getElementById('fav-cards');
    if (!container) return;
    if (favs.length === 0) {{
        container.innerHTML = '<div class="fav-empty">⭐ 点击文章卡片上的 ☆ 按钮即可收藏<br>收藏的文章会显示在这里</div>';
        return;
    }}
    let html = '';
    favs.forEach(id => {{
        const card = document.querySelector(`.card[data-id="${{id.replace(/"/g, '&quot;')}}"]`);
        if (card) {{
            html += card.outerHTML;
        }}
    }});
    container.innerHTML = html || '<div class="fav-empty">⭐ 暂无收藏文章</div>';
}}

const VIEW_MODE_KEY = 'weflow_vmode_{date_str}';
function getViewMode() {{
    return localStorage.getItem(VIEW_MODE_KEY) || 'html';
}}
function setViewMode(mode) {{
    localStorage.setItem(VIEW_MODE_KEY, mode);
    applyViewMode();
}}
const UNREAD_KEY = 'weflow_unread_{date_str}';
let unreadFilterOn = sessionStorage.getItem(UNREAD_KEY) === '1';
function toggleUnreadFilter() {{
    unreadFilterOn = !unreadFilterOn;
    sessionStorage.setItem(UNREAD_KEY, unreadFilterOn ? '1' : '0');
    const btn = document.getElementById('btn-filter-unread');
    if (btn) {{
        if (unreadFilterOn) {{
            btn.classList.add('active-mode');
            btn.textContent = '👁 仅看未读 ✓';
        }} else {{
            btn.classList.remove('active-mode');
            btn.textContent = '👁 仅看未读';
        }}
    }}
    applyUnreadFilter();
}}
function applyUnreadFilter() {{
    document.querySelectorAll('.card').forEach(card => {{
        if (!unreadFilterOn) {{ card.style.display = ''; return; }}
        card.style.display = card.classList.contains('read') ? 'none' : '';
    }});
}}

function applyViewMode() {{
    const mode = getViewMode();
    const btnMd = document.getElementById('btn-mode-md');
    const btnHtml = document.getElementById('btn-mode-html');
    if (btnMd && btnHtml) {{
        btnMd.classList.toggle('active-mode', mode === 'md');
        btnHtml.classList.toggle('active-mode', mode === 'html');
    }}
    // Update all article title links and read buttons
    document.querySelectorAll('.card-title a, .btn-read').forEach(a => {{
        const card = a.closest('.card');
        if (!card) return;
        const id = card.getAttribute('data-id');
        if (!id) return;
        if (mode === 'html') {{
            const newHref = 'article.html?file=' + encodeURIComponent(id) + '&date={date_str}';
            if (a.getAttribute('data-md-href') === null) {{
                a.setAttribute('data-md-href', a.getAttribute('href') || '');
            }}
            a.setAttribute('href', newHref);
        }} else {{
            const mdHref = a.getAttribute('data-md-href');
            if (mdHref) a.setAttribute('href', mdHref);
        }}
    }});
}}

function exportFav() {{
    const favs = getFavState();
    if (IS_LOCALHOST) {{
        alert('✅ 收藏已实时同步到磁盘！\\n\\n' + favs.length + ' 篇收藏在 收藏/ 文件夹中');
        return;
    }}
    const blob = new Blob([JSON.stringify(favs, null, 2)], {{type: 'application/json'}});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = '.fav_state.json';
    a.click();
    URL.revokeObjectURL(url);
    alert('已导出 ' + favs.length + ' 篇收藏到 .fav_state.json\\n\\n运行: python scripts/sync_fav.py --date {date_str}');
}}

// Init
(async function() {{
    applyState();
    applyFavState();
    applyViewMode();

    // 恢复"仅看未读"筛选状态
    if (unreadFilterOn) {{
        const btn = document.getElementById('btn-filter-unread');
        if (btn) {{ btn.classList.add('active-mode'); btn.textContent = '👁 仅看未读 ✓'; }}
        applyUnreadFilter();
    }}

    // 恢复滚动位置（兼容 bfcache）
    const KEY = 'weflow_scroll_{date_str}';
    function restoreScroll() {{
        const y = sessionStorage.getItem(KEY);
        if (y) window.scrollTo(0, parseInt(y));
    }}
    restoreScroll();
    window.addEventListener('pageshow', (e) => {{ if (e.persisted) restoreScroll(); }});

    // 滚动时保存
    let scrollTimer;
    window.addEventListener('scroll', () => {{
        clearTimeout(scrollTimer);
        scrollTimer = setTimeout(() => sessionStorage.setItem(KEY, String(window.scrollY)), 150);
    }}, {{passive: true}});

    // 点击文章链接时立即保存
    document.addEventListener('click', (e) => {{
        const link = e.target.closest('.card-title a, .btn-read');
        if (link) sessionStorage.setItem(KEY, String(window.scrollY));
    }});
    if (IS_LOCALHOST) {{
        await loadFavFromServer();
        const indicator = document.getElementById('sync-indicator');
        if (indicator) {{ indicator.style.display = ''; }}
        const exportBtn = document.getElementById('btn-export-fav');
        if (exportBtn) {{ exportBtn.textContent = '📥 导出'; exportBtn.title = '收藏已实时同步到磁盘'; }}
    }}
    const hash = window.location.hash.slice(1);
    switchTab(hash || 'AI');
}})();
</script>
</body>
</html>'''
    return html


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='生成公众号日报 HTML 页面')
    parser.add_argument('--date', help='日期 YYYY-MM-DD, 默认今天')
    parser.add_argument('--output', help='输出路径（默认在日报目录下的 index.html）')
    args = parser.parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, '%Y-%m-%d')
    else:
        from datetime import timezone, timedelta
        tz = timezone(timedelta(hours=8))
        target_date = datetime.now(tz)
    date_str = target_date.strftime('%Y-%m-%d')
    date_dir = os.path.join(SOURCE_ROOT, date_str)

    if not os.path.isdir(date_dir):
        print(f'[ERROR] 目录不存在: {date_dir}')
        sys.exit(1)

    topics = collect_articles(date_dir)
    if not topics:
        print(f'[ERROR] 未找到任何文章')
        sys.exit(1)

    action_exist = os.path.exists(os.path.join(date_dir, '行动建议.md'))

    # Extract briefing from README
    briefing = ''
    readme_path = os.path.join(date_dir, 'README.md')
    if os.path.exists(readme_path):
        try:
            with open(readme_path, 'r', encoding='utf-8') as f:
                readme = f.read()
            # Find briefing line, extract all subsequent > lines (blockquote)
            lines = readme.split('\n')
            in_briefing = False
            briefing_lines = []
            for line in lines:
                if '📋' in line and '简报' in line:
                    in_briefing = True
                    continue
                if in_briefing:
                    if line.startswith('> '):
                        text = line[2:].strip()
                        if text:
                            briefing_lines.append(text)
                    elif not line.startswith('>') and briefing_lines:
                        break  # End of blockquote
            briefing = ' '.join(briefing_lines)
        except:
            pass

    html = generate_html(date_str, topics, action_exist, briefing)

    out_path = args.output or os.path.join(date_dir, 'index.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    # 同时生成 article.html（Markdown 渲染阅读器）
    article_html_path = os.path.join(date_dir, 'article.html')
    if not os.path.exists(article_html_path):
        generate_article_viewer(article_html_path)

    total = sum(len(v) for v in topics.values())
    print(f'✓ HTML 生成完成: {out_path}')
    print(f'  共 {total} 篇文章, {len(topics)} 个主题')


if __name__ == '__main__':
    main()
