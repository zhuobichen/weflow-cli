"""read_document.py 的解析测试：各格式都要能读，坏文件/不存在要如实报错。

这是"力求什么都能看"那条要求的判据——加格式时跟着加用例，
别让"支持 X 格式"变成一句没人验证的话。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'read_document.py'


def run(path, extra=None):
    cmd = [sys.executable, str(SCRIPT), '--path', str(path), '--json'] + (extra or [])
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    return json.loads(r.stdout.decode('utf-8', 'replace'))


class ReadDocumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_text(self):
        p = self.dir / 'note.txt'
        p.write_text('你好\n第二行', encoding='utf-8')
        d = run(p)
        self.assertTrue(d['ok'])
        self.assertIn('第二行', d['text'])

    def test_docx(self):
        try:
            import docx
        except ImportError:
            self.skipTest('python-docx 未装')
        p = self.dir / 'a.docx'
        doc = docx.Document()
        doc.add_paragraph('标题段')
        doc.add_paragraph('正文段')
        doc.save(p)
        d = run(p)
        self.assertTrue(d['ok'], d.get('error'))
        self.assertIn('标题段', d['text'])
        self.assertIn('正文段', d['text'])

    def test_xlsx(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest('openpyxl 未装')
        p = self.dir / 'a.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['姓名', '年龄'])
        ws.append(['张三', 18])
        wb.save(p)
        d = run(p)
        self.assertTrue(d['ok'], d.get('error'))
        self.assertIn('姓名', d['text'])
        self.assertIn('张三', d['text'])

    def test_html_strips_tags(self):
        p = self.dir / 'a.html'
        p.write_text('<html><script>evil()</script><body><p>正文</p></body></html>', encoding='utf-8')
        d = run(p)
        self.assertTrue(d['ok'])
        self.assertIn('正文', d['text'])
        self.assertNotIn('evil()', d['text'])

    def test_missing_file(self):
        d = run(self.dir / '不存在.txt')
        self.assertFalse(d['ok'])
        self.assertIn('不存在', d['error'])

    def test_truncation(self):
        p = self.dir / 'big.txt'
        p.write_text('x' * 5000, encoding='utf-8')
        d = run(p, ['--max-chars', '100'])
        self.assertTrue(d['truncated'])
        self.assertEqual(d['chars'], 100)

    def test_locate_by_name(self):
        """按文件名在微信文件目录定位（"这个 pdf"那条路）"""
        sub = self.dir / 'msg' / 'file' / '2026-11'
        sub.mkdir(parents=True)
        target = sub / 'figure (2).pdf'
        target.write_text('dummy', encoding='utf-8')
        r = subprocess.run(
            [sys.executable, str(SCRIPT), '--name', 'figure (2).pdf',
             '--wechat-dir', str(self.dir / 'msg'), '--locate-only', '--json'],
            capture_output=True, timeout=60,
        )
        d = json.loads(r.stdout.decode('utf-8', 'replace'))
        self.assertTrue(d['ok'], d.get('error'))
        self.assertEqual(d['found_path'], str(target))
        self.assertEqual(d['text'], '')  # locate-only 不读内容

    def test_locate_by_name_not_found(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), '--name', '不存在的文件.pdf',
             '--wechat-dir', str(self.dir), '--locate-only', '--json'],
            capture_output=True, timeout=60,
        )
        d = json.loads(r.stdout.decode('utf-8', 'replace'))
        self.assertFalse(d['ok'])
        self.assertIn('没找到', d['error'])


if __name__ == '__main__':
    unittest.main()
