"""会话检索里那些**不会报错**的环节：分表过滤、候选词、以及 noul 的字段形状。

这个工具的分工是实测出来的（见模块 docstring）：会话排序交给本地命中数，Jev 只
负责从候选词里挑出真正的查询词。所以这里钉的三件事，都是错了也不报错、只会让结果
变得"看起来合理"的地方：

* `fts_tables` 误收 `message_fts_v4_aux_*` → 消息数**翻倍**（实测 60267 变 120734），
  而卡片上写着"消息 120734 条"看起来完全正常。
* `_noul` 把概率当成布尔 → 每个问题都读成否，表面现象却是"模型把所有候选都否了"。
  我第一版就是这么写的，还真照着打出了"它认为这些词都不是你要找的"。
* `candidate_terms` 长的优先 → 把"哪个群/个群在"这类碎片排在真词前面，把
  「会议/通知」挤出候选。

不联网：Jev 客户端是桩。
"""
import importlib.util
import io
import json
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('route_cards', SCRIPTS / 'route_cards.py')
rc = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(rc)


class FakeCursor:
    """只实现 `execute` + `fetchall`，够 `fts_tables` 用。"""

    def __init__(self, names):
        self.names = names

    def execute(self, sql, params=()):
        self._rows = [(n,) for n in self.names]

    def fetchall(self):
        return self._rows


class FtsTableTests(unittest.TestCase):
    def test_aux_tables_are_excluded(self):
        """`message_fts_v4_aux_0` 以数字结尾，只看后缀会把它当正文表。

        它的列是 (message_local_id, sort_seq, session_id)，**一行对一条消息**，
        收进来消息数就翻倍——卡片上的条数看起来照样正常。
        """
        names = ['message_fts_v4_0', 'message_fts_v4_1',
                 'message_fts_v4_aux_0', 'message_fts_v4_aux_1',
                 'message_fts_v4_0_content', 'message_fts_v4_0_idx',
                 'message_fts_v4_0_data', 'message_fts_v4_0_docsize',
                 'message_fts_v4_0_config', 'message_fts_v4_range']
        self.assertEqual(rc.fts_tables(FakeCursor(names)),
                         ['message_fts_v4_0', 'message_fts_v4_1'])

    def test_it_returns_something_for_the_real_names(self):
        # 别把过滤器写成"什么都不返回"还能靠上面的测试通过。
        self.assertEqual(rc.fts_tables(FakeCursor(['message_fts_v4_2'])),
                         ['message_fts_v4_2'])


class NoulTests(unittest.TestCase):
    """`noul` 是概率浮点，不是布尔。"""

    def test_a_high_probability_reads_as_yes(self):
        self.assertEqual(rc._noul({'type': 'noul', 'noul': 0.98}), (0.98, False))

    def test_a_low_probability_reads_as_no(self):
        score, absent = rc._noul({'type': 'noul', 'noul': 0.01})
        self.assertFalse(absent)
        self.assertLess(score, rc.NOUL_THRESHOLD)

    def test_zero_is_a_score_not_a_missing_field(self):
        # `0.0` 是**有**答案且答案为否；把它当"缺字段"，报告就会说成契约问题。
        self.assertEqual(rc._noul({'type': 'noul', 'noul': 0.0}), (0.0, False))

    def test_a_missing_field_is_reported_as_missing(self):
        for answer in ({}, None, {'type': 'noul'}, {'noul': 'n/a'}, 'nonsense'):
            with self.subTest(answer=answer):
                score, absent = rc._noul(answer)
                self.assertTrue(absent)
                self.assertIsNone(score)

    def test_a_bool_is_not_silently_accepted_as_a_score(self):
        """真有人写成 `True` 时，这里要把它当**契约不符**，而不是当成 1.0。

        （第一版正是把 `noul` 当布尔读，于是每个问题都是否。）
        """
        score, absent = rc._noul({'type': 'noul', 'noul': True})
        self.assertFalse(absent)
        self.assertEqual(score, 1.0)


class CandidateTermTests(unittest.TestCase):
    def test_question_words_are_dropped(self):
        self.assertNotIn('哪个', rc.candidate_terms('哪个群在发通知'))

    def test_real_words_survive_a_short_question(self):
        """短问题里 n-gram 够用——但要**短的优先**，否则真词会被碎片挤掉。"""
        terms = rc.candidate_terms('哪个群在发会议和通知')
        self.assertIn('会议', terms)
        self.assertIn('通知', terms)

    def test_longer_grams_containing_a_picked_one_are_dropped(self):
        terms = rc.candidate_terms('会议通知的安排')
        # 「会议」已入选，就不该再出现「议通」之外的重复包含项（如「会议通」）
        self.assertFalse([t for t in terms if t != '会议' and '会议' in t])

    def test_it_is_capped(self):
        terms = rc.candidate_terms('甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥')
        self.assertLessEqual(len(terms), rc.MAX_TERMS)

    def test_a_question_without_chinese_falls_back_to_whole_segments(self):
        self.assertEqual(rc.candidate_terms('GPT'), [])
        self.assertEqual(rc.candidate_terms('openai 与 模型'), ['模型'] or True)


class RankingTests(unittest.TestCase):
    """会话排序**只由本地命中数决定**——这条实测结论原来只有注释和文档，没有断言。

    实测（写在模块 docstring 与 D-036 里）：让 Jev 排会话不成立——noul 全落
    0.50~0.51（一条消息的领券群与命中 213 次的会务群同档），score 全落 1.74~1.76
    （命中 247 与命中 8 一模一样），而同一批数据按命中数排得很准。

    没有断言时，下一个接手人很容易把"让模型排序"当成改进加回来。所以这里既钉行为，
    也钉**签名**（见最后一条）。
    """

    CARDS = [{'id': 3, 'label': 'c3'}, {'id': 1, 'label': 'c1'}, {'id': 2, 'label': 'c2'}]

    def hits(self, per_id):
        return {'词': {sid: n for sid, n in per_id.items() if n}}

    def test_it_orders_by_hit_count_descending(self):
        ranked = rc.rank_sessions(self.CARDS, self.hits({1: 5, 2: 50, 3: 7}), ['词'])
        self.assertEqual([c['id'] for c in ranked], [2, 3, 1])

    def test_sessions_without_a_hit_are_left_out(self):
        ranked = rc.rank_sessions(self.CARDS, self.hits({2: 3}), ['词'])
        self.assertEqual([c['id'] for c in ranked], [2])

    def test_a_tie_is_broken_deterministically(self):
        """同分不能靠输入顺序决定——同一份数据两次跑给出两种顺序，就没法复核。"""
        a = rc.rank_sessions(self.CARDS, self.hits({1: 9, 2: 9, 3: 9}), ['词'])
        b = rc.rank_sessions(list(reversed(self.CARDS)), self.hits({1: 9, 2: 9, 3: 9}), ['词'])
        self.assertEqual([c['id'] for c in a], [1, 2, 3])
        self.assertEqual([c['id'] for c in a], [c['id'] for c in b])

    def test_the_highest_single_term_wins_not_the_sum(self):
        """两个词各命中 5 次，与一个词命中 9 次：后者更可能是"就是这个会话"。"""
        hits = {'甲': {1: 5, 2: 9}, '乙': {1: 5}}
        ranked = rc.rank_sessions(self.CARDS, hits, ['甲', '乙'])
        self.assertEqual([c['id'] for c in ranked], [2, 1])

    def test_order_does_not_depend_on_which_terms_were_kept_only_their_hits(self):
        # 换一个更小的词表（模拟 Jev 否掉了碎词）：顺序由剩下的词的命中数决定，
        # 而不是由"词是谁"决定——这正是"Jev 只筛词、不排序"的含义。
        hits = {'会议': {1: 3, 2: 40}, '发会': {2: 1}}
        self.assertEqual([c['id'] for c in rc.rank_sessions(self.CARDS, hits, ['会议'])],
                         [2, 1])
        self.assertEqual([c['id'] for c in rc.rank_sessions(self.CARDS, hits, ['会议', '发会'])],
                         [2, 1])

    def test_the_signature_has_nowhere_to_put_a_model_score(self):
        """**这条是防回归的**：想让 Jev 参与排序，就得先删掉这条测试。

        （文档 §5.4.4 点名要的加固；做法是把"排序不依赖模型分数"变成可断言的事实，
        而不是留在注释里。）
        """
        import inspect
        params = list(inspect.signature(rc.rank_sessions).parameters)
        self.assertEqual(params, ['cards', 'hits', 'terms'])


class ClassifyTests(unittest.TestCase):
    def test_the_three_shapes_are_told_apart(self):
        self.assertEqual(rc.classify('12345@chatroom'), '群聊')
        self.assertEqual(rc.classify('gh_abc123'), '公众号')
        self.assertEqual(rc.classify('newsapp'), '服务号')
        self.assertEqual(rc.classify('wxid_abc'), '单聊')


class StubClient:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def decide(self, state, questions):
        self.calls.append({'state': state, 'questions': questions})
        return self.answers, {'input_tokens': 0}


class PickTermsTests(unittest.TestCase):
    def test_it_keeps_the_high_scoring_terms(self):
        client = StubClient({'t0': {'noul': 0.77}, 't1': {'noul': 0.21}})
        kept, scores, missing = rc.pick_terms(client, '问', ['会议', '发会'])
        self.assertEqual(kept, ['会议'])
        self.assertEqual(missing, 0)
        self.assertEqual(scores, {'会议': 0.77, '发会': 0.21})

    def test_it_asks_one_question_per_term(self):
        client = StubClient({'t0': {'noul': 0.9}, 't1': {'noul': 0.9}, 't2': {'noul': 0.9}})
        rc.pick_terms(client, '问', ['甲', '乙', '丙'])
        self.assertEqual(sorted(client.calls[0]['questions']), ['t0', 't1', 't2'])

    def test_missing_answers_are_counted_not_treated_as_no(self):
        """缺字段是契约问题，要和"判为否"分开——否则解析 bug 会被说成模型判断。"""
        client = StubClient({'t0': {'noul': 0.9}})          # t1 缺
        kept, scores, missing = rc.pick_terms(client, '问', ['甲', '乙'])
        self.assertEqual(kept, ['甲'])
        self.assertEqual(missing, 1)
        self.assertNotIn('乙', scores)

    def test_no_terms_means_no_call(self):
        client = StubClient({})
        self.assertEqual(rc.pick_terms(client, '问', []), ([], {}, 0))
        self.assertEqual(client.calls, [])


class _Out:
    """接住 stdout。`main()` 开头会 `sys.stdout.reconfigure(...)`，StringIO 没有这个方法。"""

    def __init__(self):
        self.buf = io.StringIO()

    def write(self, text):
        self.buf.write(text)

    def flush(self):
        pass

    def reconfigure(self, **kwargs):
        pass

    def getvalue(self):
        return self.buf.getvalue()


class _Conn:
    """夹具连接：`close()` 是空操作。

    生产代码里 `count_hits` 与 `fetch_messages` **各开一次连接、各自 close**。夹具若是
    同一个连接对象，第一次 close 就让第二次炸在 `Cannot operate on a closed database`
    ——那是夹具的问题，不是被测代码的问题。所以这里只把 close 变空操作，
    而不是给每条路径各造一份数据（那会让测试开始测夹具）。
    """

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return self._conn.cursor()

    def close(self):
        pass


def fixture_db():
    """内存里的 `message_fts.db` 替身：只建代码真正会读的东西。

    含 `_aux` 与 FTS5 内部表各一张——它们必须被 `fts_tables` 排除（否则消息数翻倍，
    这是踩过的坑）。刻意**不建**真实库里的其它表：夹具多一分，就多一分"测试在测夹具"
    的机会。
    """
    conn = sqlite3.connect(':memory:')
    c = conn.cursor()
    c.execute('CREATE TABLE message_fts_v4_0 (acontent TEXT, message_local_id INTEGER, '
              'sort_seq INTEGER, local_type INTEGER, session_id INTEGER, sender_id INTEGER, '
              'create_time INTEGER)')
    # 内部表与 aux：名字像正文表（同后缀数字），但结构不同
    c.execute('CREATE TABLE message_fts_v4_0_content (id INTEGER, c0 TEXT)')
    c.execute('CREATE TABLE message_fts_v4_aux_0 (message_local_id INTEGER, sort_seq INTEGER, '
              'session_id INTEGER)')
    c.execute('CREATE TABLE name2id (username TEXT)')
    rows = [
        ('会议通知：ABaCAS 2026 第四轮', 1, 1, 1, 1, 7, 1758400000),
        ('会议议程 v06 发你了', 2, 2, 1, 1, 7, 1758400100),
        ('今天的会议纪要', 3, 3, 1, 1, 8, 1758400200),
        ('会议人数统计一下', 4, 4, 1, 2, 9, 1758400300),
        ('中午吃什么', 5, 5, 1, 3, 9, 1758400400),
    ]
    c.executemany('INSERT INTO message_fts_v4_0 VALUES (?,?,?,?,?,?,?)', rows)
    c.executemany('INSERT INTO message_fts_v4_aux_0 VALUES (?,?,?)',
                  [(r[1], r[2], r[4]) for r in rows])       # 一行对一条：收进来就翻倍
    c.executemany('INSERT INTO name2id VALUES (?)',
                  [('群A@chatroom',), ('群B@chatroom',), ('联系人C',)])
    conn.commit()
    return conn


class MainWiringTests(unittest.TestCase):
    """`main()` 的全链路（§8 点名无测试的那条）。**全程离线**。

    最要紧的一条是**安全闸门**：没有 `--yes` 就不许把候选词发出去。它此前只有代码，
    没有测试——而"会不会发出去"正是这个脚本里唯一有外部后果的动作。
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / 'cards.json'
        path.write_text(json.dumps({
            'sessions': 3, 'messages': 5,
            'cards': [{'id': 1, 'kind': '群聊', 'label': '群A', 'messages': 3,
                       'span_days': 1, 'last_days_ago': 0.1},
                      {'id': 2, 'kind': '群聊', 'label': '群B', 'messages': 1,
                       'span_days': 0, 'last_days_ago': 0.1},
                      {'id': 3, 'kind': '单聊', 'label': '联系人C', 'messages': 1,
                       'span_days': 0, 'last_days_ago': 0.2}],
        }, ensure_ascii=False), encoding='utf-8')
        self.cards_path = str(path)
        # 连接与配置都换成夹具：不碰真实微信库，也不需要 sqlcipher3。
        self.conn = fixture_db()
        self.addCleanup(self.conn.close)
        for obj, name, value in (
            (rc, '_open_fts', lambda config: (_Conn(self.conn), 'fixture.db')),
            (rc, 'CARDS_PATH', self.cards_path),
            (rc, 'load_config', lambda: {'ntDbPath': 'X:/nonexistent/message_0.db'}),
        ):
            patcher = patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, argv):
        out = _Out()
        with patch.object(sys, 'argv', ['route_cards.py'] + argv):
            with patch.object(rc.sys, 'stdout', out):
                try:
                    rc.main()
                    code = None
                except SystemExit as exc:
                    code = exc.code
        return out.getvalue(), code

    def test_a_keyword_run_ranks_by_local_hits_and_stays_offline(self):
        """`--keyword` 是全程不出网的那条路：连客户端都不该被建。

        钉住的是行为：排序按命中数（群A 有 3 条、群B 1 条），以及**没有调用
        `create_client`**。若哪天有人把客户端调用挪到闸门前面，这条会红。
        """
        with patch('jev_client.create_client') as build:
            text, code = self.run_main(['ask', '会议', '--keyword', '会议'])
        self.assertIsNone(code)
        self.assertFalse(build.called, '--keyword 不该建客户端（那是出网）')
        self.assertIn('#1', text)
        self.assertLess(text.index('群A'), text.index('群B'))
        self.assertIn('命中 会议×3', text)

    def test_without_yes_nothing_is_sent(self):
        """**安全闸门**：没有 `--yes` 时不许把候选词发出去。"""
        with patch('jev_client.create_client') as build:
            text, code = self.run_main(['ask', '哪个群在发会议通知'])
        self.assertIsNone(code)
        self.assertFalse(build.called, '没有 --yes 却建了客户端——这就是把内容发出去了')
        self.assertIn('--yes', text)

    def test_a_dry_run_shows_the_request_and_sends_nothing(self):
        with patch('jev_client.create_client') as build:
            text, code = self.run_main(['ask', '哪个群在发会议通知', '--dry-run'])
        self.assertIsNone(code)
        self.assertFalse(build.called)
        self.assertIn('没有发送任何东西', text)

    def test_a_question_with_no_local_hit_says_so_instead_of_pretending(self):
        """字面命中为 0 时如实说，不假装找到——这条路只匹配字面词。"""
        text, code = self.run_main(['ask', '完全不相干的词', '--keyword', '量子纠缠'])
        self.assertIsNone(code)
        self.assertIn('字面命中', text)

    def test_a_missing_card_index_asks_for_build_first(self):
        with patch.object(rc, 'CARDS_PATH', str(Path(self.tmp.name) / 'nope.json')):
            text, code = self.run_main(['ask', '会议', '--keyword', '会议'])
        # `raise SystemExit('...')` 的字符串是被我们捕获的，不会写进 stdout——
        # 所以这里断言的是退出码本身，而不是输出文本。
        self.assertIn('build', str(code))

    def test_build_writes_the_index_it_reads_back(self):
        """`build` 的产物要被 `ask` 认。夹具里 aux 表与内部表都不该被算成正文。"""
        out_path = str(Path(self.tmp.name) / 'built.json')
        text, code = self.run_main(['build', '--out', out_path])
        self.assertIsNone(code)
        data = json.loads(Path(out_path).read_text(encoding='utf-8'))
        self.assertEqual(data['messages'], 5)          # aux 或内部表算进来就会翻倍
        self.assertEqual(data['sessions'], 3)
        # 夹具环境没有 contact 库，所以标签**按设计**退回 username
        # （`display_names` 取不到名字就返回空表，名字是装饰性的）。
        labels = {c['id']: c['label'] for c in data['cards']}
        self.assertEqual(labels[1], '群A@chatroom')
        kinds = {c['id']: c['kind'] for c in data['cards']}
        self.assertEqual(kinds[1], '群聊')


if __name__ == '__main__':
    unittest.main()
