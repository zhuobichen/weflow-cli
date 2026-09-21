"""Shard discovery, failure reporting, and the guarantee that reporting is additive.

Synthetic databases: `sqlcipher3` is stubbed with the stdlib `sqlite3`, so a
plain SQLite file with the NT table shape stands in for a real shard. The same
trick the exporter tests use (see export_chat_media_test.py:12-19), and it works
because connect_nt_db only issues `PRAGMA key`, which stock SQLite ignores.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'

# 先把 nt_common 导进来再进 patch 块。**顺序在这里是有意义的**：
# `patch.dict(sys.modules, ...)` 退出时会把它块内**新增**的条目删掉，而下面两个
# 模块都是在这个块里加载的——于是各自会新建一个 nt_common，同一个文件变成三个模块
# 对象，`is` 断言必然失败。先导入它就进了 patch 的快照，恢复时不会被删。
sys.path.insert(0, str(SCRIPTS))
import nt_common  # noqa: E402,F401  （见上：必须在 patch 之前）

spec = importlib.util.spec_from_file_location('nt_decrypt', SCRIPTS / 'nt_decrypt.py')
nt = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(nt)

# sqlcipher3 is genuinely installed on some machines, and require_sqlcipher()
# caches its module global on first use. Patching sys.modules only covers
# import time, so pin the cached global too - otherwise the real SQLCipher
# opens these plain SQLite fixtures and reports "file is not a database".
nt.sqlcipher = sqlite3

export_spec = importlib.util.spec_from_file_location(
    'export_chat_html_shards', SCRIPTS / 'export_chat_html.py')
export = importlib.util.module_from_spec(export_spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    export_spec.loader.exec_module(export)

TALKER = 'wxid_synthetic_contact'
MSG_COLUMNS = (
    'local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER,'
    ' real_sender_id INTEGER, create_time INTEGER, status INTEGER,'
    ' upload_status INTEGER, download_status INTEGER, server_seq INTEGER,'
    ' origin_source INTEGER, source TEXT, message_content TEXT, compress_content BLOB'
)


# A message table that shares no column name with the real one. Stands in for
# a schema the reader cannot name anything in, which is a different outcome
# from a readable shard that simply holds nothing for this conversation.
ALIEN_COLUMNS = 'foo TEXT, bar TEXT'


def make_shard(path, talker=TALKER, rows=(), with_name_table=True, columns=None):
    """One NT-shaped shard file.

    Closing in a finally matters: a leaked connection keeps the file locked on
    Windows, and the TemporaryDirectory teardown then fails with a
    PermissionError that looks nothing like the actual mistake.
    """
    table = 'Msg_' + hashlib.md5(talker.encode()).hexdigest()
    columns = columns or MSG_COLUMNS
    if columns != MSG_COLUMNS:
        # A custom column list changes the row width, so the caller has to
        # supply rows that match it - or none at all.
        assert not rows, 'custom columns change the row width; do not pass rows'
    conn = sqlite3.connect(path)
    try:
        conn.execute('CREATE TABLE "%s" (%s)' % (table, columns))
        if rows:
            width = columns.count(',') + 1
            conn.executemany(
                'INSERT INTO "%s" VALUES (%s)' % (table, ','.join('?' * width)), rows)
        if with_name_table:
            conn.execute('CREATE TABLE Name2Id (user_name TEXT)')
            conn.execute("INSERT INTO Name2Id (user_name) VALUES ('wxid_sender')")
        conn.commit()
    finally:
        conn.close()


def row(local_id, create_time, content='synthetic', local_type=1, server_id=0):
    return (local_id, server_id, local_type, 0, 1, create_time, 0, 0, 0, 0, 0,
            '', content, b'')


class ShardDiscoveryTests(unittest.TestCase):
    def test_discovery_skips_derived_tables_and_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('message_0.db', 'message_1.db', 'message_fts.db',
                         'message_resource.db', 'session.db'):
                make_shard(os.path.join(tmp, name))
            found = [os.path.basename(p) for p in nt.discover_message_shards(
                os.path.join(tmp, 'message_0.db'))]
            self.assertEqual(found, ['message_0.db', 'message_1.db'])

    def test_there_is_only_one_discovery_implementation(self):
        """原先 `nt_decrypt` 与 `export_chat_html` **各有一份，而契约不同**。

        `nt_decrypt` 版 glob 不到分片时返回 `[配置的那个库]`；导出器版返回空表，
        由调用点自己补。补偿写在调用点，意味着新调用方忘了它就会静默拿到零个分片。
        现在两份都是 `nt_common` 的那一个对象——所以这里断言的是**同一性**，
        而不是"跑出来一样"：相等断言能靠"各抄一份、恰好抄对"通过，而抄一份正是它
        当初漂掉的方式。
        """
        import nt_common
        self.assertIs(nt.discover_message_shards, nt_common.discover_message_shards)
        self.assertIs(export.discover_message_shards, nt_common.discover_message_shards)
        self.assertIs(nt.derive_database_key, nt_common.derive_database_key)
        self.assertIs(export.derive_database_key, nt_common.derive_database_key)
        self.assertIs(nt.table_columns, nt_common.table_columns)
        self.assertIs(export.table_columns, nt_common.table_columns)

    def test_discovery_never_returns_an_empty_list(self):
        """契约：glob 不到就退回配置的那个库，**调用方不必自己兜底**。

        这一条正是两份旧实现的分歧点，也是导出器调用点上那段
        `if not shards: shards = [db_path]` 的存在理由（现在已删）。
        """
        import nt_common
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'message_0.db')
            make_shard(target)
            self.assertEqual(nt_common.discover_message_shards(target), [target])
            # 父目录不存在时也要给出一个可用的答案，而不是空表。
            ghost = os.path.join(tmp, 'nope', 'message_0.db')
            self.assertEqual(nt_common.discover_message_shards(ghost), [ghost])

    def test_discovery_excludes_the_derived_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('message_0.db', 'message_1.db',
                         'message_fts.db', 'message_resource.db'):
                make_shard(os.path.join(tmp, name))
            found = [os.path.basename(p) for p in nt.discover_message_shards(
                os.path.join(tmp, 'message_0.db'))]
            self.assertEqual(found, ['message_0.db', 'message_1.db'])


class ShardConnectionTests(unittest.TestCase):
    def test_an_unreadable_shard_is_reported_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'), rows=[row(1, 1_700_000_000)])
            Path(os.path.join(tmp, 'message_1.db')).write_bytes(b'not a database')

            pairs, failures = nt.connect_message_shards_detailed(
                os.path.join(tmp, 'message_0.db'), 'a' * 64, 'b' * 32)
            self.assertEqual(len(pairs), 1)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]['name'], 'message_1.db')
            self.assertIn(failures[0]['reason'], ('KEY_REJECTED', 'OPEN_FAILED'))
            for _path, conn in pairs:
                conn.close()

    def test_the_plain_helper_still_returns_exactly_what_it_used_to(self):
        """The refactor's core guarantee: a delegate cannot change the result."""
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'), rows=[row(1, 1)])
            make_shard(os.path.join(tmp, 'message_1.db'), rows=[row(2, 2)])
            Path(os.path.join(tmp, 'message_2.db')).write_bytes(b'broken')

            target = os.path.join(tmp, 'message_0.db')
            pairs, failures = nt.connect_message_shards_detailed(target, 'a' * 64, 'b' * 32)
            conns = nt.connect_message_shards(target, 'a' * 64, 'b' * 32)
            self.assertEqual(len(conns), len(pairs))
            self.assertEqual(len(pairs) + len(failures), 3)
            for conn in conns:
                conn.close()
            for _path, conn in pairs:
                conn.close()


class MessageShardReportTests(unittest.TestCase):
    def _open(self, tmp):
        return nt.connect_message_shards(
            os.path.join(tmp, 'message_0.db'), 'a' * 64, 'b' * 32)

    def test_reporting_does_not_change_the_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'),
                       rows=[row(1, 1_700_000_000, 'first'), row(2, 1_700_000_100, 'second')])
            make_shard(os.path.join(tmp, 'message_1.db'), rows=[row(3, 1_700_000_200, 'third')])

            conns = self._open(tmp)
            plain = nt.get_messages(conns, TALKER, 10)
            report = []
            reported = nt.get_messages(conns, TALKER, 10, shard_names=['message_0.db', 'message_1.db'],
                                       shard_report=report)
            self.assertEqual(plain['messages'], reported['messages'])
            self.assertEqual(len(report), 2)
            for conn in conns:
                conn.close()

    def test_report_counts_rows_per_shard_and_flags_absent_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'),
                       rows=[row(1, 1_700_000_000), row(2, 1_700_000_100)])
            # A shard that simply has no table for this conversation.
            make_shard(os.path.join(tmp, 'message_1.db'), talker='wxid_someone_else')

            conns = self._open(tmp)
            report = []
            nt.get_messages(conns, TALKER, 10,
                            shard_names=['message_0.db', 'message_1.db'],
                            shard_report=report)
            for conn in conns:
                conn.close()

            by_name = {item['name']: item for item in report}
            self.assertEqual(by_name['message_0.db']['rowsForTalker'], 2)
            self.assertTrue(by_name['message_0.db']['hasTalkerTable'])
            self.assertIsNone(by_name['message_0.db']['reason'])
            # Absent table is not the same as unreadable, and must not be.
            self.assertFalse(by_name['message_1.db']['hasTalkerTable'])
            self.assertIsNone(by_name['message_1.db']['rowsForTalker'])
            self.assertIsNone(by_name['message_1.db']['reason'])

    def test_an_unnameable_shard_is_flagged_while_the_others_still_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'), rows=[row(1, 1_700_000_000)])
            # The table exists but shares no column name with the real one, so
            # there is nothing to read rows as. That is a schema mismatch, not
            # an empty conversation, and must not be reported as one.
            make_shard(os.path.join(tmp, 'message_1.db'), columns=ALIEN_COLUMNS)

            conns = self._open(tmp)
            report = []
            result = nt.get_messages(conns, TALKER, 10,
                                     shard_names=['message_0.db', 'message_1.db'],
                                     shard_report=report)
            for conn in conns:
                conn.close()

            by_name = {item['name']: item for item in report}
            self.assertEqual(by_name['message_1.db']['reason'], 'SCHEMA_MISMATCH')
            self.assertIsNone(by_name['message_0.db']['reason'])
            # The readable shard's message must still come back.
            self.assertEqual(len(result['messages']), 1)


class ExporterColumnVariationTests(unittest.TestCase):
    """The HTML exporter reads rows by position, so a gap must not shift them.

    `export_chat_html.fetch_messages` returns tuples that the formatter indexes
    (`row[4]` for the sender, `row[5]` for the time). A column dropped from the
    SELECT would move every later field up one and export wrong data with no
    error at all - the one failure a caller cannot notice.
    """

    def _connect(self, tmp):
        return export.connect(os.path.join(tmp, 'message_0.db'), 'a' * 64, 'b' * 32)

    def _insert(self, path, statement):
        conn = sqlite3.connect(path)
        try:
            table = 'Msg_' + hashlib.md5(TALKER.encode()).hexdigest()
            conn.execute(statement % table)
            conn.commit()
        finally:
            conn.close()

    def test_a_column_gap_does_not_shift_the_fields_after_it(self):
        # `sort_seq` is index 3 and nothing reads it; `real_sender_id` is 4.
        columns = MSG_COLUMNS.replace('sort_seq INTEGER, ', '')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'message_0.db')
            make_shard(path, columns=columns)
            self._insert(path,
                         'INSERT INTO "%s" (local_id, server_id, local_type, real_sender_id,'
                         " create_time, message_content) VALUES (7, 99, 1, 42, 1700000000, 'text')")

            conn, _c = self._connect(tmp)
            try:
                rows = export.fetch_messages(conn, TALKER)
            finally:
                conn.close()

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row[0], 7)           # local_id
            # sort_seq is index 3 and is the column that is missing: substituting
            # NULL keeps every later field at the index the formatter expects.
            self.assertIsNone(row[3])
            self.assertEqual(row[4], 42)          # real_sender_id, not create_time
            self.assertEqual(row[5], 1_700_000_000)
            self.assertEqual(row[8], 'text')
            self.assertIsNone(row[9])             # compress_content, absent

    def test_a_date_export_is_refused_when_the_day_cannot_be_expressed(self):
        # `date` narrows the export. Without create_time it cannot be honoured,
        # and exporting everything would widen a bounded export silently.
        columns = MSG_COLUMNS.replace('create_time INTEGER, ', '')
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'), columns=columns)
            conn, _c = self._connect(tmp)
            try:
                with self.assertRaises(ValueError):
                    export.fetch_messages(conn, TALKER, date='2026-09-20')
            finally:
                conn.close()


class MessageColumnVariationTests(unittest.TestCase):
    """A column nobody reads must not be able to break a read.

    The SELECT used to name all fourteen columns of the message table while
    `_message_dict` consumed six of them, so a version that renamed one of the
    other eight made every shard unreadable and the conversation came back
    empty. The SELECT is now built from the columns the reader uses.
    """

    def _open(self, tmp):
        return nt.connect_message_shards(
            os.path.join(tmp, 'message_0.db'), 'a' * 64, 'b' * 32)

    def test_a_missing_used_column_degrades_instead_of_failing(self):
        # `server_id` is one of the six the reader consumes, and losing it is
        # survivable: the message still has an id, a time, and its text. What
        # must not happen is the shard coming back as unreadable, or the loss
        # going unrecorded.
        degraded = MSG_COLUMNS.replace('server_id INTEGER, ', '')
        degraded = degraded.replace(', compress_content BLOB', '')
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'), columns=degraded)
            path = os.path.join(tmp, 'message_0.db')
            conn = sqlite3.connect(path)
            try:
                table = 'Msg_' + hashlib.md5(TALKER.encode()).hexdigest()
                conn.execute('INSERT INTO "%s" (local_id, create_time, message_content)'
                             ' VALUES (1, 1700000000, %s)' % (table, "'kept'"))
                conn.commit()
            finally:
                conn.close()

            conns = self._open(tmp)
            report = []
            result = nt.get_messages(conns, TALKER, 10,
                                     shard_names=['message_0.db'], shard_report=report)
            for conn in conns:
                conn.close()

            self.assertIsNone(report[0]['reason'])
            # Only the columns the reader wanted: `compress_content` was never
            # selected by it, so its absence is not a loss worth reporting.
            self.assertEqual(report[0]['missingColumns'], ['server_id'])
            self.assertEqual([m['content'] for m in result['messages']], ['kept'])
            self.assertEqual(result['messages'][0]['serverId'], '')

    def test_a_missing_sender_table_loses_names_not_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'),
                       rows=[row(1, 1_700_000_000, 'survives')],
                       with_name_table=False)

            conns = self._open(tmp)
            result = nt.get_messages(conns, TALKER, 10)
            for conn in conns:
                conn.close()

            self.assertEqual(len(result['messages']), 1)
            self.assertEqual(result['messages'][0]['content'], 'survives')
            self.assertEqual(result['messages'][0]['senderUsername'], '')


class MessageWindowTests(unittest.TestCase):
    """`from_time`/`to_time` are pushed into SQL, so they must be exact.

    Getting this wrong is quiet: a window that is off by a message looks like
    a conversation that simply had less in it.
    """

    def _build(self, tmp):
        make_shard(os.path.join(tmp, 'message_0.db'), rows=[
            row(1, 1_700_000_000, 'too old'),
            row(2, 1_700_000_100, 'first in window'),
            row(3, 1_700_000_200, 'second in window'),
        ])
        make_shard(os.path.join(tmp, 'message_1.db'), rows=[
            row(4, 1_700_000_300, 'third in window'),
            row(5, 1_700_000_400, 'too new'),
        ])

    def _read(self, tmp, **kwargs):
        conns = nt.connect_message_shards(
            os.path.join(tmp, 'message_0.db'), 'a' * 64, 'b' * 32)
        try:
            return nt.get_messages(conns, TALKER, 10, **kwargs)
        finally:
            for conn in conns:
                conn.close()

    def test_both_bounds_are_inclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._build(tmp)
            result = self._read(tmp, from_time=1_700_000_100, to_time=1_700_000_300)
            self.assertEqual([m['content'] for m in result['messages']],
                             ['third in window', 'second in window', 'first in window'])

    def test_the_window_spans_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._build(tmp)
            # Shard 0 has one message in range, shard 1 has one: a window that
            # only reached one shard would silently halve the answer.
            result = self._read(tmp, from_time=1_700_000_200, to_time=1_700_000_300)
            self.assertEqual([m['content'] for m in result['messages']],
                             ['third in window', 'second in window'])

    def test_an_empty_window_returns_no_messages_rather_than_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._build(tmp)
            result = self._read(tmp, from_time=1_800_000_000)
            self.assertEqual(result['messages'], [])

    def test_a_window_is_reported_not_approximated_when_it_cannot_be_applied(self):
        # No create_time, so there is no way to honour the window. Returning
        # these rows anyway would have a caller tracking coverage record a
        # range they never actually read.
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'),
                       columns='local_id INTEGER, message_content TEXT')
            conn = sqlite3.connect(os.path.join(tmp, 'message_0.db'))
            try:
                table = 'Msg_' + hashlib.md5(TALKER.encode()).hexdigest()
                conn.execute('INSERT INTO "%s" VALUES (1, %s)' % (table, "'x'"))
                conn.commit()
            finally:
                conn.close()

            conns = self._open(tmp)
            report = []
            result = nt.get_messages(conns, TALKER, 10, from_time=1_700_000_000,
                                     shard_names=['message_0.db'], shard_report=report)
            for conn in conns:
                conn.close()

            self.assertEqual(report[0]['reason'], 'WINDOW_UNAVAILABLE')
            self.assertEqual(result['messages'], [])

    def _open(self, tmp):
        return nt.connect_message_shards(
            os.path.join(tmp, 'message_0.db'), 'a' * 64, 'b' * 32)

    def test_the_report_never_carries_an_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'), rows=[row(1, 1_700_000_000)])
            Path(os.path.join(tmp, 'message_1.db')).write_bytes(b'broken')

            target = os.path.join(tmp, 'message_0.db')
            pairs, failures = nt.connect_message_shards_detailed(target, 'a' * 64, 'b' * 32)
            conns = [conn for _p, conn in pairs]
            names = [os.path.basename(p) for p, _c in pairs]
            report = []
            nt.get_messages(conns, TALKER, 10, shard_names=names, shard_report=report)
            for conn in conns:
                conn.close()

            blob = json.dumps(failures + report)
            # No absolute path, in either separator style: the report is meant
            # to be safe to drop into a state file.
            self.assertNotIn(tmp, blob)
            self.assertNotIn(tmp.replace('\\', '/'), blob)
            for item in failures + report:
                self.assertEqual(os.path.basename(item['name']), item['name'])
                self.assertNotIn('/', item['name'])
                self.assertNotIn('\\', item['name'])


if __name__ == '__main__':
    unittest.main()
