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
            tags_html = ''.join(f'<span class="tag">{escape_html(t)}</span>' for t in (art['tags'] or [])[:5])
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
                                <a href="{escape_html(art['rel_path'])}" target="_blank"
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
                        <a href="{escape_html(art['rel_path'])}" target="_blank"
                           class="btn-read" onclick="markRead('{escape_html(art['rel_path'])}')">
                           📖 阅读
                        </a>
                        <button class="btn-toggle" onclick="toggleRead('{escape_html(art['rel_path'])}')">
                           <span class="toggle-label">✓ 标记已读</span>
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
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", "PingFang SC", sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
}}
.container {{ max-width: 960px; margin: 0 auto; padding: 20px; }}

/* Header */
.header {{
    text-align: center;
    padding: 40px 20px 30px;
    border-bottom: 1px solid #1e293b;
    margin-bottom: 24px;
}}
.header h1 {{
    font-size: 28px;
    font-weight: 700;
    background: linear-gradient(135deg, #818cf8, #c084fc);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}}
.header .meta {{
    margin-top: 8px;
    color: #94a3b8;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 16px;
    flex-wrap: wrap;
}}
.action-link {{
    color: #fbbf24;
    text-decoration: none;
    font-weight: 600;
    padding: 4px 12px;
    border: 1px solid #fbbf24;
    border-radius: 20px;
    font-size: 13px;
    transition: all .2s;
}}
.action-link:hover {{ background: #fbbf241a; }}

/* Briefing */
.briefing {{
  background: linear-gradient(135deg, #1a1040, #0d1b3e);
  border: 1px solid #2d2d6e;
  border-radius: 14px;
  padding: 20px 24px;
  margin-bottom: 16px;
}}
.briefing-title {{
  font-size: 15px;
  font-weight: 700;
  color: #a5b4fc;
  margin-bottom: 10px;
}}
.briefing-text {{
  font-size: 14px;
  line-height: 1.9;
  color: #cbd5e1;
}}

/* Stats bar */
.stats-bar {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 24px;
    padding: 12px 20px;
    background: #1e293b;
    border-radius: 12px;
    font-size: 14px;
    color: #94a3b8;
}}
.stats-bar strong {{ color: #e2e8f0; }}
.btn-clear {{
    background: #334155;
    color: #cbd5e1;
    border: none;
    padding: 6px 14px;
    border-radius: 8px;
    cursor: pointer;
    font-size: 13px;
    transition: background .2s;
}}
.btn-clear:hover {{ background: #475569; }}

/* Tabs */
.tabs {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 24px;
    position: sticky;
    top: 12px;
    z-index: 10;
    padding: 8px 0;
    background: #0f172a;
}}
.tab-btn {{
    background: #1e293b;
    color: #94a3b8;
    border: 2px solid transparent;
    padding: 8px 16px;
    border-radius: 10px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    transition: all .2s;
}}
.tab-btn:hover {{ color: #e2e8f0; background: #334155; }}
.tab-btn.active {{
    color: #e2e8f0;
    background: #1e293b;
    border-color: var(--accent);
}}
.tab-btn .count {{
    background: #334155;
    padding: 1px 7px;
    border-radius: 10px;
    font-size: 12px;
    margin-left: 4px;
}}

/* Topic sections */
.topic-section {{ display: none; }}
.topic-section.active {{ display: block; }}
.topic-heading {{
    font-size: 20px;
    font-weight: 700;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
}}
.topic-heading::after {{
    content: '';
    flex: 1;
    height: 1px;
    background: #1e293b;
    margin-left: 8px;
}}

/* Cards */
.cards-grid {{
    display: flex;
    flex-direction: column;
    gap: 12px;
}}
.card {{
    background: #1e293b;
    border-radius: 12px;
    padding: 16px 20px;
    border: 1px solid #334155;
    transition: all .2s;
    position: relative;
}}
.card:hover {{ border-color: #475569; }}
.card.read {{
    opacity: 0.55;
    border-color: #1e293b;
}}
.card.read:hover {{ opacity: 0.7; border-color: #334155; }}

.card-header {{ margin-bottom: 8px; }}
.card-title-row {{
    display: flex;
    align-items: center;
    gap: 8px;
}}
.unread-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #3b82f6;
    flex-shrink: 0;
    transition: background .3s;
}}
.card.read .unread-dot {{ background: #475569; }}
.card-title {{
    font-size: 15px;
    font-weight: 600;
    flex: 1;
    line-height: 1.4;
}}
.card-title a {{
    color: #e2e8f0;
    text-decoration: none;
    transition: color .2s;
}}
.card-title a:hover {{ color: #818cf8; }}
.card.read .card-title a {{ color: #94a3b8; }}

.card-meta {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 4px;
    font-size: 12px;
    color: #64748b;
}}
.source {{ color: #818cf8; }}

.relevance {{
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    font-weight: 600;
    flex-shrink: 0;
}}
.relevance.high {{ background: #dc262620; color: #f87171; border: 1px solid #dc262640; }}
.relevance.mid {{ background: #f59e0b20; color: #fbbf24; border: 1px solid #f59e0b40; }}

.card-tags {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }}
.tag {{
    font-size: 11px;
    padding: 2px 8px;
    background: #334155;
    border-radius: 6px;
    color: #94a3b8;
}}

.card-preview {{
    font-size: 13px;
    color: #64748b;
    line-height: 1.6;
    margin-bottom: 10px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
}}

.card-actions {{
    display: flex;
    gap: 8px;
    align-items: center;
}}
.btn-read {{
    font-size: 12px;
    color: #818cf8;
    text-decoration: none;
    padding: 4px 12px;
    border: 1px solid #818cf840;
    border-radius: 6px;
    transition: all .2s;
}}
.btn-read:hover {{ background: #818cf820; }}
.btn-toggle {{
    font-size: 12px;
    color: #64748b;
    background: none;
    border: 1px solid #334155;
    padding: 4px 12px;
    border-radius: 6px;
    cursor: pointer;
    transition: all .2s;
}}
.btn-toggle:hover {{ color: #e2e8f0; border-color: #475569; }}
.card.read .btn-toggle {{ color: #10b981; border-color: #10b98140; }}
.card.read .toggle-label::before {{ content: '↩ 标记未读'; }}
.toggle-label::before {{ content: '✓ 标记已读'; }}

/* Footer */
.footer {{
    text-align: center;
    padding: 30px 20px;
    color: #475569;
    font-size: 12px;
    margin-top: 20px;
    border-top: 1px solid #1e293b;
}}

/* Search */
.search-box {{
    width: 100%;
    max-width: 400px;
    margin: 0 auto 16px;
    display: block;
    padding: 10px 16px;
    border-radius: 10px;
    border: 1px solid #334155;
    background: #1e293b;
    color: #e2e8f0;
    font-size: 14px;
    outline: none;
    transition: border-color .2s;
}}
.search-box:focus {{ border-color: #818cf8; }}
.search-box::placeholder {{ color: #475569; }}

@media (max-width: 640px) {{
    .container {{ padding: 12px; }}
    .header h1 {{ font-size: 22px; }}
    .card {{ padding: 12px 14px; }}
    .tabs {{ gap: 4px; }}
    .tab-btn {{ padding: 6px 10px; font-size: 13px; }}
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
    </div>
</div>

    {briefing_html}

<div class="stats-bar">
    <span>📊 <strong id="unread-count">-</strong> 篇未读 / {total} 篇</span>
    <span style="color:#334155">|</span>
    <button class="btn-clear" onclick="markAllRead()">✅ 全部标为已读</button>
    <button class="btn-clear" onclick="clearAll()">🔄 重置所有标记</button>
</div>

<input type="text" class="search-box" placeholder="🔍 搜索文章标题、来源、标签..." oninput="doSearch(this.value)">

<nav class="tabs">
    {''.join(nav_items)}
</nav>

{''.join(topic_sections)}

<div class="footer">
    生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 阅读状态保存在浏览器中
</div>

</div>

<script>
const STORAGE_KEY = 'weflow_read_{date_str}';
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
    document.getElementById('unread-count').textContent = unread;
}}
function switchTab(topic) {{
    document.querySelectorAll('.topic-section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
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
    // Show all sections when searching
    if (q) {{
        document.querySelectorAll('.topic-section').forEach(s => s.classList.add('active'));
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    }} else {{
        switchTab(window.location.hash.slice(1) || 'AI');
    }}
}}

// Init
(function() {{
    applyState();
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
    total = sum(len(v) for v in topics.values())
    print(f'✓ HTML 生成完成: {out_path}')
    print(f'  共 {total} 篇文章, {len(topics)} 个主题')


if __name__ == '__main__':
    main()
