# -*- coding: utf-8 -*-
"""wechat_extras.py 的回归测试。

钉的是**那些静默坏掉、看不出来的地方**：

* 文本列存了非法 UTF-8 时，整条查询会抛 `Could not decode to UTF-8` —— 吞掉它
  就变成"0 行"，于是"读不到"和"没有"是同一个数字。本机实测
  `WeAppBizAttrSyncBufferTableV02.version` 就是这种列。
* `row_count` 失败必须返回 `None` 而不是 0。
* `SessionDeleteTable` 是"删过哪些会话"的唯一痕迹，不能被当成附属丢掉。
* `SearchRecent` 与 `websearch_record` 交集是 0，是两个来源；合成一张会丢一半。
* `solitaire` 的表名前缀必须**从长到短**匹配，否则 `SolitaireValid_x` 会被认成主表。

夹具一律是**普通 sqlite3**（不加密）——这些 reader 只吃一个连接，加密与否是
`connect()` 的事，测试不重复验它。
"""
import json
import os
import sqlite3
import sys
import pathlib
import unittest
import pathlib
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'scripts'))

import wechat_extras as X  # noqa: E402


def make_db(schema_sql, rows_sql=()):
    """内存里建一个库并填好。返回连接。"""
    conn = sqlite3.connect(':memory:')
    for sql in schema_sql:
        conn.execute(sql)
    for sql in rows_sql:
        conn.execute(sql)
    conn.commit()
    return conn


class TextDecodingTests(unittest.TestCase):
    """`T()` + `text()`：坏字节不许变成抛错，更不许变成空表。"""

    def test_valid_utf8_round_trips(self):
        conn = make_db(
            ['CREATE TABLE t (s TEXT)'],
            ["INSERT INTO t VALUES ('中文 abc')"])
        got = conn.execute('SELECT %s FROM t' % X.T('s')).fetchone()[0]
        self.assertEqual(X.text(got), '中文 abc')
        conn.close()

    def test_null_becomes_empty_string(self):
        conn = make_db(['CREATE TABLE t (s TEXT)'], ['INSERT INTO t VALUES (NULL)'])
        got = conn.execute('SELECT %s FROM t' % X.T('s')).fetchone()[0]
        self.assertEqual(X.text(got), '')
        conn.close()

    def test_invalid_utf8_does_not_raise_and_does_not_lose_the_row(self):
        """这条是那个真 bug 的回归：直接 SELECT 会抛，hex 路线给替换字符。"""
        conn = make_db(['CREATE TABLE t (s TEXT)'])
        # 0xFF 0xFE 不是合法 UTF-8
        conn.execute("INSERT INTO t VALUES (CAST(x'fffe41' AS TEXT))")
        conn.commit()

        # 对照：直接 SELECT 确实会抛（说明这个夹具真的能复现那个错）
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute('SELECT s FROM t').fetchall()

        rows = conn.execute('SELECT %s FROM t' % X.T('s')).fetchall()
        self.assertEqual(len(rows), 1, '走了 hex 之后这一行必须还在')
        decoded = X.text(rows[0][0])
        self.assertIn('A', decoded)
        self.assertTrue(decoded.startswith('\ufffd'), '坏字节应变成替换字符')
        conn.close()


class ReaderTests(unittest.TestCase):
    """`Reader`：查询失败要说出来，不能静默返回空列表。"""

    def test_failed_query_is_recorded_not_swallowed(self):
        conn = make_db(['CREATE TABLE t (a INTEGER)'])
        r = X.Reader(conn)
        rows = r.rows('要读的表', 'SELECT a FROM 不存在的表')
        self.assertEqual(rows, [])
        self.assertEqual(len(r.errors), 1)
        self.assertEqual(r.errors[0]['what'], '要读的表')
        self.assertIn('OperationalError', r.errors[0]['error'])
        conn.close()

    def test_attach_only_adds_the_key_when_there_were_errors(self):
        conn = make_db(['CREATE TABLE t (a INTEGER)'])
        ok = X.Reader(conn)
        self.assertEqual(ok.attach({'x': 1}), {'x': 1}, '没出错就不该多这个字段')
        bad = X.Reader(conn)
        bad.rows('l', 'SELECT * FROM 没有')
        self.assertIn('readErrors', bad.attach({'x': 1}))
        conn.close()

    def test_row_count_returns_none_on_failure_not_zero(self):
        conn = make_db(['CREATE TABLE t (a INTEGER)'])
        self.assertEqual(X.row_count(conn, 't'), 0)
        self.assertIsNone(X.row_count(conn, '不存在'), '"读不到"必须是 None，不是 0')
        conn.close()


class OverviewTests(unittest.TestCase):
    def test_empty_table_and_missing_table_are_distinguishable(self):
        def opener(path):
            return make_db(['CREATE TABLE empty_t (a INTEGER)'])

        result = X.read_overview({'lib': '/nonexistent/db.sqlite'}, opener)
        # 路径不存在 → 整个库标 exists=False，不去猜
        self.assertFalse(result['libraries'][0]['exists'])
        self.assertEqual(result['libraries'][0]['tables'], [])

    def test_zero_rows_and_unreadable_are_not_the_same_number(self):
        """0 行是"表在、空的"；读不出是 None。把它们并成 0，用户会以为库是空的。"""
        class _Cur:
            def __init__(self, names, fail=False):
                self._names = names
                self._fail = fail

            def __iter__(self):
                return iter(self._names)

            def fetchall(self):
                return self._names

            def fetchone(self):
                if self._fail:
                    raise sqlite3.OperationalError('boom')
                return (0,)

        class _Conn:
            """一个只有两张表的假库：empty_t 读得出来（0 行），bad_t 读不出。"""
            def execute(self, sql, params=()):
                if 'sqlite_master' in sql:
                    return _Cur([('empty_t',), ('bad_t',)])
                return _Cur([], fail='bad_t' in sql)

            def close(self):
                pass

        # `read_overview` 先看 `Path(path).exists()`，所以要用一个真存在的占位文件
        # （opener 仍返回假库，文件本身不会被读）
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            placeholder = os.path.join(tmp, 'placeholder.db')
            open(placeholder, 'wb').close()
            result = X.read_overview({'lib': placeholder}, lambda _p: _Conn())
        tables = {t['name']: t['rows'] for t in result['libraries'][0]['tables']}
        self.assertEqual(tables['empty_t'], 0, '空表就是 0')
        self.assertIsNone(tables['bad_t'], '读不出必须是 None')
        self.assertEqual(result['nonEmptyTableCount'], 0, 'None 不该被算成"有数据"')
        self.assertEqual(result['unreadableTableCount'], 1)

    def test_real_file_with_one_empty_table(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'x.db')
            conn = sqlite3.connect(db)
            conn.execute('CREATE TABLE empty_t (a INTEGER)')
            conn.execute('CREATE TABLE one_row (a INTEGER)')
            conn.execute('INSERT INTO one_row VALUES (1)')
            conn.commit()
            conn.close()

            result = X.read_overview({'lib': db}, lambda p: sqlite3.connect(p))
            tables = {t['name']: t['rows'] for t in result['libraries'][0]['tables']}
            self.assertEqual(tables, {'empty_t': 0, 'one_row': 1})
            self.assertEqual(result['tableCount'], 2)
            self.assertEqual(result['nonEmptyTableCount'], 1)
            self.assertEqual(result['unreadableTableCount'], 0)


class SearchHistoryTests(unittest.TestCase):
    def _conn(self):
        return make_db(
            ['CREATE TABLE websearch_record (keyword TEXT, pay_load_ TEXT, create_time INTEGER)',
             'CREATE TABLE SearchRecent (username TEXT, query TEXT, score INTEGER, last_click_time INTEGER)',
             'CREATE TABLE brand_search_record (keyword TEXT, pay_load_ TEXT, create_time INTEGER)'],
            ["INSERT INTO websearch_record VALUES ('词A', "
             "'{\"requestId\":\"r-1\",\"scene\":88,\"hotword\":\"词A\","
             "\"extReqParams\":[{\"key\":\"lastSearchId\",\"textValue\":\"999\"}]}', 300)",
             "INSERT INTO websearch_record VALUES ('词B', '{\"requestId\":\"r-2\"}', 100)",
             "INSERT INTO SearchRecent VALUES ('wxid_x', '人名', 5, 200)",
             "INSERT INTO brand_search_record VALUES ('品牌', '{\"requestId\":\"r-3\"}', 400)"])

    def test_three_sources_are_kept_separate(self):
        """实测三路交集为 0，所以合成一张表就是丢数据。"""
        conn = self._conn()
        data = X.search_history_rows(conn)
        self.assertEqual(data['counts']['websearch'], 2)
        self.assertEqual(data['counts']['searchRecent'], 1)
        self.assertEqual(data['counts']['brandSearch'], 1)
        self.assertEqual(len(data['rows']), 4)
        self.assertEqual({r['source'] for r in data['rows']},
                         {'websearch', 'search-recent', 'brand-search'})
        conn.close()

    def test_sorted_newest_first(self):
        conn = self._conn()
        times = [r['time'] for r in X.search_history_rows(conn)['rows']]
        self.assertEqual(times, sorted(times, reverse=True))
        conn.close()

    def test_payload_fields_reach_the_rows(self):
        """`pay_load_` 是要解析的：requestId/scene 必须出现在行上。

        （上一版的判据是"只报长度、不解析"——那在实测出 JSON 形状和哪些键真在变之后
        已经过时了，解析取舍由 `PayloadTests` 钉住。）
        """
        conn = self._conn()
        row = next(r for r in X.search_history_rows(conn)['rows']
                   if r['source'] == 'websearch' and r['keyword'] == '词A')
        self.assertEqual(row['requestId'], 'r-1')
        self.assertEqual(row['scene'], 88)
        self.assertEqual(row['extReqParams'], [{'key': 'lastSearchId', 'value': '999'}])
        self.assertNotIn('hotWord', row)
        conn.close()

    def test_unparsable_payload_is_counted_not_hidden(self):
        conn = make_db(
            ['CREATE TABLE websearch_record (keyword TEXT, pay_load_ TEXT, create_time INTEGER)',
             'CREATE TABLE SearchRecent (username TEXT, query TEXT, score INTEGER, last_click_time INTEGER)',
             'CREATE TABLE brand_search_record (keyword TEXT, pay_load_ TEXT, create_time INTEGER)'],
            ["INSERT INTO websearch_record VALUES ('词', 'not json', 1)"])
        data = X.search_history_rows(conn)
        self.assertEqual(data['counts']['payloadUnparsed'], 1)
        self.assertTrue(data['rows'][0].get('jsonError'))
        conn.close()


class SessionTests(unittest.TestCase):
    def _conn(self, deleted_in_session=False):
        conn = make_db([
            'CREATE TABLE Name2Id (user_name TEXT)',
            'CREATE TABLE SessionTable (username TEXT, type INTEGER, unread_count INTEGER, '
            'is_hidden INTEGER, summary TEXT, draft TEXT, status INTEGER, '
            'last_timestamp INTEGER, last_msg_sender TEXT, last_sender_display_name TEXT)',
            'CREATE TABLE SessionDeleteTable (username TEXT, delete_time INTEGER)',
            'CREATE TABLE SessionUnreadListTable_1 (username_id INTEGER, server_id INTEGER, '
            'create_time INTEGER, local_id INTEGER)',
        ], [
            # rowid 1 = 会话A，rowid 2 = 会话B
            "INSERT INTO Name2Id VALUES ('会话A')",
            "INSERT INTO Name2Id VALUES ('会话B')",
            "INSERT INTO SessionTable VALUES ('会话A', 0, 3, 0, '摘要A', '草稿', 0, 500, '会话A', 'A')",
            "INSERT INTO SessionTable VALUES ('会话B', 0, 0, 1, '摘要B', '', 0, 400, '会话B', 'B')",
            "INSERT INTO SessionUnreadListTable_1 VALUES (1, 11, 450, 1)",
            "INSERT INTO SessionUnreadListTable_1 VALUES (2, 12, 460, 2)",
            "INSERT INTO SessionUnreadListTable_1 VALUES (99, 13, 470, 3)",
            "INSERT INTO SessionDeleteTable VALUES ('删掉的会话', 300)",
        ])
        return conn

    def test_sorted_by_last_timestamp(self):
        conn = self._conn()
        data = X.session_rows(conn)
        self.assertEqual([s['username'] for s in data['sessions']], ['会话A', '会话B'])
        self.assertEqual(data['counts'], {'sessions': 2, 'deleted': 1})
        conn.close()

    def test_draft_becomes_hasDraft_boolean_not_the_text(self):
        """草稿内容不外泄成字段，只报有没有——摘要已经在 summary 里了。"""
        conn = self._conn()
        a = next(s for s in X.session_rows(conn)['sessions'] if s['username'] == '会话A')
        self.assertTrue(a['hasDraft'])
        self.assertNotIn('draft', a)
        conn.close()

    def test_deleted_is_a_separate_list_not_dropped(self):
        conn = self._conn()
        data = X.session_rows(conn)
        self.assertEqual(data['deleted'][0]['username'], '删掉的会话')
        self.assertNotIn('删掉的会话', [s['username'] for s in data['sessions']])
        conn.close()

    def test_type_is_declared_unverified_because_it_is_constant(self):
        conn = self._conn()
        data = X.session_rows(conn)
        self.assertIn('SessionTable.type', data['unverifiedEnums'])
        conn.close()

    def test_unread_joins_via_name2id_rowid(self):
        conn = self._conn()
        data = X.session_rows(conn, include_unread=True)
        self.assertEqual(data['unreadJoin']['resolved'], 2)
        self.assertEqual(data['unreadJoin']['unresolved'], 1, 'rowid 99 解不到')
        unresolved = [u for u in data['unread'] if u['unresolved']]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]['usernameId'], 99)
        self.assertEqual(unresolved[0]['username'], '')
        conn.close()

    def test_unread_off_by_default(self):
        """2.6 万行的表不该在默认调用里被读出来。"""
        conn = self._conn()
        self.assertNotIn('unread', X.session_rows(conn))
        conn.close()


class SolitaireTests(unittest.TestCase):
    def _conn(self):
        return make_db([
            'CREATE TABLE "Solitaire_aaa" (header TEXT, footer TEXT, content TEXT, '
            'solitaire_string TEXT, active_time INTEGER, sid TEXT)',
            'CREATE TABLE "SolitaireValid_aaa" (title TEXT, valid INTEGER, '
            'local_id INTEGER, sort_seq INTEGER, create_time INTEGER)',
            'CREATE TABLE "SolitaireFold_aaa" (front_content TEXT, behind_content TEXT, '
            'local_id INTEGER, sort_seq INTEGER, create_time INTEGER)',
            'CREATE TABLE "Solitaire_bbb" (header TEXT, footer TEXT, content TEXT, '
            'solitaire_string TEXT, active_time INTEGER, sid TEXT)',
        ], [
            "INSERT INTO Solitaire_aaa VALUES ('头', '尾', '正文', '接龙串', 100, 's1')",
            "INSERT INTO SolitaireValid_aaa VALUES ('标题', 1, 1, 0, 90)",
            "INSERT INTO SolitaireFold_aaa VALUES ('前', '后', 1, 0, 80)",
            "INSERT INTO Solitaire_bbb VALUES ('头2', '', '', '', 200, 's2')",
        ])

    def test_prefix_match_is_longest_first(self):
        """`SolitaireValid_x` 也以 `Solitaire` 开头——顺序错了会把 valid 认成主表。"""
        groups = X.solitaire_groups(self._conn())
        self.assertEqual(sorted(groups), ['aaa', 'bbb'])
        self.assertEqual(sorted(groups['aaa']),
                         ['SolitaireFold_', 'SolitaireValid_', 'Solitaire_'])
        self.assertNotIn('Solitaire_', groups['aaa'].get('SolitaireValid_', ''))

    def test_resolved_and_unresolved_are_separated(self):
        conn = self._conn()
        data = X.solitaire_rows(conn, name_map={'aaa': '某群@chatroom'})
        by_md5 = {g['sessionMd5']: g for g in data['groups']}
        self.assertEqual(by_md5['aaa']['session'], '某群@chatroom')
        self.assertTrue(by_md5['aaa']['resolved'])
        self.assertEqual(by_md5['bbb']['session'], '', '解不到就留空，不猜')
        self.assertFalse(by_md5['bbb']['resolved'])
        self.assertEqual(data['counts']['resolved'], 1)
        conn.close()


class EmoticonTests(unittest.TestCase):
    def test_metadata_counts_and_the_decryption_caveat(self):
        conn = make_db([
            'CREATE TABLE kNonStoreEmoticonTable (type INTEGER, md5 TEXT, caption TEXT, '
            'product_id TEXT, aes_key TEXT, thumb_url TEXT, tp_url TEXT, auth_key TEXT, '
            'cdn_url TEXT, extern_url TEXT, extern_md5 TEXT, encrypt_url TEXT, '
            'designer_id TEXT, activity_id TEXT)',
            'CREATE TABLE kStoreEmoticonPackageTable (package_id_ TEXT, package_name_ TEXT, '
            'payment_status_ INTEGER, download_status_ INTEGER, install_time_ INTEGER, '
            'remove_time_ INTEGER, sort_order_ INTEGER, introduction_ TEXT, '
            'full_description_ TEXT, copyright_ TEXT, author_ TEXT, store_icon_url_ TEXT, '
            'panel_url_ TEXT)',
            'CREATE TABLE kStoreEmoticonFilesTable (package_id_ TEXT, md5_ TEXT, '
            'type_ INTEGER, sort_order_ INTEGER, emoticon_size_ INTEGER, '
            'emoticon_offset_ INTEGER, thumb_size_ INTEGER, thumb_offset_ INTEGER)',
            'CREATE TABLE kStoreEmoticonCaptionsTable (package_id_ TEXT, md5_ TEXT, '
            'language_ TEXT, caption_ TEXT)',
        ], [
            "INSERT INTO kNonStoreEmoticonTable VALUES (3, 'm1', '笑', '', "
            "'0123456789abcdef0123456789abcdef', '', '', '', 'http://cdn/1', '', '', '', '', '')",
            "INSERT INTO kStoreEmoticonPackageTable VALUES ('p1', '包名', 0, 1, 100, 0, 1, "
            "'介绍', '', '', '', '', '')",
            "INSERT INTO kStoreEmoticonFilesTable VALUES ('p1', 'f1', 0, 0, 100, 0, 10, 0)",
            "INSERT INTO kStoreEmoticonCaptionsTable VALUES ('p1', 'f1', 'zh', '文案')",
        ])
        data = X.emoticon_rows(conn)
        self.assertEqual(data['counts'],
                         {'custom': 1, 'packages': 1, 'packageFiles': 1, 'packageCaptions': 1})
        self.assertIn('notDecryptable', data, '解不开这件事必须写在结果里，不是只在文档里')
        self.assertIn('对照', data['notDecryptable']['why'], '要说明否定带正对照')
        self.assertTrue(data['custom'][0]['hasAesKey'])
        conn.close()


class FMessageTests(unittest.TestCase):
    def test_direction_counts(self):
        conn = make_db([
            'CREATE TABLE FMessageTable (user_name_ TEXT, type_ INTEGER, '
            'timestamp_ INTEGER, encrypt_user_name_ TEXT, content_ TEXT, '
            'is_sender_ INTEGER, ticket_ TEXT, scene_ INTEGER, '
            'fmessage_detail_buf_ TEXT, remark_ TEXT, label_ids_ TEXT)'],
            ["INSERT INTO FMessageTable VALUES ('a', 37, 100, '', '你好', 0, '', 0, '', '', '')",
             "INSERT INTO FMessageTable VALUES ('b', 37, 200, '', '在吗', 0, '', 0, '', '', '')",
             "INSERT INTO FMessageTable VALUES ('c', 37, 300, '', '我加你', 1, '', 0, '', '', '')"])
        data = X.fmessage_rows(conn)
        self.assertEqual(data['counts'], {'total': 3, 'incoming': 2, 'outgoing': 1})
        self.assertEqual([r['time'] for r in data['rows']], [300, 200, 100])
        conn.close()


class ProtobufTests(unittest.TestCase):
    """`contact_pack_data` 的最小解码器。

    编码器写在测试里，是为了让夹具和被测代码**不共享实现**：如果两边用同一份
    wire-format 逻辑，写错会一起错、测试照样绿。
    """

    @staticmethod
    def _varint(n):
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            out.append(b | (0x80 if n else 0))
            if not n:
                return bytes(out)

    @classmethod
    def _field(cls, fno, payload):
        """wire type 2（长度前缀）的一个字段。"""
        return cls._varint((fno << 3) | 2) + cls._varint(len(payload)) + payload

    def test_parses_length_delimited_and_varint(self):
        blob = (self._field(2, '腾讯微保'.encode())
                + self._field(3, b'tengxunweibao')
                + self._varint((7 << 3) | 0) + self._varint(0))
        fields = X.parse_protobuf(blob)
        self.assertEqual([(f, w) for f, w, _ in fields], [(2, 2), (3, 2), (7, 0)])
        self.assertEqual(fields[2][2], 0, 'wire 0 的值是整数')

    def test_decode_contact_pack_maps_known_fields(self):
        blob = (self._field(2, '喜茶GO'.encode())
                + self._field(3, b'xichaGO')
                + self._field(4, b'http://wx.qlogo.cn/mmhead/AAA')
                + self._field(5, b'http://wx.qlogo.cn/mmhead/AAA')
                + self._field(6, ('这是一段简介' * 3).encode()))
        got = X.decode_contact_pack(blob)
        self.assertEqual(got['name'], '喜茶GO')
        self.assertEqual(got['englishId'], 'xichaGO')
        self.assertEqual(got['logoUrl'], 'http://wx.qlogo.cn/mmhead/AAA')
        self.assertIn('这是一段简介', got['description'])

    def test_unknown_fields_are_reported_not_dropped(self):
        """没有实测证据的字段号要进 unknownFields，不能默默消失。"""
        blob = self._field(2, '名字'.encode()) + self._field(99, b'?\x01\x02')
        got = X.decode_contact_pack(blob)
        self.assertIn(99, got['unknownFields'])

    def test_garbage_returns_none_instead_of_empty_dict(self):
        """解不出必须返回 None —— 回 `{}` 会让调用方以为"这个号没有资料"。"""
        self.assertIsNone(X.decode_contact_pack(b'\xff\xff\xff\xff\xff'))
        self.assertIsNone(X.decode_contact_pack(b''))
        self.assertIsNone(X.decode_contact_pack(None))

    def test_truncated_length_raises_rather_than_silently_short(self):
        blob = self._varint((2 << 3) | 2) + self._varint(10) + b'ab'
        with self.assertRaises(ValueError):
            X.parse_protobuf(blob)


class PayloadTests(unittest.TestCase):
    """`pay_load_` 的取舍：只留**真的在变**的键。"""

    def test_keeps_the_fields_that_vary(self):
        payload = json.dumps({
            'requestId': 'r-1', 'scene': 88, 'parentType': 7,
            'hotword': '某词', 'optype': 1, 'businesstype': 0, 'timestamp': 111,
            'extReqParams': [{'key': 'lastSearchId', 'textValue': '999'}],
        })
        got = X.summarize_payload(payload)
        self.assertEqual(got['requestId'], 'r-1')
        self.assertEqual(got['scene'], 88)
        self.assertEqual(got['parentType'], 7)
        self.assertEqual(got['extReqParams'], [{'key': 'lastSearchId', 'value': '999'}])
        # 这四个实测无新信息（84/84 相同 / 常量 / 同刻），不该往外抛
        for dropped in ('hotWord', 'optype', 'businessType', 'payloadTimestamp'):
            self.assertNotIn(dropped, got, '%s 实测无新信息' % dropped)

    def test_empty_string_searchId_is_omitted(self):
        got = X.summarize_payload(json.dumps({'searchId': '', 'scene': 1}))
        self.assertNotIn('searchId', got)
        self.assertEqual(X.summarize_payload(json.dumps({'searchId': 'abc'}))['searchId'],
                         'abc')

    def test_non_json_marks_itself_instead_of_returning_empty(self):
        self.assertEqual(X.summarize_payload('not json at all'), {'jsonError': True})
        self.assertEqual(X.summarize_payload(json.dumps([1, 2])), {'jsonError': True})

    def test_accepts_hex_from_the_reader(self):
        raw = '{"scene": 5, "requestId": "x"}'
        self.assertEqual(X.summarize_payload(raw.encode().hex())['scene'], 5)
        self.assertEqual(X.summarize_payload(raw)['scene'], 5)


class BizSubscribeTests(unittest.TestCase):
    def test_mini_programs_with_decoded_pack(self):
        """`wacontact` 是小程序资料卡（id 形如 `gh_`，但 `gh_` 不专属于公众号）。"""
        enc = ProtobufTests
        name = '喜茶GO'
        pack = (enc._varint((2 << 3) | 2) + enc._varint(len(name.encode()))
                + name.encode())
        conn = make_db([
            'CREATE TABLE wacontact (user_name TEXT, type INTEGER, '
            'brand_icon_url TEXT, external_info TEXT, contact_pack_data BLOB, '
            'wx_app_opt INTEGER, head_image_status TEXT, app_id TEXT)',
            'CREATE TABLE biz_subscribe_status (biz_username TEXT, '
            'template_id TEXT, is_subscribe INTEGER)',
            'CREATE TABLE WeAppBizAttrSyncBufferTableV02 (user_name TEXT, '
            'last_update_time INTEGER, version TEXT)',
        ], [
            "INSERT INTO biz_subscribe_status VALUES ('gh_a', 't1', 1)",
            "INSERT INTO biz_subscribe_status VALUES ('gh_b', 't2', 1)",
        ])
        conn.execute("INSERT INTO wacontact VALUES ('gh_a', 0, '', '', ?, 0, '', '')",
                     (pack,))
        conn.execute("INSERT INTO wacontact VALUES ('gh_c', 0, '', '', NULL, 0, '', '')")
        conn.commit()

        data = X.biz_subscribe_rows(conn)
        self.assertIn('miniPrograms', data)
        by_name = {w['username']: w for w in data['miniPrograms']}
        self.assertEqual(by_name['gh_a']['name'], '喜茶GO')
        self.assertTrue(by_name['gh_a']['packDecoded'])
        self.assertFalse(by_name['gh_c']['packDecoded'], 'NULL BLOB 要标成没解出来')
        self.assertEqual(data['counts']['packUndecoded'], 1)

        # 订阅 ≠ 有资料卡：2 条订阅里只有 1 条能找到卡
        self.assertEqual(data['joins']['subscribedWithMiniProgramCard'], 1)
        self.assertEqual(data['joins']['subscribedWithoutCard'], 1)
        self.assertEqual(data['joins']['miniProgrammingNotSubscribed'], 1)
        conn.close()


class PathTests(unittest.TestCase):
    def test_db_paths_are_derived_from_the_message_database(self):
        # 平台中立：POSIX 上 Path('C:\\...') 会把整串当一个文件名
        msg = str(pathlib.Path(tempfile.gettempdir()) / 'acct' / 'wxid_x'
                  / 'db_storage' / 'message' / 'message_0.db')
        paths = X.db_paths(msg)
        self.assertTrue(str(paths['general']).endswith(
            os.path.join('general', 'general.db')))
        self.assertIn('db_storage', str(paths['general']))
        self.assertTrue(str(paths['weclaw']).endswith('weclaw.db'))
        self.assertEqual(paths['weclaw'].parent.name, 'message',
                         'weclaw.db 在 message/ 下，不在自己的目录里')
        contact = X.contact_db_path(msg)
        self.assertTrue(str(contact).endswith(os.path.join('contact', 'contact.db')))

    def test_all_kinds_are_covered_by_readers_or_overview(self):
        """KINDS 里除 overview 之外每个都要有 reader —— 少一个就是选了什么都不发生。"""
        for kind in X.KINDS:
            if kind == 'overview':
                continue
            self.assertIn(kind, X.READERS, 'KINDS 里有 %s，但没有 reader' % kind)

    def test_reader_db_keys_are_known_libraries(self):
        known = set(X.db_paths(r'C:\a\b\db_storage\message\message_0.db'))
        for kind, (db_key, _reader) in X.READERS.items():
            self.assertIn(db_key, known, '%s 指向了不存在的库键 %s' % (kind, db_key))


if __name__ == '__main__':
    unittest.main()
