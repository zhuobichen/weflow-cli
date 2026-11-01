# -*- coding: utf-8 -*-
"""`import qchat` 这条链的三段：存储读取器、知识卡、语义索引。

链路上的失效全是静默的：schema 判据松了会读进无关 JSON；QQ 卡的字段少了
main() 后半段直接 KeyError 或写出的卡下游不认；索引记录形状与微信侧不一致时
同一条记录在两半索引里语义不同。所以这里分别钉：
读取器的 schema 门、卡片字典与微信卡**逐键一致**、索引记录与
`collect_chat_messages` **逐字段一致**、以及"没配微信库也能建索引"这条新路的
端到端（假 embedding，不联网）。
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from _tmpenv import mkdtemp  # noqa: E402  临时目录不许落在仓库里

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ic = load('imported_chats')
cn = load('chat_notes')
ss = load('semantic_search')

TZ = timezone(timedelta(hours=8))
NOW = datetime.now(TZ)


def qq_session(sess_id='123456', name='老张', messages=None, hours_ago=1):
    return {
        'schema': 'weflow-import/qq-v1',
        'session': {'id': sess_id, 'name': name, 'type': 'private'},
        'importedAt': '2026-10-24T00:00:00Z',
        'sourceFiles': ['fake.json'],
        'messages': messages if messages is not None else [],
    }


def qq_text(server_id, hours_ago, text='这是导入的一条消息，聊聊空气质量模型部署', **kw):
    msg = {
        'serverId': str(server_id),
        'createTime': int((NOW - timedelta(hours=hours_ago)).timestamp()),
        'senderUsername': '123456',
        'senderName': kw.get('sender', '老张'),
        'messageType': kw.get('mtype', 'text'),
        'parsedContent': kw.get('content', text),
        'rawContent': '',
    }
    if 'isSend' in kw:
        msg['isSend'] = kw['isSend']
    return msg


class EnvRoot:
    """把 WEFLOW_IMPORT_ROOT 指到临时目录的上下文（读取器与 TS 写入方读同一个变量）。"""

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix='qq-import-'))
        (self.root / 'sessions').mkdir(parents=True)
        self._saved = os.environ.get('WEFLOW_IMPORT_ROOT')
        os.environ['WEFLOW_IMPORT_ROOT'] = str(self.root)
        return self.root

    def __exit__(self, *exc):
        if self._saved is None:
            os.environ.pop('WEFLOW_IMPORT_ROOT', None)
        else:
            os.environ['WEFLOW_IMPORT_ROOT'] = self._saved
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)
        return False


def write_session(root, data):
    path = root / 'sessions' / ('%s.json' % data['session']['id'])
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


class ReaderTests(unittest.TestCase):
    def test_schema_gate_skips_foreign_json(self):
        with EnvRoot() as root:
            write_session(root, qq_session(messages=[qq_text(1, 1)]))
            (root / 'sessions' / 'other.json').write_text(
                json.dumps({'schema': 'something-else', 'messages': []}, ensure_ascii=False),
                encoding='utf-8')
            (root / 'sessions' / 'broken.json').write_text('{not json', encoding='utf-8')
            sessions = ic.sessions()
            self.assertEqual(len(sessions), 1, '只有本 schema 的文件算数')
            self.assertEqual(sessions[0]['session']['id'], '123456')

    def test_window_filter_uses_days(self):
        with EnvRoot() as root:
            write_session(root, qq_session(messages=[
                qq_text(1, 1),     # 1 小时前
                qq_text(2, 24 * 40),  # 40 天前
            ]))
            msgs = ic.session_messages(ic.sessions()[0], days=7)
            self.assertEqual([m['serverId'] for m in msgs], ['1'])
            self.assertEqual(len(ic.session_messages(ic.sessions()[0])), 2)


class ChatNotesQqTests(unittest.TestCase):
    def test_qq_format_message_maps_display_forms(self):
        # 非文本给中文占位（与微信侧同一套词），有名字用名字
        img = cn.qq_format_message({'createTime': 1, 'messageType': 'image',
                                    'parsedContent': '', 'senderName': '老张'})
        self.assertEqual(img['parsedContent'], '[图片]')
        line = cn.format_line(img)
        self.assertIn('老张', line)
        self.assertIn('[图片]', line)
        # isSend 缺省按"不是我"处理：标签落 senderName，而不是抛 KeyError
        no_dir = cn.qq_format_message(qq_text(5, 1, content='一句话'))
        self.assertIs(no_dir['isSend'], False)
        # isSend=True 标「我」
        mine = cn.qq_format_message(qq_text(6, 1, content='我说的', isSend=True))
        self.assertIn('我：', cn.format_line(mine))

    def test_collect_imported_card_dict_matches_wechat_shape(self):
        with EnvRoot() as root:
            write_session(root, qq_session(messages=[
                qq_text(i, 1, text='第%d条：聊聊空气质量模型与数据融合的做法和坑' % i)
                for i in range(20)]))
            cards, thin = cn.collect_imported('out', days=7, limit=10)
            self.assertEqual(len(cards), 1)
            card = cards[0]
            # main() 后半段读的就是微信卡这几个键——少一个就是 KeyError 或半张卡
            self.assertEqual(sorted(card.keys()),
                             sorted(['talker', 'name', 'messages', 'chars',
                                     'lines', 'truncated', 'dropped']))
            self.assertTrue(card['name'].startswith('QQ-'), 'QQ- 前缀把两个 IM 的卡分开')
            self.assertTrue(card['talker'].startswith('qq:'))
            self.assertEqual(thin, [])

    def test_thin_sessions_are_skipped_not_carded(self):
        with EnvRoot() as root:
            write_session(root, qq_session(
                sess_id='42', name='只有一个短话的人',
                messages=[qq_text(1, 1, text='嗯')]))
            cards, thin = cn.collect_imported('out', days=7, limit=10)
            self.assertEqual(cards, [])
            self.assertEqual(len(thin), 1)
            self.assertTrue(thin[0]['name'].startswith('QQ-'))

    def test_out_of_window_sessions_produce_nothing(self):
        with EnvRoot() as root:
            write_session(root, qq_session(messages=[qq_text(1, 24 * 40)]))
            cards, thin = cn.collect_imported('out', days=7, limit=10)
            self.assertEqual(cards, [])
            self.assertEqual(thin, [])

    def test_cli_source_qq_dry_run_end_to_end(self):
        with EnvRoot() as root:
            write_session(root, qq_session(messages=[
                qq_text(i, 1, text='第%d条：聊聊空气质量模型与数据融合的做法和坑' % i)
                for i in range(30)]))
            env = {**os.environ,
                   'WEFLOW_IMPORT_ROOT': str(root),
                   'PYTHONIOENCODING': 'utf-8',
                   # 没有微信配置也要能跑：HOME 指到空目录，config 里就不会有 ntDbPath
                   'HOME': str(root), 'USERPROFILE': str(root)}
            r = subprocess.run(
                [sys.executable, str(SCRIPTS / 'chat_notes.py'),
                 '--source', 'qq', '--days', '7', '--dry-run'],
                capture_output=True, env=env, timeout=120)
            out = (r.stdout or b'').decode('utf-8', 'replace')
            self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8', 'replace')[:300])
            self.assertIn('会话 1 个', out)
            self.assertIn('30 条', out)


class SemanticImportTests(unittest.TestCase):
    def test_record_shape_matches_wechat_side(self):
        with EnvRoot() as root:
            write_session(root, qq_session(messages=[
                qq_text(1, 1, text='一句话凑够十个字以上的正文内容'),
                qq_text(2, 1, text='短'),        # <10 字：与微信侧同一阈值，不收
                qq_text(3, 24 * 40, text='这条在窗口外，也不该收，字数凑够十个字'),
            ]))
            items = ss.collect_imported_chat_items(days=7)
            self.assertEqual(len(items), 1)
            rec = items[0]
            # 与 collect_chat_messages 的记录逐字段同形（id/type/talker/sender/time/text）
            self.assertEqual(sorted(rec.keys()),
                             sorted(['id', 'type', 'talker', 'sender', 'time', 'text']))
            self.assertEqual(rec['type'], 'chat')
            self.assertEqual(rec['id'], 'chat:qq:123456:1')
            self.assertTrue(rec['talker'].startswith('QQ-'))
            self.assertEqual(rec['sender'], '老张')

    def test_build_without_wechat_config_works_with_imports(self):
        saved = (ss.INDEX_DIR, ss.VECTORS_FILE, ss.META_FILE,
                 ss.OUTPUT_ROOT, ss.load_config, ss.get_embeddings)
        try:
            root = Path(mkdtemp('qq-idx-'))
            ss.INDEX_DIR = root
            ss.VECTORS_FILE = root / 'vectors.npy'
            ss.META_FILE = root / 'meta.json'
            # `OUTPUT_ROOT` 也必须指到空目录：`build_index(full=True)` 会扫它下面的
            # 日报文章，不隔离就会读到真实 `output/biz-daily`（本机 10,981 篇），
            # 于是 `new` 断言 5 变成 10981（2026-10-26 的红）。
            ss.OUTPUT_ROOT = root

            def onehot(i):
                import numpy as np
                v = np.zeros(ss.EMBEDDING_DIM, dtype=np.float32)
                v[i % ss.EMBEDDING_DIM] = 1.0
                return v

            def fake_embed(texts, key, on_batch=None):
                embs = [onehot(i) for i in range(len(texts))]
                if on_batch:
                    on_batch(len(texts), len(texts), embs)
                return embs

            ss.get_embeddings = fake_embed
            ss.load_config = lambda: {}

            with EnvRoot() as iroot:
                write_session(iroot, qq_session(messages=[
                    qq_text(i, 1, text='第%d条正文，凑够十个字以上才会进索引' % i)
                    for i in range(5)]))
                # 开关关着：行为与从前一致——没有微信库就拒绝
                refused = ss.build_index('key', full=True, include_imports=False)
                self.assertIn('未初始化', str(refused))
                # 开着：收集走导入会话，假 embedding 下端到端写盘成功
                built = ss.build_index('key', full=True, include_imports=True)
                self.assertEqual(built.get('status'), 'success', built)
                self.assertEqual(built.get('new'), 5)
                meta = json.loads(ss.META_FILE.read_text(encoding='utf-8'))
                ids = [m['id'] for m in meta]
                self.assertEqual(len(ids), 5)
                self.assertTrue(all(i.startswith('chat:qq:123456:') for i in ids),
                                '索引里的 QQ 记录要能从 id 上认出来')
        finally:
            (ss.INDEX_DIR, ss.VECTORS_FILE, ss.META_FILE,
             ss.OUTPUT_ROOT, ss.load_config, ss.get_embeddings) = saved


if __name__ == '__main__':
    unittest.main()
