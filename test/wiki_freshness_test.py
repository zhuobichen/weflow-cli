"""`wiki_lint` 的时效检查：最新来源日期 + 落后天数。

2026-11-09 实测的背景：wiki 的来源停在 2026-09-28，之后 6 周一条都没进，
而当时的 lint 只查死链/孤儿/空页/重复——**「这套知识库已停止更新」没有任何东西会说**。
用 30 道 11 月的自然问句去测召回只命中 1 道，看起来像内容质量差，实际是没编译。
"""
import datetime
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import wiki_lint as wl


def page(sources):
    return {'stem': 'x', 'sources': sources}


class FreshnessTests(unittest.TestCase):
    def test_takes_the_latest_source_date(self):
        pages = [page(['2026-08-01-甲']), page(['2026-09-28-乙']), page(['2025-12-31-丙'])]
        got = wl.freshness(pages, today=datetime.date(2026, 11, 9))
        self.assertEqual(got['latest'], '2026-09-28')
        self.assertEqual(got['daysBehind'], 42)
        self.assertTrue(got['stale'])

    def test_fresh_wiki_is_not_stale(self):
        pages = [page(['2026-11-05-甲'])]
        got = wl.freshness(pages, today=datetime.date(2026, 11, 9))
        self.assertEqual(got['daysBehind'], 4)
        self.assertFalse(got['stale'])

    def test_sources_without_date_are_skipped_not_counted_as_oldest(self):
        """老格式卡片没有日期前缀；把它们当"最旧"会把结论带偏。"""
        pages = [page(['没有日期的卡片名']), page(['2026-11-01-乙'])]
        got = wl.freshness(pages, today=datetime.date(2026, 11, 9))
        self.assertEqual(got['latest'], '2026-11-01')
        self.assertEqual(got['datedSources'], 1)

    def test_no_dated_sources_reports_unknown_not_stale(self):
        got = wl.freshness([page(['无日期'])], today=datetime.date(2026, 11, 9))
        self.assertIsNone(got['latest'])
        self.assertIsNone(got['stale'], '判不了就报未知，不要断言"停更"')
        self.assertEqual(got['datedSources'], 0)

    def test_threshold_is_recorded_in_output(self):
        got = wl.freshness([page(['2026-11-01-甲'])], today=datetime.date(2026, 11, 9))
        self.assertEqual(got['staleAfterDays'], wl.STALE_AFTER_DAYS)


if __name__ == '__main__':
    unittest.main()
