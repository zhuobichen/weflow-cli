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


def item(score, include, jev_topic='AI', human_topic=None, stored_topic='AI',
         relevance_score=None, human_relevance=None, topic_confidence=None):
    jev = {'includeScore': score, 'topic': jev_topic}
    if relevance_score is not None:
        jev['relevanceScore'] = relevance_score
        jev['relevance'] = qe._level_for(relevance_score, *qe.CURRENT_RELEVANCE_CUTS)
    if topic_confidence is not None:
        jev['topicConfidence'] = topic_confidence
    label = {'topic': human_topic if human_topic is not None else jev_topic,
             'include': include}
    if human_relevance is not None:
        label['relevance'] = human_relevance
    return {'jev': jev, 'storedTopic': stored_topic, 'label': label}


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


class BandStratifiedSamplingTests(unittest.TestCase):
    """按**概率档位**分层取样。

    只按主题取样会得到这种样本：49 篇里 45 篇的收录分都在 0.2 以下。拿它去标，
    校准曲线只有 4 个点，阈值扫描被一堆容易的负例主导——**标了 49 条，买到的信息量
    等于标了 4 条**。所以取样必须先分档。
    """

    def scored(self, score, n=1):
        return [{'id': '%s-%d' % (score, i), 'jev': {'includeScore': score}}
                for i in range(n)]

    def test_band_boundaries(self):
        self.assertEqual(qe.band_of(0.5), (0.5, 0.65))
        self.assertEqual(qe.band_of(0.0), (0.0, 0.2))
        self.assertEqual(qe.band_of(1.0), (0.8, 1.01))
        self.assertIsNone(qe.band_of(None))

    def test_rare_bands_still_get_represented(self):
        # 45 条 0.1 + 2 条 0.55 + 1 条 0.9 —— 边界档位少，但必须被取到，
        # 否则曲线就只剩一个点。
        pool = self.scored(0.1, 45) + self.scored(0.55, 2) + self.scored(0.9, 1)
        picked = qe.draw_by_band(pool, 12, seed=1)
        scores = [p['jev']['includeScore'] for p in picked]
        self.assertIn(0.55, scores)
        self.assertIn(0.9, scores)

    def test_it_is_deterministic(self):
        pool = self.scored(0.1, 10) + self.scored(0.9, 10)
        first = [p['id'] for p in qe.draw_by_band(pool, 6, seed=3)]
        second = [p['id'] for p in qe.draw_by_band(pool, 6, seed=3)]
        self.assertEqual(first, second)

    def test_asking_for_more_than_the_pool_has(self):
        self.assertEqual(len(qe.draw_by_band(self.scored(0.1, 3), 50, seed=1)), 3)

    def test_an_unscored_item_does_not_crash_the_bucketing(self):
        pool = self.scored(0.1, 4) + [{'id': 'x', 'jev': {'includeScore': None}}]
        picked = qe.draw_by_band(pool, 5, seed=1)
        self.assertEqual(len(picked), 5)


if __name__ == '__main__':
    unittest.main()


class RelevanceLevelTests(unittest.TestCase):
    """原始分 → 三个字。切点是可扫的变量，所以这个映射必须与生产那份一致。"""

    def test_the_cut_points_are_the_production_ones(self):
        # `jev_client.score_to_relevance` 用 int(score+0.5) 取最近档，
        # 等价于这两条线。不一致的话，扫描出来的最优切点会对不上生产。
        low, high = qe.CURRENT_RELEVANCE_CUTS
        self.assertEqual((low, high), (0.5, 1.5))
        for score, expected in [(0.0, '低'), (0.49, '低'), (0.5, '中'), (1.49, '中'),
                                (1.5, '高'), (2.0, '高')]:
            self.assertEqual(qe._level_for(score, low, high), expected, 'score=%s' % score)

    def test_a_moved_cut_moves_the_level(self):
        self.assertEqual(qe._level_for(0.9, 0.5, 1.5), '中')
        self.assertEqual(qe._level_for(0.9, 1.0, 1.75), '低')


class RelevanceScoringTests(unittest.TestCase):
    def test_agreement_counts_only_items_the_human_graded(self):
        items = [item(0.1, True, relevance_score=0.2, human_relevance='低'),
                 item(0.1, True, relevance_score=1.9, human_relevance='低'),   # 这一条错
                 item(0.1, True, relevance_score=0.2)]                          # 没标，不算
        report = qe.score_labels(items)
        self.assertEqual(report['relevanceLabelled'], 2)
        self.assertEqual(report['relevanceAgreement'], 0.5)

    def test_the_mae_carries_the_direction_the_accuracy_hides(self):
        # 两篇都判成「中」，一篇偏高 0.4 档、一篇偏低 0.4 档：准确率是 100%，
        # 而平均差 0.4 说明分数整体没对齐——准确率看不见这件事。
        items = [item(0.1, True, relevance_score=1.4, human_relevance='中'),
                 item(0.1, True, relevance_score=0.6, human_relevance='中')]
        report = qe.score_labels(items)
        self.assertEqual(report['relevanceAgreement'], 1.0)
        self.assertAlmostEqual(report['relevanceMae'], 0.4, places=2)

    def test_the_sweep_prefers_the_cuts_that_reproduce_the_human(self):
        # 人工的分档其实在 1.0 / 1.75 上：现行 0.5/1.5 会把 0.7 判成「中」（人标「低」），
        # 扫描应该找出更贴的那一组。
        items = [item(0.1, True, relevance_score=0.3, human_relevance='低'),
                 item(0.1, True, relevance_score=0.7, human_relevance='低'),
                 item(0.1, True, relevance_score=1.2, human_relevance='中'),
                 item(0.1, True, relevance_score=1.6, human_relevance='中'),
                 item(0.1, True, relevance_score=1.9, human_relevance='高')]
        report = qe.score_labels(items)
        best = report['bestRelevanceCuts']
        self.assertEqual(best['agreement'], 1.0)
        self.assertLessEqual(best['low'], 1.0)
        self.assertLessEqual(best['high'], 1.75)
        self.assertLess(report['relevanceCuts']['agreement'], 1.0,
                        '现行切点在这份数据上应当不是满分——否则这条测试没在测东西')

    def test_a_handful_of_items_is_not_enough_to_sweep(self):
        items = [item(0.1, True, relevance_score=1.0, human_relevance='中')] * 3
        report = qe.score_labels(items)
        self.assertIsNone(report['bestRelevanceCuts'], '样本太少就不该给出"最优切点"')


class ConsistencyTests(unittest.TestCase):
    """题目之间打不打架——这一项不需要人工标签。"""

    def test_high_relevance_but_nothing_to_use_is_flagged(self):
        report = qe.consistency([item(0.2, True, relevance_score=1.8)])
        self.assertEqual(report['conflicts'], 1)
        self.assertEqual(report['highButUseless'], 1)
        self.assertEqual(report['examples'][0]['kind'], '高相关却无内容')

    def test_low_relevance_but_usable_is_flagged(self):
        report = qe.consistency([item(0.9, True, relevance_score=0.1)])
        self.assertEqual(report['lowButUseful'], 1)

    def test_agreement_is_not_a_conflict(self):
        report = qe.consistency([item(0.9, True, relevance_score=1.8),
                                 item(0.1, True, relevance_score=0.2)])
        self.assertEqual(report['conflicts'], 0)
        self.assertEqual(report['conflictRate'], 0.0)

    def test_the_borderline_bands_are_left_alone(self):
        # 相关度「中」（0.5~1.5）与收录分 0.5~0.8 之间都不判矛盾：那一段本来就是灰的，
        # 在那儿报警会把真正打架的那些淹掉。
        report = qe.consistency([item(0.6, True, relevance_score=0.9),
                                 item(0.79, True, relevance_score=1.49)])
        self.assertEqual(report['usable'], 2)
        self.assertEqual(report['conflicts'], 0)

    def test_a_missing_score_is_not_counted_as_agreement(self):
        # 把"有一题没答"当成"没矛盾"，是这个仓库反复踩过的坑（缺值装成默认值）。
        report = qe.consistency([{'jev': {'relevanceScore': 1.8}, 'title': 'x'},
                                 {'jev': {'includeScore': 0.1}, 'title': 'y'}])
        self.assertEqual(report['usable'], 0)
        self.assertEqual(report['unscored'], 2)
        self.assertIsNone(report['conflictRate'], '没有可判定的数据时不许给 0%')

    def test_low_topic_confidence_is_counted_separately(self):
        report = qe.consistency([item(0.2, True, relevance_score=1.8, topic_confidence=0.3)])
        self.assertEqual(report['lowTopicConfidence'], 1)
        self.assertEqual(report['conflicts'], 1, '两件事分别计数，不混在一起')

    def test_no_items_at_all(self):
        report = qe.consistency([])
        self.assertEqual(report['total'], 0)
        self.assertEqual(report['conflicts'], 0)
        self.assertIsNone(report['conflictRate'])


class ExcerptTests(unittest.TestCase):
    """给标注者看的那 400 字：跳过样板，否则改判标题。"""

    def test_the_source_block_is_skipped(self):
        body = ('# 某标题\n'
                '> 来源：某号\n> 时间：2026-09-01 10:00\n'
                '> 原文：[阅读原文](http://mp.weixin.qq.com/s?__biz=x)\n'
                '---\n## AI 摘要\n正文的第一句话在这里，这才是要判的东西。')
        self.assertTrue(qe.excerpt_of(body).startswith('正文的第一句话'))
        self.assertNotIn('阅读原文', qe.excerpt_of(body))

    def test_the_excerpt_is_bounded(self):
        body = '正文。' * 500
        self.assertLessEqual(len(qe.excerpt_of(body, 100)), 100)

    def test_newlines_are_flattened(self):
        # 清单是一行一条打印的，换行会把编号错开
        excerpt = qe.excerpt_of('第一行\n第二行\n第三行')
        self.assertNotIn('\n', excerpt)
        self.assertIn('第一行 第二行', excerpt)

    def test_a_body_that_is_all_boilerplate_does_not_crash(self):
        self.assertEqual(qe.excerpt_of('# 标题\n> 来源：x'), '')
        self.assertEqual(qe.excerpt_of(''), '')
        self.assertEqual(qe.excerpt_of(None), '')


class ExcerptSectionTests(unittest.TestCase):
    """摘要段优先——正文里还有一份标题、一份样板和清洗残留。"""

    FULL = ('# 某标题\n'
            '> 来源：某号\n> 时间：2026-09-01 10:00\n'
            '> 原文：[阅读原文](http://mp.weixin.qq.com/s?__biz=x)\n'
            '---\n## AI 摘要\n摘要的第一句话，这才是要判的东西。\n'
            '---\n## 正文\n# 某标题\n某号 某号 某号\n'
            '在小说阅读器读本章\n正文里那句又长又啰嗦的原话在这儿。')

    def test_the_summary_is_preferred_over_the_scraped_body(self):
        excerpt = qe.excerpt_of(self.FULL)
        self.assertTrue(excerpt.startswith('摘要的第一句话'))
        self.assertNotIn('某号 某号', excerpt, '样板不该进来')
        self.assertNotIn('在小说阅读器', excerpt, '清洗残留不该进来')

    def test_without_a_summary_it_falls_back_to_the_body(self):
        body = '# 某标题\n> 来源：某号\n---\n## 正文\n正文第一句。\n## 一、小节\n后面的话'
        self.assertTrue(qe.excerpt_of(body).startswith('正文第一句'))

    def test_with_neither_it_takes_the_first_real_paragraph(self):
        body = '# 某标题\n> 来源：某号\n---\n没有小标题的正文第一句。'
        self.assertTrue(qe.excerpt_of(body).startswith('没有小标题的正文第一句'))

    def test_a_summary_heading_with_nothing_under_it_falls_through(self):
        # 空摘要（`--no-summary` 的产物）不能把整条摘录变成空串
        body = '# 某标题\n## AI 摘要\n## 正文\n正文在这儿。'
        self.assertTrue(qe.excerpt_of(body).startswith('正文在这儿'))


class BandSamplingDeterminismTests(unittest.TestCase):
    """同一个 seed 必须抽出同一批——否则"固定种子"是句空话。"""

    @staticmethod
    def item(n, score):
        return {'day': '2026-09-%02d' % (n % 28 + 1), 'url': 'http://x/%d' % n,
                'title': '第 %d 篇' % n, 'jev': {'includeScore': score}}

    def test_the_same_items_in_a_different_order_give_the_same_draw(self):
        # 这正是线上发生的事：`scored` 按并发完成顺序追加，两次运行的顺序不同，
        # 于是同一个 seed 抽出的却是两批不同的文章。
        items = [self.item(n, (n % 10) / 10.0) for n in range(60)]
        first = qe.draw_by_band(list(items), 20, 20260921)
        second = qe.draw_by_band(list(reversed(items)), 20, 20260921)
        self.assertEqual([i['title'] for i in first], [i['title'] for i in second])

    def test_a_different_seed_still_gives_a_different_draw(self):
        items = [self.item(n, (n % 10) / 10.0) for n in range(60)]
        a = qe.draw_by_band(list(items), 20, 1)
        b = qe.draw_by_band(list(items), 20, 2)
        self.assertNotEqual([i['title'] for i in a], [i['title'] for i in b])
