"""定标的数学部分：校准分桶、阈值扫描、以及"标注不够就别下结论"。

这一整轮里所有概率都缺金标准。这个脚本是补它的入口，所以它自己算出来的数字
必须是被测过的——一个算错的校准曲线比没有校准曲线更糟，因为它会以一个"结论"的样子
出现在报告里。

纯函数测试，不联网、不读用户数据。
"""
import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('quality_eval', SCRIPTS / 'quality_eval.py')
qe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qe)


def item(score, include, jev_topic='AI', human_topic=None, stored_topic='AI'):
    return {
        'jev': {'includeScore': score, 'topic': jev_topic},
        'storedTopic': stored_topic,
        'label': {'topic': human_topic if human_topic is not None else jev_topic,
                  'include': include},
    }


class CalibrationTests(unittest.TestCase):
    def test_a_meaningful_score_shows_a_rising_curve(self):
        # 分数与人工判断一致：低分全说不收，高分全说收。
        items = [item(0.1, False), item(0.15, False), item(0.9, True), item(0.95, True)]
        report = qe.score_labels(items)
        buckets = {tuple(b['range']): b for b in report['buckets']}
        self.assertEqual(buckets[(0.0, 0.2)]['observed'], 0.0)
        self.assertEqual(buckets[(0.8, 1.01)]['observed'], 1.0)

    def test_a_meaningless_score_shows_a_flat_curve(self):
        # 分数与人工判断无关——这正是"分数没有分辨力"的样子，曲线应当是平的。
        # 每个桶里对半分：这样观测率不随桶变化，斜率就是 0。
        items = []
        for low, high in qe.BUCKETS:
            mid = (low + min(high, 0.99)) / 2
            items += [item(mid, True), item(mid, False)]
        report = qe.score_labels(items)
        observed = [b['observed'] for b in report['buckets']]
        self.assertGreaterEqual(len(observed), 4, '桶太少，这条测试就说明不了什么')
        self.assertTrue(all(abs(rate - 0.5) < 1e-9 for rate in observed),
                        '没有分辨力的分数不该在某个桶里看起来像有效信号：%r' % observed)

    def test_unlabelled_items_are_left_out_rather_than_counted_as_wrong(self):
        # 留 null 是"没把握"，不是"标错了"。两者混起来会让准确率凭空变低。
        items = [item(0.9, True), {'jev': {'includeScore': 0.8, 'topic': 'AI'},
                                   'storedTopic': 'AI', 'label': {'topic': None, 'include': None}}]
        report = qe.score_labels(items)
        self.assertEqual(report['labelled'], 1)
        self.assertEqual(sum(b['n'] for b in report['buckets']), 1)

    def test_items_without_a_score_are_excluded_from_the_curve(self):
        items = [item(0.9, True),
                 {'jev': {'includeScore': None, 'topic': 'AI'},
                  'storedTopic': 'AI', 'label': {'topic': 'AI', 'include': True}}]
        report = qe.score_labels(items)
        self.assertEqual(report['unscored'], 1)
        self.assertEqual(sum(b['n'] for b in report['buckets']), 1)

    def test_a_string_score_does_not_break_the_curve(self):
        # 产物里读回来的可能是字符串，别让它把整条曲线变成空。
        items = [item('0.9', True), item('0.1', False)]
        report = qe.score_labels(items)
        self.assertEqual(sum(b['n'] for b in report['buckets']), 2)


class ThresholdSweepTests(unittest.TestCase):
    def test_the_best_threshold_separates_a_clean_split(self):
        # 人工说不收的最高 0.4，说收的最低 0.8 —— 最优点应当落在两者之间。
        items = [item(0.1, False), item(0.4, False), item(0.8, True), item(0.95, True)]
        report = qe.score_labels(items)
        self.assertGreater(report['best']['threshold'], 0.4)
        self.assertLessEqual(report['best']['threshold'], 0.8)
        self.assertEqual(report['best']['agreement'], 1.0)

    def test_an_inverted_signal_does_not_produce_a_perfect_threshold(self):
        # 分数与人工判断相反时，任何阈值都分不开——不能报出一个 100% 的最优点。
        items = [item(0.9, False), item(0.8, False), item(0.1, True), item(0.2, True)]
        report = qe.score_labels(items)
        self.assertLess(report['best']['agreement'], 1.0)

    def test_the_current_constant_is_visible_in_the_sweep(self):
        # 报告要能回答"现在用的 0.5 是不是离最优点很远"，所以 0.5 必须在扫描里。
        items = [item(0.1, False), item(0.9, True)]
        report = qe.score_labels(items)
        self.assertIn(0.5, [row['threshold'] for row in report['sweep']])


class TopicAgreementTests(unittest.TestCase):
    def test_jev_and_the_stored_label_are_both_compared_to_the_human(self):
        # 存档标签一直是"基准"，但它本身就是要被检验的东西，所以两个都要报。
        items = [
            # Jev 对、存档错
            item(0.5, True, jev_topic='政治', human_topic='政治', stored_topic='学术'),
            # Jev 错、存档对
            item(0.5, True, jev_topic='文学', human_topic='新闻', stored_topic='新闻'),
        ]
        report = qe.score_labels(items)
        self.assertEqual(report['topicAgreement'], 0.5)
        self.assertEqual(report['storedAgreement'], 0.5)

    def test_it_only_counts_items_whose_topic_was_labelled(self):
        items = [item(0.5, True, jev_topic='政治', human_topic='政治'),
                 {'jev': {'includeScore': 0.5, 'topic': 'AI'}, 'storedTopic': 'AI',
                  'label': {'topic': None, 'include': None}}]
        report = qe.score_labels(items)
        self.assertEqual(report['topicLabelled'], 1)
        self.assertEqual(report['topicAgreement'], 1.0)


class SamplingTests(unittest.TestCase):
    def test_the_same_seed_draws_the_same_articles(self):
        # 标了一半再跑一次，如果样本变了，标签就全错位了。
        buckets = {'AI': [{'id': 'a%d' % i} for i in range(10)],
                   '文学': [{'id': 'b%d' % i} for i in range(10)]}
        first = [x['id'] for x in qe.draw_sample(buckets, 6, 42)]
        second = [x['id'] for x in qe.draw_sample(buckets, 6, 42)]
        self.assertEqual(first, second)

    def test_it_rotates_across_topics_instead_of_draining_one(self):
        buckets = {'AI': [{'id': 'a%d' % i} for i in range(10)],
                   '文学': [{'id': 'b%d' % i} for i in range(10)]}
        picked = qe.draw_sample(buckets, 6, 1)
        topics = ['ai' if x['id'].startswith('a') else 'lit' for x in picked]
        self.assertEqual(topics[:2], ['ai', 'lit'])

    def test_asking_for_more_than_exists_returns_what_exists(self):
        buckets = {'AI': [{'id': 'a0'}]}
        self.assertEqual(len(qe.draw_sample(buckets, 50, 1)), 1)


class ArticleReadingTests(unittest.TestCase):
    def test_a_report_artifact_is_not_an_article(self):
        # 日报目录里还有 README.md、行动建议.md 这类产物，它们没有 url。
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.md'
            path.write_text('---\ntitle: "行动建议"\ntopic: AI\n---\n\n正文\n', encoding='utf-8')
            self.assertIsNone(qe.read_article(str(path)))

    def test_a_real_article_parses(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.md'
            path.write_text('---\ntitle: "标题"\ntopic: 学术\nurl: "http://x"\n---\n\n正文内容\n',
                            encoding='utf-8')
            meta, body = qe.read_article(str(path))
            self.assertEqual(meta['topic'], '学术')
            self.assertIn('正文内容', body)


if __name__ == '__main__':
    unittest.main()
