"""摘要失败率的熔断：超过阈值就中止，别把降级产物当正常日报发出去。

**这条规则来自一次真实的静默失效**（2026-11-15）：换模型供应商时 `bin` 里取 key 有
两处，只改了一处，`daily` 把 DeepSeek 的 key 发去了通义端点——373 篇摘要全部 `HTTP 401`。
每篇失败都被兜底成「正文前 300 字 + 主题=默认值」，于是主题全变「学术」、摘要是截断
正文、邮件照常发出、退出码 0。判据不能是"跑完了没有"，只能是"多少篇没拿到模型结果"。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'test'))

import biz_daily as B  # noqa: E402


class FailureBudgetTests(unittest.TestCase):
    def test_nothing_failed_is_not_breached(self):
        r = B.check_failure_budget(100, 0, 0.2)
        self.assertFalse(r['breached'])
        self.assertEqual(r['rate'], 0.0)

    def test_a_few_failures_tolerated(self):
        # 偶尔几篇抓不到正文、单次超时属正常波动
        r = B.check_failure_budget(374, 20, 0.2)
        self.assertFalse(r['breached'], '5% 的降级不该熔断')
        self.assertAlmostEqual(r['rate'], 20/374, places=6)

    def test_all_failed_breaches(self):
        r = B.check_failure_budget(373, 373, 0.2)
        self.assertTrue(r['breached'])
        self.assertEqual(r['rate'], 1.0)

    def test_exactly_at_limit_is_not_breached(self):
        # 判据是"超过"，不是"达到"：阈值边界不该因为浮点误差翻脸
        r = B.check_failure_budget(100, 20, 0.2)
        self.assertFalse(r['breached'])

    def test_zero_total_never_breaches(self):
        """没有样本时不能熔断——"0 篇失败"和"没跑"分辨不出来。"""
        r = B.check_failure_budget(0, 0, 0.0)
        self.assertFalse(r['breached'])
        self.assertEqual(r['rate'], 0.0)

    def test_limit_zero_aborts_on_any_failure(self):
        self.assertTrue(B.check_failure_budget(10, 1, 0.0)['breached'])

    def test_limit_one_is_effectively_off(self):
        self.assertFalse(B.check_failure_budget(10, 10, 1.0)['breached'],
                         '阈值 1.0 等于关掉熔断，全失败也不该拦')

    def test_negative_inputs_are_clamped(self):
        r = B.check_failure_budget(-5, -2, 0.2)
        self.assertEqual((r['total'], r['failed']), (0, 0))
        self.assertFalse(r['breached'])


class LimitResolutionTests(unittest.TestCase):
    def test_config_wins(self):
        self.assertAlmostEqual(B.summary_failure_limit({'dailyMaxFailureRate': '0.5'}), 0.5)

    def test_default_when_unset(self):
        self.assertAlmostEqual(B.summary_failure_limit({}), B.MAX_SUMMARY_FAILURE_RATE)
        self.assertAlmostEqual(B.MAX_SUMMARY_FAILURE_RATE, 0.2)

    def test_blank_falls_back_to_default(self):
        self.assertAlmostEqual(B.summary_failure_limit({'dailyMaxFailureRate': '  '}),
                               B.MAX_SUMMARY_FAILURE_RATE)

    def test_garbage_falls_back_to_default(self):
        self.assertAlmostEqual(B.summary_failure_limit({'dailyMaxFailureRate': 'abc'}),
                               B.MAX_SUMMARY_FAILURE_RATE)

    def test_out_of_range_is_clamped(self):
        self.assertEqual(B.summary_failure_limit({'dailyMaxFailureRate': '3'}), 1.0)
        self.assertEqual(B.summary_failure_limit({'dailyMaxFailureRate': '-1'}), 0.0)


if __name__ == '__main__':
    unittest.main()
