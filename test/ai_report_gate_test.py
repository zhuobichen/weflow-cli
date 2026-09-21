"""日报的收录判据。

这道判据以前**只存在于一条兜底路径里**：`.articles.json` 存在时走的主路径完全不筛，
而 `.articles.json` 才是正常情况下走的那条。所以「非焦点主题只收相关度高的文章」
这条规则从来没有真正生效过。这些测试盯的就是"两条路径同一个判据"，
以及新旧字段之间怎么过渡。

不联网、不读用户配置。
"""
import importlib.util
import os
from pathlib import Path
import sys
import unittest

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


if __name__ == '__main__':
    unittest.main()
