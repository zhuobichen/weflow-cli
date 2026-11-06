"""检索重排：编号对应关系、只增字段、以及失败时退回原序。

重排最危险的失效方式**不会报错**：只要问题编号和候选编号错位，最相关的那条
就会被排到最底下，看起来像一个"模型觉得不相关"的正常结果。所以这里的重点不是
分数高低，而是**这条链路本身有没有错位**，以及拿不到分数时会不会把"未知"
当成"不相关"。

不联网：客户端是桩。也不依赖 numpy/sqlcipher3 —— 这个模块的 import 已经被改成
懒加载，好让 CI（只装 zstandard pycryptodome）也能跑这份测试。
"""
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('semantic_rerank', SCRIPTS / 'semantic_search.py')
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


class StubClient:
    """按候选序号返回 noul，另附一个 best 单选。"""

    def __init__(self, scores=None, best=None, error=None):
        self.scores = scores or {}
        self.best = best
        self.error = error
        self.calls = []

    def decide(self, state, questions, strict_sum=True):
        self.calls.append({'state': state, 'questions': questions})
        if self.error is not None:
            raise self.error
        answers = {key: {'type': 'noul', 'noul': self.scores[key]}
                   for key in self.scores}
        if self.best is not None:
            answers['best'] = {'type': 'choice', 'choice': self.best}
        return answers, {'input_tokens': 0}


def items(*titles):
    return [{'title': t, 'source': 'src', 'text': t + ' 的正文', 'score': 0.1} for t in titles]


class QuestionShapeTests(unittest.TestCase):
    def test_one_question_per_candidate_and_the_numbering_is_explicit(self):
        """`state` 里的【候选k】标签是编号不跑偏的唯一依靠，必须存在。"""
        questions = ss.build_rerank_questions(4)
        self.assertEqual(sorted(questions), ['c0', 'c1', 'c2', 'c3'])
        for index in range(4):
            self.assertEqual(questions['c%d' % index]['type'], 'noul')
            self.assertIn('【候选%d】' % index, questions['c%d' % index]['instructions'])

    def test_the_request_labels_every_candidate(self):
        client = StubClient(scores={'c0': 0.5, 'c1': 0.5}, best='候选0')
        ss.rerank('问题', items('甲', '乙'), client)
        state = client.calls[0]['state']
        self.assertIn('【候选0】甲', state)
        self.assertIn('【候选1】乙', state)
        criteria = client.calls[0]['questions']['best']['criteria']
        self.assertEqual(sorted(criteria), ['候选0', '候选1'])


class OrderingTests(unittest.TestCase):
    def test_the_order_follows_the_scores_across_a_sizeable_pool(self):
        # 相关的那条放在中间，验证它会被顶上来——编号错位的话它反而会沉底。
        pool = items(*'ABCDE')
        client = StubClient(scores={'c0': .1, 'c1': .2, 'c2': .9, 'c3': .05, 'c4': .01},
                            best='候选2')
        out = ss.rerank('问题', pool, client)
        # .9 > .2 > .1 > .05 > .01：C 从中间被顶上来，D/E 沉底。
        self.assertEqual([x['title'] for x in out], list('CBADE'))
        self.assertEqual([x['rerankScore'] for x in out], [.9, .2, .1, .05, .01])

    def test_the_original_score_is_preserved(self):
        # 下游可能在展示 `score`。改它的含义属于改既有契约。
        pool = items('甲', '乙')
        for entry in pool:
            entry['score'] = 0.42
        out = ss.rerank('问题', pool, StubClient(scores={'c0': .1, 'c1': .9}, best='候选1'))
        self.assertEqual([x['score'] for x in out], [0.42, 0.42])

    def test_candidates_beyond_the_pool_keep_their_place_at_the_end(self):
        pool = items(*'ABCD')
        out = ss.rerank('问题', pool, StubClient(scores={'c0': .5, 'c1': .4}, best='候选0'),
                        pool_size=2)
        self.assertEqual([x['title'] for x in out], ['A', 'B', 'C', 'D'])
        self.assertIsNone(out[2].get('rerankScore'))

    def test_a_missing_score_means_unknown_not_irrelevant(self):
        """没拿到分数的排在最后，但**不能**当成 0 分——0 分是"明确不相关"。"""
        pool = items('甲', '乙', '丙')
        client = StubClient(scores={'c1': .9, 'c2': .2}, best='候选1')  # c0 缺席
        out = ss.rerank('问题', pool, client)
        self.assertEqual([x['title'] for x in out], ['乙', '丙', '甲'])
        self.assertNotIn('rerankScore', out[-1])
        self.assertEqual(out[-1]['rerankScore'] if 'rerankScore' in out[-1] else None, None)

    def test_all_scores_missing_leaves_the_order_alone(self):
        pool = items('甲', '乙', '丙')
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            out = ss.rerank('问题', pool, StubClient(scores={}, best=None))
        self.assertEqual([x['title'] for x in out], ['甲', '乙', '丙'])
        self.assertEqual(out_buf.getvalue(), '', 'stdout 是机器在读的那一份，不许混进告警')


class FailSoftTests(unittest.TestCase):
    def test_no_client_is_exactly_the_old_behaviour(self):
        pool = items('甲', '乙')
        self.assertIs(ss.rerank('问题', pool, None), pool)

    def test_a_single_candidate_is_not_worth_a_call(self):
        pool = items('甲')
        client = StubClient(scores={'c0': .9})
        self.assertIs(ss.rerank('问题', pool, client), pool)
        self.assertEqual(client.calls, [])

    def test_a_failed_call_returns_the_original_order(self):
        pool = items('甲', '乙')
        out_buf, err_buf = io.StringIO(), io.StringIO()
        # 告警必须走 **stderr**：2026-10-13 真机实测，默认路径（带重排）在 `search --json` 下
        # stdout 第一行曾是 `  [WARN] 重排失败（JevError）… HTTP 451 …`，机器读到的就不是 JSON 了。
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            out = ss.rerank('问题', pool, StubClient(error=RuntimeError('boom')))
        self.assertEqual([x['title'] for x in out], ['甲', '乙'])
        self.assertTrue(all('rerankScore' not in x for x in out))
        self.assertIn('重排失败', err_buf.getvalue())
        self.assertEqual(out_buf.getvalue(), '', 'stdout 是机器在读的那一份，不许混进告警')

    def test_a_disagreeing_self_check_is_reported_but_does_not_reorder(self):
        """逐条打分与单选对不上——必须说出来（但不是错位，别误报成 WARN）。"""
        pool = items('甲', '乙', '丙')
        client = StubClient(scores={'c0': .1, 'c1': .2, 'c2': .9}, best='候选0')
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            out = ss.rerank('问题', pool, client)
        # 两路不一致本身是正常噪声（模型既逐条打分又做单选，两路可以不一致）；
        # 真正的编号错位是"逐条打分缺候选"，那才升成 WARN。
        self.assertIn('重排两路输出不一致', err_buf.getvalue())
        self.assertNotIn('[WARN]', err_buf.getvalue())
        self.assertEqual(out_buf.getvalue(), '', 'stdout 是机器在读的那一份，不许混进告警')
        # 顺序仍按逐条打分——单选只用来报警，不参与排序（否则一次错答就全盘换序）。
        self.assertEqual(out[0]['title'], '丙')


class KeywordFallbackFilterTests(unittest.TestCase):
    """关键词兜底不能把**生成的产物**当成文章返回。

    实测搜到过「行动建议 — 2026-08-30」——那是日报生成的报告，不是文章。
    索引那条路（`collect_articles`）本来就只走子目录，所以这是两条路径不一致。

    同时要防**过度过滤**：`收藏/` 目录里是真内容，不该被一起排掉。
    """

    def tree(self, tmp):
        """一棵小树，覆盖**两种深度**的产物——我第一版只按路径判断，漏掉了后者。

        真文章带 `url:`（判据），产物不带。
        """
        nl = chr(10)
        root = Path(tmp) / 'biz-daily' / '2026-01-01'
        root.mkdir(parents=True, exist_ok=True)

        def write(path, has_url):
            front = '---%stitle: "x"%s%s---%s' % (
                nl, nl, ('url: "http://x"%s' % nl) if has_url else '', nl)
            path.write_text(front + '%s关键词在这里%s' % (nl, nl), encoding='utf-8')

        # 产物：日期目录下与主题目录下**都可能有**（真实数据里各 4 个）。
        write(root / 'README.md', False)
        write(root / '行动建议.md', False)
        (root / 'AI').mkdir(parents=True, exist_ok=True)
        write(root / 'AI' / '行动建议.md', False)
        # 真文章。
        write(root / 'AI' / '一篇真文章.md', True)
        (root / '收藏').mkdir(parents=True, exist_ok=True)
        write(root / '收藏' / '一条收藏.md', True)
        return str(Path(tmp) / 'biz-daily')

    def test_report_artifacts_are_not_returned_as_articles(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            results = ss.keyword_search('关键词', top_k=20, root=self.tree(tmp))
        titles = [r['title'] for r in results]
        self.assertNotIn('行动建议', titles)
        self.assertNotIn('README', titles)

    def test_real_content_is_still_returned(self):
        # 过度过滤是这条检查最容易犯的错：把"排除产物"写成"只收某种目录"会连收藏一起丢。
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            results = ss.keyword_search('关键词', top_k=20, root=self.tree(tmp))
        titles = [r['title'] for r in results]
        self.assertIn('一篇真文章', titles)
        self.assertIn('一条收藏', titles)


class IndexWindowTests(unittest.TestCase):
    """索引窗口：原先写死在两处调用里（聊天 90 天、日报最近 30 个日期目录）。

    这个数字决定"知识库记多久"，而它原先既看不见也改不了——"个人微信知识库只有三个月
    记忆"就是它造成的。现在窗口是参数，这里钉三件事：**传得进去、算得对、结果里报得出**。
    """

    def test_the_window_reaches_both_collectors_and_comes_back_in_the_result(self):
        seen = {}

        def fake_chat(conn, name_map, days=90):
            seen['chat'] = days
            return []

        # 签名跟着 collect_articles 走：2026-11-05 起它多了 chunk_chars / chunk_overlap，
        # 桩不接受就会 TypeError（这正是这条测试当时红掉的原因）。
        def fake_articles(article_days=30, chunk_chars=0, chunk_overlap=60):
            seen['articles'] = article_days
            return []

        real = (ss.INDEX_DIR, ss.META_FILE, ss.VECTORS_FILE)
        with tempfile.TemporaryDirectory() as tmp:
            ss.INDEX_DIR = Path(tmp)
            ss.META_FILE = Path(tmp) / 'meta.json'
            ss.VECTORS_FILE = Path(tmp) / 'vectors.npy'
            try:
                # PDF 那一路也打桩：不打就会去读**真实的** `output/pdf-text`，
                # 这台机器上只要有一份 PDF 文字，items 就非空、流程会走到向量那一层，
                # 这条只关心"窗口传没传到"的测试就红了（2026-10-27 实测如此）。
                with patch.object(ss, 'require_numpy', return_value=object()), \
                     patch.object(ss, 'load_config',
                                  return_value={'ntDbPath': 'synthetic/message_0.db',
                                                'ntKey': '', 'ntSalt': ''}), \
                     patch.object(ss, 'decrypt_lock', return_value=''), \
                     patch.object(ss, 'get_name_map', return_value={}), \
                     patch.object(ss, 'open_db', return_value=object()), \
                     patch.object(ss, 'collect_chat_messages', side_effect=fake_chat), \
                     patch.object(ss, 'collect_articles', side_effect=fake_articles), \
                     patch.object(ss, 'collect_pdf_text', return_value=[]):
                    result = ss.build_index('fake-key', full=True, days=365, article_days=7)
            finally:
                ss.INDEX_DIR, ss.META_FILE, ss.VECTORS_FILE = real

        self.assertEqual(seen, {'chat': 365, 'articles': 7},
                         '两边收集器都要拿到调用方给的窗口，而不是各自的默认值')
        self.assertEqual(result.get('chatDays'), 365, '结果里要报出这次用的窗口（CLI 的 --json 直接透出去）')
        self.assertEqual(result.get('articleDays'), 7)

    def test_collect_articles_takes_only_the_requested_number_of_days(self):
        # 纯文件系统，不碰数据库：窗口就是"最多看几个日期目录"
        with tempfile.TemporaryDirectory() as tmp:
            daily = Path(tmp) / 'biz-daily'
            for day in ('2026-09-20', '2026-09-21', '2026-09-22', '2026-09-23'):
                topic = daily / day / 'AI'
                topic.mkdir(parents=True)
                (topic / '某号-标题.md').write_text('title: 标题\n## 正文\n内容\n', encoding='utf-8')
            with patch.object(ss, 'OUTPUT_ROOT', tmp):
                two = ss.collect_articles(article_days=2)
                four = ss.collect_articles(article_days=99)
        self.assertEqual(len(two), 2, '只取最近 2 个日期目录')
        self.assertEqual(len(four), 4, '窗口够大时全都取（默认值不许悄悄限制）')

    def test_a_zero_window_is_refused_before_any_work(self):
        """0 天等于建一个空库——而它看起来会像"建好了"。所以要在动手之前挡住。

        同时桩掉 `build_index`：万一这道闸门哪天没了，这条测试会红，**而不是**真的去读用户的库。
        """
        printed = []
        with patch.object(sys, 'argv', ['semantic_search.py', 'build', '--days', '0']), \
             patch.object(ss, 'build_index') as build, \
             patch.object(ss, 'load_config', return_value={}), \
             patch.object(ss, 'json_output', side_effect=lambda payload: printed.append(payload)):
            ss.main()
        build.assert_not_called()
        self.assertTrue(printed, '拒绝也要有一句可读的话，而不是静默退出')
        self.assertIn('都要 ≥ 1', str(printed[0].get('error', '')))


if __name__ == '__main__':
    unittest.main()


class ArticleTextCapTests(unittest.TestCase):
    """文章正文塞进向量的上限必须真的可调（2026-11-06 之前写死 500）。

    500 字只覆盖长文的 7%~30%，正文中段根本进不了向量——这是"知识库构建"层面的
    召回损失，和排序算法无关。默认改成 6000，并由 `ARTICLE_TEXT_CHARS` 控制。
    """

    def test_cap_is_respected(self):
        import tempfile
        from pathlib import Path
        import json as _json
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
        import semantic_search as ss2

        body = '正文句子。' * 400                      # ~2000 字
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root / 'biz-daily' / '2026-01-01' / 'AI'
            day.mkdir(parents=True)
            (day / '测试文章.md').write_text(
                '---\ntitle: 测试标题\n---\n\n## 正文\n' + body, encoding='utf-8')
            old_root, old_cap = ss2.OUTPUT_ROOT, ss2.ARTICLE_TEXT_CHARS
            try:
                ss2.OUTPUT_ROOT = str(root)
                ss2.ARTICLE_TEXT_CHARS = 800
                items = ss2.collect_articles(article_days=1)
            finally:
                ss2.OUTPUT_ROOT, ss2.ARTICLE_TEXT_CHARS = old_root, old_cap

        self.assertEqual(len(items), 1, items)
        text = items[0]['text']
        # 标题一行 + 不超过上限的正文
        self.assertLessEqual(len(text), 800 + 20, len(text))
        self.assertGreater(len(text), 400, len(text))
