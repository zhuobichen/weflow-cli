"""主题的兜底值只有一处定义，且在写入之前就定稿。

为什么值得单独一份测试：这个兜底曾经在**同一条写入路径**上有两个不同的值，
相隔七行——`.articles.json` 那条路默认 `''`（空主题），分组/落 md 那条路默认
`'学术'`。于是一篇文章在 md 里写 `topic: 学术`、在 json 里写 `topic: ""`，
**两个产物给出不同的主题，而且都不报错**。

2026-09-04 的真实产出就是这么分叉的：178 篇全落在 `学术/` 目录、frontmatter 写
`topic: 学术`（其中两篇是「OpenAI 深夜发布 GPT-6」「专为高管准备的 AI 助手」），
而同一批的 `.articles.json` 全是 `topic: ""` —— 报告读的正是 json，
判据 `topic != FOCUS_TOPIC and relevance != '高'` 把它们整批静默排除了。

会走到兜底的真实情形：`daily --no-ai`、或者没配 API key 时 Phase 2（分类）整段
不跑，文章 dict 里**根本没有 `topic` 键**，而 Phase 3 照常写文件。

`sqlcipher3` 是桩掉的，做法同 `biz_daily_parallel_test.py`（CI 只装 zstandard
与 pycryptodome）。
"""
import ast
import importlib.util
from pathlib import Path
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from _utils import TOPICS, DEFAULT_TOPIC  # noqa: E402

spec = importlib.util.spec_from_file_location('biz_daily_defaulttopic', SCRIPTS / 'biz_daily.py')
biz = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(biz)


class DefaultTopicItselfTests(unittest.TestCase):
    def test_the_fallback_is_a_real_category(self):
        """兜底值必须在分类法之内，否则它自己就是一个越界主题。"""
        self.assertIn(DEFAULT_TOPIC, TOPICS)
        self.assertIsInstance(DEFAULT_TOPIC, str)
        self.assertTrue(DEFAULT_TOPIC.strip())

    def test_the_taxonomy_has_no_duplicates(self):
        # 有重复的话 `topic_groups` 会少一个桶，而 count 之类看起来仍然正常。
        self.assertEqual(len(TOPICS), len(set(TOPICS)))


_MISSING = object()


class NormalizeTests(unittest.TestCase):
    def norm(self, *topics):
        articles = [{'title': 't%d' % i, **({} if t is _MISSING else {'topic': t})}
                    for i, t in enumerate(topics)]
        count = biz._normalize_topics(articles)
        return articles, count

    def test_a_missing_topic_key_falls_back(self):
        # `daily --no-ai` 就是这个形状：文章 dict 里从来没有 topic 键。
        articles, count = self.norm(_MISSING, _MISSING)
        self.assertEqual([a['topic'] for a in articles], [DEFAULT_TOPIC] * 2)
        self.assertEqual(count, 2)

    def test_an_empty_string_falls_back(self):
        # 空字符串不是"没这个键"，`.get(k, default)` 兜不住它——两条路的分叉正是
        # 一边用 .get 的默认值、另一边用 `or`，所以空串必须单独测。
        articles, count = self.norm('', '')
        self.assertEqual([a['topic'] for a in articles], [DEFAULT_TOPIC] * 2)
        self.assertEqual(count, 2)

    def test_a_topic_outside_the_taxonomy_falls_back(self):
        """`source_category` 来自人工配置，写错一个词就会给出越界的值。

        越界的主题会让按主题取值的下游崩（`TOPIC_LABELS[t]` 之类），
        所以这里校验的是**成员资格**，不只是"非空"。
        """
        articles, count = self.norm('生活', '投资', 'AI地')
        self.assertEqual([a['topic'] for a in articles],
                         [DEFAULT_TOPIC, '投资', DEFAULT_TOPIC])
        self.assertEqual(count, 2)

    def test_valid_topics_are_left_alone(self):
        articles, count = self.norm(*TOPICS)
        self.assertEqual([a['topic'] for a in articles], TOPICS)
        self.assertEqual(count, 0)

    def test_it_edits_in_place(self):
        """就地改是契约：调用点拿的是同一个列表对象，返回新列表会让下游读到旧值。"""
        articles = [{'topic': ''}]
        biz._normalize_topics(articles)
        self.assertEqual(articles[0]['topic'], DEFAULT_TOPIC)

    def test_it_is_idempotent(self):
        articles = [{'topic': '生活'}]
        biz._normalize_topics(articles)
        self.assertEqual(biz._normalize_topics(articles), 0)
        self.assertEqual(articles[0]['topic'], DEFAULT_TOPIC)


class GroupingTests(unittest.TestCase):
    """分组键与落盘主题必须是同一个值。

    md 的 frontmatter 写的**不是** `a['topic']`，而是分组键（Phase 3 里
    `fm['topic'] = topic`）。所以"md 与 json 说的是同一个主题"这条契约，
    实际依赖的是"分组键 == `a['topic']`"——归一化必须发生在分组**之前**。
    """

    def build(self, *topics):
        return [{'title': 't%d' % i, **({} if t is _MISSING else {'topic': t})}
                for i, t in enumerate(topics)]

    def test_every_group_key_equals_what_the_json_will_record(self):
        articles = self.build(_MISSING, '', '生活', 'AI', '政治')
        groups, _ = biz._group_by_topic(articles)
        for key, members in groups.items():
            for a in members:
                # 分组键 == md 的 topic；.articles.json 的 topic 也必须等于它。
                self.assertEqual(key, a['topic'])
                self.assertEqual(key, biz._serializable_article(a, '2026-01-01')['topic'])

    def test_every_category_gets_a_bucket_even_when_empty(self):
        # Phase 3 按 TOPICS 建目录、也按 TOPICS 遍历分栏，桶缺一个就会少一栏。
        groups, _ = biz._group_by_topic(self.build('AI'))
        self.assertEqual(sorted(groups), sorted(TOPICS))

    def test_articles_without_a_topic_land_in_the_fallback_bucket(self):
        groups, count = biz._group_by_topic(self.build(_MISSING, _MISSING, 'AI'))
        self.assertEqual([a['title'] for a in groups[DEFAULT_TOPIC]], ['t0', 't1'])
        self.assertEqual([a['title'] for a in groups['AI']], ['t2'])

    def test_it_reports_how_many_fell_back(self):
        """兜底篇数必须真的传出来。

        写这个函数的第一版把 `_normalize_topics` 调了两次（先调一次、返回时又调
        一次），第二次当然返回 0——于是整批兜底时那条 WARN 永远不响，看起来
        像一次正常分类。这条断言就是钉住那个。
        """
        _, count = biz._group_by_topic(self.build(_MISSING, '', 'AI', '政治'))
        self.assertEqual(count, 2)
        _, clean = biz._group_by_topic(self.build('AI', '政治'))
        self.assertEqual(clean, 0)


class TheTwoWritersAgreeTests(unittest.TestCase):
    """json 与 md 必须对同一篇文章给出同一个主题——这正是当初被打破的契约。"""

    def normalized(self, *topics):
        articles = [{'title': 't%d' % i, **({} if t is _MISSING else {'topic': t})}
                    for i, t in enumerate(topics)]
        biz._normalize_topics(articles)
        return articles

    def test_the_json_topic_is_always_a_real_category(self):
        """一组敌意输入下，落盘的 topic 必须**永远**在分类法之内。

        md 那条路用的是归一化后的 `a['topic']`（分组键），所以要钉死的是
        "json 写出来的和它就是同一个值"。
        """
        for articles in ([{'title': 'x'}], [{'topic': ''}], [{'topic': None}],
                         [{'topic': '生活'}], [{'topic': 0}], [{'topic': 'AI'}]):
            biz._normalize_topics(articles)
            for a in articles:
                entry = biz._serializable_article(a, '2026-01-01')
                self.assertIn(entry['topic'], TOPICS, a)
                self.assertEqual(entry['topic'], a['topic'], a)

    def test_the_json_writer_itself_never_emits_an_empty_topic(self):
        """即使有人绕过归一化直接调它，也不能落一个空主题下去。

        归一化在写入前一定会跑，这是第二道——但两道用的必须是同一个默认值。
        """
        entry = biz._serializable_article({'title': 'x'}, '2026-01-01')
        self.assertEqual(entry['topic'], DEFAULT_TOPIC)
        entry = biz._serializable_article({'title': 'x', 'topic': ''}, '2026-01-01')
        self.assertEqual(entry['topic'], DEFAULT_TOPIC)


class SingleDefinitionTests(unittest.TestCase):
    """兜底字面量不许在 `_utils.py` 之外再出现一次。

    判据用 AST 挑得很窄，只匹配**"未知主题的默认值"**这两种语法结构：

    * `x or '学术'`            → `BoolOp(Or, ...)` 里有个该字面量
    * `d.get(k, '学术')`       → `.get(...)` 的第二个实参（或 `default=`）是它

    **不能靠"剔掉字符串再拿正则找"**：要找的目标本身就是字符串字面量，剔掉之后
    规则永远不可能命中——这个写法我写出来了、又被下面那条探针测试当场抓住。

    故意不匹配 `return '学术'` —— 那可能是关键词命中的**结论**
    （`_guess_topic` 里就是：命中学术关键词才返回它，函数真正的默认是"新闻"）。
    也不匹配 `topic == '学术'` 这种比较：那是拿合法类别做判断，不是兜底。
    把这两类也禁掉会逼出一个更差的实现。
    """

    # 兜底值的字面量。这里写死而不是引用 DEFAULT_TOPIC：要抓的就是"有人又写了
    # 一个字面量"，引用常量的话这条检查会因为断言的表达式本身而失效。
    LITERAL = '学术'

    @classmethod
    def _is_literal(cls, node):
        return isinstance(node, ast.Constant) and node.value == cls.LITERAL

    @classmethod
    def find_fallbacks(cls, source):
        """返回源码里所有"未知主题兜底"的 (行号, 形态) 列表。"""
        found = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return [('?', 'SyntaxError：无法解析，按可疑处理')]
        for node in ast.walk(tree):
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                if any(cls._is_literal(v) for v in node.values):
                    found.append((node.lineno, "or 兜底"))
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == 'get':
                    args = list(node.args[1:]) + [k.value for k in node.keywords]
                    if any(cls._is_literal(a) for a in args):
                        found.append((node.lineno, ".get 默认"))
        return found

    def test_no_fallback_literal_outside_the_one_definition(self):
        offenders = []
        for path in sorted(SCRIPTS.glob('*.py')):
            if path.name == '_utils.py':
                continue  # 定义就在这儿，它当然要写这个字面量
            for line, shape in self.find_fallbacks(path.read_text(encoding='utf-8')):
                offenders.append('%s 行 %s [%s]' % (path.name, line, shape))
        self.assertEqual(offenders, [], '兜底字面量应只在 _utils.DEFAULT_TOPIC 一处：\n' +
                         '\n'.join(offenders))

    def test_the_scan_would_notice_a_new_fallback(self):
        """给检查本身一个反例：它必须能抓到这两种写法。

        没有这条，规则写歪了也会一路绿——第一版规则（剔字符串再正则）就是这么
        一路绿的，是这条测试把它拦下来的。
        """
        found = self.find_fallbacks(
            'topic = row.get("topic", "学术")\n'
            'other = value or "学术"\n')
        self.assertEqual([shape for _, shape in found], ['.get 默认', 'or 兜底'])

    def test_legitimate_uses_are_not_flagged(self):
        """关键词命中的结论、对合法类别的比较，都不算兜底，不能被误伤。"""
        found = self.find_fallbacks(
            'if any(k in text for k in academic_keywords):\n'
            '    return "学术"\n'
            'if topic == "学术":\n'
            '    pass\n'
            'tags = [t for t in TOPICS if t == "学术"]\n')
        self.assertEqual(found, [])


class TaxonomyCopiesAgreeTests(unittest.TestCase):
    """四个脚本里各存了一份 `TOPIC_ORDER` 字面量——它们必须与 `_utils.TOPICS` 相同。

    **这四份没有合并，是故意的，原因不是嫌麻烦**：`create_reading_notes` 和
    `generate_html` 根本没有 `_utils` 导入边，给它们加一条，这些命令能不能跑就
    取决于调用方从哪个工作目录起、有没有把 `scripts/` 放进 `PYTHONPATH`——
    为一份**当前逐字相同**的字面量去引入一个新的失败模式不划算。

    不合并的代价是"往 `TOPICS` 加第七类时，这四处会静默停在六类"：按
    `TOPIC_ORDER` 迭代分栏的报告会**直接不显示那一栏**，不报错。所以这里把
    漂移从静默变成响声——加了一类而忘了改这四份，跑测试就会红。
    """

    COPIES = ('auto_tag.py', 'create_reading_notes.py',
              'enrich_backlinks.py', 'generate_html.py')

    def literal_of(self, path):
        """把文件里 `TOPIC_ORDER = [...]` 的字面量取出来。"""
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'TOPIC_ORDER':
                    return ast.literal_eval(node.value)
        return None

    def test_every_copy_still_matches_the_one_definition(self):
        for name in self.COPIES:
            path = SCRIPTS / name
            self.assertTrue(path.is_file(), name)
            self.assertEqual(self.literal_of(path), TOPICS,
                             '%s 里的 TOPIC_ORDER 与 _utils.TOPICS 已经不一致了' % name)

    def test_the_check_would_notice_a_drifted_copy(self):
        """给检查本身一个反例：它必须抓得到少一类的复制品。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / 'probe.py'
            probe.write_text("TOPIC_ORDER = ['AI', '学术', '新闻', '文学', '投资']\n",
                             encoding='utf-8')
            self.assertNotEqual(self.literal_of(probe), TOPICS)


if __name__ == '__main__':
    unittest.main()
