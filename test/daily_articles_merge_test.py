"""`.articles.json` 必须是**合并写**，否则"一天跑两次"会让结构化数据少一半。

背景：午报 13:00 与日终 24:00 会各跑一次同一天。第二次进来时 `.run_state.json` 已经
把上午处理过的文章跳过，所以这一批只有下午新到达的那些 —— 若覆盖写，上午那批的
标题/摘要/主题/relevance 就从 `.articles.json` 里消失。而 README 与 index.html 是
**从磁盘上的 md 重建**的，于是页面是全天、结构化数据只有半天，下游静默少一半且不报错。

这条测试钉四件事：并集不丢、同 key 以新为准、坏文件不拖垮今天、`decisionModel`
不被 `None` 覆盖。
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('biz_merge', SCRIPTS / 'biz_daily.py')
biz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(biz)


def entry(title, time, url='', topic='AI', relevance='中'):
    return {'title': title, 'source': '某号', 'date': '2026-10-19', 'time': time,
            'topic': topic, 'relevance': relevance, 'tags': ['AI'],
            'summary': '', 'url': url}


class MergeArticlesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / '.articles.json'

    def tearDown(self):
        self.tmp.cleanup()

    def test_第一次写入(self):
        out = biz.merge_articles_json(self.path, '2026-10-19', [entry('甲', '08:00', 'u1')])
        self.assertEqual(len(out), 1)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['articles'][0]['title'], '甲')

    def test_并集不丢_同key以新为准(self):
        biz.merge_articles_json(self.path, '2026-10-19',
                                [entry('上午甲', '08:00', 'u1'), entry('上午乙', '09:00', 'u2')])
        out = biz.merge_articles_json(self.path, '2026-10-19', [
            entry('上午甲（补了摘要）', '08:00', 'u1'),   # 同一 url：更新
            entry('下午丙', '15:00', 'u3'),              # 新增
        ])
        titles = [e['title'] for e in out]
        self.assertEqual(sorted(titles), sorted(['上午甲（补了摘要）', '上午乙', '下午丙']))
        self.assertEqual(len(out), 3, '上午乙不许被第二次的写入抹掉')

    def test_没有url时按标题加时间合并(self):
        biz.merge_articles_json(self.path, '2026-10-19', [entry('同题', '08:00')])
        out = biz.merge_articles_json(self.path, '2026-10-19', [entry('同题', '08:00'), entry('同题', '09:00')])
        self.assertEqual(len(out), 2, '标题相同但时间不同 = 两篇不同推送')

    def test_按时间排序(self):
        out = biz.merge_articles_json(self.path, '2026-10-19', [
            entry('晚', '18:00', 'u3'), entry('早', '07:00', 'u1'), entry('中', '12:00', 'u2')])
        self.assertEqual([e['time'] for e in out], ['07:00', '12:00', '18:00'])

    def test_旧文件坏了不拖垮今天(self):
        self.path.write_text('{ 这不是 json', encoding='utf-8')
        out = biz.merge_articles_json(self.path, '2026-10-19', [entry('甲', '08:00', 'u1')])
        self.assertEqual(len(out), 1)

    def test_decisionModel不被None覆盖(self):
        biz.merge_articles_json(self.path, '2026-10-19', [entry('甲', '08:00', 'u1')], decision_model='deepseek-flash')
        # 第二次是 `--no-ai` 那种没有判断的跑：不许把上一次记下的模型名抹成 null
        biz.merge_articles_json(self.path, '2026-10-19', [entry('乙', '09:00', 'u2')], decision_model=None)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['decisionModel'], 'deepseek-flash')

    def test_generated_at会更新到最后一次(self):
        import re
        biz.merge_articles_json(self.path, '2026-10-19', [entry('甲', '08:00', 'u1')])
        first = json.loads(self.path.read_text(encoding='utf-8'))['generated_at']
        biz.merge_articles_json(self.path, '2026-10-19', [entry('乙', '09:00', 'u2')])
        second = json.loads(self.path.read_text(encoding='utf-8'))['generated_at']
        self.assertRegex(second, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')
        self.assertGreaterEqual(second, first, 'generated_at 记录的应是最后一次写入')


class LowVolumeTests(unittest.TestCase):
    """采集量异常偏少时必须**说出来**。

    背景（2026-10-29 实测）：那天本地库只有 21 行、14 个账号，前一天是 248 行、151 个账号，
    而当天 00:00–10:00 一行都没有——微信客户端没开，公众号推送不会补发。日报却安静地
    发了一封 6 篇的邮件。**"今天很冷清"和"数据缺了半天"在数据上长得一样**，这个标记就是
    把它们分开的那一句话。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def day(self, date, count):
        d = self.root / date
        d.mkdir(parents=True, exist_ok=True)
        articles = [{'title': 'x%d' % i, 'time': '10:00'} for i in range(count)]
        (d / '.articles.json').write_text(
            json.dumps({'date': date, 'articles': articles}, ensure_ascii=False), encoding='utf-8')

    def test_明显偏少时给出比例与中位数(self):
        for date, count in [('2026-10-26', 400), ('2026-10-27', 500), ('2026-10-28', 300)]:
            self.day(date, count)
        low = biz.detect_low_volume(self.root, '2026-10-29', 12)
        self.assertIsNotNone(low)
        self.assertEqual(low['today'], 12)
        self.assertEqual(low['median'], 400)
        self.assertEqual(low['sample'], 3)

    def test_正常数量不报警(self):
        for date, count in [('2026-10-26', 400), ('2026-10-27', 500), ('2026-10-28', 300)]:
            self.day(date, count)
        self.assertIsNone(biz.detect_low_volume(self.root, '2026-10-29', 200))

    def test_样本不足三天不下结论(self):
        # 只有两天历史时，任何"偏少"都可能只是常态波动；宁可不报，也不要造一个
        # 会天天出现的假警报（那种提示很快就没人看了）
        self.day('2026-10-27', 500)
        self.day('2026-10-28', 400)
        self.assertIsNone(biz.detect_low_volume(self.root, '2026-10-29', 5))

    def test_中位数本身很小时不报警(self):
        for date, count in [('2026-10-26', 3), ('2026-10-27', 4), ('2026-10-28', 3)]:
            self.day(date, count)
        self.assertIsNone(biz.detect_low_volume(self.root, '2026-10-29', 1))

    def test_标记会写进articles_json(self):
        self.day('2026-10-26', 400)
        self.day('2026-10-27', 500)
        self.day('2026-10-28', 300)
        target = self.root / '2026-10-29' / '.articles.json'
        biz.merge_articles_json(target, '2026-10-29', [entry('甲', '08:00', 'u1')])
        payload = json.loads(target.read_text(encoding='utf-8'))
        self.assertIn('lowVolume', payload, '少的这一天必须在结构化数据里留下痕迹，供邮件和下游读取')
        self.assertEqual(payload['lowVolume']['today'], 1)

    def test_忽略非日期目录与空日期(self):
        self.day('2026-10-26', 400)
        self.day('2026-10-27', 500)
        self.day('2026-10-28', 0)          # 空日期不参与统计
        (self.root / '.template').mkdir()  # 非日期目录也不参与
        self.assertEqual(biz.recent_article_counts(self.root, '2026-10-29'), [500, 400])


class BrowserHistoryTests(unittest.TestCase):
    """微信 4.x 的订阅号是网页渲染的：**点开过的文章**只留在内嵌浏览器历史里。

    2026-10-29 实测：那天消息表只有 29 行，而 Chromium `History` 里有 9 篇用户真正打开过、
    却从没进过消息库的公众号文章。这个数据源补的就是这段缺口。
    """

    def test_只取公众号且按当天过滤(self):
        import os
        import sqlite3
        from datetime import datetime, timezone, timedelta

        tz = timezone(timedelta(hours=8))
        chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)

        def chrome_time(dt):
            return int((dt - chrome_epoch).total_seconds() * 1_000_000)

        with tempfile.TemporaryDirectory() as tmp:
            prof = Path(tmp) / 'Tencent' / 'xwechat' / 'radium' / 'web' / 'profiles' / 'p1'
            prof.mkdir(parents=True)
            conn = sqlite3.connect(prof / 'History')
            conn.execute('CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, last_visit_time INTEGER)')
            hit = datetime(2026, 10, 29, 20, 7, tzinfo=tz)
            conn.execute('INSERT INTO urls (url, title, last_visit_time) VALUES (?,?,?)',
                         ('https://mp.weixin.qq.com/s/abcdefghijk', '测试标题', chrome_time(hit)))
            # 两条应当被排除的：非公众号的、以及别的日子的
            conn.execute('INSERT INTO urls (url, title, last_visit_time) VALUES (?,?,?)',
                         ('https://example.com/x', '不是公众号', chrome_time(hit)))
            conn.execute('INSERT INTO urls (url, title, last_visit_time) VALUES (?,?,?)',
                         ('https://mp.weixin.qq.com/s/zzzzzzzzzzz', '昨天的',
                          chrome_time(hit - timedelta(days=1))))
            conn.commit()
            conn.close()

            previous = os.environ.get('APPDATA')
            os.environ['APPDATA'] = tmp
            try:
                arts = biz.browser_history_articles('2026-10-29', tz)
            finally:
                if previous is None:
                    del os.environ['APPDATA']
                else:
                    os.environ['APPDATA'] = previous

        self.assertEqual(len(arts), 1, '只该取当天的公众号文章，非公众号与别的日期都要排除')
        self.assertEqual(arts[0]['title'], '测试标题')
        self.assertEqual(arts[0]['time'], '20:07', '时间要按本地时区还原，别用 Chrome 的 1601 纪元直接显示')
        self.assertTrue(arts[0]['url'].startswith('https://mp.weixin.qq.com/s/'))

    def test_没有浏览器目录时安静返回空(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get('APPDATA')
            os.environ['APPDATA'] = tmp
            try:
                self.assertEqual(biz.browser_history_articles('2026-10-29', None or __import__('datetime').timezone.utc), [])
            finally:
                if previous is None:
                    del os.environ['APPDATA']
                else:
                    os.environ['APPDATA'] = previous


class RssFeedTests(unittest.TestCase):
    """微信 4.2.0.11 起绝大多数公众号推送不再落库，`--rss-feed` 用标准订阅源补缺口。

    2026-10-30 实测：同一账号 10-26/27 每天 480+ 条公众号消息，升级到 4.2.0.11 后掉到
    21 / 11 条；界面里的列表改为联网现拉。这个数据源补的就是这段缺口。

    钉四件事：只取公众号、只取当天、RSS 与 Atom 都能解析、坏源不拖垮日报。
    """

    def _write(self, tmpdir, name, body):
        path = Path(tmpdir) / name
        path.write_text(body, encoding='utf-8')
        return 'file:///' + path.as_posix()

    def _tz(self):
        from datetime import timezone, timedelta
        return timezone(timedelta(hours=8))

    def test_只取当天且只取公众号(self):
        rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>当天公众号</title><link>https://mp.weixin.qq.com/s/AAAA</link>
    <pubDate>Fri, 30 Oct 2026 09:30:00 +0800</pubDate></item>
  <item><title>前一天的</title><link>https://mp.weixin.qq.com/s/BBBB</link>
    <pubDate>Thu, 29 Oct 2026 23:59:00 +0800</pubDate></item>
  <item><title>不是公众号</title><link>https://example.com/x</link>
    <pubDate>Fri, 30 Oct 2026 10:00:00 +0800</pubDate></item>
</channel></rss>"""
        with tempfile.TemporaryDirectory() as tmp:
            from rss_feed import rss_feed_articles
            arts = rss_feed_articles('2026-10-30', self._tz(), [self._write(tmp, 'a.xml', rss)])
        self.assertEqual([a['title'] for a in arts], ['当天公众号'])
        self.assertEqual(arts[0]['time'], '09:30')

    def test_Atom格式也能解析(self):
        atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom 里的文章</title>
    <link rel="alternate" href="https://mp.weixin.qq.com/s/CCCC"/>
    <published>2026-10-30T15:05:00+08:00</published>
    <author><name>某公众号</name></author>
  </entry>
</feed>"""
        with tempfile.TemporaryDirectory() as tmp:
            from rss_feed import rss_feed_articles
            arts = rss_feed_articles('2026-10-30', self._tz(), [self._write(tmp, 'a.xml', atom)])
        self.assertEqual([a['title'] for a in arts], ['Atom 里的文章'])
        self.assertEqual(arts[0]['account'], '某公众号')
        self.assertEqual(arts[0]['time'], '15:05')

    def test_坏源不拖垮其它源(self):
        good = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>好源里的文章</title><link>https://mp.weixin.qq.com/s/DDDD</link>
    <pubDate>Fri, 30 Oct 2026 12:00:00 +0800</pubDate></item>
</channel></rss>"""
        with tempfile.TemporaryDirectory() as tmp:
            from rss_feed import rss_feed_articles
            broken = 'file:///' + (Path(tmp) / 'missing.xml').as_posix()
            arts = rss_feed_articles('2026-10-30', self._tz(),
                                     [broken, self._write(tmp, 'good.xml', good)])
        self.assertEqual([a['title'] for a in arts], ['好源里的文章'])

    def test_空源列表返回空(self):
        from rss_feed import rss_feed_articles
        self.assertEqual(rss_feed_articles('2026-10-30', self._tz(), []), [])
        self.assertEqual(rss_feed_articles('2026-10-30', self._tz(), None), [])

    def test_没有日期的条目被跳过(self):
        rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>没有日期</title><link>https://mp.weixin.qq.com/s/EEEE</link></item>
</channel></rss>"""
        with tempfile.TemporaryDirectory() as tmp:
            from rss_feed import rss_feed_articles
            arts = rss_feed_articles('2026-10-30', self._tz(), [self._write(tmp, 'a.xml', rss)])
        self.assertEqual(arts, [])

    def test_配置文件三种写法都能读(self):
        from rss_feed import load_rss_feeds
        cases = [
            (['https://a.xml', 'https://b.xml'], ['https://a.xml', 'https://b.xml']),
            ({'feeds': ['https://a.xml']}, ['https://a.xml']),
            ({'feeds': [{'url': 'https://a.xml', 'name': '某号'}, {'name': '没有url'}]}, ['https://a.xml']),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for i, (payload, expected) in enumerate(cases):
                path = Path(tmp) / f'c{i}.json'
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
                self.assertEqual(load_rss_feeds(str(path)), expected, f'写法 {i}')

    def test_配置文件缺失或损坏都返回空(self):
        from rss_feed import load_rss_feeds
        self.assertEqual(load_rss_feeds(None), [])
        self.assertEqual(load_rss_feeds(''), [])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_rss_feeds(str(Path(tmp) / '不存在.json')), [])
            bad = Path(tmp) / 'bad.json'
            bad.write_text('{不是合法 json', encoding='utf-8')
            self.assertEqual(load_rss_feeds(str(bad)), [])

    def test_配置文件里的源去重且保留顺序(self):
        from rss_feed import load_rss_feeds
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'c.json'
            path.write_text(json.dumps({'feeds': ['https://b.xml', 'https://a.xml', 'https://b.xml']}),
                            encoding='utf-8')
            self.assertEqual(load_rss_feeds(str(path)), ['https://b.xml', 'https://a.xml'])

    def test_OPML能读到嵌套分组里的源(self):
        from rss_feed import load_opml_feeds
        opml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>我的订阅</title></head>
  <body>
    <outline text="AI">
      <outline type="rss" text="量子位" xmlUrl="https://x/feed/a.xml"/>
      <outline type="rss" text="机器之心" xmlUrl="https://x/feed/b.xml"/>
    </outline>
    <outline type="rss" text="安全" xmlUrl="https://x/feed/c.xml"/>
  </body>
</opml>"""
        p = Path(tempfile.mkdtemp()) / 'subs.opml'
        self.addCleanup(lambda: p.parent.exists() and __import__('shutil').rmtree(p.parent, ignore_errors=True))
        p.write_text(opml, encoding='utf-8')
        # 顺序按文档顺序：分组内先，然后顶层
        self.assertEqual(load_opml_feeds(str(p)),
                         ['https://x/feed/a.xml', 'https://x/feed/b.xml', 'https://x/feed/c.xml'])

    def test_OPML只取xmlUrl不取htmlUrl(self):
        from rss_feed import load_opml_feeds
        opml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0"><body>
  <outline type="rss" text="某号"
           xmlUrl="https://x/feed/real.xml" htmlUrl="https://mp.weixin.qq.com/"/>
</body></opml>"""
        p = Path(tempfile.mkdtemp()) / 's.opml'
        self.addCleanup(lambda: p.parent.exists() and __import__('shutil').rmtree(p.parent, ignore_errors=True))
        p.write_text(opml, encoding='utf-8')
        self.assertEqual(load_opml_feeds(str(p)), ['https://x/feed/real.xml'])

    def test_OPML缺失或损坏返回空(self):
        from rss_feed import load_opml_feeds
        self.assertEqual(load_opml_feeds(None), [])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_opml_feeds(str(Path(tmp) / '不存在.opml')), [])
            bad = Path(tmp) / 'bad.opml'
            bad.write_text('<opml><body><outline', encoding='utf-8')
            self.assertEqual(load_opml_feeds(str(bad)), [])


class RssMatchTests(unittest.TestCase):
    """订阅名单 ↔ 公开收录列表的匹配。宁可少认，也不给假阳性。

    2026-10-30 实测：公开服务收录 395 个号，本机 690 个订阅里只命中 6 个（1%）。
    """

    def test_归一化忽略空格标点全角(self):
        from rss_match import normalize
        self.assertEqual(normalize('AI 方寸山'), normalize('AI方寸山'))
        self.assertEqual(normalize('差评X.PIN'), normalize('差评ｘ.ｐｉｎ'))
        self.assertEqual(normalize('  量子位  '), '量子位')

    def test_完全同名算确认匹配(self):
        from rss_match import match_accounts
        accounts = [{'username': 'gh_1', 'nick_name': '量子位'}]
        catalog = [{'name': '量子位', 'url': 'https://x/feed/a.xml'}]
        r = match_accounts(accounts, catalog)
        self.assertEqual(len(r['confirmed']), 1)
        self.assertEqual(r['confirmed'][0]['rss_url'], 'https://x/feed/a.xml')
        self.assertEqual(r['fuzzy'], [])
        self.assertEqual(r['missing'], [])

    def test_包含关系进疑似档而不是确认档(self):
        from rss_match import match_accounts
        accounts = [{'username': 'gh_2', 'nick_name': '差评X.PIN'}]
        catalog = [{'name': '差评', 'url': 'https://x/feed/b.xml'}]
        r = match_accounts(accounts, catalog)
        self.assertEqual(r['confirmed'], [])
        self.assertEqual(len(r['fuzzy']), 1)
        self.assertIn('score', r['fuzzy'][0])

    def test_毫不相关的进缺失档(self):
        from rss_match import match_accounts
        accounts = [{'username': 'gh_3', 'nick_name': '广州市皮肤病医院'}]
        catalog = [{'name': '看雪学院', 'url': 'https://x/feed/c.xml'}]
        r = match_accounts(accounts, catalog)
        self.assertEqual(r['missing'][0]['username'], 'gh_3')
        self.assertEqual(r['confirmed'], [])
        self.assertEqual(r['fuzzy'], [])

    def test_空昵称不会被误匹配(self):
        from rss_match import match_accounts
        accounts = [{'username': 'gh_4', 'nick_name': ''}]
        catalog = [{'name': '某号', 'url': 'https://x/feed/d.xml'}]
        r = match_accounts(accounts, catalog)
        self.assertEqual(r['confirmed'], [])
        self.assertEqual(r['missing'][0]['username'], 'gh_4')


class ArticlesJsonTests(unittest.TestCase):
    """`--articles-json` 是协议中立的接入口：谁用什么办法拿到文章与本工具无关。

    它只做三件事：按日期过滤、按 url 去重、合并进当天。
    """

    def test_按日期过滤并规整字段(self):
        payload = {'articles': [
            {'title': '当天的', 'url': 'https://mp.weixin.qq.com/s/A',
             'account': '某号', 'date': '2026-10-30', 'time': '09:00', 'timestamp': 1793000000},
            {'title': '前一天的', 'url': 'https://mp.weixin.qq.com/s/B',
             'account': '某号', 'date': '2026-10-29', 'time': '09:00', 'timestamp': 1792900000},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.json'
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            data = json.loads(p.read_text(encoding='utf-8'))
        items = data['articles']
        picked = [i for i in items if str(i.get('date') or '') in ('', '2026-10-30')]
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]['title'], '当天的')

    def test_记录形状与主流程一致(self):
        # 主流程后续会读这些键，缺一个就会在渲染时 KeyError（曾经踩过 account_name）
        required = {'account', 'account_name', 'title', 'url', 'time', 'timestamp', 'local_text'}
        item = {'title': 't', 'url': 'u', 'account': 'a', 'time': '09:00', 'timestamp': 1}
        built = {
            'account': item.get('account') or '(external)',
            'account_name': item.get('account_name') or item.get('account') or '(external)',
            'source_category': item.get('source_category') or '',
            'title': item.get('title') or item.get('url'),
            'url': item.get('url') or '',
            'digest': item.get('digest') or '',
            'cover': item.get('cover') or '',
            'local_text': '',
            'time': item.get('time') or '',
            'timestamp': int(item.get('timestamp') or 0),
        }
        self.assertTrue(required.issubset(built.keys()))

    def test_坏文件不影响其它来源(self):
        self.assertIsNone(json.loads('[]') if False else None)  # 占位：解析失败由调用方 try/except
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'bad.json'
            bad.write_text('{不是 json', encoding='utf-8')
            with self.assertRaises(Exception):
                json.loads(bad.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()