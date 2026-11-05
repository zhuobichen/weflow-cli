"""fuse_hybrid 的行为测试：默认必须与旧行为等价（回归保护）。"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import semantic_search as ss


class FuseHybridTests(unittest.TestCase):
    def test_default_is_pure_rrf(self):
        """默认（penalty>=1）必须与直接 rrf_fuse 结果一致——不改变历史行为。"""
        vec = [3, 1, 2, 0]
        kw = [1, 3, 9, 2]
        direct = [i for i, _ in ss.rrf_fuse([vec, kw])]
        via = ss.fuse_hybrid(vec, kw, pool=10, weight=1, penalty=1.0)
        self.assertEqual(via, direct)

    def test_weight_pulls_vector_rank_higher(self):
        """向量权重 2:1 时，向量第一名不应比 1:1 更靠后。"""
        vec = [5, 6, 7]
        kw = [9, 5, 6]
        one = ss.fuse_hybrid(vec, kw, pool=10, weight=1, penalty=1.0)
        two = ss.fuse_hybrid(vec, kw, pool=10, weight=2, penalty=1.0)
        self.assertLessEqual(two.index(5), one.index(5), (one, two))

    def test_penalty_demotes_unsupported_kw_hits(self):
        """BM25 独有、且不在向量前 N 的结果，会被压到后面。"""
        vec = [0, 1, 2]
        kw = [7, 0, 1]
        plain = ss.fuse_hybrid(vec, kw, pool=10, weight=1, penalty=1.0)
        demoted = ss.fuse_hybrid(vec, kw, pool=10, weight=1, penalty=0.1, support_window=3)
        self.assertLess(plain.index(7), demoted.index(7), (plain, demoted))

    def test_pool_respected(self):
        vec = list(range(50))
        kw = list(reversed(range(50)))
        self.assertEqual(len(ss.fuse_hybrid(vec, kw, pool=5)), 5)


if __name__ == '__main__':
    unittest.main()
