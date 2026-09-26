"""`wiki_lint.py`：知识库体检（纯函数，不联网、不读真库）。

为什么值得有这份测试：体检工具**自己**的失效方式是"报得吓人但不准"——
一视同仁地报"40 条死链"，看的人只会以为库烂了，于是下次不再看它。
所以这里钉的是**分类对不对**：`## 相关概念` 里没建页的是扩张候选，
`## 来源` 里断掉的才是真断；卡片侧入链要算进"孤儿"判定，否则每张页都会被误报成孤儿。
"""
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('wiki_lint', SCRIPTS / 'wiki_lint.py')
wl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wl)


def page(stem, body, title=''):
    return {'stem': stem, 'title': title or stem, 'links': wl.extract_links(body), 'body': body}


# 正文要够长（体检把"少于 80 字的页"当空页），所以这两条 fixture 写成正常概念的篇幅
PAGE_A = ('# 甲\n甲是一个概念，这句话充当它的定义，长度按真实概念页来写，免得被当成空页。\n\n'
          '## 关键要点\n\n- 第一条要点，写得具体一点\n- 第二条要点，也写得具体一点\n\n'
          '## 相关概念\n\n- [[乙]]\n- [[还没建的概念]]\n\n'
          '## 来源\n\n- [[2026-09-05-某篇.md]] — 某篇\n')
PAGE_B = ('# 乙\n乙也是一个概念，同样给一段够长的定义，让它不至于被空页规则误伤。\n\n'
          '## 关键要点\n\n- 要点一，具体\n- 要点二，具体\n\n'
          '## 相关概念\n\n- [[甲]]\n')


class LinkTests(unittest.TestCase):
    def test_按小节取链接(self):
        # 分类全靠它：`## 相关概念` 里的是扩张候选，`## 来源` 里的是卡片引用
        pairs = wl.links_by_section(PAGE_A)
        self.assertIn(('相关概念', '乙'), pairs)
        self.assertIn(('相关概念', '还没建的概念'), pairs)
        self.assertIn(('来源', '2026-09-05-某篇.md'), pairs)

    def test_故意不过滤路径与主题词(self):
        # 生成那侧会滤掉路径链接；体检**必须不滤**——滤掉就等于把断链藏起来
        self.assertEqual(wl.extract_links('- [[a/b.md]] [[X]]'), ['a/b.md', 'X'])


class ResolveTests(unittest.TestCase):
    def test_概念看同名页_卡片看目录里有没有那个文件(self):
        stems = {'甲', '乙'}
        self.assertTrue(wl.resolve('乙', stems, ()))
        self.assertFalse(wl.resolve('丙', stems, ()))
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '2026-09-05-某篇.md').write_text('x', encoding='utf-8')
            self.assertTrue(wl.resolve('2026-09-05-某篇.md', stems, (tmp,)))
            self.assertFalse(wl.resolve('2026-09-05-别的.md', stems, (tmp,)))


class InspectTests(unittest.TestCase):
    def classify(self, pages, cards=(), existing=()):
        stems = {p['stem'] for p in pages}
        allowed = set(existing) | stems | {'2026-09-05-某篇.md'}
        return wl.inspect(pages, lambda name: name in allowed, cards)

    def test_相关概念里没建页的算扩张候选_不算断链(self):
        report = self.classify([page('甲', PAGE_A), page('乙', PAGE_B)])
        self.assertEqual(report['broken'], [], '相关概念那节里的不该报成断链')
        self.assertEqual([item['target'] for item in report['aspirational']], ['还没建的概念'])

    def test_来源指向不存在的卡片才算断链(self):
        body = PAGE_A.replace('2026-09-05-某篇.md', '2026-09-05-已经删了的.md')
        report = self.classify([page('甲', body)])
        self.assertEqual([item['target'] for item in report['broken']],
                         ['2026-09-05-已经删了的.md'])

    def test_卡片入链要算进孤儿判定(self):
        # 甲被卡片提到（`cards=['甲']`），而丙**没有任何入链**（页面不链它、卡片不链它）
        lonely = page('丙', '# 丙\n一个够长的定义，写满八十个字以上，免得被空页规则误伤，'
                            '这里再补几个字凑够长度。\n\n## 关键要点\n\n- 要点一，具体\n- 要点二，具体\n')
        report = self.classify([page('甲', PAGE_A), lonely], cards=['甲'])
        self.assertEqual(report['orphans'], ['丙'])
        # 反过来：卡片不提甲的话，甲就成了孤儿（页面之间没人链它）。
        # 用集合比，别依赖排序——中文按 Unicode 排，不是拼音
        self.assertEqual(set(self.classify([page('甲', PAGE_A), lonely])['orphans']), {'甲', '丙'})

    def test_页面互链也算入链(self):
        report = self.classify([page('甲', PAGE_A), page('乙', PAGE_B)])
        self.assertEqual(report['orphans'], [], '甲↔乙互相链着，两个都不该是孤儿')

    def test_空页与同名页(self):
        report = self.classify([page('甲', '# 甲\n短'), page('乙', '# 另一个\n短', title='甲')])
        self.assertEqual([item['page'] for item in report['empty']], ['甲', '乙'])
        self.assertEqual(report['duplicateTitles'], {'甲': ['甲', '乙']})

    def test_没问题时四个清单都是空的(self):
        report = self.classify([page('甲', PAGE_A), page('乙', PAGE_B)])
        self.assertEqual(report['broken'], [])
        self.assertEqual(report['orphans'], [])
        self.assertEqual(report['empty'], [])
        self.assertEqual(report['duplicateTitles'], {})


class NearDuplicateTests(unittest.TestCase):
    """同一件事被多个来源写成两个概念名——**孤儿检查抓不到它**（两个名字都有入链）。

    实测抓到的一组：`尼泊尔热索瓦泥石流` 与 `热索瓦泥石流灾害`。只报告不去重：
    不同来源的写法可能各有信息，删哪个该由人定。
    """

    def pages(self, *titles):
        body = '# x' + chr(10) + chr(10) + '够长的正文，写满八十个字以上免得被当空页，这里再补几个字。' * 3
        return [dict(page(t, body, title=t)) for t in titles]

    def test_同一件事的两种写法会被认出来(self):
        got = wl.near_duplicate_titles(self.pages('尼泊尔热索瓦泥石流', '热索瓦泥石流灾害'))
        self.assertEqual(len(got), 1)

    def test_不相关的标题不会误报(self):
        got = wl.near_duplicate_titles(self.pages('椰子水全覆盖风险排查', '全国人大常委会会议'))
        self.assertEqual(got, [])

    def test_公共子串长度(self):
        self.assertEqual(wl.longest_common_run('热索瓦泥石流灾害', '尼泊尔热索瓦泥石流'), 6)
        self.assertEqual(wl.longest_common_run('甲', '乙'), 0)
        self.assertEqual(wl.longest_common_run('', '乙'), 0)


class DegenerateFieldTests(unittest.TestCase):
    """一个字段整列同一个值——看起来像有元数据，实际什么也没说，而**它会被照着信**。

    实测：39 张概念页的 `topics` 全是「学术」（同一批语料上另一个独立测量：标题像新闻的
    224 篇里 40 篇也被标成「学术」）。这条不修分类器（那是日报那条线的事），只是让它可见。
    """

    def pages_with_topic(self, topic, count):
        # 正文用换行拼出来（不写字面量里的反斜杠-n：这文件是 CRLF，混着写很容易写坏）
        body = chr(10).join([
            '# 第%d' % count,
            '一段够长的正文，写满八十个字以上免得被当空页规则误伤，这里再补几个字凑够长度。',
            '',
            '## 关键要点',
            '',
            '- 要点一',
        ])
        return [dict(page('第%d' % i, body, title='第%d' % i), topic=topic)
                for i in range(count)]

    def test_整列同值会被报出来(self):
        got = wl.degenerate_fields(self.pages_with_topic('学术', 6))
        self.assertIn('topic', got)
        self.assertEqual(got['topic']['值'], '学术')

    def test_取值有区分时不报(self):
        pages = self.pages_with_topic('学术', 3) + self.pages_with_topic('新闻', 3)
        self.assertEqual(wl.degenerate_fields(pages), {}, '各占一半不算退化')

    def test_页太少时不报(self):
        self.assertEqual(wl.degenerate_fields(self.pages_with_topic('学术', 2)), {},
                         '样本太小不下结论')


if __name__ == '__main__':
    unittest.main()
