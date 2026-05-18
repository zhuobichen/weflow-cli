#!/usr/bin/env python3
"""
年度报告生成器 — 汇总全年聊天 + 公众号数据，生成精美 HTML 报告。

用法:
  python scripts/annual_report.py 2026
  python scripts/annual_report.py 2026 --output report.html --share

输出: output/annual-report-2026.html
"""

import sys, os, json, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict, Counter

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print(json.dumps({"error": "请安装: pip install sqlcipher3"}))
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import load_config, decrypt_lock, parse_frontmatter, call_deepseek

TZ = timezone(timedelta(hours=8))
OUTPUT_ROOT = 'output'


# ====== Database ======

def open_db(db_path, key_hex, salt_hex):
    raw_key = f"x'{key_hex}{salt_hex}'"
    conn = sqlcipher.connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    c.execute("SELECT count(*) FROM sqlite_master")
    return conn


def get_name_map(contact_db, contact_key, contact_salt):
    name_map = {}
    if not contact_db or not contact_key or not os.path.exists(contact_db):
        return name_map
    try:
        conn = open_db(contact_db, contact_key, contact_salt)
        c = conn.cursor()
        c.execute("SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username) FROM contact")
        for r in c.fetchall():
            name_map[r[0]] = r[1]
        conn.close()
    except:
        pass
    return name_map


# ====== Data Collection ======

def collect_chat_stats(conn, year, name_map):
    """Collect monthly message stats and top contacts."""
    monthly_msg = [0] * 12
    monthly_chars = [0] * 12
    contact_count = Counter()
    hourly = [0] * 24
    sender_count = Counter()

    c = conn.cursor()
    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        sessions = [r[0] for r in c.fetchall()]
    except:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
        sessions = [r[0] for r in c.fetchall()]

    for talker in sessions[:100]:
        tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        try:
            c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
            if c.fetchone()[0] == 0:
                continue

            c.execute(f'''
                SELECT create_time, real_sender_id, message_content
                FROM "{tbl}"
                WHERE create_time >= ? AND create_time < ?
            ''', (
                int(datetime(year, 1, 1, tzinfo=TZ).timestamp()),
                int(datetime(year + 1, 1, 1, tzinfo=TZ).timestamp()),
            ))

            for ts, sender_id, content in c.fetchall():
                if not isinstance(content, str) or len(content) < 1:
                    continue
                dt = datetime.fromtimestamp(ts, tz=TZ)
                if dt.year != year:
                    continue

                month_idx = dt.month - 1
                monthly_msg[month_idx] += 1
                monthly_chars[month_idx] += len(content)
                hourly[dt.hour] += 1

                talker_name = name_map.get(talker, talker)
                contact_count[talker_name] += 1
                sender_count[str(sender_id)] += 1
        except:
            pass

    # Top contacts (exclude system/self)
    top_contacts = [(name, cnt) for name, cnt in contact_count.most_common(15)
                    if not name.startswith('wxid_')]

    return {
        'monthly_msg': monthly_msg,
        'monthly_chars': monthly_chars,
        'hourly': hourly,
        'top_contacts': top_contacts[:10],
        'total_msgs': sum(monthly_msg),
        'total_chars': sum(monthly_chars),
        'contact_count': len([c for c in contact_count if not c.startswith('wxid_')]),
    }


def collect_article_stats(year):
    """Collect article stats from biz-daily output."""
    biz_dir = Path(OUTPUT_ROOT) / 'biz-daily'
    monthly_topic = [defaultdict(int) for _ in range(12)]
    monthly_count = [0] * 12
    total_articles = 0
    all_sources = Counter()
    all_tags = Counter()

    for date_dir in biz_dir.iterdir():
        if not date_dir.is_dir():
            continue
        try:
            d = datetime.strptime(date_dir.name, '%Y-%m-%d')
            if d.year != year:
                continue
        except:
            continue

        month_idx = d.month - 1
        for topic_dir in date_dir.iterdir():
            if not topic_dir.is_dir() or topic_dir.name.startswith('.'):
                continue
            topic = topic_dir.name
            for md_file in topic_dir.glob('*.md'):
                if md_file.name == 'README.md' or md_file.name.startswith('.'):
                    continue
                try:
                    content = md_file.read_text(encoding='utf-8')
                    meta, _ = parse_frontmatter(content)
                except:
                    continue
                total_articles += 1
                monthly_count[month_idx] += 1
                monthly_topic[month_idx][topic] += 1
                if meta.get('source'):
                    all_sources[meta['source']] += 1
                if meta.get('tags'):
                    for tag in meta['tags']:
                        all_tags[tag] += 1

    # Aggregate topic totals across months
    topic_totals = defaultdict(int)
    for mt in monthly_topic:
        for t, c in mt.items():
            topic_totals[t] += c

    # Monthly topic data for stacked chart
    topics_set = sorted(topic_totals.keys())
    monthly_topic_data = {t: [monthly_topic[i].get(t, 0) for i in range(12)] for t in topics_set}

    return {
        'monthly_count': monthly_count,
        'monthly_topic': monthly_topic_data,
        'topic_totals': dict(topic_totals),
        'total_articles': total_articles,
        'top_sources': all_sources.most_common(8),
        'top_tags': all_tags.most_common(15),
        'active_months': sum(1 for c in monthly_count if c > 0),
    }


def collect_payments(conn, year):
    """Collect payment stats from messages."""
    monthly_income = [0] * 12
    monthly_expense = [0] * 12
    total_count = 0

    c = conn.cursor()
    # Use chatroom "微信支付" or filter by content pattern
    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        sessions = [r[0] for r in c.fetchall()]
    except:
        return None

    pay_talkers = []
    for t in sessions:
        if 'pay' in t.lower() or '支付' in t.lower() or 'weixin' in t.lower() or 'gh_22fbb5a5c21c' in t:
            pay_talkers.append(t)

    for talker in pay_talkers[:5]:
        tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        try:
            c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
            if c.fetchone()[0] == 0:
                continue
            c.execute(f'''
                SELECT create_time, message_content
                FROM "{tbl}"
                WHERE create_time >= ? AND create_time < ?
            ''', (
                int(datetime(year, 1, 1, tzinfo=TZ).timestamp()),
                int(datetime(year + 1, 1, 1, tzinfo=TZ).timestamp()),
            ))

            for ts, content in c.fetchall():
                if not isinstance(content, str):
                    continue
                dt = datetime.fromtimestamp(ts, tz=TZ)
                if dt.year != year:
                    continue

                total_count += 1
                # Extract amount
                import re
                amounts = re.findall(r'[¥￥](\d+\.?\d*)', content)
                for amt in amounts:
                    val = float(amt)
                    month_idx = dt.month - 1
                    if '收入' in content or '入账' in content or '收款' in content:
                        monthly_income[month_idx] += val
                    else:
                        monthly_expense[month_idx] += val
        except:
            pass

    if total_count == 0:
        return None

    return {
        'monthly_income': [round(x, 2) for x in monthly_income],
        'monthly_expense': [round(x, 2) for x in monthly_expense],
        'total_count': total_count,
    }


# ====== AI Summary ======

def generate_ai_summary(stats: dict, api_key: str) -> str:
    """Generate personalized year summary with AI."""
    if not api_key:
        return ''

    prompt = f"""你是一个年度报告生成助手。基于以下数据，为用户的 {stats['year']} 年生成一段 200-300 字的个人年度总结。

数据：
- 聊天消息总数：{stats.get('total_msgs', 0):,}
- 最常联系的人：{', '.join(f'{n}({c}条)' for n, c in stats.get('top_contacts', [])[:5])}
- 公众号文章阅读：{stats.get('total_articles', 0)} 篇
- 文章主题分布：{json.dumps(stats.get('topic_totals', {}), ensure_ascii=False)}
- 最爱公众号：{', '.join(f'{n}' for n, _ in stats.get('top_sources', [])[:5])}
- 活跃时段：{
    f'{stats.get("hourly", [0]*24).index(max(stats.get("hourly", [0]*24)))}点最活跃' if stats.get('hourly') else '无数据'
    }
- 活跃月份：{stats.get('active_months', 0)} 个月有公众号阅读

请用温暖、鼓励的中文写出总结（不要序号或列表，一个连贯的段落），突出你的数字生活亮点。"""

    try:
        return call_deepseek(prompt, api_key, max_tokens=500, timeout=60)
    except:
        return ''


# ====== HTML Generation ======

MONTH_LABELS = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']

TOPIC_COLORS = {
    'AI': '#8b5cf6',
    '学术': '#3b82f6',
    '新闻': '#f59e0b',
    '文学': '#ec4899',
    '投资': '#10b981',
}

PALETTE = [
    '#818cf8', '#c084fc', '#f472b6', '#34d399', '#fbbf24',
    '#60a5fa', '#f87171', '#a78bfa', '#4ade80', '#fb923c',
    '#38bdf8', '#e879f9', '#22d3ee', '#facc15', '#ff6b6b',
    '#45b7d1', '#f8a5c2', '#7bed9f', '#70a1ff', '#eccc68',
]


def color_for(index: int) -> str:
    return PALETTE[index % len(PALETTE)]


def generate_html(year: int, stats: dict, ai_summary: str, share_mode: bool = False) -> str:
    """Generate self-contained HTML report."""

    top_contacts_json = json.dumps(stats.get('top_contacts', [])[:10], ensure_ascii=False)
    monthly_topic_json = json.dumps(stats.get('monthly_topic', {}), ensure_ascii=False)
    monthly_count_json = json.dumps(stats.get('monthly_count', []), ensure_ascii=False)
    topic_totals_json = json.dumps(stats.get('topic_totals', {}), ensure_ascii=False)
    hourly_json = json.dumps(stats.get('hourly', [0] * 24), ensure_ascii=False)
    monthly_msg_json = json.dumps(stats.get('monthly_msg', [0] * 12), ensure_ascii=False)
    payments = stats.get('payments')

    # Payment section
    payment_html = ''
    if payments:
        payment_html = f'''
    <div class="section">
      <h2>💰 财务概览</h2>
      <div class="chart-box"><canvas id="paymentChart"></canvas></div>
    </div>'''

    # AI summary section
    ai_html = ''
    if ai_summary:
        ai_html = f'''
    <div class="section highlight">
      <h2>🤖 AI 年度总结</h2>
      <p class="ai-summary">{ai_summary}</p>
    </div>'''

    # Share mode: card-style layout
    share_class = 'share-mode' if share_mode else ''

    top_sources = stats.get('top_sources', [])[:8]
    top_tags = stats.get('top_tags', [])[:12]

    # Build a robust topic color map with fallback to palette
    topic_keys = list(stats.get('topic_totals', {}).keys())
    topic_color_map = {}
    for i, t in enumerate(topic_keys):
        topic_color_map[t] = TOPIC_COLORS.get(t, PALETTE[i % len(PALETTE)])
    topic_color_map_js = json.dumps(topic_color_map, ensure_ascii=False)

    # For monthly message chart: only show months with data (up to current month)
    monthly_msg = stats.get('monthly_msg', [0]*12)
    now_month = datetime.now(TZ).month
    # Truncate to current month
    display_months = MONTH_LABELS[:now_month]
    display_msg = monthly_msg[:now_month]

    # For article chart: same
    monthly_count = stats.get('monthly_count', [0]*12)
    display_article_months = MONTH_LABELS[:now_month]
    display_article_count = monthly_count[:now_month]

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{year} 年度数字生活报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "PingFang SC", sans-serif;
  background: #0a0a1a;
  color: #e2e8f0;
  min-height: 100vh;
  line-height: 1.6;
}}
.container {{ max-width: 800px; margin: 0 auto; padding: 40px 20px; }}

/* Hero */
.hero {{
  text-align: center;
  padding: 60px 20px 50px;
  position: relative;
}}
.hero .year-badge {{
  font-size: 96px;
  font-weight: 900;
  background: linear-gradient(135deg, #818cf8, #c084fc, #f472b6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  line-height: 1;
}}
.hero .subtitle {{
  font-size: 20px;
  color: #64748b;
  margin-top: 12px;
  font-weight: 300;
  letter-spacing: 4px;
}}
.hero .stats-row {{
  display: flex;
  justify-content: center;
  gap: 40px;
  margin-top: 30px;
  flex-wrap: wrap;
}}
.stat-item {{ text-align: center; }}
.stat-item .num {{
  font-size: 36px;
  font-weight: 800;
  background: linear-gradient(135deg, #e2e8f0, #94a3b8);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}}
.stat-item .label {{ font-size: 13px; color: #475569; margin-top: 4px; }}

/* Sections */
.section {{
  background: #111133;
  border: 1px solid #1e1e4a;
  border-radius: 20px;
  padding: 32px 28px;
  margin-bottom: 24px;
}}
.section.highlight {{
  background: linear-gradient(135deg, #1a1040, #0d1b3e);
  border-color: #2d2d6e;
}}
.section h2 {{
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 24px;
  display: flex;
  align-items: center;
  gap: 8px;
}}
.chart-box {{ max-width: 100%; margin: 0 auto; }}
.chart-box canvas {{ max-height: 300px; }}

/* AI Summary */
.ai-summary {{
  font-size: 16px;
  line-height: 2;
  color: #cbd5e1;
}}

/* Contact list */
.contact-list {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}}
.contact-chip {{
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 500;
}}

/* Tags */
.tag-cloud {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}}
.tag-item {{
  padding: 4px 10px;
  border-radius: 8px;
  font-size: 12px;
  background: #1e1e4a;
  color: #94a3b8;
}}

/* Sources */
.source-list {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 8px;
  margin-top: 8px;
}}
.source-item {{
  padding: 8px 12px;
  border-radius: 10px;
  background: #1a1a40;
  font-size: 12px;
  text-align: center;
  border: 2px solid #334155;
}}
.source-item .cnt {{ font-size: 18px; font-weight: 700; display: block; }}

/* Footer */
.footer {{
  text-align: center;
  padding: 40px 20px;
  color: #334155;
  font-size: 13px;
}}

/* Share mode */
.share-mode .container {{ max-width: 500px; }}
.share-mode .hero .year-badge {{ font-size: 72px; }}
.share-mode .section {{ padding: 24px 20px; }}

@media (max-width: 600px) {{
  .container {{ padding: 20px 14px; }}
  .hero .year-badge {{ font-size: 72px; }}
  .hero .stats-row {{ gap: 20px; }}
  .stat-item .num {{ font-size: 28px; }}
  .section {{ padding: 24px 18px; }}
}}
</style>
</head>
<body>
<div class="container {share_class}">

<div class="hero">
  <div class="year-badge">{year}</div>
  <div class="subtitle">我的数字生活年度报告</div>
  <div class="stats-row">
    <div class="stat-item">
      <div class="num">{stats.get('total_msgs', 0):,}</div>
      <div class="label">条消息</div>
    </div>
    <div class="stat-item">
      <div class="num">{stats.get('total_articles', 0)}</div>
      <div class="label">篇文章</div>
    </div>
    <div class="stat-item">
      <div class="num">{stats.get('contact_count', 0)}</div>
      <div class="label">位联系人</div>
    </div>
  </div>
</div>

{ai_html}

<div class="section">
  <h2>💬 每月消息量</h2>
  <div class="chart-box"><canvas id="msgChart"></canvas></div>
</div>

<div class="section">
  <h2>📚 文章阅读趋势</h2>
  <div class="chart-box"><canvas id="articleChart"></canvas></div>
</div>

<div class="section">
  <h2>🎯 主题分布</h2>
  <div class="chart-box"><canvas id="topicChart"></canvas></div>
</div>

<div class="section">
  <h2>⏰ 活跃时段</h2>
  <div class="chart-box"><canvas id="hourlyChart"></canvas></div>
</div>

<div class="section">
  <h2>👥 最常联系的人</h2>
  <div class="contact-list">
    {''.join(f'<span class="contact-chip" style="background:{color_for(i)}20;color:{color_for(i)}">{n} · {c}条</span>' for i, (n, c) in enumerate(stats.get('top_contacts', [])[:8]))}
  </div>
</div>

<div class="section">
  <h2>📰 最爱公众号</h2>
  <div class="source-list">
    {''.join(f'<div class="source-item" style="border-color:{color_for(i)}40"><span class="cnt" style="color:{color_for(i)}">{c}</span><span style="color:#94a3b8">{n}</span></div>' for i, (n, c) in enumerate(top_sources))}
  </div>
</div>

<div class="section">
  <h2>🏷️ 热门标签</h2>
  <div class="tag-cloud">
    {''.join(f'<span class="tag-item">{t[0]} · {t[1]}</span>' for t in top_tags)}
  </div>
</div>

{payment_html}

<div class="footer">
  由 WeFlow CLI 生成 · {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}
</div>

</div>

<script>
const chartDefaults = {{
  responsive: true,
  maintainAspectRatio: true,
  plugins: {{ legend: {{ labels: {{ color: '#94a3b8', font: {{ size: 12 }} }} }} }},
  scales: {{
    x: {{ ticks: {{ color: '#64748b', font: {{ size: 11 }} }}, grid: {{ color: '#1e1e4a' }} }},
    y: {{ ticks: {{ color: '#64748b', font: {{ size: 11 }} }}, grid: {{ color: '#1e1e4a' }}, beginAtZero: true }},
  }},
}};

// Monthly messages
new Chart(document.getElementById('msgChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(display_months, ensure_ascii=False)},
    datasets: [{{
      label: '消息数',
      data: {json.dumps(display_msg, ensure_ascii=False)},
      backgroundColor: ['#818cf8','#c084fc','#f472b6','#34d399','#fbbf24','#60a5fa','#f87171','#a78bfa','#4ade80','#fb923c','#38bdf8','#facc15'].slice(0, {len(display_msg)}),
      borderWidth: 1,
      borderRadius: 6,
    }}],
  }},
  options: chartDefaults,
}});

// Monthly articles
const topicDatasets = {json.dumps(monthly_topic_json, ensure_ascii=False)};
const topicColors = {topic_color_map_js};
new Chart(document.getElementById('articleChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(display_article_months, ensure_ascii=False)},
    datasets: Object.entries(topicDatasets).map(([topic, data]) => ({{
      label: topic,
      data: data.slice(0, {len(display_article_months)}),
      backgroundColor: (topicColors[topic] || '#64748b') + '80',
      borderColor: topicColors[topic] || '#64748b',
      borderWidth: 1,
      borderRadius: 4,
    }})),
  }},
  options: {{
    ...chartDefaults,
    scales: {{
      ...chartDefaults.scales,
      x: {{ ...chartDefaults.scales.x, stacked: true }},
      y: {{ ...chartDefaults.scales.y, stacked: true, title: {{ display: true, text: '篇', color: '#94a3b8' }} }},
    }},
  }},
}});

// Topic donut
const topicTotals = {json.dumps(topic_totals_json)};
new Chart(document.getElementById('topicChart'), {{
  type: 'doughnut',
  data: {{
    labels: Object.keys(topicTotals),
    datasets: [{{
      data: Object.values(topicTotals),
      backgroundColor: Object.keys(topicTotals).map(function(t) {{ return (topicColors[t] || '#64748b') + 'c0'; }}),
      borderColor: Object.keys(topicTotals).map(function(t) {{ return topicColors[t] || '#64748b'; }}),
      borderWidth: 2,
    }}],
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{
        position: 'bottom',
        labels: {{ color: '#94a3b8', padding: 16, font: {{ size: 13 }} }}
      }},
    }},
  }},
}});

// Hourly activity
new Chart(document.getElementById('hourlyChart'), {{
  type: 'line',
  data: {{
    labels: Array.from({{length: 24}}, (_, i) => i + '时'),
    datasets: [{{
      label: '消息量',
      data: {json.dumps(hourly_json)},
      borderColor: '#818cf8',
      backgroundColor: '#818cf820',
      fill: true,
      tension: 0.4,
      pointRadius: 2,
      pointHoverRadius: 6,
    }}],
  }},
  options: {{
    ...chartDefaults,
    scales: {{
      ...chartDefaults.scales,
      y: {{ ...chartDefaults.scales.y, title: {{ display: true, text: '条', color: '#94a3b8' }} }},
    }},
  }},
}});
'''

    if payments:
        pay_income = json.dumps(payments['monthly_income'], ensure_ascii=False)
        pay_expense = json.dumps(payments['monthly_expense'], ensure_ascii=False)
        html += f'''
// Payment chart
new Chart(document.getElementById('paymentChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(MONTH_LABELS, ensure_ascii=False)},
    datasets: [
      {{ label: '支出', data: {pay_expense}, backgroundColor: '#f8717160', borderColor: '#f87171', borderWidth: 1, borderRadius: 4 }},
      {{ label: '收入', data: {pay_income}, backgroundColor: '#34d39960', borderColor: '#34d399', borderWidth: 1, borderRadius: 4 }},
    ],
  }},
  options: {{
    ...chartDefaults,
    scales: {{
      ...chartDefaults.scales,
      y: {{ ...chartDefaults.scales.y, title: {{ display: true, text: '元', color: '#94a3b8' }} }},
    }},
  }},
}});
'''

    html += '''
</script>
</body>
</html>'''
    return html


# ====== Main ======

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='年度报告生成器')
    parser.add_argument('year', nargs='?', type=int, default=datetime.now(TZ).year,
                        help='年份（默认今年）')
    parser.add_argument('--output', help='输出路径')
    parser.add_argument('--share', action='store_true', help='分享模式（卡片布局）')
    parser.add_argument('--api-key', help='DeepSeek API key')
    parser.add_argument('--skip-ai', action='store_true', help='跳过 AI 总结')
    args = parser.parse_args()

    year = args.year
    config = load_config()
    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '') or config.get('deepseekApiKey', '')

    print(f'📊 正在生成 {year} 年度报告...')

    # Load DB
    nt_db = config.get('ntDbPath', '')
    stats = {'year': year}
    if nt_db:
        nt_key = decrypt_lock(config.get('ntKey', ''))
        nt_salt = config.get('ntSalt', '')
        contact_key = decrypt_lock(config.get('contactKey', ''))
        contact_salt = config.get('contactSalt', '')
        msg_dir = os.path.dirname(nt_db.replace('\\', '/'))
        wxid_dir = os.path.dirname(os.path.dirname(msg_dir))
        contact_db = os.path.join(wxid_dir, 'db_storage', 'contact', 'contact.db')

        name_map = get_name_map(contact_db, contact_key, contact_salt)

        try:
            conn = open_db(nt_db, nt_key, nt_salt)
            chat_stats = collect_chat_stats(conn, year, name_map)
            stats.update(chat_stats)
            print(f'  ✓ 聊天数据: {chat_stats["total_msgs"]:,} 条消息')

            payments = collect_payments(conn, year)
            if payments:
                stats['payments'] = payments
                print(f'  ✓ 支付数据: {payments["total_count"]} 条')
            conn.close()
        except Exception as e:
            print(f'  [WARN] 聊天数据: {e}')

    # Article stats
    try:
        article_stats = collect_article_stats(year)
        stats.update(article_stats)
        print(f'  ✓ 文章数据: {article_stats["total_articles"]} 篇')
    except Exception as e:
        print(f'  [WARN] 文章数据: {e}')

    # AI summary
    ai_summary = ''
    if not args.skip_ai and api_key:
        print('  ⏳ 生成 AI 总结...')
        ai_summary = generate_ai_summary(stats, api_key)
        if ai_summary:
            print('  ✓ AI 总结生成完成')

    # Generate HTML
    html = generate_html(year, stats, ai_summary, share_mode=args.share)
    out_path = args.output or os.path.join(OUTPUT_ROOT, f'annual-report-{year}.html')
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'\n✓ 报告已生成: {out_path}')
    print(f'  文件大小: {len(html) / 1024:.1f} KB')


if __name__ == '__main__':
    main()
