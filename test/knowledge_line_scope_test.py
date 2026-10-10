# -*- coding: utf-8 -*-
"""**检索按线分** —— 三个读者放在一起钉，而不是一个模块一个测试文件。

为什么跨模块：这一条失效的样子是"**某个读者忘了带线**" —— `vault search --line chat`
里混进文章结果、语义检索只掩码了一半。那种失效在各自的模块测试里**看不出来**，因为
模块内的断言说的是"我这条线过滤对了"。所以判据集中在这里：`wiki` / `chat` / `all`
三种取值在**每一个**读者上都必须给出不同的、可预期的结果集。

语义那一段**不碰真索引**（本机从来没建过）：在临时目录里手造一个两条线的迷你索引，
再把模块级的两个路径与嵌入函数 patch 掉 —— 不联网、不花钱、不依赖用户数据。
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vs = load('vault_search')
vr = load('vault_rag')
ss = load('semantic_search')


class VaultFixture:
    """两条线各有概念页、各有材料的最小库。

    **故意不是 `TestCase`**：它只提供夹具。做成 TestCase 的话，三个子类会把它那几条
    断言各跑一遍（同一件事测三次，报出来的条数也会虚高）。
    """

    # 每一处都对得上一条线：
    #   Wiki/Concepts      文章线的概念页
    #   Chat/Concepts      聊天线的概念页
    #   Sources/Chat       聊天卡（`chat_notes --vault-copy` 放的）
    #   002_Literature     用户自己的阅读笔记（文章线）
    #   biz-daily 文章     文章线
    WIKI_CONCEPT = 'vault/Wiki/Concepts/文章概念.md'
    CHAT_CONCEPT = 'vault/Chat/Concepts/聊天概念.md'
    CHAT_CARD = 'vault/Sources/Chat/会话-某个群.md'
    USER_NOTE = 'vault/002_Literature/读书笔记.md'

    def build(self, tmp):
        vault = Path(tmp) / 'vault'
        for rel in ('Wiki/Concepts/文章概念.md', 'Chat/Concepts/聊天概念.md',
                    'Sources/Chat/会话-某个群.md', '002_Literature/读书笔记.md'):
            path = vault / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# %s\n\n正文里有 甲甲甲\n' % path.stem, encoding='utf-8')
        day = Path(tmp) / 'biz' / '2026-01-01' / 'AI'
        day.mkdir(parents=True, exist_ok=True)
        (day / '某号-某篇.md').write_text(
            '---\ntitle: "x"\nurl: http://example.invalid/x\n---\n\n甲甲甲\n', encoding='utf-8')
        return str(vault), str(Path(tmp) / 'biz')

    def collected(self, line, search_type='all'):
        """`vault_search.collect_files` 收到的文件（相对临时根，正斜杠）。"""
        with tempfile.TemporaryDirectory() as tmp:
            vault, biz = self.build(tmp)
            files = vs.collect_files(vault, biz, search_type, 3650, line=line)
            return {str(Path(f[0]).relative_to(tmp)).replace('\\', '/') for f in files}


class VaultSearchLineTests(VaultFixture, unittest.TestCase):
    """`vault search --line`：材料层也要跟着分，不只是概念页。"""

    def test_聊天线只给聊天那两处(self):
        got = self.collected('chat')
        self.assertIn(self.CHAT_CONCEPT, got)
        self.assertIn(self.CHAT_CARD, got, '聊天卡是聊天线的材料')
        self.assertNotIn(self.WIKI_CONCEPT, got)
        self.assertNotIn(self.USER_NOTE, got, '用户自己的笔记是文章线')
        self.assertEqual([p for p in got if p.startswith('biz/')], [], '日报文章是文章线')

    def test_文章线不给聊天那两处(self):
        got = self.collected('wiki')
        self.assertIn(self.WIKI_CONCEPT, got)
        self.assertIn(self.USER_NOTE, got)
        self.assertTrue([p for p in got if p.startswith('biz/')], '日报文章该在文章线里')
        self.assertNotIn(self.CHAT_CONCEPT, got)
        self.assertNotIn(self.CHAT_CARD, got)

    def test_all_两条都给(self):
        got = self.collected('all')
        for rel in (self.WIKI_CONCEPT, self.CHAT_CONCEPT, self.CHAT_CARD, self.USER_NOTE):
            self.assertIn(rel, got)
        self.assertTrue([p for p in got if p.startswith('biz/')])

    def test_type_与_line_是两根独立的轴(self):
        # `--type concept --line chat` 只该给聊天线的概念页：一个不给另一个让路
        self.assertEqual(self.collected('chat', search_type='concept'), {self.CHAT_CONCEPT})
        self.assertEqual(self.collected('wiki', search_type='concept'), {self.WIKI_CONCEPT})
        # 反过来也钉一下：`--type note --line chat` 只剩聊天卡那一层
        self.assertEqual(self.collected('chat', search_type='note'), {self.CHAT_CARD})


class VaultRagLineTests(VaultFixture, unittest.TestCase):
    """`vault rag` 的上下文也要按线分（用户的笔记层与日报都归文章线）。"""

    def context(self, line):
        with tempfile.TemporaryDirectory() as tmp:
            vault, biz = self.build(tmp)
            return vr.collect_context(vault, biz, '甲甲甲', 10, line=line)

    def test_聊天线只给聊天线的概念页(self):
        ctx = self.context('chat')
        self.assertTrue(ctx, '聊天线那两张页里都写着这个词，不该是空的')
        self.assertEqual({c['source'] for c in ctx}, {'concept'})
        self.assertEqual({c['title'] for c in ctx}, {'聊天概念'})

    def test_文章线不给聊天线的概念页(self):
        ctx = self.context('wiki')
        titles = {c['title'] for c in ctx}
        self.assertIn('文章概念', titles)
        self.assertNotIn('聊天概念', titles)
        self.assertIn('note', {c['source'] for c in ctx}, '用户自己的笔记是文章线的上下文')
        self.assertIn('article', {c['source'] for c in ctx}, '日报文章也是')

    def test_all_三条来源都在(self):
        self.assertEqual({c['source'] for c in self.context('all')},
                         {'concept', 'note', 'article'})


class SemanticLineTests(VaultFixture, unittest.TestCase):
    """语义检索的 `line`：**掩码必须在取候选之前生效**。"""

    # 4 条记录：2 条文章（与查询同向、余弦 1.0）、2 条聊天（正交、余弦 0.0）。
    # 这么造是为了让"先取 top-k 再过滤"那种写法**必然露馅**：它按分数取到 2 条文章，
    # 过滤掉之后返回 0 条 —— 而正确答案是 2 条聊天。
    VECTORS = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
    META = [{'type': 'article', 'title': 'a1'}, {'type': 'article', 'title': 'a2'},
            {'type': 'chat', 'title': 'c1'}, {'type': 'chat', 'title': 'c2'}]

    def fake_index(self, tmp):
        np = ss.require_numpy()
        index = Path(tmp) / 'idx'
        index.mkdir(parents=True, exist_ok=True)
        vectors = index / 'vectors.npy'
        meta = index / 'meta.json'
        np.save(str(vectors), np.array(self.VECTORS, dtype=np.float32))
        meta.write_text(json.dumps(self.META, ensure_ascii=False), encoding='utf-8')
        return vectors, meta

    def run_search(self, line, tmp, top_k=10):
        vectors, meta = self.fake_index(tmp)
        with patch.object(ss, 'VECTORS_FILE', vectors), \
             patch.object(ss, 'META_FILE', meta), \
             patch.object(ss, 'get_embeddings', lambda texts, key: [[1.0, 0.0]]):
            return ss.search('任意查询', 'fake-key', top_k=top_k,
                             rerank_results=False, line=line)

    def test_聊天线只回聊天那两条_且条数不是零(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.run_search('chat', tmp)
        # 用集合比：这 4 条的分数只有 1.0 和 0.0 两种，**并列的两条之间不该钉顺序**
        # （钉了就是在断言 argsort 的稳定顺序，那不是我们要保护的性质）
        self.assertEqual(sorted(r['title'] for r in rows), ['c1', 'c2'],
                         '掩码要在 argsort 之前：先取 top-k 再过滤会回 0 条')

    def test_文章线只回文章那两条(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.run_search('wiki', tmp)
        self.assertEqual({r['title'] for r in rows}, {'a1', 'a2'})

    def test_all_四条都回(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.run_search('all', tmp)
        self.assertEqual(len(rows), 4)

    def test_要求十条也只有本线的那两条(self):
        # `pool_size` 不能因为"掩码挡掉的那些还在数组里"而给出 -inf 的垃圾行
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.run_search('chat', tmp, top_k=10)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertNotIn(row['type'], ('article',), '别的线不许混进来')

    def test_索引里没有这条线时回空(self):
        with tempfile.TemporaryDirectory() as tmp:
            np = ss.require_numpy()
            index = Path(tmp) / 'idx'
            index.mkdir(parents=True, exist_ok=True)
            vectors = index / 'vectors.npy'
            meta = index / 'meta.json'
            np.save(str(vectors), np.array([[1.0, 0.0]], dtype=np.float32))
            meta.write_text(json.dumps([{'type': 'article', 'title': 'a1'}]), encoding='utf-8')
            with patch.object(ss, 'VECTORS_FILE', vectors), \
                 patch.object(ss, 'META_FILE', meta), \
                 patch.object(ss, 'get_embeddings', lambda texts, key: [[1.0, 0.0]]):
                rows = ss.search('任意查询', 'fake-key', rerank_results=False, line='chat')
        self.assertEqual(rows, [], '一条聊天记录都没有时回空，不许拿文章条充数')

    def test_line_counts_按线数(self):
        with tempfile.TemporaryDirectory() as tmp:
            vectors, meta = self.fake_index(tmp)
            with patch.object(ss, 'META_FILE', meta):
                counts = ss.line_counts()
        self.assertEqual(counts, {'all': 4, 'wiki': 2, 'chat': 2})

    def test_认不出的线要报错_不静默当all(self):
        with self.assertRaises(ValueError):
            ss.line_types('wik')
        with self.assertRaises(ValueError):
            ss.search('x', 'fake-key', line='wik')

    def test_关键词回退也按线分(self):
        # 没有索引时走关键词那条：文章线只扫日报、聊天线只扫聊天消息。
        # **只断言文章那一侧的进出**，不断言总数 —— 聊天那一侧要 import `mcp_bridge`，
        # 它会去读本机的库，在别的机器上可能整段跳过（那段本来就是 try/except）。
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / 'biz' / '2026-01-01' / 'AI'
            day.mkdir(parents=True, exist_ok=True)
            (day / 'p.md').write_text(
                '---\ntitle: "x"\nurl: http://example.invalid/x\n---\n\n甲甲甲\n', encoding='utf-8')
            root = str(Path(tmp) / 'biz')

            def article_hits(line):
                return [h for h in ss.keyword_search('甲甲甲', 5, root=root, line=line)
                        if str(h['source']).endswith('p.md')]

            # 对照（正向）：这条材料在 wiki / all 下必须找得到 —— 少了它，
            # 下面那条"聊天线找不到"可能只是"什么都没跑"
            self.assertEqual(len(article_hits('wiki')), 1)
            self.assertEqual(len(article_hits('all')), 1)
            self.assertEqual(article_hits('chat'), [], '只看聊天线时不该扫日报文章')


if __name__ == '__main__':
    unittest.main()
