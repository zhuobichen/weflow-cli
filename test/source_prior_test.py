"""来源级先验：让「哪个号一贯发哪一类」从真实判断里长出来。

写死一张来源→主题的表是我不该做的事（拉取前按来源+标题判类型实测只有 60% 一致率），
但**抓回来之后**每天都有 Jev 按正文判的主题，攒起来就能看出某个号一贯发什么。
所以这张表是长出来的。这些测试盯四件事：

1. 只增不减——累计次数是事后判断"这号稳不稳"的唯一依据；
2. 判不出来时返回 `None`，且调用方不会把 `None` 当成某个默认主题（那是这类功能最坏的错法）；
3. 读坏、写不进去都**不抛异常**：它是辅助数据，不该让日报挂掉，但也必须**出声**；
4. 表落在仓库外（家目录）——里面是公众号名，不该有被提交的机会。

不联网、不读用户配置。
"""
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import _utils  # noqa: E402

from _utils import (SOURCE_PRIOR_MIN_SAMPLES, SOURCE_PRIOR_SHARE,  # noqa: E402
                    load_source_topics, record_source_topics, source_prior_candidates,
                    source_topics_path, stable_source_topic)


class PathTests(unittest.TestCase):
    def test_the_table_lives_outside_the_repository(self):
        """里面是公众号名。放仓库里就有被提交的机会。"""
        home = os.path.expanduser('~')
        repo = str(Path(__file__).resolve().parents[1])
        path = source_topics_path()
        self.assertTrue(os.path.abspath(path).startswith(os.path.abspath(home)))
        self.assertFalse(os.path.abspath(path).startswith(os.path.abspath(repo)))

    def test_an_explicit_path_wins(self):
        # 测试与将来的"多用例"都靠这个入口，不能悄悄退回默认位置。
        self.assertEqual(source_topics_path('/tmp/x/st.json'), '/tmp/x/st.json')

    def test_it_follows_config_path_so_tests_never_write_to_home(self):
        """与 `CONFIG_PATH` 同目录，且**在调用时**取——测试把它指到临时目录，
        表就跟着走。仓库里已有的 pipeline_security_test 就是这个约定。"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(_utils, 'CONFIG_PATH', os.path.join(tmp, 'config.json')):
                self.assertTrue(source_topics_path().startswith(tmp))
                record_source_topics([('甲号', '新闻')])
                self.assertTrue(os.path.isfile(os.path.join(tmp, 'source_topics.json')))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, 'nested', 'source_topics.json')

    def test_counts_accumulate(self):
        record_source_topics([('甲号', '新闻'), ('甲号', '新闻'), ('甲号', '文学')], self.path)
        store = load_source_topics(self.path)
        self.assertEqual(store, {'甲号': {'新闻': 2, '文学': 1}})

    def test_recording_is_only_increase(self):
        """第二次只报一篇，第一次的计数不许被冲掉。"""
        record_source_topics([('甲号', '新闻'), ('甲号', '新闻')], self.path)
        record_source_topics([('甲号', '新闻')], self.path)
        self.assertEqual(load_source_topics(self.path)['甲号']['新闻'], 3)

    def test_the_missing_directory_is_created(self):
        # 路径是 ~/.weflow-cli/…，第一次跑时目录可能不存在。
        self.assertFalse(os.path.isdir(os.path.dirname(self.path)))
        summary = record_source_topics([('甲号', '新闻')], self.path)
        self.assertEqual(summary['error'], '')
        self.assertTrue(os.path.isfile(self.path))

    def test_an_empty_source_or_unknown_topic_is_not_counted(self):
        """计进去会污染分母，让占比算错。"""
        summary = record_source_topics([('甲号', '新闻'), ('', '新闻'), ('甲号', '科技'),
                                        ('乙号', '')], self.path)
        self.assertEqual(summary['added'], 1)
        self.assertEqual(summary['skipped'], 3)
        self.assertEqual(load_source_topics(self.path), {'甲号': {'新闻': 1}})

    def test_a_corrupted_count_restarts_instead_of_killing_the_file(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'甲号': {'新闻': '很多'}, '乙号': {'文学': 4}}, f, ensure_ascii=False)
        record_source_topics([('甲号', '新闻')], self.path)
        store = load_source_topics(self.path)
        self.assertEqual(store['甲号']['新闻'], 1)   # 从 1 重新数
        self.assertEqual(store['乙号']['文学'], 4)   # 同一文件里好的那部分不受影响

    def test_a_broken_file_degrades_to_empty(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{不是 json')
        self.assertEqual(load_source_topics(self.path), {})
        self.assertEqual(record_source_topics([('甲号', '新闻')], self.path)['added'], 1)

    def test_a_valid_json_of_the_wrong_shape_degrades_to_empty(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(['甲号'], f)
        self.assertEqual(load_source_topics(self.path), {})

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(load_source_topics(self.path), {})

    def test_an_unwritable_target_reports_instead_of_raising(self):
        """写不进去（权限/磁盘）只是记不上账，不能让整天的日报失败——但必须出声。"""
        summary = record_source_topics([('甲号', '新闻')], self._tmp.name)  # 目标是个目录
        self.assertTrue(summary['error'])
        self.assertEqual(summary['added'], 1)   # 账本身是记上的，只是没落盘

    def test_nothing_to_record_still_leaves_a_readable_file(self):
        record_source_topics([], self.path)
        self.assertEqual(load_source_topics(self.path), {})


class StableTopicTests(unittest.TestCase):
    def test_too_few_samples_says_i_do_not_know_yet(self):
        self.assertIsNone(stable_source_topic({'新闻': SOURCE_PRIOR_MIN_SAMPLES - 1}))

    def test_exactly_enough_samples_is_enough(self):
        stable = stable_source_topic({'新闻': SOURCE_PRIOR_MIN_SAMPLES})
        self.assertEqual(stable, ('新闻', 1.0, SOURCE_PRIOR_MIN_SAMPLES))

    def test_a_mixed_account_is_not_stable(self):
        # 一半新闻一半文学 —— 这种号不能按主题跳，跳了就丢另一半。
        self.assertIsNone(stable_source_topic({'新闻': 5, '文学': 5}))

    def test_the_share_boundary_is_inclusive(self):
        counts = {'新闻': 8, '文学': 2}      # 0.8
        self.assertEqual(stable_source_topic(counts), ('新闻', SOURCE_PRIOR_SHARE, 10))

    def test_the_dominant_topic_is_the_one_reported(self):
        topic, ratio, total = stable_source_topic({'新闻': 19, '投资': 1})
        self.assertEqual((topic, total), ('新闻', 20))
        self.assertAlmostEqual(ratio, 0.95)

    def test_no_history_at_all_is_unknown_not_a_default_topic(self):
        """`None` 是"还不知道"，不是"没有主题"。调用方不能拿默认主题顶上。"""
        self.assertIsNone(stable_source_topic({}))
        self.assertIsNone(stable_source_topic(None))

    def test_junk_values_do_not_count_towards_the_sample_size(self):
        self.assertIsNone(stable_source_topic({'新闻': '很多', '文学': 3}))

    def test_zero_counts_are_not_samples(self):
        self.assertIsNone(stable_source_topic({'新闻': 0}))


class CandidateTests(unittest.TestCase):
    STORE = {
        '新闻号': {'新闻': 19, '投资': 1},     # 稳，且在排除集里
        '投资号': {'投资': 12},                # 稳，不在排除集里 → 不列
        '杂食号': {'新闻': 6, '文学': 6},       # 不稳 → 不列
        '小号': {'新闻': 3},                   # 样本不够 → 不列
    }

    def test_only_stable_and_excluded_sources_are_listed(self):
        rows = source_prior_candidates(self.STORE, {'新闻'})
        self.assertEqual([r[0] for r in rows], ['新闻号'])

    def test_the_share_in_the_row_matches_the_history(self):
        source, topic, ratio, total = source_prior_candidates(self.STORE, {'新闻'})[0]
        self.assertEqual((source, topic, total), ('新闻号', '新闻', 20))
        self.assertAlmostEqual(ratio, 0.95)

    def test_more_samples_come_first(self):
        store = {'老号': {'新闻': 30}, '新号': {'新闻': 9}}
        self.assertEqual([r[0] for r in source_prior_candidates(store, {'新闻'})],
                         ['老号', '新号'])

    def test_an_empty_exclusion_set_yields_nothing(self):
        self.assertEqual(source_prior_candidates(self.STORE, set()), [])
        self.assertEqual(source_prior_candidates(self.STORE, None), [])

    def test_an_empty_store_yields_nothing(self):
        self.assertEqual(source_prior_candidates({}, {'新闻'}), [])


class DailyWiringTests(unittest.TestCase):
    """日报那边接得对不对——静态看，不跑真实抓取。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (SCRIPTS / 'biz_daily.py').read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.src)

    def _main_calls(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'main':
                return [n for n in ast.walk(node)
                        if isinstance(n, ast.Call) and getattr(n.func, 'id', '') == name]
        return []

    def test_the_daily_records_the_prior(self):
        self.assertEqual(len(self._main_calls('record_source_topics')), 1)

    def test_it_records_the_archived_articles_not_everything_fetched(self):
        """按盘上真有的记（written_urls），表才和 output/ 里的语料一一对应。

        这条是钉一个我踩过的坑：`topic_groups` 在简报那一段被换成了别的形状
        （topic → 字符串），拿它来记账会记出空表。
        """
        call = self._main_calls('record_source_topics')[0]
        self.assertEqual([a.id for a in call.args], ['pairs'])
        # 再往前一步：喂给它的 pairs 必须是从 written_urls 里对回来的。
        assignments = [n for n in ast.walk(self._main_node())
                       if isinstance(n, ast.Assign)
                       and any(getattr(t, 'id', '') == 'pairs' for t in n.targets)]
        self.assertEqual(len(assignments), 1)
        self.assertIn('written_urls', ast.dump(assignments[0]))

    def _main_node(self):
        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == 'main':
                return node
        raise AssertionError('没找到 main()')

    def test_it_does_not_import_a_second_copy_of_the_helper(self):
        # 一处定义：表的路径与累加规则都只在 _utils 里。
        self.assertIn('from _utils import', self.src)
        self.assertNotIn('def record_source_topics', self.src)


if __name__ == '__main__':
    unittest.main()
