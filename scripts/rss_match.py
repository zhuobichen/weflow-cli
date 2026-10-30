#!/usr/bin/env python3
"""把本地订阅的公众号名单，和公开 RSS 服务的收录列表做匹配。

用途：微信 4.2.0.11 起订阅号图文不再落库（D-143），只有把关注的号迁到 RSS 才能恢复体验。
本脚本回答一个问题：**我关注的 N 个号里，有多少能在公开服务里找到？**

输出三档，宁可少认也不给假阳性：
  confirmed  归一化后完全相同
  fuzzy      高度相似（去空格标点后包含/被包含，或相似度 ≥ 0.86）—— 需要人工确认
  missing    找不到

用法：
  python scripts\\rss_match.py                        # 用 output/followed-accounts.json
  python scripts\\rss_match.py --refresh              # 重新抓收录列表
  python scripts\\rss_match.py --accounts 其它.json
"""
import argparse
import json
import pathlib
import re
import sys
import unicodedata
import urllib.request
from difflib import SequenceMatcher

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG_URL = 'https://wechat2rss.xlab.app/list/all.html'
CATALOG_CACHE = ROOT / 'output' / 'cache' / 'rss_catalog.json'
DEFAULT_ACCOUNTS = ROOT / 'output' / 'followed-accounts.json'
REPORT_PATH = ROOT / 'output' / 'rss-match-report.json'

SITE_PREFIX = 'https://wechat2rss.xlab.app/feed/'


def fetch_catalog(url=CATALOG_URL, timeout=30):
    """抓公开收录列表，返回 [{'name':..., 'url':...}]。

    只认指向 /feed/*.xml 的链接——那是真正的 RSS 地址；页面里其它链接（导航、GitHub）跳过。
    """
    req = urllib.request.Request(url, headers={'User-Agent': 'WeFlow/1.0'})
    html = urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')
    items = []
    seen = set()
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href, text = m.group(1), m.group(2)
        if '/feed/' not in href or not href.endswith('.xml'):
            continue
        name = re.sub(r'<[^>]+>', '', text)
        name = re.sub(r'\s+', ' ', name).strip()
        if not name:
            continue
        url = href if href.startswith('http') else SITE_PREFIX + href.rsplit('/', 1)[-1]
        if url in seen:
            continue
        seen.add(url)
        items.append({'name': name, 'url': url})
    return items


def normalize(name):
    """归一化：全角转半角、去掉空白与常见标点、统一小写。"""
    if not name:
        return ''
    s = unicodedata.normalize('NFKC', name)
    s = s.lower()
    s = re.sub(r'[\s\u3000]+', '', s)
    s = re.sub(r'[·•・\-—_.,，。:：!！?？|｜/\\()（）\[\]【】"\'“”‘’]', '', s)
    return s


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def match_accounts(accounts, catalog, fuzzy_ratio=0.86):
    """把订阅号名单和收录列表匹配成三档。

    参数：
      accounts  [{'username':..., 'nick_name':...}]
      catalog   fetch_catalog() 的返回

    返回 {'confirmed': [...], 'fuzzy': [...], 'missing': [...]}
    每项带 username / nick_name / 以及匹配到的 rss_name / rss_url（missing 没有）。
    """
    by_norm = {}
    for item in catalog:
        key = normalize(item['name'])
        if key:
            by_norm.setdefault(key, []).append(item)

    confirmed, fuzzy, missing = [], [], []
    for acc in accounts:
        nick = acc.get('nick_name') or ''
        key = normalize(nick)
        base = {'username': acc.get('username', ''), 'nick_name': nick}

        if key and key in by_norm:
            for hit in by_norm[key]:
                confirmed.append({**base, 'rss_name': hit['name'], 'rss_url': hit['url']})
            continue

        # 模糊：包含关系或相似度够高
        best = None
        best_score = 0.0
        for item in catalog:
            other = normalize(item['name'])
            if not other or not key:
                continue
            if key in other or other in key:
                score = 0.9
            else:
                score = similarity(key, other)
            if score > best_score:
                best_score, best = score, item

        if best is not None and best_score >= fuzzy_ratio:
            fuzzy.append({**base, 'rss_name': best['name'], 'rss_url': best['url'],
                          'score': round(best_score, 3)})
        else:
            missing.append(base)

    return {'confirmed': confirmed, 'fuzzy': fuzzy, 'missing': missing}


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='公众号订阅 → RSS 迁移匹配报告')
    parser.add_argument('--accounts', default=str(DEFAULT_ACCOUNTS),
                        help='订阅名单 JSON（默认 output/followed-accounts.json）')
    parser.add_argument('--refresh', action='store_true', help='重新抓收录列表（默认用缓存）')
    parser.add_argument('--catalog', default=None, help='用本地收录列表 JSON，跳过抓取')
    parser.add_argument('--limit', type=int, default=40, help='报告里最多列出几条明细')
    args = parser.parse_args()

    # 收录列表
    if args.catalog:
        catalog = json.loads(pathlib.Path(args.catalog).read_text(encoding='utf-8'))
    elif CATALOG_CACHE.exists() and not args.refresh:
        catalog = json.loads(CATALOG_CACHE.read_text(encoding='utf-8'))
        print(f'用缓存的收录列表（{len(catalog)} 个号）；要更新加 --refresh')
    else:
        print('正在抓收录列表…')
        catalog = fetch_catalog()
        CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        CATALOG_CACHE.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'收录 {len(catalog)} 个号，已缓存到 {CATALOG_CACHE}')

    accounts_path = pathlib.Path(args.accounts)
    if not accounts_path.exists():
        print(f'[ERROR] 找不到订阅名单：{accounts_path}')
        print('先导出：见 docs 或联系工具作者（contact.db 里的 gh_ 账号）')
        return 1
    accounts = json.loads(accounts_path.read_text(encoding='utf-8'))
    print(f'本地关注 {len(accounts)} 个公众号')

    result = match_accounts(accounts, catalog)
    total = len(accounts)
    n_ok = len({r['username'] for r in result['confirmed']})
    n_fuzzy = len({r['username'] for r in result['fuzzy']})
    n_miss = len(result['missing'])

    print()
    print('=' * 56)
    print(f'  确认可迁移 : {n_ok:>4} 个 ({n_ok/total:.0%})')
    print(f'  疑似（待确认）: {n_fuzzy:>4} 个 ({n_fuzzy/total:.0%})')
    print(f'  迁移不了   : {n_miss:>4} 个 ({n_miss/total:.0%})')
    print('=' * 56)

    if result['fuzzy']:
        print(f'\n疑似匹配（前 {min(args.limit, len(result["fuzzy"]))} 条，需你人工确认）:')
        for r in result['fuzzy'][:args.limit]:
            print(f"  {r['nick_name'][:22]:<24} ≈ {r['rss_name'][:22]:<24} ({r['score']})")
    if result['missing']:
        print(f'\n迁移不了的（前 {min(args.limit, n_miss)} 条）:')
        for r in result['missing'][:args.limit]:
            print(f"  {r['nick_name'][:30]}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({**result, 'stats': {
        'total': total, 'confirmed': n_ok, 'fuzzy': n_fuzzy, 'missing': n_miss,
    }}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n完整报告：{REPORT_PATH}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
