"""文章分块与父文档聚合（纯函数，不需要索引或网络）。

2026-11-05 的实验结论（见 docs/DECISIONS.md D-137）：分块在长文上确实更好，
但幅度有限（recall@10 +1.6 点），代价是索引体积 ×6，所以**默认关闭**，
由 `--article-chunk-chars` 显式开启。这两个函数是那条开关的核心，钉住它们的行为。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import semantic_search as ss


class SplitForIndexTests(unittest.TestCase):
    def test_short_text_stays_one_chunk(self):
        self.assertEqual(ss.split_for_index('短文本。', 400, 60), ['短文本。'])

    def test_long_text_splits_and_keeps_content(self):
        body = '这是一句话。' * 200            # 1200 字
        chunks = ss.split_for_index(body, 400, 60)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 400 + 60 + 1)   # 尺寸 + 重叠
        # 内容不丢：所有块的并集覆盖原文的每一句
        joined = ''.join(chunks)
        for sent in set(body.split('。')) - {''}:
            self.assertIn(sent, joined)

    def test_splits_on_chinese_sentence_boundary(self):
        body = '甲' * 300 + '。' + '乙' * 300 + '。'
        chunks = ss.split_for_index(body, 320, 0)
        self.assertTrue(chunks[0].endswith('。'), chunks[0][-10:])

    def test_empty_returns_empty(self):
        self.assertEqual(ss.split_for_index('', 400, 60), [])


class CollapseChunksTests(unittest.TestCase):
    def test_same_parent_collapses_to_best(self):
        results = [
            {'id': 'a#c0', 'parent': 'a', 'score': 0.2},
            {'id': 'a#c1', 'parent': 'a', 'score': 0.9},
            {'id': 'b#c0', 'parent': 'b', 'score': 0.5},
        ]
        out = ss.collapse_chunks(results, top_k=10)
        self.assertEqual(len(out), 2)
        # 同一父文档只留最高分那块：a 的 0.9 胜出，且它排第一
        self.assertEqual(out[0]['parent'], 'a')
        self.assertEqual(out[0]['id'], 'a#c1')
        self.assertEqual(out[0]['score'], 0.9)

    def test_records_without_parent_are_kept(self):
        out = ss.collapse_chunks([{'id': 'x', 'score': 0.1}, {'id': 'y', 'score': 0.2}], 10)
        self.assertEqual({r['id'] for r in out}, {'x', 'y'})

    def test_top_k_respected(self):
        results = [{'id': f'a{i}#c0', 'parent': f'a{i}', 'score': i / 10} for i in range(5)]
        self.assertEqual(len(ss.collapse_chunks(results, top_k=2)), 2)


if __name__ == '__main__':
    unittest.main()
