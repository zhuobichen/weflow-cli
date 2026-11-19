"""`ask` 管线的硬行为：续跑、未实现、引用校验、降级。

用**合成夹具**跑集成用例（`output/wx_mp/demo/home`），不碰真实微信库。
夹具不存在时集成用例跳过，纯逻辑用例照常跑。
"""
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPTS = REPO / 'scripts'
DEMO = REPO / 'output' / 'wx_mp' / 'demo' / 'home'

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    'ask_under_test', SCRIPTS / 'ask.py')
ask_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask_mod)


class RunStateTest(unittest.TestCase):
    """状态容器：原子写、可读回、同一问题同一 id。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ask-state-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_question_same_id_different_profile_different_id(self):
        a = ask_mod.Run('问题', 'balanced', root=self.tmp)
        b = ask_mod.Run('问题', 'balanced', root=self.tmp)
        c = ask_mod.Run('问题', 'offline', root=self.tmp)
        self.assertEqual(a.id, b.id, '同一问题同一档位必须复用同一个 run')
        self.assertNotEqual(a.id, c.id, '换档位不能混进同一个 run')

    def test_state_survives_new_instance(self):
        r1 = ask_mod.Run('问题', 'balanced', root=self.tmp)
        r1.put('route', 'promises')
        r2 = ask_mod.Run('问题', 'balanced', root=self.tmp)
        self.assertEqual(r2.get('route'), 'promises', '状态必须能从磁盘读回')
        self.assertTrue(r2.has('route'))


class StageSkipTest(unittest.TestCase):
    """续跑的实质：已完成的阶段不再执行。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ask-skip-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_completed_retrieve_is_not_called_again(self):
        r = ask_mod.Run('问题', 'balanced', root=self.tmp)
        r.put('candidates', [{'kind': 'hit', 'ts': 1, 'talker': 'x',
                              'said': '原话', 'keywords': []}])
        # 指纹一致才会复用：把两侧都钉成同一个值，测的是"跳过"而不是"指纹变了"
        r.put('db_fp', 'fixed-fp')
        with patch.object(ask_mod, '_db_fingerprint', return_value='fixed-fp'),              patch.object(ask_mod, '_open_demo_or_real',
                          side_effect=AssertionError('不该再读库')):
            got = ask_mod.stage_retrieve(r, 'search_chats')
        self.assertEqual(got[0]['said'], '原话')
        self.assertIn('retrieve', r.resumed)

    def test_completed_judge_is_not_recomputed(self):
        r = ask_mod.Run('问题', 'balanced', root=self.tmp)
        r.put('items', [{'time': '2026-01-01', 'talker': 'x', 'said': 'a',
                         'state': 'found', 'evidence': 'a', 'verdict': 'answered',
                         'path': 'direct-hit'}])
        got = ask_mod.stage_judge(r, [], {'judge': 'local'})
        self.assertEqual(len(got), 1)
        self.assertIn('judge', r.resumed)


class VerifyTest(unittest.TestCase):
    """引用校验：有结论必须有原话。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ask-verify-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_claim_without_evidence_fails(self):
        r = ask_mod.Run('问题', 'balanced', root=self.tmp)
        items = [{'state': 'delivered', 'evidence': None}]
        self.assertFalse(ask_mod.stage_verify(r, items)['ok'])

    def test_claim_with_evidence_passes(self):
        r = ask_mod.Run('问题', 'balanced', root=self.tmp)
        items = [{'state': 'delivered', 'evidence': '发你了'}]
        self.assertTrue(ask_mod.stage_verify(r, items)['ok'])

    def test_unknown_may_have_no_evidence(self):
        """`unknown` 就是在说"没有证据"，它不该被校验拦下——否则弃权表达不出来。"""
        r = ask_mod.Run('问题', 'balanced', root=self.tmp)
        self.assertTrue(ask_mod.stage_verify(r, [{'state': 'unknown', 'evidence': None}])['ok'])


@unittest.skipUnless(DEMO.exists(), '缺少合成夹具 output/wx_mp/demo/home')
class ThreadRouteTest(unittest.TestCase):
    """指代词才允许继承路由——否则新话题会被上一轮拖走。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ask-thread-'))
        self.thread = ask_mod.Thread('t1', root=self.tmp)
        self.thread.save({'route': 'search_chats', 'keywords': ['会议纪要']})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_anaphoric_question_inherits_previous_route(self):
        r = ask_mod.Run('那后来呢', 'balanced', root=self.tmp)
        got = ask_mod._inherit_route_if_anaphoric(r, 'unknown', self.thread)
        self.assertEqual(got, 'search_chats')

    def test_plain_new_topic_is_not_inherited(self):
        r = ask_mod.Run('帮我写个周报', 'balanced', root=self.tmp)
        got = ask_mod._inherit_route_if_anaphoric(r, 'unknown', self.thread)
        self.assertEqual(got, 'unknown', '不带指代词的句子不该借用上一轮路由')

    def test_known_route_is_never_overridden(self):
        r = ask_mod.Run('那后来呢', 'balanced', root=self.tmp)
        got = ask_mod._inherit_route_if_anaphoric(r, 'promises', self.thread)
        self.assertEqual(got, 'promises', '自己认得出来的路由不允许被线程覆盖')


class RelevanceTest(unittest.TestCase):
    """检索精度：单个 2-gram 命中不算相关。"""

    def test_single_two_gram_is_not_enough(self):
        # "会议"能同时命中"会议纪要""开会""会议室"——那不是同一件事
        self.assertFalse(ask_mod._is_relevant(['会议']))

    def test_three_gram_is_strong(self):
        self.assertTrue(ask_mod._is_relevant(['会议纪']))

    def test_two_distinct_two_grams_are_enough(self):
        self.assertTrue(ask_mod._is_relevant(['会议', '纪要']))

    def test_no_hits_is_not_relevant(self):
        self.assertFalse(ask_mod._is_relevant([]))

    def test_hits_preserve_gram_order(self):
        grams = ['会议纪', '议纪要', '会议']
        self.assertEqual(ask_mod._gram_hits('我下周把会议纪要整理好发你', grams),
                         ['会议纪', '议纪要', '会议'])


class VerifyRefTest(unittest.TestCase):
    """引用编号必须出现在正文里，否则读者核对不到。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ask-ref-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ref_missing_from_text_fails(self):
        r = ask_mod.Run('问题', 'balanced', root=self.tmp)
        r.put('composed', {'text': '这里没有任何编号', 'citations': [
            {'ref': 'E1', 'said': '原话'}]})
        out = ask_mod.stage_verify(r, [{'state': 'found', 'evidence': '原话'}])
        self.assertFalse(out['ok'])
        self.assertEqual(out['missing_refs'], ['E1'])

    def test_ref_present_passes(self):
        r = ask_mod.Run('问题', 'balanced', root=self.tmp)
        r.put('composed', {'text': '[E1] 原话', 'citations': [
            {'ref': 'E1', 'said': '原话'}]})
        out = ask_mod.stage_verify(r, [{'state': 'found', 'evidence': '原话'}])
        self.assertTrue(out['ok'])
        self.assertEqual(out['missing_refs'], [])


class ComposeModelTest(unittest.TestCase):
    """模型组句只是改写措辞：它可以不回引用吗？不行——缺引用必须回退模板。"""

    ITEMS = [
        {'time': '2026-11-06', 'talker': '联系人A', 'said': '我下周把会议纪要整理好发你。',
         'state': 'chased', 'evidence': '会议纪要发了吗？'},
        {'time': '2026-11-18', 'talker': '联系人B', 'said': '我明天把排期表发到群里。',
         'state': 'unknown', 'evidence': None},
    ]

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ask-compose-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_model_output_with_refs_is_used(self):
        def fake(route, items):
            return '[E1] 会议纪要这条对方催过；[E2] 排期表还没有看到后续证据。', None
        r = ask_mod.Run('问题', 'cloud', root=self.tmp)
        got = ask_mod.stage_compose(r, self.ITEMS, 'promises', composer=fake)
        self.assertEqual(got['by'], 'model')
        self.assertIn('[E1]', got['text'])

    def test_model_output_missing_refs_falls_back_to_template(self):
        def fake(route, items):
            return '对方催过会议纪要，排期表还没发。', None   # 一个编号都没有
        r = ask_mod.Run('问题', 'cloud', root=self.tmp)
        got = ask_mod.stage_compose(r, self.ITEMS, 'promises', composer=fake)
        self.assertEqual(got['by'], 'template', '缺编号必须整段回退，而不是凑一段没引用的散文')
        self.assertIn('[E1]', got['text'])

    def test_model_claiming_no_evidence_when_there_is_evidence_falls_back(self):
        """实测过的真实错误：模型把「对方催过」写成「还没有看到后续证据」。

        引用编号仍在，verify 也会通过——所以必须在 compose 阶段就拦下来。
        """
        def fake(route, items):
            return ('我对联系人A说下周发会议纪要，还没有看到后续证据 [E1]。'
                    '排期表那条也还没有看到后续证据 [E2]。'), None
        r = ask_mod.Run('问题', 'cloud', root=self.tmp)
        got = ask_mod.stage_compose(r, self.ITEMS, 'promises', composer=fake)
        self.assertEqual(got['by'], 'template',
                         '把已有后续写成"没有后续证据"必须整段回退')

    def test_conflict_detection_is_sentence_scoped(self):
        """只在同一句里判矛盾：别把 E1 的问题算到 E2 头上。"""
        items = [
            {'time': 't1', 'talker': 'A', 'said': 'a', 'state': 'chased', 'evidence': '催过'},
            {'time': 't2', 'talker': 'B', 'said': 'b', 'state': 'unknown', 'evidence': None},
        ]
        text = '第一条对方催过 [E1]。第二条还没有看到后续证据 [E2]。'
        self.assertEqual(ask_mod._compose_conflicts(text, items), [],
                         'E2 本来就没有后续，说"没有"是正确的，不该判矛盾')
        text2 = '第一条还没有看到后续证据 [E1]。'
        self.assertEqual(ask_mod._compose_conflicts(text2, items), ['E1'])

    def test_model_failure_falls_back_silently(self):
        def broken(route, items):
            return None, 'URLError: timed out'
        r = ask_mod.Run('问题', 'cloud', root=self.tmp)
        got = ask_mod.stage_compose(r, self.ITEMS, 'promises', composer=broken)
        self.assertEqual(got['by'], 'template')
        self.assertTrue(got['text'])


class CitationRelevanceTest(unittest.TestCase):
    """引用必须与正文对得上：允许改写措辞，不允许换一件事。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ask-rel-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, text, cites):
        r = ask_mod.Run('问题', 'balanced', root=self.tmp)
        r.put('composed', {'text': text, 'citations': cites})
        return ask_mod.stage_verify(r, [{'state': 'found', 'evidence': 'x'}])

    def test_citation_text_appearing_in_answer_passes(self):
        out = self._run('[E1] 对方后来问「会议纪要发了吗」',
                        [{'ref': 'E1', 'said': '会议纪要发了吗？'}])
        self.assertTrue(out['ok'], out.get('unrelated'))

    def test_citation_about_something_else_is_flagged(self):
        """正文在说 A，引用挂的却是 B —— 这是"引错一条"最典型的形态。"""
        out = self._run('[E1] 本周共 12 条消息',
                        [{'ref': 'E1', 'said': '会议纪要发了吗？'}])
        self.assertFalse(out['ok'])
        self.assertEqual(out['unrelated'], ['E1'])

    def test_short_quote_uses_exact_match(self):
        out = self._run('[E1] 对方回了「收到」', [{'ref': 'E1', 'said': '收到'}])
        self.assertTrue(out['ok'])

    def test_short_quote_missing_is_flagged(self):
        out = self._run('[E1] 对方回了别的话', [{'ref': 'E1', 'said': '收到'}])
        self.assertFalse(out['ok'])


class EndToEndTest(unittest.TestCase):
    """跑真实管线（合成库 + 临时 HOME），不联网、不碰真实配置。

    **夹具在 `output/` 下，而 `output/` 被 `.gitignore` 排掉了**——所以这条端到端用例
    在 CI 上是 ERROR 而不是 FAIL：`setUp` 的 `copytree` 找不到目录就抛。
    2026-11-20 CI 实测：python job 因为这一类 `EndToEndTest` 全红，而本机全绿
    （本机有那个 `output/wx_mp/demo/home`）。

    这里**显式跳过并说明原因**，而不是假装没这回事：跳过会出现在 CI 的
    `skipped '…'` 警告里，读者能顺着这句找到"夹具没进仓库"这个真问题。
    真正的修法是把夹具挪进 `test/fixtures/`（合成数据、可入库），而不是留在 `output/`。
    """

    @classmethod
    def setUpClass(cls):
        if not (DEMO / '.weflow-cli').is_dir():
            raise unittest.SkipTest(
                f'端到端夹具不在：{DEMO}（它在被 gitignore 的 output/ 下，CI 上没有）。'
                '修法见本类 docstring：把夹具挪进 test/fixtures/')

    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix='ask-e2e-'))
        shutil.copytree(DEMO / '.weflow-cli', self.home / '.weflow-cli')
        shutil.copytree(DEMO / 'nt', self.home / 'nt')
        # 配置里的路径指向旧 HOME，改指临时目录
        cfg_path = self.home / '.weflow-cli' / 'config.json'
        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
        cfg['ntDbPath'] = str(self.home / 'nt' / 'message_0.db')
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
        self._old = os.environ.get('USERPROFILE')
        os.environ['USERPROFILE'] = str(self.home)
        os.environ['HOME'] = str(self.home)
        # `_utils.CONFIG_PATH` 在 **import 时**就定死了（`expanduser('~')` 求值一次），
        # 所以改环境变量不会重定向它——必须显式改这个模块级常量。
        import _utils
        self._orig_cfg_path = _utils.CONFIG_PATH
        _utils.CONFIG_PATH = str(cfg_path)

    def tearDown(self):
        import _utils
        _utils.CONFIG_PATH = self._orig_cfg_path
        for k in ('USERPROFILE', 'HOME'):
            if self._old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = self._old
        shutil.rmtree(self.home, ignore_errors=True)

    def test_promises_route_answers_with_citations(self):
        res = ask_mod.ask('上个月我答应过谁什么', root=self.home / 'runs')
        self.assertEqual(res['route'], 'promises')
        self.assertEqual(res['verdict'], 'answered')
        self.assertTrue(res['verified'], '有结论的条目必须都带引用')
        self.assertGreaterEqual(len(res['citations']), 1)
        for c in res['citations']:
            self.assertTrue(c['said'], '引用必须带原话')

    def test_search_chats_route_is_not_a_special_case(self):
        res = ask_mod.ask('找一下提到会议纪要的对话', root=self.home / 'runs')
        self.assertEqual(res['route'], 'search_chats')
        self.assertTrue(res['verified'])
        self.assertTrue(any('会议纪要' in c['said'] for c in res['citations']),
                        'N-gram 关键词应当命中含"会议纪要"的消息')

    def test_second_run_resumes_every_stage(self):
        q = '上个月我答应过谁什么'
        first = ask_mod.ask(q, root=self.home / 'runs')
        second = ask_mod.ask(q, root=self.home / 'runs')
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertEqual(sorted(second['resumed']),
                         ['compose', 'judge', 'retrieve', 'route', 'verify'],
                         '第二次跑不该重算任何阶段')
        self.assertEqual(first['answer'], second['answer'], '续跑结果必须一致')

    def test_db_change_invalidates_cached_evidence(self):
        """库变了就不能复用旧证据——否则用户看到过期的原话且毫无提示。"""
        q = '上个月我答应过谁什么'
        ask_mod.ask(q, root=self.home / 'runs')
        db = self.home / 'nt' / 'message_0.db'
        os.utime(db, None)                      # 模拟微信写库
        second = ask_mod.ask(q, root=self.home / 'runs')
        self.assertNotIn('retrieve', second['resumed'],
                         '库变了必须重取证据')
        self.assertIn('route', second['resumed'], '路由与库无关，仍可复用')

    def test_thread_carries_context_across_turns(self):
        """第一轮问会议纪要，第二轮只问"那后来呢"——仍要找到同样的记录。

        这是"思考连续性"的最小验收：靠的是上一轮留下的检索词与路由，
        而不是把上一轮的原文塞进 prompt。
        """
        troot = self.home / 'threads'
        first = ask_mod.ask('找一下提到会议纪要的对话', root=self.home / 'runs',
                            thread_id='demo', thread_root=troot)
        self.assertEqual(first['route'], 'search_chats')
        second = ask_mod.ask('那后来呢', root=self.home / 'runs',
                             thread_id='demo', thread_root=troot)
        self.assertEqual(second['route'], 'search_chats', '指代词应当继承上一轮路由')
        self.assertTrue(any('会议纪要' in c['said'] for c in second['citations']),
                        '应当沿用上一轮的检索词找到同样的记录')

    def test_thread_does_not_hijack_a_new_topic(self):
        troot = self.home / 'threads'
        ask_mod.ask('找一下提到会议纪要的对话', root=self.home / 'runs',
                    thread_id='demo2', thread_root=troot)
        later = ask_mod.ask('帮我写个周报', root=self.home / 'runs',
                            thread_id='demo2', thread_root=troot)
        self.assertEqual(later['verdict'], 'unimplemented',
                         '新话题不能被上一轮拖进"查聊天"')

    def test_stats_route_reports_aggregate_with_its_scope(self):
        """聚合类结论没有"原话"可挂，挂的是**口径**——文档要这么写。"""
        res = ask_mod.ask('这个月我一共聊了多少条消息', root=self.home / 'runs')
        self.assertEqual(res['route'], 'stats')
        self.assertEqual(res['verdict'], 'answered')
        self.assertTrue(res['verified'])
        text = res['answer']
        self.assertIn('共 ', text)
        self.assertIn('覆盖', text)
        self.assertIn('时间范围', text)

    def test_unimplemented_route_says_so_instead_of_guessing(self):
        res = ask_mod.ask('把和联系人A的聊天导出成 HTML', root=self.home / 'runs')
        # 规则臂可能把它判成 export；云端不可用时也应是 unimplemented
        if res['verdict'] == 'unimplemented':
            self.assertIn('尚未实现', res['note'])
            self.assertIsNone(res['answer'])
        else:
            self.skipTest('规则臂判成了 %s，本例只覆盖 unimplemented 分支' % res['route'])

    def test_unimplemented_route_offers_an_existing_command(self):
        """"没实现"不该是终点：导出类要给出现成命令。"""
        res = ask_mod.ask('把和联系人A的聊天导出成 HTML', root=self.home / 'runs')
        if res['verdict'] != 'unimplemented':
            self.skipTest('规则臂判成了 %s' % res['route'])
        self.assertIn('尚未实现', res['note'])
        self.assertTrue(res.get('suggest'), '未实现的路由应当给出替代命令')
        self.assertIn('weflow-cli', res['suggest'])

    def test_no_jev_key_degrades_instead_of_crashing(self):
        res = ask_mod.ask('上个月我答应过谁什么', root=self.home / 'runs', resume=False)
        self.assertTrue(any('route:' in d for d in res['degraded']),
                        '没有 key 时要在 degraded 里说明，而不是静默')


if __name__ == '__main__':
    unittest.main()
