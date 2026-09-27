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
        self.assertEqual(note['wikilinks'][0], ('词向量', '这篇文章里它是这么被讲的'),
                         '破折号后面那句是"关于这个概念说的那句话"，必须留下来')
        self.assertEqual(note['wikilinks'][1], ('老王', ''),
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
        self.assertEqual([name for name, _ in note['wikilinks']], ['MCP 协议'])
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
            jobs, skipped = cw.build_jobs(concepts, out)
            self.assertEqual(skipped, 1)
            self.assertEqual([name for name, _, _ in jobs], ['乙', '丙'])
            cw.generate_concept = self.fake()
            list(cw.iter_concept_pages(jobs, 'k', workers=4))
            self.assertEqual(sorted(self.seen), ['丙', '乙'], '已存在的「甲」不该被问过一次')

    def test_顺序与输入一致(self):
        with tempfile.TemporaryDirectory() as tmp:
            concepts = [('第一个', []), ('第二个', []), ('第三个', [])]
            jobs, _ = cw.build_jobs(concepts, Path(tmp))
            cw.generate_concept = self.fake(delay=lambda name: 0.05 * ('第一个', '第二个', '第三个').index(name))
            got = [(job[0], result[0]['title'] if result else None)
                   for job, result in cw.iter_concept_pages(jobs, 'k', workers=4)]
        self.assertEqual(got, [('第一个', '第一个'), ('第二个', '第二个'), ('第三个', '第三个')],
                         '名字与内容必须成对——错位了页面照样生成、格式照样对')

    def test_一页失败不拖垮整批(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs, _ = cw.build_jobs([('甲', []), ('乙', []), ('丙', [])], Path(tmp))
            cw.generate_concept = self.fake(fail_for=('乙',))
            got = list(cw.iter_concept_pages(jobs, 'k', workers=4))
        self.assertEqual(len(got), 3, '失败的那一页占位仍在，不能少一项')
        self.assertEqual([j[0] for j, r in got if r is None], ['乙'])

    def test_第一页出得来就不等整批(self):
        import inspect
        import time as _t
        with tempfile.TemporaryDirectory() as tmp:
            jobs, _ = cw.build_jobs([('甲', []), ('乙', []), ('丙', [])], Path(tmp))
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
            jobs, _ = cw.build_jobs([('甲', []), ('乙', [])], Path(tmp))
            cw.generate_concept = self.fake(delay=lambda name: 0)
            started = _t.time()
            list(cw.iter_concept_pages(jobs, 'k', workers=1))
            elapsed = _t.time() - started
        self.assertGreaterEqual(elapsed, 0.9, '两页串行至少要各等 0.5 秒，实测只用了 %.2fs' % elapsed)


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


if __name__ == '__main__':
    unittest.main()
