"""分类法只有一份定义，而且两条路径读的是同一张表。

**这个测试是为了一个真实发生过的漂移**：`TOPICS` 在五个文件里各有一份，而
`biz_daily.TOPIC_PROMPT` 的第一行按 TOPICS 生成了六类，同一段提示词后面两处提醒
却是硬编码的五类（少了政治），判断规则里也没有政治那一栏——而政治在语料里占 26%。

散文写的枚举会漂；表不会。所以这里钉两件事：**只有一处定义**，以及
**每条路径都从那里读**。
"""
import importlib.util
import re
import sqlite3
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from _utils import TOPICS, TOPIC_CRITERIA  # noqa: E402
from _utils import RELEVANCE_NAMES, DEFAULT_RELEVANCE  # noqa: E402


def load(name):
    """按脚本方式加载，sqlcipher3 用桩（CI 的 python job 不装它）。"""
    spec = importlib.util.spec_from_file_location('tax_%s' % name, SCRIPTS / ('%s.py' % name))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
        spec.loader.exec_module(module)
    return module


class SingleDefinitionTests(unittest.TestCase):
    def test_every_consumer_reads_the_same_list(self):
        # identity 而不是相等：相等也能靠"各自抄一份、恰好抄对"通过，
        # 而抄一份正是漂移的来源。
        for name in ('biz_daily', 'classify_daily', 'fix_topics',
                     'generate_ai_report', 'jev_probe', 'quality_eval'):
            with self.subTest(module=name):
                self.assertIs(load(name).TOPICS, TOPICS,
                              '%s 里的 TOPICS 不是 _utils 那一份' % name)

    def test_both_judging_paths_share_the_same_criteria(self):
        # 提示词那条路和决策模型那条路必须判同一套定义——否则回退时换了标准，
        # 而"回退到旧行为"这句话就不成立了。
        import jev_client
        self.assertIs(jev_client.TOPIC_CRITERIA, TOPIC_CRITERIA)

    def test_the_criteria_cover_exactly_the_topics(self):
        self.assertEqual(set(TOPIC_CRITERIA), set(TOPICS))

    def test_the_order_is_stable(self):
        # generate_ai_report 按这个顺序给报告分栏，改顺序会改产物。
        self.assertEqual(TOPICS, ['AI', '学术', '新闻', '文学', '投资', '政治'])


class RelevanceVocabularyTests(unittest.TestCase):
    """相关度的三档是和 `TOPICS` 同一种东西：下游按字面量比较的封闭词表。

    它原先也有两份——`jev_client` 一份具名，`biz_daily` 的成员校验里再写一份
    `['高','中','低']`。两处一致时看不出问题，等哪天要加一档（或者改一个字），
    改了一处的那一半就静默按别的标准判。

    **不测 `extract_todos.py` 里的 `['高','中','低']`**：那是待办的紧急度
    （`--urgency` 的可选值），与文章的 relevance 只是碰巧共用三个汉字。
    把两者耦合起来会让"待办可以加一档紧急度"变成动到日报契约的事。
    """

    def test_the_vocabulary_is_read_not_copied(self):
        # 同 `TOPICS`：断言**同一性**而不是相等。
        for name in ('jev_client', 'biz_daily'):
            with self.subTest(module=name):
                self.assertIs(load(name).RELEVANCE_NAMES, RELEVANCE_NAMES,
                              '%s 里的 RELEVANCE_NAMES 不是 _utils 那一份' % name)

    def test_the_daily_writer_reads_the_shared_fallback_level(self):
        # 只有 `biz_daily` 需要这个兜底：`jev_client.score_to_relevance` 是把分数
        # 映射到档位，任何分数都有档位可返回，不存在"判不出来"的情形。
        # （第一版这里连 jev_client 一起断言了，跑起来才发现它根本没有这个名字。）
        self.assertIs(load('biz_daily').DEFAULT_RELEVANCE, DEFAULT_RELEVANCE)

    def test_the_fallback_level_is_a_real_level(self):
        self.assertIn(DEFAULT_RELEVANCE, RELEVANCE_NAMES)

    def test_the_level_order_is_stable(self):
        """顺序即语义：`jev_client.RELEVANCE_LEVELS` 按下标与它对一一对应。"""
        self.assertEqual(RELEVANCE_NAMES, ['低', '中', '高'])

    def test_no_file_writes_the_vocabulary_inline(self):
        """光"导入了词表"不够——还得**用它**。

        这条是变异测试逼出来的：把 `raw_rel in RELEVANCE_NAMES` 改回
        `raw_rel in ['高', '中', '低']`，上面那条同一性断言照样绿——导入还在，
        只是没用。而"手里有源表、却写字面量"正是漂移的下一步。

        例外清单：`extract_todos.py` 的 `['高','中','低']` 是待办的紧急度
        （`--urgency` 的可选值），与文章 relevance 是两个词汇表，只是碰巧同字。
        """
        import ast
        allowed = {'extract_todos.py'}
        vocabulary = frozenset(RELEVANCE_NAMES)
        offenders = []
        for path in sorted(SCRIPTS.glob('*.py')):
            if path.name in allowed or path.name == '_utils.py':
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                    continue
                values = [e.value for e in node.elts
                          if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if len(values) == len(node.elts) and frozenset(values) == vocabulary:
                    offenders.append('%s:%d' % (path.name, node.lineno))
        self.assertEqual(offenders, [],
                         '相关度词表应只在 _utils 一处：%s' % offenders)

    def test_the_exemption_is_still_a_different_vocabulary(self):
        """上面的例外清单要能自证：`extract_todos` 那份是**参数可选值**，不是文章档位。"""
        source = (SCRIPTS / 'extract_todos.py').read_text(encoding='utf-8')
        self.assertIn("'--urgency'", source)
        self.assertNotIn('RELEVANCE_NAMES', source)

    def test_no_bare_relevance_default_remains_in_the_daily_writer(self):
        """日报里不许再出现写死的 relevance 兜底值（`get`/`setdefault` 那种）。

        判据只认"默认值"这两种语法形态。`a['relevance'] = '中'` 那种赋值在
        遗留解析路径里是**子串命中的结论**（`elif '中' in raw_rel:`），不是兜底，
        不能一起禁掉——禁掉会逼出一段更绕的代码。
        """
        import ast
        source = (SCRIPTS / 'biz_daily.py').read_text(encoding='utf-8')
        offenders = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == 'get' and len(node.args) >= 2:
                    key, default = node.args[0], node.args[1]
                    if (isinstance(key, ast.Constant) and key.value == 'relevance'
                            and isinstance(default, ast.Constant)):
                        offenders.append(node.lineno)
                if node.func.attr == 'setdefault' and len(node.args) >= 2:
                    key, default = node.args[0], node.args[1]
                    if (isinstance(key, ast.Constant) and key.value == 'relevance'
                            and isinstance(default, ast.Constant)):
                        offenders.append(node.lineno)
        self.assertEqual(offenders, [], 'relevance 兜底值应只用 DEFAULT_RELEVANCE：%s' % offenders)
class PromptConsistencyTests(unittest.TestCase):
    """提示词里**每一处**枚举都必须覆盖全部类目。

    原先三处里有二处是硬编码的，其中一处写的是五类——而那两处恰恰是
    "必须严格"的地方。
    """

    def setUp(self):
        self.prompt = load('biz_daily').TOPIC_PROMPT

    def test_the_mandatory_line_lists_every_topic(self):
        line = next(l for l in self.prompt.splitlines() if '必须且只能是' in l)
        for topic in TOPICS:
            self.assertIn(topic, line, '这一行的枚举漏了 %s' % topic)

    def test_every_topic_has_a_judging_rule(self):
        # 政治曾经没有定义——一个占语料 26% 的类目没有判据。
        rules = re.findall(r'^- ([^：]+)：(.+)$', self.prompt[:800], re.M)
        defined = {name for name, _desc in rules}
        for topic in TOPICS:
            self.assertIn(topic, defined, '%s 没有判断规则' % topic)

    def test_the_one_word_reminder_lists_every_topic(self):
        # 这就是原先硬编码成五类的那一行。
        line = next(l for l in self.prompt.splitlines()
                    if '只写一个词' in l and '主题' in l)
        for topic in TOPICS:
            self.assertIn(topic, line, '提醒行漏了 %s' % topic)

    def test_the_rules_carry_the_same_wording_as_the_decision_model(self):
        # 两条路径用同一张表生成，所以提示词里的规则文本应当逐条等于判据表。
        for topic, description in TOPIC_CRITERIA.items():
            self.assertIn('%s：%s' % (topic, description), self.prompt,
                          '%s 的规则文本与判据表不一致' % topic)


if __name__ == '__main__':
    unittest.main()
