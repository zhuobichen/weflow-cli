"""Jev 概率校验的容差行为（2026-11-05 放宽的那条）。

背景：重排响应里概率和经常是 0.89~1.19（模型给的是相对分数）。原阈值 ±0.02 把这类
响应全部判成「契约变了」，导致 rerank 每批失败、静默退回融合顺序——"默认开启"其实没生效。
现在允许 ±0.3 并归一化放行，但「choice 不是 argmax」这条最危险的断言必须保持严格。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import jev_client as jc

CRITERIA = {'正确': {}, '错误': {}}


def ask(probs, choice):
    return ({'type': 'choice', 'criteria': CRITERIA},
            {'probabilities': probs, 'choice': choice})


class ToleranceTests(unittest.TestCase):
    def test_sum_119_is_accepted(self):
        q, a = ask({'正确': 0.69, '错误': 0.50}, '正确')      # 和 1.19
        jc._check_choice('rerank', q, a, strict_sum=False)                      # 不抛即通过

    def test_sum_089_is_accepted(self):
        q, a = ask({'正确': 0.55, '错误': 0.34}, '正确')      # 和 0.89
        jc._check_choice('rerank', q, a, strict_sum=False)

    def test_relative_scores_are_normalised_not_rejected(self):
        """和可以是任意正数（模型给的是相对分数），归一化后放行。"""
        q, a = ask({'正确': 0.9, '错误': 0.6}, '正确')        # 和 1.5
        jc._check_choice('rerank', q, a, strict_sum=False)
        q, a = ask({'正确': 1.0, '错误': 1.0}, '正确')        # 和 2.0
        jc._check_choice('rerank', q, a, strict_sum=False)

    def test_all_zero_probabilities_raise(self):
        q, a = ask({'正确': 0.0, '错误': 0.0}, '正确')
        with self.assertRaises(jc.JevError):
            jc._check_choice('rerank', q, a, strict_sum=False)

    def test_choice_not_argmax_still_raises(self):
        q, a = ask({'正确': 0.3, '错误': 0.7}, '正确')
        with self.assertRaises(jc.JevError):
            jc._check_choice('rerank', q, a, strict_sum=False)

    def test_probability_out_of_range_still_raises(self):
        q, a = ask({'正确': 1.2, '错误': 0.1}, '正确')
        with self.assertRaises(jc.JevError):
            jc._check_choice('rerank', q, a, strict_sum=False)


    def test_classification_path_stays_strict(self):
        """分类（decide）那条路的「和≈1」契约保持不变——有别的测试钉着。"""
        q, a = ask({'正确': 0.69, '错误': 0.50}, '正确')      # 和 1.19
        with self.assertRaises(jc.JevError):
            jc._check_choice('decide', q, a)                   # 不传 strict_sum=False


if __name__ == '__main__':
    unittest.main()
