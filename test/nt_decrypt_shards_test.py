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


def make_shard(path, talker=TALKER, rows=(), with_name_table=True, omit_column=False):
    """One NT-shaped shard file.

    Closing in a finally matters: a leaked connection keeps the file locked on
    Windows, and the TemporaryDirectory teardown then fails with a
    PermissionError that looks nothing like the actual mistake.
    """
    table = 'Msg_' + hashlib.md5(talker.encode()).hexdigest()
    columns = MSG_COLUMNS
    if omit_column:
        # A table missing a column the SELECT asks for must raise on read,
        # which is how a per-shard READ_FAILED is produced. Row shape no
        # longer matches, so callers must not pass rows with this option.
        columns = columns.replace(', compress_content BLOB', '')
        assert not rows, 'omit_column changes the row width; do not pass rows'
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

    def test_both_copies_of_discovery_agree(self):
        """nt_decrypt and export_chat_html each carry a copy; pin them together.

        They are deliberately not unified yet (unifying would change exporter
        behaviour), so this test is what stops them drifting apart unnoticed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('message_0.db', 'message_1.db', 'message_fts.db'):
                make_shard(os.path.join(tmp, name))
            target = os.path.join(tmp, 'message_0.db')
            self.assertEqual(
                sorted(os.path.basename(p) for p in nt.discover_message_shards(target)),
                sorted(os.path.basename(p) for p in export.discover_message_shards(target)),
            )


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

    def test_one_broken_shard_is_flagged_while_the_others_still_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_shard(os.path.join(tmp, 'message_0.db'), rows=[row(1, 1_700_000_000)])
            # No rows: the table exists but lacks a column the SELECT asks for,
            # so reading it raises mid-shard rather than returning nothing.
            make_shard(os.path.join(tmp, 'message_1.db'), omit_column=True)

            conns = self._open(tmp)
            report = []
            result = nt.get_messages(conns, TALKER, 10,
                                     shard_names=['message_0.db', 'message_1.db'],
                                     shard_report=report)
            for conn in conns:
                conn.close()

            by_name = {item['name']: item for item in report}
            self.assertEqual(by_name['message_1.db']['reason'], 'READ_FAILED')
            self.assertIsNone(by_name['message_0.db']['reason'])
            # The readable shard's message must still come back.
            self.assertEqual(len(result['messages']), 1)

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
