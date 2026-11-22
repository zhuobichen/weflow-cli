"""朋友圈归档导出的单元测试：XML 解析、媒体提取、日期边界、页面转义。

不依赖真实 sns.db：直接喂合成 XML。
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('sns_export', SCRIPTS / 'sns_export.py')
se = importlib.util.module_from_spec(spec)
spec.loader.exec_module(se)

POST = """<TimelineObject>
  <id>123</id>
  <username>wxid_demo</username>
  <createTime>1700000000</createTime>
  <contentDesc>今天下雨了</contentDesc>
  <mediaList count="2">
    <media><id>1</id><url type="1">https://example.invalid/a.jpg</url><thumb>https://example.invalid/a_t.jpg</thumb></media>
    <media><id>2</id><url type="1">https://example.invalid/b.jpg</url></media>
  </mediaList>
</TimelineObject>"""


class ParsePostTest(unittest.TestCase):
    def test_fields_and_media(self):
        p = se.parse_post(POST)
        self.assertEqual(p['time'], 1700000000)
        self.assertEqual(p['author'], 'wxid_demo')
        self.assertEqual(p['text'], '今天下雨了')
        self.assertEqual(len(p['media']), 2)
        self.assertEqual(p['media'][0]['url'], 'https://example.invalid/a.jpg')
        self.assertEqual(p['media'][0]['thumb'], 'https://example.invalid/a_t.jpg')
        self.assertEqual(p['media'][1]['thumb'], '')

    def test_missing_create_time_is_skipped(self):
        """没有 createTime 的动态不能算进来（否则会排在最前面污染时间线）。"""
        self.assertIsNone(se.parse_post('<TimelineObject><id>1</id></TimelineObject>'))

    def test_empty_content_is_skipped(self):
        self.assertIsNone(se.parse_post(''))

    def test_malformed_xml_falls_back_to_regex(self):
        broken = ('<TimelineObject><username>wxid_x</username>'
                  '<createTime>1700000001</createTime>'
                  '<contentDesc>半截')  # 没有闭合
        p = se.parse_post(broken)
        self.assertIsNotNone(p)
        self.assertEqual(p['time'], 1700000001)
        self.assertEqual(p['author'], 'wxid_x')

    def test_text_only_post_has_no_media(self):
        p = se.parse_post('<TimelineObject><username>u</username>'
                          '<createTime>1700000002</createTime><contentDesc>hi</contentDesc></TimelineObject>')
        self.assertEqual(p['media'], [])
        self.assertEqual(p['text'], 'hi')


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='sns-idx-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_escapes_and_lists_media(self):
        posts = [{'author': '<作者>', 'timeText': '2026-01-01 00:00', 'text': '<b>正文</b>',
                  'media': [{'url': 'https://x.invalid/a.jpg', 'thumb': ''}]}]
        se.write_index(self.tmp, posts, ['说明一条'], {'generatedAt': '2026-01-01'})
        html = (self.tmp / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('<作者>', html)
        self.assertIn('&lt;作者&gt;', html)
        self.assertIn('https://x.invalid/a.jpg', html)
        self.assertIn('说明一条', html)

    def test_post_without_text_still_renders(self):
        posts = [{'author': 'u', 'timeText': '2026-01-01', 'text': '', 'media': []}]
        se.write_index(self.tmp, posts, [], {'generatedAt': 'x'})
        html = (self.tmp / 'index.html').read_text(encoding='utf-8')
        self.assertIn('class="post"', html)


if __name__ == '__main__':
    unittest.main()
