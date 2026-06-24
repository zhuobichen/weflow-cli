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
            # Skip articles with empty body
            body_part = content.split('## 正文')
            if len(body_part) > 1:
                body_text = body_part[1].strip()
                body_text = ''.join(c for c in body_text if c not in ' \n\r\t')
                if len(body_text) < 30:
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
<script>
// Simple MD5 implementation for image fallback
function md5(string) {
  function md5cycle(x, k) {
    var a = x[0], b = x[1], c = x[2], d = x[3];
    a = ff(a, b, c, d, k[0], 7, -680876936);d = ff(d, a, b, c, k[1], 12, -389564586);c = ff(c, d, a, b, k[2], 17, 606105819);b = ff(b, c, d, a, k[3], 22, -1044525330);a = ff(a, b, c, d, k[4], 7, -176418897);d = ff(d, a, b, c, k[5], 12, 1200080426);c = ff(c, d, a, b, k[6], 17, -1473231341);b = ff(b, c, d, a, k[7], 22, -45705983);a = ff(a, b, c, d, k[8], 7, 1770035416);d = ff(d, a, b, c, k[9], 12, -1958414417);c = ff(c, d, a, b, k[10], 17, -42063);b = ff(b, c, d, a, k[11], 22, -1990404162);a = ff(a, b, c, d, k[12], 7, 1804603682);d = ff(d, a, b, c, k[13], 12, -40341101);c = ff(c, d, a, b, k[14], 17, -1502002290);b = ff(b, c, d, a, k[15], 22, 1236535329);a = gg(a, b, c, d, k[1], 5, -165796510);d = gg(d, a, b, c, k[6], 9, -1069501632);c = gg(c, d, a, b, k[11], 14, 643717713);b = gg(b, c, d, a, k[0], 20, -373897302);a = gg(a, b, c, d, k[5], 5, -701558691);d = gg(d, a, b, c, k[10], 9, 38016083);c = gg(c, d, a, b, k[15], 14, -660478335);b = gg(b, c, d, a, k[4], 20, -405537848);a = gg(a, b, c, d, k[9], 5, 568446438);d = gg(d, a, b, c, k[14], 9, -1019803690);c = gg(c, d, a, b, k[3], 14, -187363961);b = gg(b, c, d, a, k[8], 20, 1163531501);a = gg(a, b, c, d, k[13], 5, -1444681467);d = gg(d, a, b, c, k[2], 9, -51403784);c = gg(c, d, a, b, k[7], 14, 1735328473);b = gg(b, c, d, a, k[12], 20, -1926607734);a = hh(a, b, c, d, k[5], 4, -378558);d = hh(d, a, b, c, k[8], 11, -2022574463);c = hh(c, d, a, b, k[11], 16, 1839030562);b = hh(b, c, d, a, k[14], 23, -35309556);a = hh(a, b, c, d, k[1], 4, -1530992060);d = hh(d, a, b, c, k[4], 11, 1272893353);c = hh(c, d, a, b, k[7], 16, -155497632);b = hh(b, c, d, a, k[10], 23, -1094730640);a = hh(a, b, c, d, k[13], 4, 681279174);d = hh(d, a, b, c, k[0], 11, -358537222);c = hh(c, d, a, b, k[3], 16, -722521979);b = hh(b, c, d, a, k[6], 23, 76029189);a = hh(a, b, c, d, k[9], 4, -640364487);d = hh(d, a, b, c, k[12], 11, -421815835);c = hh(c, d, a, b, k[15], 16, 530742520);b = hh(b, c, d, a, k[2], 23, -995338651);a = ii(a, b, c, d, k[0], 6, -198630844);d = ii(d, a, b, c, k[7], 10, 1126891415);c = ii(c, d, a, b, k[14], 15, -1416354905);b = ii(b, c, d, a, k[5], 21, -57434055);a = ii(a, b, c, d, k[12], 6, 1700485571);d = ii(d, a, b, c, k[3], 10, -1894986606);c = ii(c, d, a, b, k[10], 15, -1051523);b = ii(b, c, d, a, k[1], 21, -2054922799);a = ii(a, b, c, d, k[8], 6, 1873313359);d = ii(d, a, b, c, k[15], 10, -30611744);c = ii(c, d, a, b, k[6], 15, -1560198380);b = ii(b, c, d, a, k[13], 21, 1309151649);a = ii(a, b, c, d, k[4], 6, -145523070);d = ii(d, a, b, c, k[11], 10, -1120210379);c = ii(c, d, a, b, k[2], 15, 718787259);b = ii(b, c, d, a, k[9], 21, -343485551);x[0] = add32(a, x[0]);x[1] = add32(b, x[1]);x[2] = add32(c, x[2]);x[3] = add32(d, x[3]);
  }
  function cmn(q, a, b, x, s, t) {a = add32(add32(a, q), add32(x, t));return add32((a << s) | (a >>> (32 - s)), b);}
  function ff(a, b, c, d, x, s, t) { return cmn((b & c) | ((~b) & d), a, b, x, s, t); }
  function gg(a, b, c, d, x, s, t) { return cmn((b & d) | (c & (~d)), a, b, x, s, t); }
  function hh(a, b, c, d, x, s, t) { return cmn(b ^ c ^ d, a, b, x, s, t); }
  function ii(a, b, c, d, x, s, t) { return cmn(c ^ (b | (~d)), a, b, x, s, t); }
  function md5blk(s) {var md5blks = [], i;for (i = 0; i < 64; i += 4) {md5blks[i >> 2] = s.charCodeAt(i) + (s.charCodeAt(i + 1) << 8) + (s.charCodeAt(i + 2) << 16) + (s.charCodeAt(i + 3) << 24);}return md5blks;}
  function add32(a, b) { return (a + b) & 0xFFFFFFFF; }
  function rhex(n) {var s = '', j = 0;for (; j < 4; j++)s += '0123456789abcdef'.charAt((n >> (j * 8 + 4)) & 0x0F) + '0123456789abcdef'.charAt((n >> (j * 8)) & 0x0F);return s;}
  function hex(x) {for (var i = 0; i < x.length; i++)x[i] = rhex(x[i]);return x.join('');}
  var n = string.length, state = [1732584193, -271733879, -1732584194, 271733878], i;
  for (i = 64; i <= n; i += 64) {md5cycle(state, md5blk(string.substring(i - 64, i)));}
  string = string.substring(i - 64);
  var tail = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0];
  for (i = 0; i < string.length; i++)tail[i >> 2] |= string.charCodeAt(i) << ((i % 4) << 3);
  tail[i >> 2] |= 0x80 << ((i % 4) << 3);
  if (i > 55) {md5cycle(state, tail);for (i = 0; i < 16; i++)tail[i] = 0;}
  tail[14] = n * 8;
  md5cycle(state, tail);
  return hex(state);
}
</script>
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
/* ---- Highlight & Notes ---- */
.article-body mark.hl { background: #fef08a; color: #1e1b4b; padding: 1px 0; border-radius: 2px; cursor: pointer; transition: all .2s; }
.article-body mark.hl:hover { background: #fde047; }
.article-body mark.hl.active { background: #fbbf24; box-shadow: 0 0 0 2px #fbbf2440; }
.selection-popup { position: absolute; z-index: 1000; background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 6px 10px; display: none; box-shadow: 0 8px 24px #00000040; }
.selection-popup button { background: #818cf8; color: #fff; border: none; padding: 6px 14px; border-radius: 7px; cursor: pointer; font-size: 13px; font-weight: 600; white-space: nowrap; }
.selection-popup button:hover { background: #6366f1; }
.note-modal-overlay { position: fixed; inset: 0; background: #00000060; z-index: 2000; display: flex; align-items: center; justify-content: center; }
.note-modal { background: #1e293b; border: 1px solid #334155; border-radius: 14px; padding: 24px; width: 90%; max-width: 480px; box-shadow: 0 16px 48px #00000060; }
.note-modal h3 { color: #e2e8f0; font-size: 16px; margin-bottom: 12px; }
.note-modal .hl-text { background: #1a1040; border: 1px solid #312e81; border-radius: 8px; padding: 10px 14px; color: #a5b4fc; font-size: 13px; margin-bottom: 14px; max-height: 100px; overflow-y: auto; line-height: 1.6; }
.note-modal textarea { width: 100%; height: 80px; background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; padding: 10px 14px; font-size: 13px; resize: vertical; font-family: inherit; outline: none; }
.note-modal textarea:focus { border-color: #818cf8; }
.note-modal .btn-row { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
.note-modal .btn-row button { padding: 7px 18px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; }
.note-modal .btn-save { background: #818cf8; color: #fff; }
.note-modal .btn-save:hover { background: #6366f1; }
.note-modal .btn-cancel { background: #334155; color: #94a3b8; }
.note-modal .btn-cancel:hover { background: #475569; }
.notes-sidebar { position: fixed; right: 0; top: 0; width: 340px; height: 100vh; background: #0f172a; border-left: 1px solid #1e293b; z-index: 500; overflow-y: auto; transform: translateX(100%); transition: transform .3s ease; }
.notes-sidebar.open { transform: translateX(0); }
.notes-sidebar-header { padding: 16px 20px; border-bottom: 1px solid #1e293b; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; background: #0f172a; z-index: 2; }
.notes-sidebar-header h3 { color: #e2e8f0; font-size: 15px; font-weight: 700; }
.notes-sidebar-header button { background: none; border: none; color: #64748b; font-size: 18px; cursor: pointer; }
.notes-sidebar-header button:hover { color: #e2e8f0; }
.note-item { padding: 14px 20px; border-bottom: 1px solid #1e293b; }
.note-item .hl-text { color: #fef08a; font-size: 13px; line-height: 1.6; margin-bottom: 8px; border-left: 3px solid #fbbf24; padding-left: 10px; }
.note-item .hl-note { color: #cbd5e1; font-size: 13px; line-height: 1.6; margin-bottom: 6px; }
.note-item .hl-meta { color: #475569; font-size: 11px; display: flex; align-items: center; gap: 10px; }
.note-item .hl-meta button { background: none; border: none; color: #dc5b5b; font-size: 11px; cursor: pointer; }
.note-item .hl-meta button:hover { color: #f87171; }
.notes-sidebar .empty { text-align: center; padding: 60px 20px; color: #475569; font-size: 14px; line-height: 2; }
.btn-notes-toggle { position: fixed; right: 16px; bottom: 24px; z-index: 501; background: #818cf8; color: #fff; border: none; width: 44px; height: 44px; border-radius: 12px; font-size: 18px; cursor: pointer; box-shadow: 0 4px 16px #818cf840; transition: all .2s; display: flex; align-items: center; justify-content: center; }
.btn-notes-toggle:hover { background: #6366f1; transform: scale(1.05); }
.btn-notes-toggle .badge { position: absolute; top: -4px; right: -4px; background: #ef4444; color: #fff; font-size: 10px; width: 18px; height: 18px; border-radius: 9px; display: flex; align-items: center; justify-content: center; font-weight: 700; }
@media (max-width: 640px) { .notes-sidebar { width: 100%; } }
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
    // 本地模式：通过代理加载微信CDN图片，绕过防盗链
    if (location.hostname === 'localhost' || location.hostname === '127.0.0.1') {
      htmlContent = htmlContent.replace(/src="(https?:\/\/[^"]*\.qpic\.cn\/[^"]+)"/g, function(match, url) {
        // 还原 marked.js 的 HTML 转义
        var realUrl = url.replace(/&amp;/g, '&');
        // 计算本地图片路径（与 biz_daily.py 中的 hash 算法一致）
        var hash = md5(realUrl).substring(0, 12);
        var ext = '.jpg';
        if (realUrl.indexOf('.png') !== -1 || realUrl.indexOf('wx_fmt=png') !== -1) ext = '.png';
        else if (realUrl.indexOf('.gif') !== -1 || realUrl.indexOf('wx_fmt=gif') !== -1) ext = '.gif';
        else if (realUrl.indexOf('.webp') !== -1 || realUrl.indexOf('wx_fmt=webp') !== -1) ext = '.webp';
        var localPath = 'images/' + hash + ext;
        // 优先用本地图片，不存在时走代理
        if (window._IMG_MAP && window._IMG_MAP[realUrl]) {
          return 'src="' + localPath + '"';
        }
        return 'src="/proxy?url=' + encodeURIComponent(realUrl) + '" onerror="this.onerror=null;this.src=\'' + localPath + '\'"';
      });
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
      '<div style="text-align:center;padding:40px;color:#475569;font-size:12px;">\u2014 END \u2014</div>' +
      '<div class="notes-sidebar" id="notes-sidebar">' +
        '<div class="notes-sidebar-header"><h3>\ud83d\udcdd \u7b14\u8bb0</h3><button onclick="toggleNotes()">\u2715</button></div>' +
        '<div id="notes-list"><div class="empty">\u9009\u4e2d\u6587\u5b57\u540e\u70b9\u51fb\u201c\u6807\u8bb0\u7b14\u8bb0\u201d\u5373\u53ef\u6dfb\u52a0</div></div>' +
      '</div>' +
      '<button class="btn-notes-toggle" id="btn-notes-toggle" onclick="toggleNotes()" title="\u7b14\u8bb0">\ud83d\udcdd<span class="badge" id="notes-badge" style="display:none">0</span></button>';
  } catch(e) {
    document.getElementById('app').innerHTML =
      '<div class="error-box">\u274c \u52a0\u8f7d\u5931\u8d25: ' + e.message + '<br><br><a href="./" class="btn-back">\u2190 \u8fd4\u56de\u5217\u8868</a></div>';
  }
})();

// ---- Notes / Highlight Feature ----
const NOTES_API = (location.hostname === 'localhost' || location.hostname === '127.0.0.1') ? '/api/notes' : null;
const ARTICLE_ID = file;
let allHighlights = [];
let hlPopup = null, hlModal = null;

function initNotesUI() {
  hlPopup = document.createElement('div');
  hlPopup.className = 'selection-popup';
  hlPopup.innerHTML = '<button onclick="startNote()">\ud83d\udcdd \u6807\u8bb0\u7b14\u8bb0</button>';
  document.body.appendChild(hlPopup);

  document.addEventListener('mouseup', (e) => {
    setTimeout(() => {
      const sel = window.getSelection();
      const text = sel ? sel.toString().trim() : '';
      if (!text || text.length < 2 || text.length > 500) {
        hlPopup.style.display = 'none';
        return;
      }
      const range = sel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      hlPopup.style.display = 'block';
      hlPopup.style.left = Math.min(rect.left + rect.width/2 - 50, window.innerWidth - 180) + 'px';
      hlPopup.style.top = (rect.bottom + window.scrollY + 8) + 'px';
    }, 10);
  });

  document.addEventListener('mousedown', (e) => {
    if (!hlPopup.contains(e.target)) hlPopup.style.display = 'none';
  });

  if (NOTES_API) loadHighlights();
}

function startNote() {
  const sel = window.getSelection();
  const text = sel ? sel.toString().trim() : '';
  if (!text) return;
  hlPopup.style.display = 'none';
  if (hlModal) hlModal.remove();

  hlModal = document.createElement('div');
  hlModal.className = 'note-modal-overlay';
  hlModal.innerHTML =
    '<div class="note-modal">' +
      '<h3>\ud83d\udcdd \u6dfb\u52a0\u7b14\u8bb0</h3>' +
      '<div class="hl-text">' + text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</div>' +
      '<textarea id="note-input" placeholder="\u5199\u4e0b\u4f60\u7684\u611f\u60f3\u6216\u5907\u6ce8\uff08\u53ef\u9009\uff09"></textarea>' +
      '<div class="btn-row">' +
        '<button class="btn-cancel" onclick="this.closest(\'.note-modal-overlay\').remove()">\u53d6\u6d88</button>' +
        '<button class="btn-save" onclick="saveNote()">\u4fdd\u5b58</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(hlModal);
  setTimeout(() => document.getElementById('note-input').focus(), 100);
}

async function saveNote() {
  const noteText = document.getElementById('note-input').value.trim();
  const sel = window.getSelection();
  const hlText = sel ? sel.toString().trim() : '';
  if (!hlText) return;
  if (!NOTES_API) {
    const local = JSON.parse(localStorage.getItem('weflow_hl_' + ARTICLE_ID) || '[]');
    local.push({id: Date.now().toString(36), text: hlText, note: noteText, created: new Date().toLocaleString()});
    localStorage.setItem('weflow_hl_' + ARTICLE_ID, JSON.stringify(local));
    hlModal.remove();
    loadHighlights();
    return;
  }
  try {
    const res = await fetch(NOTES_API, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({article: ARTICLE_ID, text: hlText, note: noteText})});
    await res.json();
    hlModal.remove();
    loadHighlights();
  } catch(e) { alert('\u4fdd\u5b58\u5931\u8d25: ' + e.message); }
}

async function loadHighlights() {
  if (NOTES_API) {
    try {
      const res = await fetch(NOTES_API + '?article=' + encodeURIComponent(ARTICLE_ID));
      allHighlights = await res.json();
    } catch(e) { allHighlights = []; }
  } else {
    allHighlights = JSON.parse(localStorage.getItem('weflow_hl_' + ARTICLE_ID) || '[]');
  }
  renderHighlights();
  renderNotesList();
}

function renderHighlights() {
  document.querySelectorAll('.article-body mark.hl').forEach(m => {
    const parent = m.parentNode;
    parent.replaceChild(document.createTextNode(m.textContent), m);
    parent.normalize();
  });
  if (allHighlights.length === 0) return;

  const body = document.querySelector('.article-body');
  if (!body) return;

  // 收集所有文本节点及其在拼接字符串中的偏移
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, null, false);
  const textNodes = [];
  let node;
  while (node = walker.nextNode()) {
    if (node.textContent.length > 0) textNodes.push(node);
  }

  // 构建拼接字符串和偏移映射
  let fullText = '';
  const offsets = []; // [{node, start, end}]
  textNodes.forEach(tn => {
    offsets.push({node: tn, start: fullText.length, end: fullText.length + tn.textContent.length});
    fullText += tn.textContent;
  });

  // 对每个高亮，在拼接字符串中查找并跨节点标记
  allHighlights.forEach(hl => {
    const searchText = hl.text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    // 尝试精确匹配，再尝试去除空白差异的模糊匹配
    let idx = fullText.indexOf(searchText);
    if (idx < 0) {
      // 模糊匹配：将搜索文本和全文的连续空白归一化为单空格
      const normFull = fullText.replace(/\s+/g, ' ');
      const normSearch = searchText.replace(/\s+/g, ' ');
      idx = normFull.indexOf(normSearch);
      if (idx < 0) return; // 找不到，跳过
      // 需要从 normFull 的 idx 映射回 fullText 的真实 idx
      let fi = 0, ni = 0;
      while (ni < idx) {
        if (/\s/.test(fullText[fi])) {
          while (fi < fullText.length && /\s/.test(fullText[fi])) fi++;
          ni++;
        } else {
          fi++; ni++;
        }
      }
      idx = fi;
    }
    const hlEnd = idx + searchText.length;

    // 找出涉及的文本节点并逐段标记
    for (const off of offsets) {
      if (off.end <= idx || off.start >= hlEnd) continue; // 不重叠
      const segStart = Math.max(idx, off.start) - off.start;
      const segEnd = Math.min(hlEnd, off.end) - off.start;
      try {
        const range = document.createRange();
        range.setStart(off.node, segStart);
        range.setEnd(off.node, segEnd);
        const mark = document.createElement('mark');
        mark.className = 'hl';
        mark.setAttribute('data-hid', hl.id);
        mark.title = hl.note || hl.text;
        mark.onclick = function(e) {
          if (hl.note) alert('\u7b14\u8bb0: ' + hl.note);
        };
        range.surroundContents(mark);
      } catch(e) {
        // 跨元素选区可能失败，静默跳过
      }
    }
  });
}

function renderNotesList() {
  const container = document.getElementById('notes-list');
  const badge = document.getElementById('notes-badge');
  if (!container) return;
  if (allHighlights.length === 0) {
    container.innerHTML = '<div class="empty">\u9009\u4e2d\u6587\u5b57\u540e\u70b9\u51fb\u201c\u6807\u8bb0\u7b14\u8bb0\u201d\u5373\u53ef\u6dfb\u52a0</div>';
    if (badge) badge.style.display = 'none';
    return;
  }
  if (badge) { badge.style.display = ''; badge.textContent = allHighlights.length; }
  container.innerHTML = allHighlights.map(hl =>
    '<div class="note-item">' +
      '<div class="hl-text">' + hl.text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</div>' +
      (hl.note ? '<div class="hl-note">\ud83d\udcac ' + hl.note.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</div>' : '') +
      '<div class="hl-meta"><span>' + (hl.created || '') + '</span>' +
        '<button onclick="deleteNote(\'' + hl.id + '\')">\u5220\u9664</button>' +
      '</div>' +
    '</div>'
  ).join('');
}

async function deleteNote(hid) {
  if (!confirm('\u5220\u9664\u8fd9\u6761\u7b14\u8bb0\uff1f')) return;
  if (NOTES_API) {
    try {
      await fetch(NOTES_API + '/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({article: ARTICLE_ID, id: hid})});
    } catch(e) {}
  } else {
    allHighlights = allHighlights.filter(h => h.id !== hid);
    localStorage.setItem('weflow_hl_' + ARTICLE_ID, JSON.stringify(allHighlights));
  }
  loadHighlights();
}

function toggleNotes() {
  const sidebar = document.getElementById('notes-sidebar');
  if (sidebar) sidebar.classList.toggle('open');
}

setTimeout(initNotesUI, 500);
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
                        <button class="btn-toggle" onclick="event.stopPropagation();toggleRead('{escape_html(art['rel_path'])}')">
                           <span class="toggle-label"></span>
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
.card.read {{ opacity: .6; }}
.card.read:hover {{ opacity: .85; }}

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
    if (IS_LOCALHOST) fetch('/api/read/toggle', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id}})}}).catch(()=>{{}});
}}
function markUnread(id) {{
    const state = getState();
    delete state[id];
    saveState(state);
    applyState();
    if (IS_LOCALHOST) fetch('/api/read/toggle', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id}})}}).catch(()=>{{}});
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
    sessionStorage.setItem('weflow_tab_{date_str}', topic);
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
        const card = Array.from(document.querySelectorAll('.card')).find(c => c.getAttribute('data-id') === id);
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

    // 点击卡片空白处切���已读/未读
    document.addEventListener('click', (e) => {{
        const card = e.target.closest('.card');
        if (card && !e.target.closest('a, button')) {{
            const id = card.getAttribute('data-id');
            if (id) toggleRead(id);
        }}
        const link = e.target.closest('.card-title a, .btn-read');
        if (link) sessionStorage.setItem(KEY, String(window.scrollY));
    }});
    if (IS_LOCALHOST) {{
        await loadFavFromServer();
        // 合并服务端已读状态（只补充，不覆盖本地）
        try {{
            const res = await fetch('/api/read/list');
            const serverState = await res.json();
            const local = getState();
            for (const [k,v] of Object.entries(serverState)) {{
                if (!local[k]) local[k] = true;  // 只补充，不删除
            }}
            // 本地���也同步到服务端
            for (const [k,v] of Object.entries(local)) {{
                if (!(k in serverState)) {{
                    fetch('/api/read/toggle', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:k}})}}).catch(()=>{{}});
                }}
            }}
            saveState(local);
        }} catch(e) {{}}
        const indicator = document.getElementById('sync-indicator');
        if (indicator) {{ indicator.style.display = ''; }}
        const exportBtn = document.getElementById('btn-export-fav');
        if (exportBtn) {{ exportBtn.textContent = '📥 导出'; exportBtn.title = '收藏已实时同步到磁盘'; }}
    }}
    // 无论是否 localhost，都应用已读状态到界面
    applyState();
    const savedTab = sessionStorage.getItem('weflow_tab_{date_str}');
    const hash = window.location.hash.slice(1);
    switchTab(hash || savedTab || 'AI');
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
        import shutil
        # 优先使用永久模板（不会被日期覆盖）
        template = os.path.join(SOURCE_ROOT, '.template', 'article.html')
        # 兼容旧路径
        if not os.path.exists(template):
            template_old = os.path.join(SOURCE_ROOT, '2026-05-19', 'article.html')
            if os.path.exists(template_old):
                template = template_old
        if os.path.exists(template):
            shutil.copy2(template, article_html_path)
        else:
            generate_article_viewer(article_html_path)

    # 注入本地图片映射，优先用本地图片避免代理延迟
    image_map_path = os.path.join(date_dir, '.image_map.json')
    if os.path.exists(image_map_path):
        with open(image_map_path, 'r', encoding='utf-8') as f:
            img_map = json.load(f)
        if img_map and os.path.exists(article_html_path):
            with open(article_html_path, 'r', encoding='utf-8') as f:
                article_html = f.read()
            img_map_js = '<script>window._IMG_MAP=' + json.dumps(img_map, ensure_ascii=False) + ';</script>'
            # 注入到 </head> 之前
            article_html = article_html.replace('</head>', img_map_js + '\n</head>', 1)
            with open(article_html_path, 'w', encoding='utf-8') as f:
                f.write(article_html)

    total = sum(len(v) for v in topics.values())
    print(f'✓ HTML 生成完成: {out_path}')
    print(f'  共 {total} 篇文章, {len(topics)} 个主题')


if __name__ == '__main__':
    main()
