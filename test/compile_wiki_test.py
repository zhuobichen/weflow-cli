"""`compile_wiki.py` 的解析与聚合（纯函数，不联网、不读库）。

这个脚本此前**一个测试都没有**，而它是知识库那条文章线的核心：`output/biz-daily` 的上万篇
材料靠它聚成概念页。它的失效方式全是静默的——wikilink 解析错了就少聚合一批概念、
`desc` 丢了就退化成"用泛泛的摘要拼每一页"，都不会报错。

新加的 `scripts/chat_notes.py` 也必须满足这里的**输入契约**（同一套 frontmatter + `[[…]]`），
所以这份测试同时是那条新管线的契约：生产方写的 markdown 必须能被 `scan_articles` 认出来。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('compile_wiki', SCRIPTS / 'compile_wiki.py')
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)


def write_note(root: Path, rel: str, frontmatter: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\n{frontmatter}\n---\n\n{body}\n', encoding='utf-8')


class ScanTests(unittest.TestCase):
    def test_scan_reads_frontmatter_wikilinks_and_descriptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_note(root, '2026-09-20/AI/某号-标题.md',
                       'title: 某号-标题\nsource: 某号\ntopic: AI\ntags: [a, b]',
                       '## AI 摘要\n\n这段话说的是摘要正文。\n\n'
                       '## 主题\n\n- [[词向量]] — 这篇文章里它是这么被讲的\n- [[老王]]\n')
            notes = cw.scan_articles(str(root))
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note['title'], '某号-标题')
        self.assertEqual(note['source'], '某号')
        self.assertEqual(note['tags'], ['a', 'b'])
        self.assertIn('摘要正文', note['summary'])
        # 三元组的第三位是**"它是人物还是话题"**（聊天卡分 `### 人` / `### 话题` 两节写），
        # 文章笔记里没有那两个小节，所以是空串——"没标注"与"标为话题"分得开
        self.assertEqual(note['wikilinks'][0], ('词向量', '这篇文章里它是这么被讲的', ''),
                         '破折号后面那句是"关于这个概念说的那句话"，必须留下来')
        self.assertEqual(note['wikilinks'][1], ('老王', '', ''),
                         '没有描述的 wikilink 也要收（描述可以空）')

    def test_没有_wikilink_的材料被跳过(self):
        # 跳过是有意的：没有 wikilink 就没有概念可聚合
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_note(root, 'a.md', 'title: 甲', '## AI 摘要\n\n没有链接的材料。\n')
            self.assertEqual(cw.scan_articles(str(root)), [])

    def test_README_不参与(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_note(root, 'README.md', 'title: 说明', '- [[某概念]]')
            write_note(root, 'b.md', 'title: 乙', '- [[某概念]]')
            notes = cw.scan_articles(str(root))
        self.assertEqual([n['title'] for n in notes], ['乙'])


class AggregateTests(unittest.TestCase):
    def test_同一篇里重复提到只算一次引用(self):
        notes = [{'title': '甲', 'source': 's', 'summary': '摘要', 'file': 'a.md',
                  'wikilinks': [('概念X', '第一次'), ('概念X', '第二次')]}]
        concepts = cw.aggregate_concepts(notes)
        self.assertEqual(len(concepts['概念X']), 1, '同一篇里的第二次提及不该算成第二个来源')
        self.assertEqual(concepts['概念X'][0]['desc'], '第一次', '保留的是第一次那条描述')

    def test_多篇聚到同一个概念上(self):
        notes = [
            {'title': '甲', 'source': 's1', 'summary': 's', 'file': 'a.md', 'wikilinks': [('X', 'x1')]},
            {'title': '乙', 'source': 's2', 'summary': 's', 'file': 'b.md', 'wikilinks': [('X', 'x2')]},
        ]
        concepts = cw.aggregate_concepts(notes)
        self.assertEqual([r['title'] for r in concepts['X']], ['甲', '乙'])


class SummaryHeadingTests(unittest.TestCase):
    def test_认_vault_里那批笔记的标题_而不靠兜底(self):
        # 1633 篇笔记用的是 `## 📋 摘要`（`create_reading_notes.py` 写的），而这里原来只认
        # `AI 摘要`/`深度解析`——一直靠"正文第一段"兜底碰巧读到同一段。这条把它变成契约。
        body = '## 📋 摘要\n\n这就是摘要正文。\n\n## 💡 核心观点\n\n- 要点\n'
        self.assertEqual(cw._extract_summary(body), '这就是摘要正文。')

    def test_两个标题都在时取文档里先出现的那个(self):
        # 我一开始把这条写成"AI 摘要 优先"，而代码并不是那样——**按文档顺序取第一个**。
        # 保持这个更简单的规则（可预测，不因为标题叫什么而跳段），并把行为写在这里：
        # 一篇笔记通常只有其中一个小节，两个都在是异常情况。
        body = '## 📋 摘要\n\n先出现的这个。\n\n## AI 摘要\n\n后出现的那个。\n\n## 其他\n\nx\n'
        self.assertEqual(cw._extract_summary(body), '先出现的这个。')

    def test_没有摘要小节时仍然退回第一段(self):
        # 兜底要留着：不是每个生产方都会写那一节
        body = '一段没有标题的话。\n\n## 别的\n\nx\n'
        self.assertEqual(cw._extract_summary(body), '一段没有标题的话。')

    def test_引文与列表不算第一段(self):
        body = '> 引用不算\n\n- 列表也不算\n\n这句才算。\n'
        self.assertEqual(cw._extract_summary(body), '这句才算。')


class TopicTests(unittest.TestCase):
    def test_两个键都认_且拆掉方括号(self):
        # `hasTopic: [[AI]]` 是 Vault 里的写法（Obsidian 的双链），拆成 `AI`
        self.assertEqual(cw.article_topic({'hasTopic': ['[AI]']}), 'AI')
        self.assertEqual(cw.article_topic({'hasTopic': '[AI]'}), 'AI')
        self.assertEqual(cw.article_topic({'topic': 'AI'}), 'AI')
        self.assertEqual(cw.article_topic({}), '')

    def test_topic_优先于_hasTopic(self):
        self.assertEqual(cw.article_topic({'topic': '聊天', 'hasTopic': ['[AI]']}), '聊天')


class VaultNoteContractTests(unittest.TestCase):
    """**现有 1633 篇笔记的契约**：拿真实形状的文件走一遍聚合器。

    这条比逐条单测重要：它钉的是"库里已经躺着的那批材料，现在还能不能进概念页"。
    形状取自实测的一篇（frontmatter 带 hasTopic、正文七个 emoji 小节）。
    """

    # 形状取自实测的一篇真笔记（除了最后那行"真概念"——**真实库里没有**，见下面那条测试）
    REAL_NOTE = ('---\n'
                 'title: 某篇文章\n'
                 'aliases: []\n'
                 'source: 开源星探\n'
                 'source_type: wechat-article\n'
                 'hasTopic: [[AI]]\n'
                 'tags: [source/wechat, ai, status/unread]\n'
                 'published: 2026-08-27\n'
                 '---\n\n'
                 '## 📋 摘要\n\n'
                 '这篇文章讲了一个新工具。\n\n'
                 '> - **主题**: [[AI]]\n\n'
                 '## 💡 核心观点\n\n'
                 '- 观点一\n\n'
                 '## 🧩 概念\n\n'
                 '- [[MCP 协议]] — 它把时间线开放给 Agent 操作\n\n'
                 '## 🔗 关联网络\n\n'
                 '- [[AI/另一篇文章.md]]\n'
                 '- [[学术/再一篇.md]]\n')

    def test_真实形状的笔记能被完整读进来(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_note(root, '占位.md', 'title: 占位', '- [[占位概念]]')   # 目录建好就删
            (root / '占位.md').unlink()
            (root / '某篇文章.md').write_text(self.REAL_NOTE, encoding='utf-8')
            notes = cw.scan_articles(str(root))

        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note['title'], '某篇文章')
        self.assertEqual(note['source'], '开源星探')
        self.assertEqual(note['topic'], 'AI', 'hasTopic 要能读出来（Obsidian 的 dataview 靠这个字段）')
        self.assertIn('这篇文章讲了一个新工具', note['summary'], '📋 摘要 那一段要抽得出来')
        # **只有"概念"那节里那条算概念**：主题行（与 hasTopic 同名）和关联网络（路径）都不算
        self.assertEqual([item[0] for item in note['wikilinks']], ['MCP 协议'])
        refs = cw.aggregate_concepts(notes)['MCP 协议']
        line = cw.build_ref_lines(refs)[0]
        self.assertIn('它把时间线开放给 Agent 操作', line)
        self.assertIn('AI', line, '主题要出现在参考行里（收了就要用）')

    def test_真实语料的实测结论_主题与关联网络都不算概念(self):
        """这条钉的是一个**实测事实**，不是一个设计选择。

        2026-09-26 把库里 1633 篇全扫了一遍：正文里的 `[[…]]` 只有两类——
        **1631 条与自己的主题同名**（`> - **主题**: [[AI]]` 那一行），
        以及 `## 🔗 关联网络` 里那些**路径形状**的笔记互链。**"其它"候选概念 0 条**：
        这批文章笔记里**根本没有概念那一节**。所以 `wiki compile` 在文章线上
        无论怎么跑都产不出概念页——缺的不是编译，是概念本身（要另加一道提炼）。
        """
        note = ('---\ntitle: 甲\nsource: 某号\nhasTopic: [[学术]]\n---\n\n'
                '## 📋 摘要\n\n摘要。\n\n'
                '> - **主题**: [[学术]]\n\n'
                '## 🔗 关联网络\n\n- [[学术/乙.md]]\n- [[学术/丙.md]]\n')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '甲.md').write_text(note, encoding='utf-8')
            # 过滤之后没有概念链接 → 这篇**不进**聚合（`scan_articles` 会跳过它）
            self.assertEqual(cw.scan_articles(str(root)), [],
                             '主题与关联网络都不是概念：这样的笔记不该产出概念页')
            # 但直接看过滤函数，能看清它丢的是哪两类
            _, body = cw.parse_frontmatter(note)
            kept = cw.concept_links(body, '学术')
            self.assertEqual(kept, [], f'不该留下任何概念，实际: {kept}')


class FilterTests(unittest.TestCase):
    """两处过滤**各自**都要能咬住——这是变异检查发现的洞。

    我原来只测了"关联网络那一节里的路径链接"，而那两个过滤是互补的：把路径过滤去掉，
    那节本来就被剥掉了；把"剥那一节"去掉，路径又被形状过滤挡住了——**随便去掉哪个都不红**。
    所以下面两条各自只针对一个过滤，且都放在对方管不到的位置上。
    """

    def test_路径形状的链接在普通小节里也不许当概念(self):
        body = '## 💡 核心观点\n\n- [[AI/某篇文章.md]] — 这是笔记引用，不是概念\n'
        self.assertEqual(cw.concept_links(body, 'AI'), [])

    def test_关联网络那一节里写什么都不算概念(self):
        # 那一节**按结构**就是"笔记之间的关系"；里面哪怕写的是个像概念的东西，也不该长出概念页
        body = '## 🔗 关联网络\n\n- [[MCP 协议]] — 看着像概念，但它在关系那一节里\n'
        self.assertEqual(cw.concept_links(body, 'AI'), [])


class OverviewHelperTests(unittest.TestCase):
    """索引页的两个坑（都是实测撞上的，不是想出来的）。

    - 概念页写出去的标题带引号（`title: "甲"`），当键用之前**必须去引号**，
      否则整页显示"引用数 0"——而它看起来只是"没人引用"；
    - 索引是每次 compile 重写的，而三条线各跑一次。用"本次源"的概念表数引用，
      索引会轮流被覆盖成只看到最后那条源的视角（实测症状：收藏线的概念全显示 0）。
    """

    def test_跨所有卡片目录数引用(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (('a.md', '- [[甲]]\n- [[乙]]\n'), ('b.md', '- [[甲]]\n')):
                (Path(tmp) / name).write_text(body, encoding='utf-8')
            counts = cw.count_cards_per_concept([tmp])
        self.assertEqual(counts, {'甲': 2, '乙': 1})

    def test_同一张卡里提到两次只算一次(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'a.md').write_text('- [[甲]]\n- [[甲]]\n', encoding='utf-8')
            self.assertEqual(cw.count_cards_per_concept([tmp]), {'甲': 1})

    def test_来源标签从卡片住在哪推断(self):
        # 借自那个考公库的 private/ 与"注意隐私"：我们的公开文章与私人对话是混在一起的，
        # 用嵌套标签分开，`tag:#来源/聊天` 一眼看出哪些页该当私密内容对待
        with tempfile.TemporaryDirectory() as tmp:
            article, chat = Path(tmp) / 'article-notes', Path(tmp) / 'chat-notes'
            article.mkdir(); chat.mkdir()
            (article / '某篇.md').write_text('x', encoding='utf-8')
            (chat / '某会话.md').write_text('x', encoding='utf-8')
            # 用集合比，别依赖顺序：中文按 Unicode 排（文 < 来），不是拼音——这一处我又栽了一次
            self.assertEqual(set(cw.source_kinds_for(['某篇.md', '某会话.md'], [str(article), str(chat)])),
                             {'来源/文章', '来源/聊天'})
            self.assertEqual(cw.source_kinds_for(['找不到.md'], [str(article)]), [],
                             '找不到就不加——不猜')

    def test_认不出的来源目录不加标签(self):
        self.assertEqual(cw.origin_tag('output/某个新目录'), '')

    def test_重贴标签是幂等的_并且会补未核验标记(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '甲.md'
            from _utils import write_with_frontmatter
            write_with_frontmatter(str(path), {'title': '"甲"', 'tags': ['话题']}, '# 甲\n正文。\n')
            self.assertEqual(cw.relabel_pages(tmp), 1, '第一次该补')
            fm, _ = cw.parse_frontmatter(path.read_text(encoding='utf-8'))
            self.assertEqual(fm['tags'], ['知识/概念', '话题'])
            self.assertIn('verified', fm)
            self.assertEqual(cw.relabel_pages(tmp), 0, '第二次不该再动（幂等）')


class RankTests(unittest.TestCase):
    """够不够格建页——这条闸门是**实测定的**。

    120 篇材料聚出 387 个概念，其中只有 37 个被 ≥2 篇提到：其余多半是那篇新闻里的人名、
    机构、单次事件。给它们逐条写页不是知识库，是剪报。
    """

    MAP = {'甲': [1, 2, 3], '乙': [1], '丙': [1, 2]}

    def test_按被提到的篇数排序(self):
        self.assertEqual([name for name, _ in cw.rank_concepts(self.MAP)], ['甲', '丙', '乙'])

    def test_min_refs_滤掉只被一篇提到的(self):
        got = [name for name, _ in cw.rank_concepts(self.MAP, 2)]
        self.assertEqual(got, ['甲', '丙'])

    def test_默认不过滤(self):
        self.assertEqual(len(cw.rank_concepts(self.MAP)), 3)

    def test_全都不够格时是空表_而不是报错(self):
        self.assertEqual(cw.rank_concepts({'甲': [1]}, 5), [])


class RefLineTests(unittest.TestCase):
    def test_参考行优先用_desc_而不是摘要(self):
        # 这条是"人物时间线"能不能成立的关键：desc 是"那时候发生了什么"，
        # 摘要只是那篇材料的整体摘要——只用后者的话，每个概念页都长一个样。
        refs = [{'title': '会话A', 'source': '老王', 'summary': '泛泛的摘要', 'desc': '9-20 说要把文件发我'}]
        line = cw.build_ref_lines(refs)[0]
        self.assertIn('9-20 说要把文件发我', line)
        self.assertNotIn('泛泛的摘要', line)

    def test_没有_desc_时退回摘要(self):
        refs = [{'title': '会话A', 'source': '老王', 'summary': '只有摘要', 'desc': ''}]
        self.assertIn('只有摘要', cw.build_ref_lines(refs)[0])

    def test_最多五条参考(self):
        refs = [{'title': f't{i}', 'source': 's', 'summary': 'x', 'desc': 'd'} for i in range(9)]
        self.assertEqual(len(cw.build_ref_lines(refs)), 5)

    def test_描述过长会被截断(self):
        refs = [{'title': 't', 'source': 's', 'summary': '', 'desc': '长' * 400}]
        line = cw.build_ref_lines(refs)[0]
        self.assertLessEqual(len(line), 150 + len('- [t]（s）：'))


class ParallelPageTests(unittest.TestCase):
    """并发生成概念页：**省的是时间，不许改的是"花多少钱"**。

    这一步是知识库线里第二处按页花钱的地方（第一处是 `article_notes`），所以两条纪律
    和那边一样：边收边写（不然几千次调用期间磁盘上一页都没有），以及——**这里多一条**：
    已存在的页要在提交给线程池**之前**滤掉。
    """

    def setUp(self):
        self.original = cw.generate_concept
        self.seen = []

    def tearDown(self):
        cw.generate_concept = self.original

    def fake(self, delay=None, fail_for=()):
        import time as _t
        if delay is None:
            delay = lambda name: 0.02 * (5 - len(name))   # noqa: E731
        def call(name, refs, api_key, origin=''):
            self.seen.append(name)
            _t.sleep(delay(name))
            if name in fail_for:
                return None
            return ({'title': name, 'verified': False}, '正文 %s' % name)
        return call

    def test_已有页在花钱之前就被滤掉(self):
        """**这条是这一步唯一会多花钱的地方。**

        串行版顺序上天然不会为已存在的页调用模型；改成"先提交、拿到结果再丢"之后，
        那次调用**已经发生了**——钱花了，产出被丢掉，账单上看不出来。
        所以这里断言的是：滤掉的那些名字，`generate_concept` **一次都没被叫到**。
        """
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / '甲.md').write_text('x', encoding='utf-8')      # 已存在
            concepts = [('甲', [{'desc': 'd'}]), ('乙', [{'desc': 'd'}]),
                        ('丙', [{'desc': 'd'}])]
            jobs, skipped, _ = cw.build_jobs(concepts, out)
            self.assertEqual(skipped, 1)
            self.assertEqual([name for name, _, _ in jobs], ['乙', '丙'])
            cw.generate_concept = self.fake()
            list(cw.iter_concept_pages(jobs, 'k', workers=4))
            self.assertEqual(sorted(self.seen), ['丙', '乙'], '已存在的「甲」不该被问过一次')

    def test_顺序与输入一致(self):
        with tempfile.TemporaryDirectory() as tmp:
            concepts = [('第一个', []), ('第二个', []), ('第三个', [])]
            jobs, _, _ = cw.build_jobs(concepts, Path(tmp))
            cw.generate_concept = self.fake(delay=lambda name: 0.05 * ('第一个', '第二个', '第三个').index(name))
            got = [(job[0], result[0]['title'] if result else None)
                   for job, result in cw.iter_concept_pages(jobs, 'k', workers=4)]
        self.assertEqual(got, [('第一个', '第一个'), ('第二个', '第二个'), ('第三个', '第三个')],
                         '名字与内容必须成对——错位了页面照样生成、格式照样对')

    def test_一页失败不拖垮整批(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs, _, _ = cw.build_jobs([('甲', []), ('乙', []), ('丙', [])], Path(tmp))
            cw.generate_concept = self.fake(fail_for=('乙',))
            got = list(cw.iter_concept_pages(jobs, 'k', workers=4))
        self.assertEqual(len(got), 3, '失败的那一页占位仍在，不能少一项')
        self.assertEqual([j[0] for j, r in got if r is None], ['乙'])

    def test_第一页出得来就不等整批(self):
        import inspect
        import time as _t
        with tempfile.TemporaryDirectory() as tmp:
            jobs, _, _ = cw.build_jobs([('甲', []), ('乙', []), ('丙', [])], Path(tmp))
            cw.generate_concept = self.fake(delay=lambda name: 0.05 * ('甲', '乙', '丙').index(name))
            started = _t.time()
            stream = cw.iter_concept_pages(jobs, 'k', workers=4)
            self.assertTrue(inspect.isgenerator(stream), '返回列表 = 整批跑完才有第一页')
            next(stream)
            self.assertLess(_t.time() - started, 0.15, '第一页等了太久，说明整批被物化了')
            list(stream)

    def test_串行那条路保留按篇节流(self):
        # 只有一次一个请求时才需要按篇等待（0.5s）；并发那条不加，否则把并发收益吃回去。
        # 假实现本身不睡，所以这里量到的就是节流那一份。
        import time as _t
        with tempfile.TemporaryDirectory() as tmp:
            jobs, _, _ = cw.build_jobs([('甲', []), ('乙', [])], Path(tmp))
            cw.generate_concept = self.fake(delay=lambda name: 0)
            started = _t.time()
            list(cw.iter_concept_pages(jobs, 'k', workers=1))
            elapsed = _t.time() - started
        self.assertGreaterEqual(elapsed, 0.9, '两页串行至少要各等 0.5 秒，实测只用了 %.2fs' % elapsed)


class SiblingConceptDirTests(unittest.TestCase):
    """一个概念名在库里只能有**一张页** —— 两条线分开之后新出现的一条约束。

    2026-09-27 把知识库分成 `Wiki/Concepts`（文章线）与 `Chat/Concepts`（聊天线）之后，
    聊天线立刻为 11 个文章线已有的概念（`DeepSeek`、`智谱`…）各建了一张同名页。
    两张同名页会让 `[[DeepSeek]]` 在 Obsidian 里变成二义的：它挑一张连上，另一张等同于断了，
    **而两边都不报错**——`wiki lint` 是唯一会说出来的人（"同名页 11 组"）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'vault'
        (self.root / 'Wiki' / 'Concepts').mkdir(parents=True)
        (self.root / 'Chat' / 'Concepts').mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_找得到另一条线(self):
        self.assertEqual(cw.sibling_concept_dirs(self.root / 'Chat' / 'Concepts'),
                         [self.root / 'Wiki' / 'Concepts'])
        self.assertEqual(cw.sibling_concept_dirs(self.root / 'Wiki' / 'Concepts'),
                         [self.root / 'Chat' / 'Concepts'])

    def test_库外的目录没有另一条线(self):
        # `--output` 指到库外时不该凭空猜一个兄弟目录出来
        outside = Path(self.tmp.name) / 'elsewhere'
        outside.mkdir()
        self.assertEqual(cw.sibling_concept_dirs(outside), [])

    def test_另一条线没有这个目录时不报错(self):
        (self.root / 'Chat' / 'Concepts').rmdir()
        self.assertEqual(cw.sibling_concept_dirs(self.root / 'Wiki' / 'Concepts'), [])

    def test_另一条线已有同名页就不重复建_也不花钱(self):
        (self.root / 'Wiki' / 'Concepts' / 'DeepSeek.md').write_text('已有', encoding='utf-8')
        jobs, skipped, elsewhere = cw.build_jobs(
            [('DeepSeek', ['r']), ('只有聊天线有的概念', ['r'])],
            self.root / 'Chat' / 'Concepts',
            cw.sibling_concept_dirs(self.root / 'Chat' / 'Concepts'))
        self.assertEqual([j[0] for j in jobs], ['只有聊天线有的概念'],
                         '同名的那张不许再建一张 —— 那就是 lint 报的"同名页"')
        self.assertEqual(skipped, 0)
        self.assertEqual(elsewhere, 1, '跳过要分类报出来：本目录已有 vs 另一条线已有，原因不同')

    def test_报数要把两类原因分开(self):
        # 合成一句"跳过 12 个"的话，读的人会以为全是重跑 —— 而另一类是新出现的
        self.assertEqual(cw.skip_note(0, 0), '')
        self.assertIn('跳过 3 个', cw.skip_note(3, 0))
        self.assertNotIn('另一条线', cw.skip_note(3, 0))
        self.assertIn('跳过 3 个', cw.skip_note(1, 2))
        self.assertIn('2 个在另一条线', cw.skip_note(1, 2), '另一类必须说出来')

    def test_不传另一条线时行为不变(self):
        # 库外目录、或单目录的旧用法：外部目录为空就没有可跳过的
        jobs, skipped, elsewhere = cw.build_jobs([('甲', ['r'])], self.root / 'Chat' / 'Concepts')
        self.assertEqual([j[0] for j in jobs], ['甲'])
        self.assertEqual((skipped, elsewhere), (0, 0))


class VaultLayoutTests(unittest.TestCase):
    """**同一条路径写在两处，就会有一处永远空着。**

    2026-09-27 实测到的形状：`create_reading_notes.VAULT_DIRS` 声明概念页在
    `007_Wiki/Concepts`，而 `compile_wiki.OUTPUT_ROOT` 写的是顶层的 `Wiki/Concepts`
    （另有 `vault_rag`/`vault_search`/`wiki_lint`/助手的知识检索/CLI 两个选项共 6 处读它）。
    结果：每次 init 都建出一个永远空的 `007_Wiki/`，而用户在 Obsidian 里看到它，
    得到的结论是"知识库没更新"。
    """

    def test_模板里的概念目录必须就是实际写入的那个(self):
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location('create_reading_notes', SCRIPTS / 'create_reading_notes.py')
        crn = _ilu.module_from_spec(spec)
        spec.loader.exec_module(crn)

        vault = Path('output/wechat-vault')
        real = Path(cw.OUTPUT_ROOT).relative_to(vault).as_posix()
        declared = [d for d in crn.VAULT_DIRS if d.endswith('Wiki/Concepts')]
        self.assertEqual(declared, [real],
                         '模板里声明的概念目录必须就是 compile_wiki 真正写入的那个；'
                         '不一致会建出一个永远空着的目录（曾发生：007_Wiki/Concepts）')

    def test_顶层_Wiki_才是概念页的家(self):
        # 六处在读它，改这里要同时改那六处——这条只是把"家在哪"写死，好让改动时必须面对它
        self.assertTrue(cw.OUTPUT_ROOT.endswith('wechat-vault/Wiki/Concepts'), cw.OUTPUT_ROOT)


class SourceLinkTests(unittest.TestCase):
    """概念页"来源"段的链接必须指向**阅读笔记**，不是卡片。

    实测：5,470 条来源链接里 125 条指不到任何文件。原因不是随机的——卡片名是
    `{日期}-{完整标题}`，阅读笔记名是 `{日期}-{标题截到 50 字}`，标题短的（98%）两者
    恰好相同**所以碰巧能解析**，长的就断。卡片里本来就记着正确的来源路径（`from`）。
    """

    def test_有_from_时用阅读笔记的文件名(self):
        fm = {'from': 'WeChat/2026-03-03/2026-03-03-短标题.md'}
        self.assertEqual(cw.source_link_target(fm, Path('2026-03-03-短标题.md')), '2026-03-03-短标题')

    def test_超长标题时两者确实不同(self):
        # 卡片名保留完整标题，阅读笔记名被 safe_filename 截到 50 字
        long_title = '别等Seedance 2.0了！她一个人，48h干出了热搜AI漫剧以及很多很多别的东西' * 2
        fm = {'from': f'WeChat/2026-03-03/2026-03-03-{long_title[:50]}.md'}
        got = cw.source_link_target(fm, Path(f'2026-03-03-{long_title}.md'))
        self.assertEqual(got, f'2026-03-03-{long_title[:50]}')
        self.assertNotEqual(got, Path(f'2026-03-03-{long_title}.md').stem,
                            '两者不同才是这个 bug 的来头')

    def test_没有_from_的老卡片退回自己的文件名(self):
        self.assertEqual(cw.source_link_target({}, Path('2026-01-01-老卡.md')), '2026-01-01-老卡')


class FixSourceLinksTests(unittest.TestCase):
    def build(self, tmp):
        cards = Path(tmp) / 'cards'
        (cards).mkdir(parents=True)
        long_title = '很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长的标题' * 2
        (cards / f'2026-03-03-{long_title}.md').write_text(
            '---\ntitle: "x"\nfrom: "WeChat/2026-03-03/2026-03-03-%s.md"\n---\n\n'
            '- [[某概念]] — 描述\n' % long_title[:50], encoding='utf-8')
        (cards / '2026-03-04-短标题.md').write_text(
            '---\ntitle: "y"\nfrom: "WeChat/2026-03-04/2026-03-04-短标题.md"\n---\n\n'
            '- [[某概念]] — 描述\n', encoding='utf-8')
        pages = Path(tmp) / 'pages'
        pages.mkdir()
        (pages / '某概念.md').write_text(
            '## 相关概念\n\n- [[另一个概念]]\n\n## 来源\n\n'
            f'- [[2026-03-03-{long_title}]] — 长标题那篇\n'
            '- [[2026-03-04-短标题]] — 短标题那篇\n', encoding='utf-8')
        return pages, cards, long_title

    def test_只改对不上的那一条(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages, cards, long_title = self.build(tmp)
            result = cw.fix_source_links(str(pages), str(cards))
            text = next(pages.glob('*.md')).read_text(encoding='utf-8')
        self.assertEqual(result['links'], 1, '只有超长标题那条该改')
        # 断言**链接目标**：它应当变成截断后的那个名字，而标题显示那句原样保留
        self.assertIn('- [[2026-03-03-%s]] — 长标题那篇' % long_title[:50], text)
        self.assertNotIn('- [[2026-03-03-%s]] — ' % long_title, text,
                         '完整标题那个（指不到文件的）该被换掉')
        self.assertIn('- [[2026-03-04-短标题]] — 短标题那篇', text, '对得上的原样留着')

    def test_相关概念那一段不受影响(self):
        # 判据是行尾的 ` — `：`## 相关概念` 也是 `- [[名字]]`，但没有破折号
        with tempfile.TemporaryDirectory() as tmp:
            pages, cards, _ = self.build(tmp)
            cw.fix_source_links(str(pages), str(cards))
            text = next(pages.glob('*.md')).read_text(encoding='utf-8')
        self.assertIn('- [[另一个概念]]\n', text)

    def test_去掉_md_后缀(self):
        """库里**其他所有**链接（笔记互链、日记、MOC）都不带 `.md`，只有这一处带。

        带后缀能不能解析我没有把握，但不带是确定的写法——而且统一之后全库一个形态。
        """
        with tempfile.TemporaryDirectory() as tmp:
            pages = Path(tmp) / 'pages'
            pages.mkdir()
            cards = Path(tmp) / 'cards'
            cards.mkdir()
            (pages / '甲.md').write_text('## 来源\n\n- [[某篇笔记.md]] — 标题\n', encoding='utf-8')
            result = cw.fix_source_links(str(pages), str(cards))
            text = (pages / '甲.md').read_text(encoding='utf-8')
        self.assertEqual(result['links'], 1)
        self.assertIn('- [[某篇笔记]] — 标题', text)
        self.assertNotIn('.md]]', text)

    def test_幂等(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages, cards, _ = self.build(tmp)
            cw.fix_source_links(str(pages), str(cards))
            first = next(pages.glob('*.md')).read_text(encoding='utf-8')
            again = cw.fix_source_links(str(pages), str(cards))
            self.assertEqual(next(pages.glob('*.md')).read_text(encoding='utf-8'), first)
            self.assertEqual(again['links'], 0, '第二次不该再改')


class JunkConceptTests(unittest.TestCase):
    """纯符号/表情/单字不该长出概念页。

    实测聊天线提出来过 `🐊`、`🤓`、`🥬 + 🔴`、`: D`、`D` 这种——每张都会长成一页，
    在图上就是几个没有意义的孤立点。
    """

    def links(self, names):
        body = '## 概念\n\n' + '\n'.join(f'- [[{n}]] — 描述' for n in names) + '\n'
        return [name for name, _ in cw.concept_links(body, '')]

    def test_表情与单字被丢掉(self):
        got = self.links(['🐊', 'D', ': D', '🤓', '🥬 + 🔴'])
        self.assertEqual(got, [], '这些都不是概念')

    def test_两字符的正常概念照收(self):
        # 判据是"至少两个汉字或字母数字"，别把 AI / MCP 这种误伤
        got = self.links(['AI', 'MCP 协议', '3 遍法', '知识蒸馏'])
        self.assertEqual(got, ['AI', 'MCP 协议', '3 遍法', '知识蒸馏'])


class LinkKindTests(unittest.TestCase):
    """`[[名字]]` 出现在哪个小节——**人物还是话题**。

    聊天卡把这两类分开写（`### 话题` / `### 人`），而聚合时这个区分以前被丢掉了。
    代价是实测出来的：一个昵称叫「白马非马」的联系人，被按字面写成了公孙龙那个哲学命题
    ——材料里明明写着"对话对方，准备面试"。带上"这是个人"，那一页才会写成人。
    """

    def test_认得两个小节(self):
        body = ('## 主题与人物\n\n### 话题\n\n- [[面试]] — 去实验室面试\n- [[学生证]] — 在我这里\n\n'
                '### 人\n\n- [[白马非马]] — 对话对方，准备面试\n- [[詹哥]] — 提到他去年的二面\n')
        kinds = cw.link_kinds(body)
        self.assertEqual(kinds['面试'], '话题')
        self.assertEqual(kinds['学生证'], '话题')
        self.assertEqual(kinds['白马非马'], '人物')
        self.assertEqual(kinds['詹哥'], '人物')

    def test_小节之外的不给标注(self):
        # "没标注"与"标为话题"是两件事：后者是**知道**它是话题
        body = '## 摘要\n\n- [[别的东西]] — 这个不在那两节里\n'
        self.assertEqual(cw.link_kinds(body), {})

    def test_人物参考行会把身份写给模型(self):
        refs = [{'title': '白马非马', 'source': '白马非马', 'topic': '聊天',
                 'desc': '对话对方，准备面试和申请博士', 'kind': '人物', 'file': '白马非马'}]
        line = cw.build_ref_lines(refs)[0]
        self.assertIn('人物', line, '不写身份，模型会按名字把它写成同名典故')

    def test_没有_kind_的参考行不受影响(self):
        refs = [{'title': '甲', 'source': '某号', 'topic': 'AI',
                 'desc': '讲了甲', 'file': 'a'}]
        self.assertNotIn('人物', cw.build_ref_lines(refs)[0])

    def test_提示词里写明了人物那一档该怎么写(self):
        prompt = cw.CONCEPT_PROMPT.format(name='甲', references='- [甲]（人物 · 聊天）：材料')
        self.assertIn('标注为「人物」', prompt)
        self.assertIn('不要', prompt, '要明确说"不许当成同名的事物/典故"')


class DanglingTests(unittest.TestCase):
    """给"被页面指向、却没有页"的概念建页——图谱里那些断线的另一端。

    这条通道的难点不是生成，是**材料从哪来**。第一版拿"提到它的那些概念页的定义句"
    当材料，而那些页面只写了 `- [[提示工程]]` 一个光名字——等于让模型凭空编。
    真实材料在**卡片摘要**里：实测 `提示工程` 被 2 张卡、`知识蒸馏` 被 3 张卡在摘要里提到过。
    """

    def page(self, root, name, related):
        (root / f'{name}.md').write_text(
            '---\ntitle: "%s"\n---\n\n# %s\n\n一句定义。\n\n## 相关概念\n\n%s\n'
            % (name, name, '\n'.join(f'- [[{r}]]' for r in related)), encoding='utf-8')

    def test_找的是被指向却没有页的概念(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = Path(tmp) / 'Concepts'
            pages.mkdir()
            self.page(pages, '甲', ['乙', '丙', '丙'])
            self.page(pages, '乙', ['甲'])
            counts = cw.collect_dangling_targets(str(pages))
        self.assertEqual(dict(counts), {'丙': 2}, '甲、乙都有页，只有丙是悬空的')

    def test_大小写不同不算悬空(self):
        """Windows 上 `[[Claude Code]]` 能解析到 `claude code.md`。

        用大小写敏感的集合去判，`Claude Code` 会以 129 张页指向排在第一名——而它有页。
        """
        with tempfile.TemporaryDirectory() as tmp:
            pages = Path(tmp) / 'Concepts'
            pages.mkdir()
            self.page(pages, 'claude code', ['甲'])
            self.page(pages, '甲', ['Claude Code'])
            counts = cw.collect_dangling_targets(str(pages))
        self.assertEqual(dict(counts), {}, '大小写不同但有同名文件 → 不是悬空')

    def test_材料只认摘要里提到的_且清掉_URL(self):
        cards = [{'name': 'c1', 'topic': 'AI', 'title': 't', 'file': '笔记名', 'source': '某号',
                  'body': '## AI 摘要\n\n讲的是提示工程这件事。![图](http://x/y.png) 后面还有话。\n'}]
        refs = cw.mention_refs(cards, '提示工程')
        self.assertEqual(len(refs), 1)
        self.assertNotIn('http', refs[0]['desc'])
        self.assertNotIn('![', refs[0]['desc'])

    def test_只在正文提到_但摘要没提的不算材料(self):
        # 实测差别很大：全文按字面找会命中广告词、图片 URL、结构碎片
        cards = [{'name': 'c1', 'topic': 'AI', 'title': 't', 'file': '笔记名', 'source': '某号',
                  'body': '## AI 摘要\n\n这段摘要里没有那个词。\n\n## 正文\n\n提示工程 出现在这里。\n'}]
        self.assertEqual(cw.mention_refs(cards, '提示工程'), [], '摘要没提就不算材料')

    def test_没材料就不建页(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = Path(tmp) / 'Concepts'
            pages.mkdir()
            self.page(pages, '甲', ['无材料的概念'])
            cw.load_card_texts = lambda *a, **k: []
            result = cw.build_dangling_pages(str(pages), 'k', 10, workers=1)
            self.assertEqual(result['generated'], 0)
            self.assertFalse((pages / '无材料的概念.md').exists())


class RefreshSourcesTests(unittest.TestCase):
    """补来源：让**已有页**也能长出新边。纯本地、不调模型、可重跑。

    场景是用户问出来的：拉新文章之后图谱只长新节点，**老页一个字节都不动** —— 新文章
    再讲到 `DeepSeek`，那一页的来源也不会多一条。这一趟补的就是那些边。
    """

    def build(self, tmp):
        cards = Path(tmp) / 'cards'
        cards.mkdir()
        (cards / '2026-03-04-一篇文章.md').write_text(
            '---\ntitle: "一篇文章"\n---\n\n- [[某概念]] — 说了点什么\n', encoding='utf-8')
        (cards / '2026-03-05-另一篇.md').write_text(
            '---\ntitle: "另一篇"\n---\n\n- [[某概念]] — 也说了点\n', encoding='utf-8')
        pages = Path(tmp) / 'pages'
        pages.mkdir()
        (pages / '某概念.md').write_text(
            '---\ntitle: "某概念"\nverified: False\n---\n\n# 某概念\n\n一句定义。\n\n## 来源\n\n'
            '- [[2026-03-04-一篇文章]] — 一篇文章\n', encoding='utf-8')
        return pages, cards

    def test_补齐缺的那条(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages, cards = self.build(tmp)
            r = cw.refresh_sources(str(pages), [str(cards)])
            text = (pages / '某概念.md').read_text(encoding='utf-8')
        self.assertEqual(r['pages'], 1)
        self.assertEqual(r['added'], 1)
        self.assertIn('- [[2026-03-05-另一篇]] — 另一篇', text)
        # 顺序是**规范形式**（按名字排）：3-04 在 3-05 前面
        self.assertLess(text.index('2026-03-04'), text.index('2026-03-05'))

    def test_幂等(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages, cards = self.build(tmp)
            cw.refresh_sources(str(pages), [str(cards)])
            first = (pages / '某概念.md').read_text(encoding='utf-8')
            r2 = cw.refresh_sources(str(pages), [str(cards)])
            second = (pages / '某概念.md').read_text(encoding='utf-8')
        self.assertEqual(r2['pages'], 0, '第二次一个字都不该改')
        self.assertEqual(first, second)

    def test_页里多出来的旧行照原样留着(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages, cards = self.build(tmp)
            p = pages / '某概念.md'
            p.write_text(p.read_text(encoding='utf-8') + '- [[很久以前的一篇]] — 出处已不可考\n',
                         encoding='utf-8')
            r = cw.refresh_sources(str(pages), [str(cards)])
            text = p.read_text(encoding='utf-8')
        self.assertIn('很久以前的一篇', text, '只增不删')
        self.assertEqual(r['oldUnknown'], 1, '但要报出来')

    def test_不动_frontmatter(self):
        """**这条是契约，不是顺手。**

        正文与 `sources:` 是**故意不一样**的两套名字：正文要 Obsidian 能解析（聊天线写
        `会话-<名字>`，指向 `Sources/Chat/` 里那张卡），而 `sources:` 要 `source_kinds_for`
        能在 `output/<线>/<卡名>.md` 找到（聊天线是**不带**前缀的卡名）。同步任何一边都会让
        `来源/聊天` 这类标签静默失效，而标签只增不减 —— 失效之后**看起来仍然是对的**。
        """
        with tempfile.TemporaryDirectory() as tmp:
            pages, cards = self.build(tmp)
            p = pages / '某概念.md'
            before = p.read_text(encoding='utf-8').split('---')[1]
            cw.refresh_sources(str(pages), [str(cards)])
            after = p.read_text(encoding='utf-8').split('---')[1]
        self.assertEqual(before, after, 'frontmatter 必须一个字不动')

    def test_聊天线的卡要加会话前缀(self):
        with tempfile.TemporaryDirectory() as tmp:
            cards = Path(tmp) / 'chat-notes'
            cards.mkdir()
            (cards / '白马非马.md').write_text(
                '---\ntitle: "白马非马"\n---\n\n- [[某话题]] — 聊到的\n', encoding='utf-8')
            pages = Path(tmp) / 'pages'
            pages.mkdir()
            (pages / '某话题.md').write_text('---\ntitle: "某话题"\n---\n\n## 来源\n\n', encoding='utf-8')
            cw.refresh_sources(str(pages), [str(cards)])
            text = (pages / '某话题.md').read_text(encoding='utf-8')
        # 前缀由 `build_source_name_map` 按**目录名**判定（chat-notes 才加），漏了这里
        # Vault 里那张卡叫 `会话-白马非马`，写裸名就是一条断链（实测会有 129 条）
        self.assertIn('[[会话-白马非马]]', text)

    def test_页名大小写不同也要匹配上(self):
        with tempfile.TemporaryDirectory() as tmp:
            cards = Path(tmp) / 'cards'
            cards.mkdir()
            (cards / '2026-03-04-x.md').write_text(
                '---\ntitle: "x"\n---\n\n- [[Claude Code]] — 说了\n', encoding='utf-8')
            pages = Path(tmp) / 'pages'
            pages.mkdir()
            (pages / 'claude code.md').write_text('---\ntitle: "claude code"\n---\n\n## 来源\n\n',
                                                  encoding='utf-8')
            r = cw.refresh_sources(str(pages), [str(cards)])
            text = (pages / 'claude code.md').read_text(encoding='utf-8')
        # 库里真实存在这个形状：`claude code.md` 装着 `Claude Code` 的 408 条。
        # 精确匹配会一条都匹配不上，而且**没人会知道**（`collect_dangling_targets` 栽过同一个坑）
        self.assertEqual(r['pages'], 1)
        self.assertIn('[[2026-03-04-x]]', text)

    def test_名字带方括号的不追加只报数(self):
        with tempfile.TemporaryDirectory() as tmp:
            cards = Path(tmp) / 'cards'
            cards.mkdir()
            (cards / '2026-03-05-[TGRS]遥感那篇.md').write_text(
                '---\ntitle: "t"\n---\n\n- [[某概念]] — 说了\n', encoding='utf-8')
            pages = Path(tmp) / 'pages'
            pages.mkdir()
            (pages / '某概念.md').write_text('---\ntitle: "某概念"\n---\n\n## 来源\n\n', encoding='utf-8')
            r = cw.refresh_sources(str(pages), [str(cards)])
            text = (pages / '某概念.md').read_text(encoding='utf-8')
        self.assertEqual(r['bracketed'], 1)
        self.assertNotIn('TGRS', text, '写进去只会多一条断链：连 Obsidian 都解析不了这种名字')

    def test_标题本身以_md_结尾的也不追加(self):
        """标题**本身**以 `.md` 结尾（库里实测 1 条）时，`source_link_target` 剥掉一层后缀
        给出 `….md`，而 `wiki_lint.resolve` 见到 `.md` 结尾就把它当**完整文件名**去查，
        于是报一条断链 —— 体检本来是 0 条。写不得，只报数。
        """
        with tempfile.TemporaryDirectory() as tmp:
            cards = Path(tmp) / 'cards'
            cards.mkdir()
            (cards / '2026-06-09-也许你该试试 Agents.md.md').write_text(
                '---\ntitle: "t"\n---\n\n- [[某概念]] — 说了\n', encoding='utf-8')
            pages = Path(tmp) / 'pages'
            pages.mkdir()
            (pages / '某概念.md').write_text(
                '---\ntitle: "某概念"\n---\n\n## 来源\n\n', encoding='utf-8')
            r = cw.refresh_sources(str(pages), [str(cards)])
            text = (pages / '某概念.md').read_text(encoding='utf-8')
        self.assertEqual(r['dotmd'], 1)
        self.assertNotIn('Agents', text, '写进去只会让体检多一条断链')


    def test_没有对应概念的页一个字不动(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages, cards = self.build(tmp)
            orphan = pages / '没人提过的概念.md'
            orphan.write_text('---\ntitle: "x"\n---\n\n## 来源\n\n- [[旧的一篇]] — 旧\n',
                              encoding='utf-8')
            before = orphan.read_text(encoding='utf-8')
            r = cw.refresh_sources(str(pages), [str(cards)])
            after = orphan.read_text(encoding='utf-8')      # 必须在 with 里读：出去目录就没了
        self.assertEqual(after, before)
        self.assertEqual(r['unmatched'], 1)

    def test_预览时一个字都不写(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages, cards = self.build(tmp)
            p = pages / '某概念.md'
            before = p.read_text(encoding='utf-8')
            r = cw.refresh_sources(str(pages), [str(cards)], dry_run=True)
            after = p.read_text(encoding='utf-8')
        self.assertEqual(r['pages'], 1, '预览也要报出"会改几张"')
        self.assertEqual(r['added'], 1)
        self.assertEqual(after, before, 'dry_run 不许写盘')


class MergeDuplicatePagesTests(unittest.TestCase):
    """把"同一个概念的多个写法"合并成一张。**改的是图谱的形状**，不调模型。

    用户的判断是"有的本质上是一样的，但弄成不一样…… 我要确保图谱的有效性，
    而不是像垃圾那样越堆越多"。实测库里 74 组、80 张冗余页。
    """

    def build(self, tmp):
        pages = Path(tmp) / 'pages'
        pages.mkdir()
        # **按名字排 `GPT 5.6` 在前**（空格 0x20 < 连字符 0x2D），所以把两条来源放在
        # `GPT-5.6` 上："留来源最多的"与"留名字排前的"于是是两个不同答案，测试才真的
        # 在钉前者。第一版把两条来源放在 `GPT 5.6` 上，两种策略给出同一个结果 ——
        # 变异检查（改成按名字选主）溜了过去。
        (pages / 'GPT 5.6.md').write_text(
            '---\ntitle: "GPT 5.6"\n---\n\n# GPT 5.6\n\n定义。\n\n## 相关概念\n\n- [[甲]]\n\n'
            '## 来源\n\n- [[2026-01-01-一]] — 一\n', encoding='utf-8')
        (pages / 'GPT-5.6.md').write_text(
            '---\ntitle: "GPT-5.6"\n---\n\n# GPT-5.6\n\n另一种写法。\n\n## 来源\n\n'
            '- [[2026-01-02-二]] — 二\n- [[2026-01-03-三]] — 三\n', encoding='utf-8')
        return pages

    def test_留来源多的那张_其余的删掉(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = self.build(tmp)
            r = cw.merge_duplicate_pages(str(pages))
            names = sorted(p.stem for p in pages.glob('*.md'))
            text = (pages / 'GPT-5.6.md').read_text(encoding='utf-8')
        self.assertEqual(r['groups'], 1)
        self.assertEqual(names, ['GPT-5.6'], '留**来源最多**的那张，不是名字排前的那个')
        for src in ('2026-01-01-一', '2026-01-02-二', '2026-01-03-三'):
            self.assertIn(src, text, '被合并那张的来源要并进来')
        self.assertEqual(len([l for l in text.split('\n') if l.startswith('- [[') and ']] — ' in l]), 3)

    def test_旧名字进别名_否则那批链接就断了(self):
        """**这是整个方案能这么便宜的原因**：库里 98 条链接指向被合并掉的名字，
        写进 aliases 之后 Obsidian 照旧解析，一条都不用改。"""
        with tempfile.TemporaryDirectory() as tmp:
            pages = self.build(tmp)
            cw.merge_duplicate_pages(str(pages))
            fm = (pages / 'GPT-5.6.md').read_text(encoding='utf-8').split('---')[1]
        self.assertIn('GPT 5.6', fm)

    def test_幂等(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = self.build(tmp)
            cw.merge_duplicate_pages(str(pages))
            first = (pages / 'GPT-5.6.md').read_text(encoding='utf-8')
            r2 = cw.merge_duplicate_pages(str(pages))
            second = (pages / 'GPT-5.6.md').read_text(encoding='utf-8')
        self.assertEqual(r2['groups'], 0, '第二次没有可合并的了')
        self.assertEqual(first, second, '第二次一个字都不该改')

    def test_英文单复数要合并(self):
        """`AI skill` 与 `AI skills` 是同一件事。

        第一版没测这条 —— 变异检查（把"去英文复数"那一步删掉）溜了过去，说明测试没钉住它。
        """
        with tempfile.TemporaryDirectory() as tmp:
            pages = Path(tmp) / 'pages'
            pages.mkdir()
            for n in ('AI skill', 'AI skills'):
                (pages / (n + '.md')).write_text(
                    '---\ntitle: "%s"\n---\n\n## 来源\n\n- [[2026-01-01-一]] — 一\n' % n,
                    encoding='utf-8')
            r = cw.merge_duplicate_pages(str(pages))
            names = sorted(p.stem for p in pages.glob('*.md'))
        self.assertEqual(r['groups'], 1, '只差一个复数尾 s，是同一个概念')
        self.assertEqual(len(names), 1)

    def test_只是名字像的不合并(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = Path(tmp) / 'pages'
            pages.mkdir()
            for n in ('Claude Code', 'Claude 4.8'):
                (pages / (n + '.md')).write_text('---\ntitle: "%s"\n---\n\n## 来源\n\n' % n,
                                                 encoding='utf-8')
            r = cw.merge_duplicate_pages(str(pages))
        # 规范形是 `claudecode` 与 `claude48`，不同 —— lint 那个"公共子串 ≥5 字符"的宽判据
        # 会把这两个报成一组，合并**不认**那个判据。
        self.assertEqual(r['groups'], 0)

    def test_预览不删页(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = self.build(tmp)
            r = cw.merge_duplicate_pages(str(pages), dry_run=True)
            names = sorted(p.stem for p in pages.glob('*.md'))
        self.assertEqual(r['groups'], 1, '预览也要报出会合并几组')
        self.assertEqual(names, ['GPT 5.6', 'GPT-5.6'], 'dry_run 不许删')


if __name__ == '__main__':
    unittest.main()
