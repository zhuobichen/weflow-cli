"""`wiki_lint.py`：知识库体检（纯函数，不联网、不读真库）。

为什么值得有这份测试：体检工具**自己**的失效方式是"报得吓人但不准"——
一视同仁地报"40 条死链"，看的人只会以为库烂了，于是下次不再看它。
所以这里钉的是**分类对不对**：`## 相关概念` 里没建页的是扩张候选，
`## 来源` 里断掉的才是真断；卡片侧入链要算进"孤儿"判定，否则每张页都会被误报成孤儿。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('wiki_lint', SCRIPTS / 'wiki_lint.py')
wl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wl)


def page(stem, body, title='', aliases=()):
    return {'stem': stem, 'title': title or stem, 'links': wl.extract_links(body), 'body': body,
            'aliases': list(aliases)}


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
        # 与 `resolvable_names` 一致：**别名也算"存在"**（真代码里它就是这么算的，
        # 所以那 28 张的断链是 0 —— 缺的只是入链计数那一步）
        aliases = {a for p in pages for a in (p.get('aliases') or [])}
        allowed = set(existing) | stems | aliases | {'2026-09-05-某篇.md'}
        return wl.inspect(pages, lambda name: name in allowed, cards)

    def test_别名要在入链计数里归位到它那一页(self):
        """文件名是概念名的**有损变换**（`:` `/ ? * " < > |` 换成 `_`、再截到 60 字符），
        而卡片里那条链接用的是**原名**。

        `resolvable_names` 早就把别名算进"存在"了（所以断链报的是 0），入链计数原来却只认页码
        —— 一张明明被卡片链着的页，因为名字对不上而报成孤儿。2026-10-01 实测：卡片写
        `[[Qwen3.5:9B]]`、文件名是 `Qwen3.5_9B.md`，49,953 页里有 28 张是这样。
        """
        report = self.classify([page('Qwen3.5_9B', PAGE_B, aliases=['Qwen3.5:9B'])],
                               cards=['Qwen3.5:9B'])
        self.assertEqual(report['orphans'], [], '别名归位之后不该再是孤儿')
        # 反向钉住"别名确实起了作用"：同样的页、同样的卡片链接，**没有别名时它就该是孤儿**。
        # （少了这一条，一个把孤儿恒判为空的实现也能让上面那句通过。）
        self.assertEqual(
            self.classify([page('Qwen3.5_9B', PAGE_B)], cards=['Qwen3.5:9B'])['orphans'],
            ['Qwen3.5_9B'])

    def test_标题与别名里自己的引号不许被剥掉(self):
        """`parse_frontmatter` 已经把外层引号拆掉了，加载时**不能再剥一次**。

        `strip('"')` 剥的是"首尾所有引号字符"，于是名字**自己**末尾那个引号也被吃掉：
        `AI 长出"手脚"` → `AI 长出"手脚`，与卡片里那条链接差一个字符，永远匹配不上 ——
        那页就被报成孤儿，而且看不出为什么。（2026-10-01 实测：49,953 页里 1 张。）
        """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'x.md').write_text(
                '---\ntitle: "AI 长出"手脚""\naliases: [AI 长出"手脚"]\n---\n\n正文\n',
                encoding='utf-8')
            pages = wl.collect(tmp, ())
        self.assertEqual(pages[0]['title'], 'AI 长出"手脚"')
        self.assertEqual(pages[0]['aliases'], ['AI 长出"手脚"'])

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
    """同一个节点被写成两个概念名——**孤儿检查抓不到它**（两个名字都有入链）。

    2026-09-28 换过一次判据，测试跟着重写。旧的那条（"最长公共子串 ≥5 字且占较短标题
    ≥40%"）在 22,374 张页上报出 424,697 组、失去了分辨力，量测与抽样记录在
    `near_duplicate_titles` 的 docstring 里。现在分两档，两档各有用例，另外两条钉的是
    **分桶等价性**与**枢纽过滤**——那两处错了都不会报错，只会安静地少给或多给。
    """

    def pages(self, *titles):
        body = '# x' + chr(10) + chr(10) + '够长的正文，写满八十个字以上免得被当空页，这里再补几个字。' * 3
        return [dict(page(t, body, title=t)) for t in titles]

    def test_只是写法不同归到同一个节点(self):
        got = wl.near_duplicate_titles(
            self.pages('GLM 5.1', 'GLM-5.1', 'AI 编程', 'AI编程'))['sameNode']
        self.assertEqual(sorted(got), [['AI 编程', 'AI编程'], ['GLM 5.1', 'GLM-5.1']])

    def test_一个规范形有多种写法时报成一组而不是两对(self):
        """报成两两配对会让人看不出它们是同一个节点——实测有 3 种写法的
        （`Academic Research Skills` 一族）。"""
        got = wl.near_duplicate_titles(
            self.pages('Grok 4.1', 'Grok-4.1', 'Grok4.1'))['sameNode']
        self.assertEqual(got, [['Grok 4.1', 'Grok-4.1', 'Grok4.1']])

    def test_规范形一致但名字完全相同的不算(self):
        """同名是另一条检查（`duplicateTitles`）的事，这里不该重复报。"""
        got = wl.near_duplicate_titles(self.pages('甲概念', '甲概念'))['sameNode']
        self.assertEqual(got, [])

    def test_同口径与合并工具一致(self):
        """这一档的价值就在于**可执行**：名单上的每一组都是
        `compile_wiki --merge-duplicates` 真会合并的。两处口径不同的话，报出来的一半
        是那个工具不会动的 —— 2026-09-28 之前正是如此（lint 把所有非字母数字都去掉，
        于是 `DeepSeek++` 等于 `DeepSeek`，而合并工具不这么认为）。
        """
        cases = [('GLM 5.1', 'GLM-5.1'), ('AI skills', 'AI skill'), ('GPT 5.6', 'GPT-5.6')]
        for a, b in cases:
            self.assertEqual(wl.normalize_concept_name(a), wl.normalize_concept_name(b),
                             '%s / %s 应当同口径' % (a, b))

    def test_归一化只有一份实现(self):
        """两份"写得一样"的实现迟早会分叉，所以钉的是**同一个函数对象**。

        实测（2026-09-28，22,374 张页）：lint 报出的每一组都在合并工具的分组里
        （`only_lint = 0`）；反过来的 14 组是"两个文件撞了同一个名字"，那一类 lint 在
        `duplicateTitles` 里另有报告。这个包含关系是上面那句"名单可执行"的依据。
        """
        spec = importlib.util.spec_from_file_location('compile_wiki', SCRIPTS / 'compile_wiki.py')
        cw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cw)
        utils = sys.modules['_utils']
        self.assertIs(wl.normalize_concept_name, cw.normalize_concept_name,
                      'lint 与合并工具必须是同一份实现')
        self.assertIs(wl.normalize_concept_name, utils.normalize_concept_name,
                      '规范形住在 _utils，两边都从那里取')

    def test_加了限定的算线索不算同名(self):
        got = wl.near_duplicate_titles(
            self.pages('Agent Harness', 'Agent Harness研讨会'))
        self.assertEqual(got['sameNode'], [])
        self.assertEqual(got['contained'], [['Agent Harness', 'Agent Harness研讨会']])

    def test_枢纽不当重复(self):
        """`DeepSeek` 被 `DeepSeek 融资`、`DeepSeek-V4 涨价`……一个个裹进去。

        这是**枢纽**（实测 `agent` 被 731 个名字包含、`模型` 643 个），不是重复。判据是
        "短名字已经被 ≥ DUP_HUB_LIMIT 个别的名字包含"。
        """
        titles = ['DeepSeek'] + ['DeepSeek %s' % s for s in
                                ('融资', '涨价', '开源', '招聘', '封号', '算力')]
        got = wl.near_duplicate_titles(self.pages(*titles))
        self.assertEqual(got['contained'], [], '枢纽不该和它的子话题成对')

    def test_跨两条线的不算重复(self):
        """两个概念目录是**两个知识库**，同名各一张是设计使然，而且
        `--merge-duplicates` 按目录跑、永远动不了跨线的组。

        2026-09-28 实测踩到：合并跑完后报告还剩 2 组（`AI 工具`、`GLORIA`），全是跨线的，
        而报告写着"这些就是合并会合并的"。跨线同名由 `crossLineSameName` 那一节负责
        （2026-10-10 起它单列一节、**不当成问题**；`duplicateTitles` 只管同一条线内撞名）。
        """
        left, right = self.pages('AI 工具'), self.pages('AI工具')
        left[0]['dir'], right[0]['dir'] = 'Wiki/Concepts', 'Chat/Concepts'
        got = wl.near_duplicate_titles(left + right)
        self.assertEqual(got['sameNode'], [], '跨目录的两种写法不该成组')
        # 同一个目录里就该成组 —— 否则这条用例可能只是"什么都不报"而通过
        for p in left + right:
            p['dir'] = 'Wiki/Concepts'
        self.assertEqual(wl.near_duplicate_titles(left + right)['sameNode'],
                         [['AI 工具', 'AI工具']])

    def test_不相关的标题不会误报(self):
        got = wl.near_duplicate_titles(self.pages('椰子水全覆盖风险排查', '全国人大常委会会议'))
        self.assertEqual(got, {'sameNode': [], 'contained': []})

    def test_分桶与全量两两比结果逐组一致(self):
        """分桶是**等价**的优化，不是"大概一样"。

        两档判据都要求共享一个 ≥8 字的子串（同名是全等、包含是共享短的那一个），所以按
        8-gram 建倒排索引、只在桶内两两比，结果与全量两两比**逐组相同**。这条用随机数据
        对拍，防的是将来有人改判据却忘了分桶的前提（那时它会**静默漏报**：报告还是照常出，
        只是少了一批）。

        **参考实现用字面量，不读模块里的常量** —— 这是这条用例的要害。第一版读的是
        `wl.DUP_MIN_NAME`，于是"把分桶宽度从 8 改成 10"这种改动两边一起变，对拍全绿而
        报告少给一半（变异测试实测：那条变异**没被抓到**）。门槛一旦被改成字面量契约，
        它就会红。
        """
        import random
        random.seed(9)
        words = ['Agent', 'Claude', 'Code', '工具', '模型', '上下文', '窗口',
                 'Token', '计费', '节省', 'RAG', '检索']
        pages = []

        def add(name):
            pages.append({'title': name, 'stem': name})

        # 数据要**同时喂到两档**，否则对拍只证明了一条路径：一半的名字配一个只差写法的
        # 孪生（sameNode），一半配一个加限定的变体（contained），再掺入短文噪声。
        for i in range(120):
            base = ''.join(random.choice(words) for _ in range(random.randint(3, 6)))
            add(base)
            style = i % 4
            if style == 0:
                add(' '.join(base))                                  # 只差空格
            elif style == 1:
                add(base + random.choice(['版', '报告', '整理', '纪要']))   # 加限定
            elif style == 2:
                add(''.join(random.choice(words) for _ in range(2)))  # 与谁都无关
            else:
                add(base.upper() if base != base.upper() else base + 'x')  # 只差大小写

        # 判据的**契约值**（与 docstring 里写的一致）。实现改了这些数而这里没改，就该红。
        MIN_SHARED, COVER, HUB_LIMIT = 8, 0.6, 5
        self.assertEqual((wl.DUP_MIN_NAME, wl.DUP_COVER_RATIO, wl.DUP_HUB_LIMIT),
                         (MIN_SHARED, COVER, HUB_LIMIT),
                         '改了门槛就要同时改这条断言与下面的参考实现，别只改实现')

        # 全量口径的参考实现：规范化后按名字排序（与实现同序，这样"取哪个写法"也一致）
        raw, buckets = {}, {}
        for name in sorted({p['title'] or p['stem'] for p in pages}):
            key = wl.normalize_concept_name(name)
            if not key:
                continue
            raw.setdefault(key, name)
            buckets.setdefault(key, set()).add(name)
        keys = sorted(raw)
        hubs = {k: sum(1 for other in keys if other != k and k in other) for k in keys}

        ref_same = sorted(sorted(v) for v in buckets.values() if len(v) > 1)
        ref_cont = []
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                short, long_ = sorted((keys[i], keys[j]), key=len)
                if len(short) < MIN_SHARED or len(short) < COVER * len(long_):
                    continue
                if short in long_ and hubs[short] < HUB_LIMIT:
                    ref_cont.append([raw[short], raw[long_]])
        ref_cont.sort()

        got = wl.near_duplicate_titles(pages)
        self.assertTrue(len(ref_same) + len(ref_cont) > 50,
                        '这组随机数据要真的产出足够多的组，否则对拍没意义')
        self.assertEqual(got['sameNode'], ref_same)
        self.assertEqual(got['contained'], ref_cont)

    def test_比门槛还短的名字不参与(self):
        """子串不可能比名字本身长，所以短于 `DUP_MIN_NAME` 的名字永远满足不了判据。
        分桶时跳过它们是对的 —— 但要说得出为什么。"""
        pages = [{'title': '短', 'stem': '短'}, {'title': '短A', 'stem': '短A'}]
        self.assertEqual(wl.near_duplicate_titles(pages), {'sameNode': [], 'contained': []})


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


    def test_别名也算指向这一页(self):
        """`--merge-duplicates` 把 `GPT-5.6` 并进 `GPT 5.6` 时靠的就是 aliases。

        体检不认别名的话，会把它报成"还没建页、建议再跑 compile"——而它已经有页了。
        """
        pages = [
            {'stem': 'GPT 5.6', 'aliases': ['GPT-5.6'], 'title': '', 'links': [], 'body': ''},
            {'stem': '甲', 'aliases': [], 'title': '', 'links': [], 'body': ''},
        ]
        names = wl.resolvable_names(pages)
        self.assertIn('GPT-5.6', names)
        self.assertIn('GPT 5.6', names)
        self.assertNotIn('GPT5.6', names, '没写在 aliases 里的名字不在里面')


if __name__ == '__main__':
    unittest.main()
