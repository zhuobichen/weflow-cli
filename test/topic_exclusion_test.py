"""主题排除（展示层）。

用户在配置里写 `dailyExcludeTopics = 新闻,投资`，日报里就不出现这两类。
**这是展示层开关**：正文照常抓、照常归档，只是两个视图（AI 报告、日报页）不收它。
理由是实测——拉之前按"来源+标题+摘要"判类型不可靠（与来源级比对一致率 60%、
误伤 48/217），而排除是**不可逆的**：没抓就没归档。放在展示层还换来一件事：
改主意不用重抓。

这些测试盯三件事：
1. 排除**先于** `--include-all`——否则"我拿不准的"清单会把刚排掉的新闻又列出来；
2. 两个视图（`generate_ai_report` 与 `generate_html`）用同一份排除集，
   不会一个说排掉了、另一个还在列；
3. 写错的主题名与"排除焦点主题"都要**出声音**，不能静默忽略。

不联网、不读用户配置（`load_config` 只在 main 里被调，测试里不碰）。
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = _load('topic_excl_report', 'generate_ai_report.py')
html = _load('topic_excl_html', 'generate_html.py')
from _utils import excluded_topics  # noqa: E402

MD = """---
title: "%s"
source: "某号"
topic: %s
tags: [%s]
includeScore: 0.9
---

## 正文

%s
"""


def _write_md(date_dir: Path, topic: str, name: str, title: str, body_extra: str = ''):
    topic_dir = date_dir / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    body = '这是一段足够长的正文，用来通过"正文过短就跳过"的那道检查。' * 3 + body_extra
    (topic_dir / name).write_text(MD % (title, topic, topic, body), encoding='utf-8')


class ExcludedTopicsParsingTests(unittest.TestCase):
    def test_nothing_configured_means_nothing_excluded(self):
        self.assertEqual(excluded_topics({}, ''), set())
        self.assertEqual(excluded_topics(None, ''), set())

    def test_both_comma_styles_and_spaces(self):
        self.assertEqual(excluded_topics(None, '新闻，投资, 学术'), {'新闻', '投资', '学术'})

    def test_explicit_overrides_the_config(self):
        # 命令行是"就这一次"的语义，配置是常态。命令行必须赢，否则没法临时看一份全的。
        config = {'dailyExcludeTopics': '新闻'}
        self.assertEqual(excluded_topics(config, '投资'), {'投资'})

    def test_a_config_only_value_still_works(self):
        self.assertEqual(excluded_topics({'dailyExcludeTopics': '文学'}, ''), {'文学'})

    def test_an_unknown_name_is_ignored_loudly(self):
        out = io.StringIO()
        with redirect_stdout(out):
            result = excluded_topics(None, '新闻,科技')
        self.assertEqual(result, {'新闻'})
        # 静默忽略会让人以为过滤生效了 —— 必须有一行 WARN 点名那个词。
        self.assertIn('科技', out.getvalue())
        self.assertIn('WARN', out.getvalue())

    def test_the_focus_topic_cannot_be_excluded(self):
        """排掉焦点主题，报告要么没有主体、要么报「未找到文章」指错方向。"""
        out = io.StringIO()
        with redirect_stdout(out):
            result = excluded_topics(None, '新闻,AI', protected=('AI',))
        self.assertEqual(result, {'新闻'})
        self.assertIn('AI', out.getvalue())
        self.assertIn('WARN', out.getvalue())

    def test_without_protection_the_focus_topic_is_excludable(self):
        # 这条钉住"保护来自调用方传的 protected"，不是写死在 _utils 里的。
        with redirect_stdout(io.StringIO()):
            self.assertEqual(excluded_topics(None, 'AI'), {'AI'})


class ReportAdmittanceTests(unittest.TestCase):
    def test_an_excluded_topic_is_not_admitted(self):
        self.assertFalse(report.admits({'topic': '新闻', 'includeScore': 0.99},
                                       exclude={'新闻'}))

    def test_exclusion_beats_collect_everything(self):
        """`--include-all` 是"不筛"，不是"无视排除"。"""
        self.assertFalse(report.admits({'topic': '新闻', 'includeScore': 0.99},
                                       include_all=True, exclude={'新闻'}))
        self.assertFalse(report.admits({'topic': '新闻', 'relevance': '高'}, include_all=True,
                                       exclude={'新闻'}))

    def test_excluding_one_topic_leaves_the_others_alone(self):
        exclude = {'新闻'}
        self.assertTrue(report.admits({'topic': '学术', 'includeScore': 0.9}, exclude=exclude))
        self.assertFalse(report.admits({'topic': '学术', 'includeScore': 0.1}, exclude=exclude))
        self.assertTrue(report.admits({'topic': report.FOCUS_TOPIC, 'includeScore': 0.0},
                                      exclude=exclude))

    def test_an_article_without_a_topic_is_not_caught_by_another_topics_name(self):
        # 历史产物里 topic 可能是空的。排除「新闻」不该连"没主题的"一起扫掉，
        # 它只是没主题，不是新闻。
        self.assertTrue(report.admits({'topic': '', 'includeScore': 0.9}, exclude={'新闻'}))


class LoaderTests(unittest.TestCase):
    """两个装载路径都要认排除，不然 `.articles.json` 那条（正常走的那条）会漏。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = report.SOURCE_ROOT
        report.SOURCE_ROOT = self._tmp.name
        self.addCleanup(self._restore)

    def _restore(self):
        report.SOURCE_ROOT = self._old
        self._tmp.cleanup()

    def _json_day(self, arts):
        day = Path(self._tmp.name) / '2026-01-02'
        day.mkdir(parents=True, exist_ok=True)
        (day / '.articles.json').write_text(
            json.dumps({'articles': arts}, ensure_ascii=False), encoding='utf-8')

    def test_the_json_path_drops_excluded_topics(self):
        self._json_day([
            {'title': 'A', 'topic': 'AI', 'includeScore': 0.9, 'url': 'u1'},
            {'title': 'B', 'topic': '新闻', 'includeScore': 0.9, 'url': 'u2'},
        ])
        with redirect_stdout(io.StringIO()):
            kept = report.load_articles_from_date('2026-01-02', exclude={'新闻'})
        self.assertEqual([a['title'] for a in kept], ['A'])

    def test_include_all_does_not_resurrect_an_excluded_topic(self):
        self._json_day([
            {'title': 'A', 'topic': 'AI', 'includeScore': 0.9, 'url': 'u1'},
            {'title': 'B', 'topic': '新闻', 'includeScore': 0.0, 'url': 'u2'},
        ])
        with redirect_stdout(io.StringIO()):
            kept = report.load_articles_from_date('2026-01-02', include_all=True,
                                                 exclude={'新闻'})
        self.assertEqual([a['title'] for a in kept], ['A'])

    def test_the_md_fallback_path_drops_excluded_topics(self):
        day = Path(self._tmp.name) / '2026-01-03'
        _write_md(day, 'AI', 'a.md', '原标题')
        _write_md(day, '新闻', 'b.md', '要排掉的')
        kept = report.load_articles_from_date('2026-01-03', exclude={'新闻'})
        self.assertEqual([a['title'] for a in kept], ['原标题'])

    def test_the_same_day_without_exclusion_keeps_both(self):
        # 对照组：没有排除时两篇都在，证明上一条不是"路径本来就只收到一篇"。
        day = Path(self._tmp.name) / '2026-01-04'
        _write_md(day, 'AI', 'a.md', '原标题')
        _write_md(day, '新闻', 'b.md', '要排掉的')
        kept = report.load_articles_from_date('2026-01-04')
        self.assertEqual(sorted(a['title'] for a in kept), sorted(['原标题', '要排掉的']))


class UncertaintySectionTests(unittest.TestCase):
    """「我拿不准的」也是报告的一部分：排掉的新闻不该在这里又出现。

    这一栏有三份清单（没收的、卡在边界的、主题置信度低的），来源不同，
    被排除的文章能通过不同路径混进来，所以三份都要钉。
    """

    # 0.4 在边界带 (0.35, 0.65) 内、且低于收录阈值 0.5 —— 没有排除时它会以
    # 「没收」的身份出现在这一栏，正是对照组要的那个位置。
    NEWS = {'title': '被排掉的新闻', 'topic': '新闻', 'includeScore': 0.4}
    MINE = {'title': '我的文章', 'topic': 'AI', 'includeScore': 0.4}

    def test_the_not_collected_list_drops_it(self):
        without = report.build_uncertainty_section([self.MINE], [self.MINE, self.NEWS])
        self.assertIn('被排掉的新闻', without)  # 对照组：不排除时它确实在这里
        with_exclude = report.build_uncertainty_section([self.MINE], [self.MINE, self.NEWS],
                                                        exclude={'新闻'})
        self.assertNotIn('被排掉的新闻', with_exclude)

    def test_the_borderline_list_drops_it_when_everything_is_collected(self):
        """`--include-all` 时所有文章都算"收了"，于是它改从 borderline_in 那条路进来。"""
        section = report.build_uncertainty_section([self.MINE, self.NEWS],
                                                   [self.MINE, self.NEWS],
                                                   include_all=True, exclude={'新闻'})
        self.assertNotIn('被排掉的新闻', section)
        self.assertIn('我的文章', section)  # 不是把整段都清空了

    def test_the_low_confidence_list_drops_it(self):
        news = {**self.NEWS, 'includeScore': 0.9, 'topicConfidence': 0.2}
        mine = {'title': '我的文章', 'topic': 'AI', 'includeScore': 0.9, 'topicConfidence': 0.2}
        without = report.build_uncertainty_section([mine, news], [mine, news])
        self.assertIn('被排掉的新闻', without)
        with_exclude = report.build_uncertainty_section([mine, news], [mine, news],
                                                        exclude={'新闻'})
        self.assertNotIn('被排掉的新闻', with_exclude)


class HtmlReaderTests(unittest.TestCase):
    """读者页与 AI 报告共用同一份排除集。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.day = Path(self._tmp.name)
        _write_md(self.day, 'AI', 'a.md', '我要看的')
        _write_md(self.day, '新闻', 'b.md', '不想看的新闻')

    def test_the_excluded_topic_directory_is_not_collected(self):
        topics = html.collect_articles(str(self.day), exclude={'新闻'})
        self.assertIn('AI', topics)
        self.assertNotIn('新闻', topics)
        self.assertEqual([a['title'] for a in topics['AI']], ['我要看的'])

    def test_without_exclusion_both_topics_are_there(self):
        topics = html.collect_articles(str(self.day))
        self.assertEqual(sorted(topics.keys()), sorted(['AI', '新闻']))

    def test_excluding_everything_yields_nothing_rather_than_everything(self):
        # 空集是"没配"，不是"全排掉"。传集合进去时按集合走：
        topics = html.collect_articles(str(self.day), exclude={'AI', '新闻'})
        self.assertEqual(topics, {})


if __name__ == '__main__':
    unittest.main()
