"""Media coverage reporting: it must describe what the page actually did.

Separate file from export_chat_media_test.py so the 31 existing rendering tests
stay untouched and keep guarding the behaviour this report is derived from.
"""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'export_chat_html.py'
spec = importlib.util.spec_from_file_location('export_chat_html_report', SCRIPT)
export = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(export)

MD5 = 'a' * 32
IMAGE = ('R0lGODlh', 'image/gif')


def row(local_id=7, server_id=101, local_type=3, content=''):
    return (local_id, server_id, local_type, 0, 1, 1_700_000_000, 0, '', content, b'')


class ResolveMediaTests(unittest.TestCase):
    def test_resolve_media_agrees_with_get_cached_image(self):
        """The report is derived from resolve_media, so the two must not drift."""
        cases = [
            ({f'md5:{MD5}': IMAGE}, [], None),
            ({f'pair:7:1700000000': IMAGE}, [], None),
            ({'unique:7': IMAGE}, [], None),
            ({}, [], None),
        ]
        for image_map, resource_md5s, _ in cases:
            hit = export.resolve_media(image_map, 7, 1_700_000_000, '', resource_md5s)
            self.assertEqual(export.get_cached_image(image_map, 7, 1_700_000_000, '', resource_md5s),
                             (hit[0], hit[1]) if hit else None)

    def test_resolve_media_reports_which_key_matched(self):
        hit = export.resolve_media({'unique:7': IMAGE}, 7, 1_700_000_000)
        self.assertEqual(hit[2], 'unique:7')
        hit = export.resolve_media({'pair:7:1700000000': IMAGE}, 7, 1_700_000_000)
        self.assertEqual(hit[2], 'pair:7:1700000000')


class MediaRecordTests(unittest.TestCase):
    def test_a_matched_image_is_embedded_and_named(self):
        result = export.format_message(row(), 'synthetic-contact', '',
                                       {f'md5:{MD5}': IMAGE}, resource_map={'server:101': [MD5]})
        record = result['media']
        self.assertEqual(record['status'], 'embedded')
        self.assertEqual(record['kind'], 'image')
        self.assertEqual(record['mediaKey'], f'md5:{MD5}')
        # A digest of the bytes actually embedded, so "same image as last time"
        # is answerable.
        self.assertEqual(len(record['sha256']), 64)

    def test_a_message_with_no_reliable_identity_is_missing_not_guessed(self):
        # Server id zero and no md5 anywhere: D-014 forbids matching on the
        # bare local id, so the honest outcome is a recorded miss.
        result = export.format_message(row(server_id=0), 'synthetic-contact', '', {})
        record = result['media']
        self.assertEqual(record['status'], 'missing')
        self.assertEqual(record['reason'], 'no-reliable-identity')
        self.assertIsNone(record['mediaKey'])

    def test_an_uncached_image_says_why_rather_than_showing_a_bare_placeholder(self):
        result = export.format_message(row(), 'synthetic-contact', '', {},
                                       resource_map={'server:101': [MD5]})
        record = result['media']
        self.assertEqual(record['status'], 'missing')
        self.assertEqual(record['reason'], 'not-in-local-cache')

    def test_exhausted_remote_budget_is_recorded_as_the_reason(self):
        original = export.COVER_STATE['thumb_budget']
        export.COVER_STATE['thumb_budget'] = 0
        try:
            content = '<msg><appmsg><title>t</title><thumburl>https://example.test/a.jpg</thumburl></appmsg></msg>'
            result = export.format_message(row(local_type=49, content=content),
                                           'synthetic-contact', '', {})
            self.assertEqual(result['media']['reason'], 'budget-exhausted')
        finally:
            export.COVER_STATE['thumb_budget'] = original

    def test_a_voice_message_is_unsupported_not_silently_missing(self):
        result = export.format_message(row(local_type=34), 'synthetic-contact', '')
        record = result['media']
        self.assertEqual(record['kind'], 'voice')
        self.assertEqual(record['status'], 'unsupported')
        self.assertEqual(record['reason'], 'voice-not-in-media-index')

    def test_a_non_media_message_has_no_record_at_all(self):
        for local_type in (1, 42, 48, 50, 10000, 10002):
            result = export.format_message(row(local_type=local_type, content='synthetic'),
                                           'synthetic-contact', '')
            self.assertIsNone(result['media'], f'type {local_type} is not media')


class MediaReportTests(unittest.TestCase):
    def test_the_report_counts_every_status_and_points_back_at_a_part(self):
        items = [
            {'index': 0, 'mediaKey': 'pair:1:2', 'kind': 'image', 'status': 'embedded',
             'mime': 'image/jpeg', 'bytes': 10, 'sha256': 'x' * 64, 'reason': None},
            {'index': 1, 'mediaKey': None, 'kind': 'image', 'status': 'missing',
             'mime': None, 'bytes': None, 'sha256': None, 'reason': 'not-in-local-cache'},
            {'index': 3, 'mediaKey': None, 'kind': 'voice', 'status': 'unsupported',
             'mime': None, 'bytes': None, 'sha256': None, 'reason': 'voice-not-in-media-index'},
        ]
        report = export.build_media_report(items, 'Synthetic Contact', 'synthetic-contact', per_page=2)
        self.assertEqual(report['schema'], 'weflow-media-report/v1')
        self.assertEqual(report['counts']['total'], 3)
        self.assertEqual(report['counts']['embedded'], 1)
        self.assertEqual(report['counts']['missing'], 1)
        self.assertEqual(report['counts']['unsupported'], 1)
        # per_page=2 puts index 3 on the second page.
        self.assertEqual([i['locator']['part'] for i in report['items']], [1, 1, 2])

    def test_the_report_carries_no_paths_keys_or_urls(self):
        report = export.build_media_report(
            [{'index': 0, 'mediaKey': 'pair:1:2', 'kind': 'image', 'status': 'embedded',
              'mime': 'image/jpeg', 'bytes': 10, 'sha256': 'x' * 64, 'reason': None}],
            'Synthetic Contact', 'synthetic-contact', per_page=0)
        blob = json.dumps(report, ensure_ascii=False)
        self.assertNotIn('xwechat_files', blob)
        self.assertNotIn('C:' + chr(92), blob)
        self.assertNotIn('http://', blob)
        self.assertNotIn('https://', blob)

    def test_budget_counters_are_reported_rather_than_discarded(self):
        """COVER_STATE was computed and thrown away before this report existed."""
        report = export.build_media_report([], 'scope', 'talker', per_page=0)
        for key in ('coversFetched', 'coversCached', 'coversSkipped',
                    'thumbsFetched', 'thumbsCached', 'thumbsSkipped'):
            self.assertIn(key, report['budget'])


if __name__ == '__main__':
    unittest.main()
