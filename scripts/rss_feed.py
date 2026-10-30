"""RSS 订阅源接入 —— 补充微信 4.2.0.11 之后不再落库的公众号推送。

用法示例：
  python scripts/biz_daily.py --rss-feed https://example.com/feed.xml
  python scripts/biz_daily.py --rss-feed file:///D:/feeds/my-feeds.xml
  python scripts/biz_daily.py --rss-feed https://a.xml --rss-feed https://b.xml
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import re


def load_rss_feeds(path, _warn=print):
    """从 JSON 配置文件读订阅源列表。

    支持的三种写法（都容错，认不出来就跳过那一条）：
        ["https://a.xml", "https://b.xml"]
        {"feeds": ["https://a.xml"]}
        {"feeds": [{"url": "https://a.xml", "name": "某号"}]}

    为什么需要它：定时任务里写一长串 `--rss-feed` 既难维护又容易把带 token 的源
    暴露在命令行历史里；放进 `output/`（已 gitignore）更安全。

    文件不存在或解析失败都返回空列表——配置缺了不该让日报挂掉。
    """
    if not path:
        return []
    try:
        p = Path(path)
        if not p.exists():
            return []
        with open(p, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return []

    raw = data.get('feeds', []) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []

    urls = []
    for item in raw:
        url = ''
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = item.get('url') or item.get('feed') or ''
        url = str(url).strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def load_opml_feeds(path):
    """从 OPML 文件读订阅源地址。

    OPML 是 RSS 阅读器的通用导出格式（Feedly / Inoreader / FreshRSS / 各种桥都支持导出），
    所以用户不必手抄地址：从任意阅读器导出一次，这里就能一把读进来。

    边界：
      - 只取 `xmlUrl`（真正指向 feed 的地址），不取 `htmlUrl`（那是网页）；
      - 分组（嵌套 outline）会被递归展开，分组本身不是源；
      - 文件缺失/格式坏 → 返回空列表，不让日报挂掉。
    """
    if not path:
        return []
    try:
        p = Path(path)
        if not p.exists():
            return []
        root = ET.parse(str(p)).getroot()
    except Exception:
        return []

    urls = []

    def walk(node):
        for child in list(node):
            tag = child.tag.split('}')[-1].lower()
            if tag == 'outline':
                xml_url = ''
                for key, value in child.attrib.items():
                    if key.split('}')[-1].lower() == 'xmlurl':
                        xml_url = (value or '').strip()
                        break
                if xml_url and xml_url not in urls:
                    urls.append(xml_url)
            # 必须对所有节点递归：`body` 本身不是 outline，只在 outline 上递归会一个都读不到。
            walk(child)

    walk(root)
    return urls


def rss_feed_articles(date_str, tz, feed_urls, timeout=15):
    """从 RSS/Atom 订阅源读取公众号文章，按日期过滤。
    
    Args:
        date_str: 目标日期 'YYYY-MM-DD'
        tz: 时区（timezone 对象）
        feed_urls: RSS/Atom URL 列表（支持 http(s):// 和 file://）
        timeout: 网络请求超时秒数
    
    Returns:
        列表，每项字典含 account/title/url/digest/cover/local_text/time/timestamp
        形状对齐 browser_history_articles 以便合并
    
    边界：
        - 只取 mp.weixin.qq.com 的文章（公众号）
        - 按 pubDate/published 过滤：只要目标日期当天的
        - 失败返回空列表，不阻断主流程
        - 不做去重（由主流程的 _article_key 合并）
    """
    if not feed_urls:
        return []
    
    target = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=tz)
    next_day = target + timedelta(days=1)
    
    results = []
    for feed_url in feed_urls:
        try:
            articles = _parse_one_feed(feed_url, timeout)
            for art in articles:
                # 只要公众号
                url = art.get('url', '')
                if 'mp.weixin.qq.com' not in url:
                    continue
                # 日期过滤
                pub = art.get('published')
                if not pub:
                    continue
                if not (target <= pub < next_day):
                    continue
                # 转成统一格式（字段与 browser_history_articles 对齐，含 account_name）
                account = art.get('account') or '(rss)'
                results.append({
                    'account': account,
                    'account_name': account,
                    'source_category': '',
                    'title': art.get('title', ''),
                    'url': url,
                    'digest': art.get('digest', ''),
                    'cover': art.get('cover', ''),
                    'local_text': '',
                    'time': pub.strftime('%H:%M'),
                    'timestamp': int(pub.timestamp()),
                })
        except Exception as e:
            # 单个源失败不影响其他
            print(f'  RSS 源失败 {feed_url}: {type(e).__name__}', file=sys.stderr)
            continue
    
    return results


def _parse_one_feed(feed_url, timeout):
    """解析单个 RSS/Atom 源，返回原始字段（published 是 datetime 对象）。"""
    # 读取
    if feed_url.startswith('file://'):
        path = urllib.parse.unquote(feed_url[7:])
        if path.startswith('/') and len(path) > 2 and path[2] == ':':
            path = path[1:]  # file:///D:/... → D:/...
        with open(path, 'rb') as f:
            content = f.read()
    else:
        req = urllib.request.Request(feed_url, headers={'User-Agent': 'WeFlow/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
    
    # 解析
    root = ET.fromstring(content)
    ns = _detect_namespaces(root)
    
    # RSS 2.0
    if root.tag == 'rss' or root.find('channel') is not None:
        return _parse_rss2(root, ns)
    # Atom
    elif root.tag.endswith('}feed') or root.tag == 'feed':
        return _parse_atom(root, ns)
    else:
        return []


def _detect_namespaces(root):
    """提取常见命名空间。"""
    ns = {}
    # Atom
    if 'http://www.w3.org/2005/Atom' in root.tag:
        ns['atom'] = 'http://www.w3.org/2005/Atom'
    # DC (Dublin Core)
    for prefix, uri in root.attrib.items():
        if 'dublin' in uri.lower() or uri == 'http://purl.org/dc/elements/1.1/':
            ns['dc'] = uri
    return ns


def _parse_rss2(root, ns):
    """RSS 2.0 格式。"""
    items = []
    channel = root.find('channel')
    if channel is None:
        return []
    
    for item in channel.findall('item'):
        title = _text(item.find('title'))
        link = _text(item.find('link'))
        desc = _text(item.find('description'))
        pub_str = _text(item.find('pubDate'))
        
        # 尝试提取公众号名（从 dc:creator 或 author）
        account = ''
        creator = item.find('{http://purl.org/dc/elements/1.1/}creator')
        if creator is not None:
            account = _text(creator)
        if not account:
            account = _text(item.find('author'))
        
        # 封面（有些源会在 enclosure 或 media:content）
        cover = ''
        enc = item.find('enclosure')
        if enc is not None and enc.get('type', '').startswith('image'):
            cover = enc.get('url', '')
        
        # 解析日期
        pub = _parse_datetime(pub_str)
        if not pub:
            continue
        
        items.append({
            'title': title,
            'url': link,
            'digest': desc[:200] if desc else '',
            'account': account,
            'cover': cover,
            'published': pub,
        })
    
    return items


def _parse_atom(root, ns):
    """Atom 格式。"""
    items = []
    atom_ns = ns.get('atom', '')
    
    def find_atom(elem, tag):
        if atom_ns:
            return elem.find(f'{{{atom_ns}}}{tag}')
        return elem.find(tag)
    
    def findall_atom(elem, tag):
        if atom_ns:
            return elem.findall(f'{{{atom_ns}}}{tag}')
        return elem.findall(tag)
    
    for entry in findall_atom(root, 'entry'):
        title = _text(find_atom(entry, 'title'))
        
        # link[@rel='alternate'] 或第一个 link
        link = ''
        for lnk in findall_atom(entry, 'link'):
            if lnk.get('rel') in ('alternate', None):
                link = lnk.get('href', '')
                break
        
        # summary 或 content
        summary = _text(find_atom(entry, 'summary'))
        if not summary:
            summary = _text(find_atom(entry, 'content'))
        
        # published 或 updated
        pub_str = _text(find_atom(entry, 'published'))
        if not pub_str:
            pub_str = _text(find_atom(entry, 'updated'))
        
        # author
        account = ''
        author = find_atom(entry, 'author')
        if author is not None:
            account = _text(find_atom(author, 'name'))
        
        pub = _parse_datetime(pub_str)
        if not pub:
            continue
        
        items.append({
            'title': title,
            'url': link,
            'digest': summary[:200] if summary else '',
            'account': account,
            'cover': '',
            'published': pub,
        })
    
    return items


def _text(elem):
    """安全提取文本。"""
    if elem is None:
        return ''
    return (elem.text or '').strip()


def _parse_datetime(s):
    """解析 RFC 2822 或 ISO 8601 日期。"""
    if not s:
        return None
    s = s.strip()
    
    # RFC 2822 (RSS): "Mon, 28 Oct 2026 13:16:47 +0800"
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s)
    except Exception:
        pass
    
    # ISO 8601 (Atom): "2026-10-28T13:16:47+08:00"
    try:
        # Python 3.7+ 支持 fromisoformat
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        return datetime.fromisoformat(s)
    except Exception:
        pass
    
    return None


if __name__ == '__main__':
    # 自测
    import sys
    from datetime import timezone, timedelta
    
    if len(sys.argv) < 3:
        print('用法: python rss_feed.py <date> <feed-url> [feed-url2 ...]')
        sys.exit(1)
    
    date_str = sys.argv[1]
    feeds = sys.argv[2:]
    tz = timezone(timedelta(hours=8))
    
    arts = rss_feed_articles(date_str, tz, feeds)
    print(f'找到 {len(arts)} 篇文章（{date_str}）')
    for a in arts[:10]:
        print(f"  {a['time']} {a['account']} - {a['title'][:50]}")
