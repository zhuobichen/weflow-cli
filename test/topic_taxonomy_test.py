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
