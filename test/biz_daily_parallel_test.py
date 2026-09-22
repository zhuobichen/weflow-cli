"""并发分类：下标映射、资格一致、以及单篇失败不能拖掉整批。

分类对摘要没有任何依赖，所以它被提到串行循环之前、一次并发问完。这个搬动引入
的唯一新风险是**下标错位**：`decisions[k]` 必须严格对应 `articles[k]`，错一格就会
把一篇文章的主题安到另一篇上，而且不会报错——和重排的编号问题同一类。

`sqlcipher3` 是桩掉的：`biz_daily` 在模块级 import 它（它确实需要它才能读微信库），
而 CI 的 python job 只装 zstandard 与 pycryptodome。同一个做法见
`nt_decrypt_shards_test.py`。
"""
import importlib.util
import io
import sqlite3
from pathlib import Path
import hashlib
import json
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('biz_daily_parallel', SCRIPTS / 'biz_daily.py')
biz = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(biz)

TOPICS = ['AI', '学术', '新闻', '文学', '投资', '政治']


class StubClient:
    """按标题回一个可区分的判断，用来验证下标有没有错位。"""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on or set()
        self.seen = []

    def decide_article(self, title, body, topics):
        self.seen.append(title)
        if title in self.fail_on:
            raise RuntimeError('boom')
        return {'topic': 'AI' if title.startswith('AI') else '学术',
                'relevance': '中', 'relevanceScore': 1.0,
                'topicConfidence': 0.9, 'includeScore': 0.5,
                'usage': {}}


def article(title, body='正文' * 40):
    return {'title': title, 'fetched_md': body}


class EligibilityTests(unittest.TestCase):
    def run_helper(self, articles, client, workers=2):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            return biz._classify_articles_parallel(articles, client, TOPICS, workers=workers)

    def test_no_client_means_no_work_at_all(self):
        client = StubClient()
        self.assertEqual(self.run_helper([article('AI 甲')], None), {})
        self.assertEqual(client.seen, [])

    def test_short_or_missing_bodies_are_skipped_like_the_main_loop_skips_them(self):
        # 资格条件必须与主循环逐字一致：不一致的话这些文章会在这一步被跳过、
        # 然后在循环里又被问一次，白花一次调用。
        articles = [article('AI 够长'), {'title': 'AI 没有正文', 'fetched_md': ''},
                    article('AI 太短', '短'), {'title': 'AI 无字段'}]
        client = StubClient()
        decisions = self.run_helper(articles, client)
        self.assertEqual(client.seen, ['AI 够长'])
        self.assertEqual(sorted(decisions), [0])

    def test_nothing_eligible_returns_empty(self):
        self.assertEqual(self.run_helper([article('AI 太短', 'x')], StubClient()), {})


class IndexMappingTests(unittest.TestCase):
    """错一格就会把一篇文章的主题安到另一篇上，且不报错。"""

    def test_every_decision_lands_on_its_own_article(self):
        articles = [article('AI 零'), article('学术 一'), article('AI 二'),
                    article('学术 三'), article('AI 四')]
        decisions = biz._classify_articles_parallel(articles, StubClient(), TOPICS, workers=3)
        self.assertEqual(len(decisions), len(articles))
        for index, value in decisions.items():
            expected = 'AI' if articles[index]['title'].startswith('AI') else '学术'
            self.assertEqual(value['topic'], expected,
                             f'下标 {index} 拿到了别人的判断')

    def test_gaps_from_skipped_articles_do_not_shift_the_indices(self):
        # 中间那篇不合格，后面的下标必须仍然是它自己的下标。
        articles = [article('AI 零'), article('AI 被跳过', 'x'), article('学术 二')]
        decisions = biz._classify_articles_parallel(articles, StubClient(), TOPICS, workers=2)
        self.assertEqual(sorted(decisions), [0, 2])
        self.assertEqual(decisions[2]['topic'], '学术')

    def test_the_worker_count_does_not_change_the_result(self):
        articles = [article('AI %d' % i) for i in range(7)]
        one = biz._classify_articles_parallel(articles, StubClient(), TOPICS, workers=1)
        many = biz._classify_articles_parallel(articles, StubClient(), TOPICS, workers=7)
        self.assertEqual({k: v['topic'] for k, v in one.items()},
                         {k: v['topic'] for k, v in many.items()})


class PartialFailureTests(unittest.TestCase):
    def test_one_failure_does_not_take_the_batch_with_it(self):
        articles = [article('AI 好的一篇'), article('AI 会炸的一篇'), article('学术 另一篇')]
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            decisions = biz._classify_articles_parallel(
                articles, StubClient(fail_on={'AI 会炸的一篇'}), TOPICS, workers=3)
        # 失败的仍然占着自己的键（值是 None），这样主循环才认得出"这一篇没问到"
        # 并退回老路，而不是把别人的判断当自己的用。
        self.assertEqual(sorted(decisions), [0, 1, 2])
        self.assertIsNone(decisions[1])
        self.assertEqual(decisions[2]['topic'], '学术')
        self.assertIn('Jev 分类失败', buffer.getvalue())

    def test_the_progress_line_separates_asked_from_answered(self):
        articles = [article('AI 好'), article('AI 坏')]
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            biz._classify_articles_parallel(
                articles, StubClient(fail_on={'AI 坏'}), TOPICS, workers=2)
        # "问了 2 篇、答上 1 篇"必须能从一行里看出来：并发之后没有逐篇日志了。
        self.assertIn('1/2', buffer.getvalue())


class SerializableArticleTests(unittest.TestCase):
    """`.articles.json` 那条记录里必须带着概率字段。

    **报告优先读这个文件。** 只把概率写进 md 的 frontmatter 的话，它们在报告那条
    主路径上等于不存在——真的发生过：日报末尾的"我拿不准的"永远只输出一句
    "没有概率字段"，而 frontmatter 里明明有。这个测试就是钉住那个缺口。
    """

    def test_the_probability_fields_survive_into_structured_data(self):
        entry = biz._serializable_article({
            'title': '甲', 'account_name': '某号', 'topic': '学术', 'relevance': '中',
            'relevanceScore': 1.74, 'topicConfidence': 0.99, 'includeScore': 0.52,
        }, '2026-09-05')
        self.assertEqual(entry['relevanceScore'], 1.74)
        self.assertEqual(entry['topicConfidence'], 0.99)
        self.assertEqual(entry['includeScore'], 0.52)

    def test_an_article_without_them_does_not_gain_invented_keys(self):
        # 老产物没有概率就不该凭空多出字段——那会让报告以为它有。
        entry = biz._serializable_article(
            {'title': '老文章', 'topic': 'AI', 'relevance': '中'}, '2026-09-05')
        for key in ('relevanceScore', 'topicConfidence', 'includeScore'):
            self.assertNotIn(key, entry)

    def test_the_base_fields_are_unchanged(self):
        # 既有读者按这些键取值，加字段不能动它们。
        entry = biz._serializable_article({
            'title': '甲', 'account_name': '某号', 'time': '08:00', 'topic': '学术',
            'relevance': '高', 'tags': ['a'], 'summary': '摘要', 'url': 'http://x',
        }, '2026-09-05')
        self.assertEqual(entry['source'], '某号')
        self.assertEqual(entry['summary'], '摘要')
        self.assertEqual(entry['date'], '2026-09-05')


class FetchCacheTests(unittest.TestCase):
    """抓取缓存 = **断点续传**。

    日报全有全无：要把当天文章全部抓完才写盘，抓取结果只在内存里。一天 400 篇光抓取
    就一个多小时，任何中断都会让前面的抓取全部作废（2026-09-22 实测停在 105/403，
    什么都没写出来）。按 URL 缓存正文之后，重跑只抓缺的那些。
    """

    HTML = ('<html><body><div id="js_content"><p>' + ('足够长的正文内容。' * 20) +
            '</p></div></body></html>')

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(biz, 'FETCH_CACHE_DIR', self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        # fetch_article 失败会重试（3s/6s 退避）——夹具必须桩掉，否则一个失败的用例
        # 要跑 9 秒（第一版就吃了这个）。
        sleeper = patch.object(biz.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.network = []

    def serve(self, html=None):
        # 注意 `payload` 在类外算好：`Resp.read` 里的 `self` 是 Resp 实例，
        # 不是测试用例——写成 `self.HTML` 会 AttributeError（第一版就是这么错的）。
        payload = (html if html is not None else self.HTML).encode()

        class Resp:
            def __init__(self):
                self.headers = {}      # fetch_article 会读 Content-Encoding（gzip 那次改动）

            def read(self, size=None):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            self.network.append(request.full_url)
            return Resp()

        patcher = patch.object(biz.urllib.request, 'urlopen', fake_urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_miss_fetches_and_stores(self):
        self.serve()
        body, cached = biz.fetch_article_cached('https://mp.weixin.qq.com/s/a')
        self.assertFalse(cached)
        self.assertIn('足够长的正文内容', body)
        self.assertEqual(len(self.network), 1)
        self.assertEqual(len(list(Path(self.tmp.name).glob('*'))), 1)

    def test_a_hit_does_not_touch_the_network(self):
        """这条是断点续传的全部价值：第二次跑**一个请求都不发**。"""
        self.serve()
        biz.fetch_article_cached('https://mp.weixin.qq.com/s/a')
        self.network.clear()
        body, cached = biz.fetch_article_cached('https://mp.weixin.qq.com/s/a')
        self.assertTrue(cached)
        self.assertEqual(self.network, [])
        self.assertIn('足够长的正文内容', body)

    def test_a_different_url_is_a_different_cache_entry(self):
        self.serve()
        biz.fetch_article_cached('https://mp.weixin.qq.com/s/a')
        self.network.clear()
        _b, cached = biz.fetch_article_cached('https://mp.weixin.qq.com/s/b')
        self.assertFalse(cached)
        self.assertEqual(len(self.network), 1)

    def test_a_failure_is_not_cached(self):
        """失败不落盘，下次照旧重试——把失败缓存起来等于永久记住一次抖动。"""
        self.serve(html='<html><body>没有正文节点</body></html>')
        body, cached = biz.fetch_article_cached('https://mp.weixin.qq.com/s/c')
        self.assertIsNone(body)
        self.assertFalse(cached)
        self.assertEqual(list(Path(self.tmp.name).glob('*')), [])

    def test_use_cache_false_refetches_over_an_existing_entry(self):
        self.serve()
        biz.fetch_article_cached('https://mp.weixin.qq.com/s/a')
        self.network.clear()
        _b, cached = biz.fetch_article_cached('https://mp.weixin.qq.com/s/a', use_cache=False)
        self.assertFalse(cached)
        self.assertEqual(len(self.network), 1)

    def test_an_empty_cache_file_is_treated_as_a_miss(self):
        self.serve()
        path = Path(self.tmp.name) / (hashlib.md5(b'https://mp.weixin.qq.com/s/a').hexdigest() + '.md')
        path.write_text('   ', encoding='utf-8')
        _b, cached = biz.fetch_article_cached('https://mp.weixin.qq.com/s/a')
        self.assertFalse(cached)
        self.assertEqual(len(self.network), 1)


class SummaryPromptTests(unittest.TestCase):
    """Jev 判过时给 LLM 的提示词不再要分类字段——省的是**白写的 token**。

    原来无论谁判，提示词都把【主题】【相关度】连"六类判据 + 三档定义"一起塞进去，
    而 Jev 那条路上这两个答案是丢掉的：每篇约 20 个输出 token + 约 300 个输入 token。
    一天 400 篇就是十万量级的纯浪费。

    但**摘要要求那一段必须与完整提示词逐字相同**——只许去掉分类那几段，
    否则生成的摘要/标签会跟着变，那就不是"省 token"而是"换了产物"。
    """

    ARTICLE = {'title': '标题', 'account_name': '某号', 'fetched_md': '正文' * 100}

    def test_the_shared_summary_rules_are_identical_in_both_prompts(self):
        """防漂移：这段抄了两份，就必须断言它们一样（同 `TOPIC_ORDER` 那份的做法）。"""
        def block(text):
            start = text.index('**摘要要求**：')
            end = text.index('返回格式（严格）：')
            return text[start:end]
        self.assertEqual(block(biz.TOPIC_PROMPT), block(biz.SUMMARY_ONLY_PROMPT))

    def test_judged_prompts_drop_the_classification_fields(self):
        slim, mt = biz.summary_prompt_for(self.ARTICLE, '', judged=True)
        self.assertNotIn('【主题】', slim)
        self.assertNotIn('【相关度】', slim)
        self.assertIn('【标签】', slim)          # 标签仍要生成
        self.assertIn('【概念】', slim)
        self.assertEqual(mt, 2000)
        # 真的更短：判据表（六类）不在了
        full, _ = biz.summary_prompt_for(self.ARTICLE, '', judged=False)
        self.assertLess(len(slim), len(full))

    def test_unjudged_prompts_are_byte_identical_to_the_old_behaviour(self):
        """回退路径（`--classifier llm`、或单篇 Jev 失败）不能换标准。"""
        prompt, mt = biz.summary_prompt_for(self.ARTICLE, '', judged=False)
        expected = (biz.TOPIC_PROMPT + '\n\n标题：标题\n来源：某号'
                    + '\n\n内容：\n' + self.ARTICLE['fetched_md'][:4000])
        self.assertEqual(prompt, expected)
        self.assertEqual(mt, 2000)

    def test_a_category_hint_still_wins_over_judged(self):
        # 人工配了类别的来源，提示词本来就是"只要摘要"，与是否判过无关。
        prompt, mt = biz.summary_prompt_for(self.ARTICLE, '学术', judged=True)
        self.assertIn('来源类别已经确定为「学术」', prompt)
        self.assertEqual(mt, 1000)

    def test_the_choice_is_per_article_not_per_batch(self):
        """一篇 Jev 判过、一篇判失败：必须各用各的提示词。

        整批一刀切的话，判失败的那篇就拿不到【主题】/【相关度】的兜底。
        """
        seen = []

        def fake_call_ai(prompt, engine, api_key, max_tokens=2000):
            seen.append(prompt)
            return '【摘要】x【标签】a'

        import _utils
        with patch.object(_utils, 'call_ai', fake_call_ai):
            with patch.object(biz.time, 'sleep'):
                biz._summarise_articles_parallel(
                    [dict(self.ARTICLE), dict(self.ARTICLE)], 'deepseek', 'k',
                    decisions={0: {'topic': 'AI'}})
        self.assertEqual(len(seen), 2)
        self.assertNotIn('【主题】', seen[0])      # 判过的：精简
        self.assertIn('【主题】', seen[1])         # 没判的：完整


class NoSummaryModeTests(unittest.TestCase):
    """`--no-summary`：只要判断、不要生成。

    这条路的卖点是"不调 LLM 也能有分类"，所以两件事必须成立：**判定函数不再要求
    LLM key**，以及**产物里不出现任何假装是摘要的东西**（空标题像"生成失败"，
    本地 digest 冒充摘要则更糟）。
    """

    def test_the_plan_no_longer_requires_an_llm_key(self):
        """没有 DeepSeek key 时，`--no-summary` 下 Phase 2 仍要跑（因为要判断）。

        改之前这里是 `skip`——那会让"只用 Jev 分类"静默变成"整段不跑"。
        """
        self.assertEqual(biz.classifier_plan(False, 'auto', '', 'deepseek', needs_llm=False),
                         'jev')
        self.assertEqual(biz.classifier_plan(False, 'llm', '', 'deepseek', needs_llm=False),
                         'llm')

    def test_the_plan_still_skips_under_no_ai(self):
        # 总闸优先：--no-ai 时哪怕 --no-summary 也给 skip（两处一起给也一样）。
        for needs_llm in (True, False):
            with self.subTest(needs_llm=needs_llm):
                self.assertEqual(
                    biz.classifier_plan(True, 'auto', 'key', 'deepseek', needs_llm=needs_llm),
                    'skip')

    def test_needing_an_llm_key_is_still_the_default(self):
        # 别把默认改掉了：正常模式下没有 key 就是整段不跑。
        self.assertEqual(biz.classifier_plan(False, 'auto', '', 'deepseek'), 'skip')

    def test_no_summary_means_no_summary_section_in_the_markdown(self):
        """没有摘要就不写那一段——空标题像"生成失败"，digest 冒充摘要更糟。"""
        self.assertEqual(biz.summary_section(''), '')
        self.assertEqual(biz.summary_section(None), '')
        self.assertEqual(biz.summary_section('   \n  '), '')
        self.assertIn('## AI 摘要', biz.summary_section('一段摘要'))
        self.assertIn('一段摘要', biz.summary_section('一段摘要'))


class SummaryPrefetchTests(unittest.TestCase):
    """摘要阶段的**预取**：并发只发生在网络等待上，解析与落字段仍由主循环串行做。

    这个设计的全部价值就是"循环体不用重写、每条兜底分支行为不变"。所以测试重点不是
    快，而是那两条前提不被破坏：**这一层不碰 article dict**，以及**失败要以异常的形式
    交回主循环**（主循环的 `except` 才知道该走哪条兜底）。
    """

    def setUp(self):
        # worker 里每篇睡 0.3s（保持对上游的请求节奏）。测试不该真的等。
        patcher = patch.object(biz.time, 'sleep')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def install(self, fail_on=()):
        def fake_call_ai(prompt, engine, api_key, max_tokens=2000):
            self.calls.append({'prompt': prompt, 'max_tokens': max_tokens})
            if len(self.calls) in fail_on:
                raise RuntimeError('llm down')
            return '【摘要】好的\n【标签】a, b'
        import _utils
        patcher = patch.object(_utils, 'call_ai', fake_call_ai)
        patcher.start()
        self.addCleanup(patcher.stop)

    def articles(self, n_long=2, n_short=1):
        out = [{'title': '标题%d' % i, 'account_name': '某号',
                'fetched_md': '正文' * 200} for i in range(n_long)]
        out += [{'title': '短%d' % i, 'account_name': '某号', 'fetched_md': '太短'}
                for i in range(n_short)]
        out += [{'title': '没正文', 'account_name': '某号'}]
        return out

    def test_only_eligible_articles_are_called_and_results_align_by_index(self):
        self.install()
        arts = self.articles()
        results = biz._summarise_articles_parallel(arts, 'deepseek', 'k')
        self.assertEqual(len(results), len(arts))          # 与 articles 等长
        self.assertEqual(len(self.calls), 2)               # 只问了够长的那两篇
        self.assertIsNotNone(results[0][0])
        self.assertIsNotNone(results[1][0])
        self.assertEqual(results[2], (None, None))         # 太短：没问
        self.assertEqual(results[3], (None, None))         # 没正文：没问

    def test_it_does_not_touch_the_articles(self):
        """这是整个设计的前提：并发层只回填结果表，文章的写入全在主循环里。

        一旦这里顺手改了 article，串行版本的写入顺序与兜底分支就不再等价了。
        """
        self.install()
        arts = self.articles()
        before = json.loads(json.dumps(arts, ensure_ascii=False, default=str))
        biz._summarise_articles_parallel(arts, 'deepseek', 'k')
        self.assertEqual(json.loads(json.dumps(arts, ensure_ascii=False, default=str)), before)

    def test_a_failed_call_comes_back_as_an_error_not_an_exception(self):
        """失败必须以 `(None, error)` 交回，好让主循环原有的 `except` 接管。

        这里若直接抛，主循环就永远看不到——那些兜底字段（summary/topic/tags）也就
        不会写，文章会带着空字段进 Phase 3。
        """
        self.install(fail_on=(1,))
        results = biz._summarise_articles_parallel(self.articles(), 'deepseek', 'k')
        self.assertIsNone(results[0][0])
        self.assertIsInstance(results[0][1], RuntimeError)
        self.assertIsNotNone(results[1][0])           # 另一篇不受影响

    def test_the_prompt_follows_the_category_hint(self):
        """配了类别的来源走短提示词、max_tokens 1000；其余走完整提示词、2000。"""
        self.install()
        hinted = [{'title': 'T', 'account_name': 'A', 'fetched_md': '正文' * 200,
                   'source_category': '学术'}]
        plain = [{'title': 'T', 'account_name': 'A', 'fetched_md': '正文' * 200}]
        biz._summarise_articles_parallel(hinted, 'deepseek', 'k')
        biz._summarise_articles_parallel(plain, 'deepseek', 'k')
        self.assertEqual(self.calls[0]['max_tokens'], 1000)
        self.assertEqual(self.calls[1]['max_tokens'], 2000)
        self.assertNotIn('【主题】', self.calls[0]['prompt'])   # 短提示词不要分类字段
        self.assertIn('【标签】', self.calls[1]['prompt'])      # 完整提示词要

    def test_nothing_eligible_means_no_calls(self):
        self.install()
        results = biz._summarise_articles_parallel([{'title': 'x'}], 'deepseek', 'k')
        self.assertEqual(self.calls, [])
        self.assertEqual(results, [(None, None)])


class ImageDownloadTests(unittest.TestCase):
    """图片并发取图，以及**映射必须指向真的存在的文件**。

    并发是实测的收益：同一批 17.2s → 2.1s，190 篇从约 18 分钟降到约 3 分钟。
    但更要紧的是映射那条：原先每张图在**下载前**就登记，于是下载失败的也留一条，
    而映射会被注入阅读器（`window._IMG_MAP`）——页面于是去找一个不存在的文件，
    比保留远程链接更糟。
    """

    MD = ('![a](https://mmbiz.qpic.cn/a.jpg)\n'
          '![b](https://mmbiz.qpic.cn/b.png)\n'
          '![a 又一次](https://mmbiz.qpic.cn/a.jpg)\n'
          '![外面的](https://example.com/x.jpg)')

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / 'images'
        self.requested = []

    def install(self, failing=()):
        class Resp:
            def read(self, size=None):
                return b'x' * 64

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            url = request.full_url
            self.requested.append(url)
            if url in failing:
                raise OSError('boom')
            return Resp()

        patcher = patch.object(biz.urllib.request, 'urlopen', fake_urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_distinct_qpic_image_lands_once(self):
        self.install()
        _md, mapping = biz.download_images_to_local(self.MD, self.dir)
        self.assertEqual(sorted(self.requested),
                         ['https://mmbiz.qpic.cn/a.jpg', 'https://mmbiz.qpic.cn/b.png'])
        self.assertEqual(len(list(self.dir.glob('*'))), 2)
        self.assertEqual(len(mapping), 2)
        # 非 qpic 的图不管（原样留在正文里，按远程取）
        self.assertNotIn('https://example.com/x.jpg', mapping)

    def test_every_mapping_entry_names_a_file_that_exists(self):
        self.install()
        _md, mapping = biz.download_images_to_local(self.MD, self.dir)
        for rel_path in mapping.values():
            with self.subTest(rel_path=rel_path):
                self.assertTrue((Path(self.tmp.name) / rel_path).exists())

    def test_a_failed_image_is_not_mapped_and_does_not_break_the_others(self):
        self.install(failing={'https://mmbiz.qpic.cn/a.jpg'})
        _md, mapping = biz.download_images_to_local(self.MD, self.dir)
        self.assertNotIn('https://mmbiz.qpic.cn/a.jpg', mapping)
        self.assertIn('https://mmbiz.qpic.cn/b.png', mapping)

    def test_a_rerun_makes_no_requests_at_all(self):
        """重跑同一天时**一张图都不再请求**——这是"先检查存在"那个 early-return 的价值。

        （第一版这里我写成"只会再请求 b.png"，其实第一次调用已经把两张都下下来了；
        断言写错的是我，不是代码。桩也必须在调用之前装好，否则那次调用就是真实网络请求。）
        """
        self.install()
        _md, first = biz.download_images_to_local(self.MD, self.dir)
        self.assertEqual(len(first), 2)
        self.requested.clear()
        _md, second = biz.download_images_to_local(self.MD, self.dir)
        self.assertEqual(self.requested, [])
        self.assertEqual(second, first)


class DecodeBodyTests(unittest.TestCase):
    """响应体解压。加这个的原因是一次实测：微信文章页 3–4 MB，`urllib` 默认不发
    `Accept-Encoding`，于是整页未压缩地传——同一篇 33–40s，发了 gzip 后 6–10s。
    少传的字节就是省下的时间，上游压力反而更小。
    """

    TEXT = '中文正文' * 50

    def test_gzip(self):
        import gzip
        self.assertEqual(biz._decode_body(gzip.compress(self.TEXT.encode()), 'gzip'), self.TEXT)

    def test_gzip_is_case_insensitive(self):
        import gzip
        self.assertEqual(biz._decode_body(gzip.compress(self.TEXT.encode()), 'GZIP'), self.TEXT)

    def test_raw_deflate(self):
        import zlib
        c = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        raw = c.compress(self.TEXT.encode()) + c.flush()
        self.assertEqual(biz._decode_body(raw, 'deflate'), self.TEXT)

    def test_zlib_wrapped_deflate(self):
        # 有的服务器发带 zlib 头的 deflate；认不出来会退化成"抓不到文章"。
        import zlib
        self.assertEqual(biz._decode_body(zlib.compress(self.TEXT.encode()), 'deflate'), self.TEXT)

    def test_an_absent_or_unknown_encoding_passes_through(self):
        # 退回原样解码，而不是抛错——抛错会触发重试与回退，把优化变成故障。
        for enc in ('', 'identity', 'br'):
            with self.subTest(encoding=enc):
                self.assertEqual(biz._decode_body(self.TEXT.encode(), enc), self.TEXT)


class FetchArticleEncodingTests(unittest.TestCase):
    """`fetch_article` 必须**真的发出** `Accept-Encoding`，并且能读懂回来的压缩体。"""

    HTML = ('<html><body><div id="js_content"><p>' + ('这是一段足够长的正文内容。' * 12) +
            '</p></div></body></html>')

    class Resp:
        def __init__(self, body, encoding=''):
            import io
            self._body = body
            self.headers = {'Content-Encoding': encoding}

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def run_fetch(self, body, encoding):
        import gzip
        sent = {}

        def fake_urlopen(request, timeout=None):
            sent['headers'] = {k.lower(): v for k, v in request.header_items()}
            return self.Resp(body, encoding)

        with patch.object(biz.urllib.request, 'urlopen', fake_urlopen):
            md = biz.fetch_article('https://mp.weixin.qq.com/s/x')
        return md, sent

    def test_it_asks_for_compression_and_parses_a_gzipped_page(self):
        import gzip
        md, sent = self.run_fetch(gzip.compress(self.HTML.encode()), 'gzip')
        self.assertEqual(sent['headers'].get('accept-encoding'), 'gzip, deflate')
        self.assertIsNotNone(md)
        self.assertIn('这是一段足够长的正文内容', md)

    def test_an_uncompressed_response_still_works(self):
        # 服务器忽略这个头时不能反过来坏掉——这是最常见的兼容路径。
        md, _ = self.run_fetch(self.HTML.encode(), '')
        self.assertIsNotNone(md)
        self.assertIn('这是一段足够长的正文内容', md)

    def test_a_body_without_the_content_node_is_still_rejected(self):
        # 原有的验证不能被这次改动放松：没有 js_content 就是没抓到。
        import gzip
        md, _ = self.run_fetch(gzip.compress(b'<html><body>nope</body></html>'), 'gzip')
        self.assertIsNone(md)


class ClassifierPlanTests(unittest.TestCase):
    """`--classifier` 的接线：三条分支决定**有没有数据出境**。

    D-031 承诺"`--classifier` 是一条命令回滚"，而这句话的实现就是这三个返回值。
    此前没有任何测试——也就是说，没人能离线确认"`llm` 真的不碰 Jev"或
    "`--no-ai` 真的连 Phase 2 都不进"，只能读代码。
    """

    def test_no_ai_skips_phase_two_for_every_classifier(self):
        """总闸。`--no-ai`（或 `dailyAiEnabled=false`，它被折进 no_ai）下，
        无论 `--classifier` 给什么，整块 Phase 2 都不执行——这是"全本地"的保证。"""
        for classifier in ('auto', 'llm', 'jev'):
            with self.subTest(classifier=classifier):
                self.assertEqual(biz.classifier_plan(True, classifier, 'key', 'deepseek'),
                                 'skip')

    def test_a_deepseek_run_without_a_key_skips_phase_two_entirely(self):
        """没 key 就没摘要——老路的解析也要靠 LLM 产出那段文字，所以是整块不跑，
        不是"跑起来再退回老路"。"""
        self.assertEqual(biz.classifier_plan(False, 'auto', '', 'deepseek'), 'skip')
        self.assertEqual(biz.classifier_plan(False, 'jev', '', 'deepseek'), 'skip')

    def test_a_non_deepseek_engine_still_runs_without_a_deepseek_key(self):
        # 无 key 时引擎会切到 local（`biz_daily` 里更早的一处），摘要照样做。
        self.assertEqual(biz.classifier_plan(False, 'llm', '', 'local'), 'llm')

    def test_llm_never_asks_for_a_jev_client(self):
        """**这就是那一条命令回滚**：`--classifier llm` 下不许建客户端。

        建客户端是唯一会让文章标题与正文发往 `api.typesafe.ai` 的动作，所以这一条
        等于"回滚之后不出网"。"""
        for key in ('key', ''):
            with self.subTest(key=bool(key)):
                self.assertEqual(biz.classifier_plan(False, 'llm', key, 'local'), 'llm')

    def test_auto_and_jev_both_ask_for_a_client(self):
        self.assertEqual(biz.classifier_plan(False, 'auto', 'key', 'deepseek'), 'jev')
        self.assertEqual(biz.classifier_plan(False, 'jev', 'key', 'deepseek'), 'jev')

    def test_jev_is_an_intention_not_a_promise(self):
        """`'jev'` 只表示"去试着建客户端"。

        没配 `typesafeApiKey` 时 `create_client` 返回 None，`auto` 安静退回老路、
        `jev` 打一行 WARN——两种都由调用方处理。把它们当成"已经用上了 Jev"是错的，
        这也是为什么这个返回值叫 plan 而不是 used。
        """
        self.assertEqual(biz.classifier_plan(False, 'auto', 'key', 'deepseek'), 'jev')


class ApplyDecisionTests(unittest.TestCase):
    """`_apply_decision` 是每个 Jev 字段落进文章的**唯一**写入点，此前零覆盖。

    （施工文档 §8 点名了这一条：全仓库没有任何测试引用它。）它错了不会报错，只会让
    判断结果少写或多写一个字段——而"少写一个"的后果是日报那道门静默按旧规则走。
    """

    DECISION = {'topic': 'AI', 'relevance': '高', 'relevanceScore': 1.8,
                'topicConfidence': 0.93, 'includeScore': 0.71}

    def test_it_writes_the_topic_and_every_probability_field(self):
        article = {}
        self.assertTrue(biz._apply_decision(article, dict(self.DECISION)))
        self.assertEqual(article['topic'], 'AI')
        self.assertEqual(article['relevance'], '高')
        self.assertEqual(article['relevanceScore'], 1.8)
        self.assertEqual(article['topicConfidence'], 0.93)
        self.assertEqual(article['includeScore'], 0.71)

    def test_set_topic_false_keeps_the_configured_category(self):
        """人工配了类别的来源，主题以配置为准（D-005），但相关度仍由判断给出。

        这条是那个分支的核心：`set_topic=False` 时**主题不许被覆盖**，其余照写。
        """
        article = {'topic': '学术', 'source_category': '学术'}
        biz._apply_decision(article, dict(self.DECISION), set_topic=False)
        self.assertEqual(article['topic'], '学术')
        self.assertEqual(article['relevance'], '高')
        self.assertEqual(article['includeScore'], 0.71)

    def test_no_decision_leaves_the_article_untouched(self):
        """判不出来时**一个字段都不许写**——调用方靠这个 False 决定走哪条老路。"""
        article = {'topic': '学术', 'summary': '已有摘要'}
        before = dict(article)
        for empty in (None, {}):
            with self.subTest(decision=empty):
                self.assertFalse(biz._apply_decision(article, empty))
        self.assertEqual(article, before)

    def test_a_decision_without_the_optional_scores_does_not_invent_keys(self):
        """没给的字段不许凭空出现——凭空出现一个 `includeScore` 会改变日报的收录判断。"""
        article = {}
        biz._apply_decision(article, {'topic': 'AI', 'relevance': '中'})
        self.assertNotIn('topicConfidence', article)
        self.assertNotIn('includeScore', article)
        # `relevanceScore` 是唯一例外的写法（无条件写，可能是 None）。这不有害：
        # 下游两处都按 `is not None` 判断（`_serializable_article`、md 写入方），
        # 所以 None 落不了盘。钉住它是为了别被当成 bug 顺手改掉——改了会让这个键
        # 有时存在有时不存在，而"存在但为 None"至少是一致的。
        self.assertIn('relevanceScore', article)
        self.assertIsNone(article['relevanceScore'])

    def test_the_written_fields_survive_serialization(self):
        """写入 → 序列化这条链要能接上：概率字段必须真的进 `.articles.json`。"""
        article = {}
        biz._apply_decision(article, dict(self.DECISION))
        entry = biz._serializable_article(article, '2026-09-05')
        self.assertEqual(entry['includeScore'], 0.71)
        self.assertEqual(entry['topicConfidence'], 0.93)
        self.assertEqual(entry['relevanceScore'], 1.8)


if __name__ == '__main__':
    unittest.main()
