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
