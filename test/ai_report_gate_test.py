"""日报的收录判据。

这道判据以前**只存在于一条兜底路径里**：`.articles.json` 存在时走的主路径完全不筛，
而 `.articles.json` 才是正常情况下走的那条。所以「非焦点主题只收相关度高的文章」
这条规则从来没有真正生效过。这些测试盯的就是"两条路径同一个判据"，
以及新旧字段之间怎么过渡。

不联网、不读用户配置。
"""
import importlib.util
import io
import os
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('ai_report_gate', SCRIPTS / 'generate_ai_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class AdmitsTests(unittest.TestCase):
    def test_the_focus_topic_is_always_included(self):
        # 焦点主题不看分：它本来就是这份报告的主体。
        self.assertTrue(report.admits({'topic': report.FOCUS_TOPIC}))
        self.assertTrue(report.admits({'topic': report.FOCUS_TOPIC, 'includeScore': 0.0}))

    def test_a_non_focus_topic_is_judged_by_its_own_question(self):
        other = {'topic': '学术'}
        self.assertTrue(report.admits({**other, 'includeScore': 0.9}))
        self.assertFalse(report.admits({**other, 'includeScore': 0.1}))

    def test_the_threshold_boundary_is_inclusive(self):
        other = {'topic': '学术', 'includeScore': report.INCLUDE_THRESHOLD}
        self.assertTrue(report.admits(other))

    def test_an_article_from_before_the_field_existed_falls_back_to_the_old_rule(self):
        # 没有 includeScore 的是历史产物。旧规则必须原样保留，否则重生成旧日期的
        # 报告会突然换一批文章，而那是谁都没想到的变化。
        self.assertFalse(report.admits({'topic': '学术', 'relevance': '中'}))
        self.assertTrue(report.admits({'topic': '学术', 'relevance': '高'}))
        # 连 relevance 都没有（更老或异常产物）→ 保持旧的默认值「中」的语义，即不收。
        self.assertFalse(report.admits({'topic': '学术'}))

    def test_a_score_of_zero_is_not_mistaken_for_a_missing_one(self):
        """`0.0` 是"明确不收"，不是"没有这个字段"。用 `if score` 判断就会搞反。"""
        self.assertFalse(report.admits({'topic': '学术', 'includeScore': 0.0}))
        self.assertFalse(report.admits({'topic': '学术', 'includeScore': 0, 'relevance': '高'}))

    def test_include_all_reverts_to_collecting_everything(self):
        # 一键回到引入判据之前的行为，和 --classifier llm 是同一个纪律。
        self.assertTrue(report.admits({'topic': '学术', 'includeScore': 0.0}, include_all=True))
        self.assertTrue(report.admits({'topic': '文学'}, include_all=True))

    def test_a_string_score_is_still_honoured(self):
        """md 兜底路径读出来的就是字符串 YAML 标量。

        只认 int/float 的话，那条路径上这个字段会被静默忽略——而它恰恰是
        「两条路径同一个判据」里最容易漏掉的一边。
        """
        self.assertTrue(report.admits({'topic': '学术', 'includeScore': '0.9'}))
        self.assertFalse(report.admits({'topic': '学术', 'includeScore': '0.2',
                                        'relevance': '高'}))

    def test_a_non_numeric_score_falls_back_instead_of_raising(self):
        # 手改坏了的产物不该让整份报告生成失败。
        self.assertTrue(report.admits({'topic': '学术', 'includeScore': '?',
                                       'relevance': '高'}))
        self.assertFalse(report.admits({'topic': '学术', 'includeScore': '?'}))


class LoaderPathTests(unittest.TestCase):
    """两条装载路径必须用同一个判据——这正是原先漏掉的地方。"""

    def test_the_json_path_filters_too(self):
        import json
        import tempfile
        from unittest.mock import patch

        payload = {'articles': [
            {'topic': report.FOCUS_TOPIC, 'title': 'focus', 'includeScore': 0.0},
            {'topic': '学术', 'title': 'kept', 'includeScore': 0.9},
            {'topic': '学术', 'title': 'dropped', 'includeScore': 0.1},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / '2026-01-01'
            day.mkdir()
            (day / '.articles.json').write_text(
                json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            with patch.object(report, 'SOURCE_ROOT', tmp):
                kept = report.load_articles_from_date('2026-01-01')
                self.assertEqual([a['title'] for a in kept], ['focus', 'kept'])
                everything = report.load_articles_from_date('2026-01-01', include_all=True)
                self.assertEqual(len(everything), 3)

    def test_the_markdown_fallback_uses_the_same_rule(self):
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            topic_dir = Path(tmp) / '2026-01-01' / '学术'
            topic_dir.mkdir(parents=True)
            (topic_dir / 'a.md').write_text(
                '---\ntitle: "kept"\nsource: "s"\ntopic: 学术\nrelevance: 中\n'
                'includeScore: 0.8\nurl: "u"\n---\n\n## AI 摘要\n\n正文\n', encoding='utf-8')
            (topic_dir / 'b.md').write_text(
                '---\ntitle: "dropped"\nsource: "s"\ntopic: 学术\nrelevance: 中\n'
                'includeScore: 0.2\nurl: "u"\n---\n\n## AI 摘要\n\n正文\n', encoding='utf-8')
            with patch.object(report, 'SOURCE_ROOT', tmp):
                kept = report.load_articles_from_date('2026-01-01')
            self.assertEqual([a['title'] for a in kept], ['kept'])


class UncertaintySectionTests(unittest.TestCase):
    """报告末尾那段「我拿不准的」。

    它存在的理由是：我们已经在存概率了，但报告一个字没用上——阈值一过就当确定的事
    报出去。这一段让报告第一次说出自己哪里不确定。

    因此这里有两条比"格式对不对"更要紧的性质：
    **没有概率字段时必须明说**（否则那一段的缺席会被读成"哪里都很确定"），
    以及**同一篇文章不能同时出现在"收了"和"没收"两边**。
    """

    def art(self, title, include=None, topic='AI', conf=None):
        article = {'title': title, 'topic': topic}
        if include is not None:
            article['includeScore'] = include
        if conf is not None:
            article['topicConfidence'] = conf
        return article

    def test_articles_without_scores_produce_a_disclaimer_not_silence(self):
        old = [self.art('老文章')]
        section = report.build_uncertainty_section(old, old)
        self.assertIn('没有概率字段', section)
        self.assertIn('无法告诉你', section)

    def test_scores_are_read_from_strings_too(self):
        # md 兜底路径读出来是 YAML 标量文本；不认字符串的话这一段在这条路上会静默消失。
        included = [self.art('勉强收', '0.52', '学术')]
        section = report.build_uncertainty_section(included, included)
        self.assertIn('勉强收', section)

    def test_both_sides_of_the_threshold_are_shown(self):
        included = [self.art('勉强收的', 0.52, '学术')]
        everything = included + [self.art('可惜没收的', 0.44, '文学')]
        section = report.build_uncertainty_section(included, everything)
        self.assertIn('勉强收的', section)
        self.assertIn('可惜没收的', section)
        self.assertIn('收了 1 篇、没收 1 篇', section)

    def test_an_article_from_before_the_field_is_not_invented_into_the_list(self):
        # 老文章没有概率，就不该出现在"拿不准"里——那是编出来的不确定。
        included = [self.art('老文章', topic='学术')]
        everything = included + [self.art('新文章', 0.52, '学术')]
        section = report.build_uncertainty_section(included, everything)
        self.assertNotIn('老文章', section)

    def test_a_confident_report_says_so_instead_of_showing_an_empty_list(self):
        included = [self.art('很清楚', 0.95, 'AI', conf=0.99)]
        section = report.build_uncertainty_section(included, included)
        self.assertIn('没有落在边界上', section)
        self.assertNotIn('- ', section)

    def test_include_all_does_not_claim_things_were_excluded(self):
        # --include-all 之下没有"没收"这回事，不能报一个不存在的排除数。
        everything = [self.art('勉强', 0.52, '学术'), self.art('也勉强', 0.44, '文学')]
        section = report.build_uncertainty_section(everything, everything, include_all=True)
        self.assertNotIn('没收', section)

    def test_low_topic_confidence_is_called_out_separately(self):
        # 收录没问题、但可能分错栏——这跟"收不收得准"是两件事，处理方式也不同。
        included = [self.art('可能归错栏', 0.95, '新闻', conf=0.53)]
        section = report.build_uncertainty_section(included, included)
        self.assertIn('主题可能归错', section)
        self.assertIn('0.53', section)

    def test_a_long_list_is_capped_and_counted(self):
        included = [self.art('边界 %d' % i, 0.5 + i * 0.001) for i in range(20)]
        section = report.build_uncertainty_section(included, included)
        self.assertIn('另有', section)

    def test_the_section_states_that_the_numbers_are_not_calibrated(self):
        included = [self.art('勉强', 0.52, '学术')]
        section = report.build_uncertainty_section(included, included)
        self.assertIn('没有金标准校准过', section)
        self.assertIn('抖', section)


class FilterLogTests(unittest.TestCase):
    """筛选日志必须说清**实际**用的是哪条规则。

    这条日志曾经无脑写"阈值 includeScore >= 0.5"，而那些产物里根本没有这个字段、
    实际走的是旧的 `relevance == 高` 规则。一条说错自己做了什么日志，比没有日志更糟。
    """

    def load(self, articles, include_all=False):
        import json
        import tempfile
        from unittest.mock import patch
        buffer = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / '2026-01-01'
            day.mkdir()
            (day / '.articles.json').write_text(
                json.dumps({'articles': articles}, ensure_ascii=False), encoding='utf-8')
            with patch.object(report, 'SOURCE_ROOT', tmp), redirect_stdout(buffer):
                report.load_articles_from_date('2026-01-01', include_all)
        return buffer.getvalue()

    def test_it_admits_when_the_scores_were_missing(self):
        log = self.load([{'topic': '学术', 'title': 'x', 'relevance': '中'}])
        self.assertIn('没有收录分', log)
        self.assertIn('relevance == 高', log)
        self.assertNotIn('includeScore >=', log)

    def test_it_names_the_mixed_case_instead_of_pretending(self):
        log = self.load([
            {'topic': '学术', 'title': 'a', 'includeScore': 0.1},
            {'topic': '文学', 'title': 'b', 'relevance': '中'},
        ])
        self.assertIn('1 篇按 includeScore', log)
        self.assertIn('其余 1 篇按旧规则', log)

    def test_a_fully_scored_batch_reports_the_threshold(self):
        log = self.load([
            {'topic': '学术', 'title': 'a', 'includeScore': 0.1},
            {'topic': '文学', 'title': 'b', 'includeScore': 0.2},
        ])
        self.assertIn('includeScore >=', log)
        self.assertNotIn('旧规则', log)


if __name__ == '__main__':
    unittest.main()
