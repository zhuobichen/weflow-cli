"""导出 HTML 的检索栏：结构 + 脚本语法 + 时间戳属性。

**为什么要跑 `node --check`**：结构断言只能证明字符串在页面里，证明不了那段 JS 能执行。
一个语法错误会让整块脚本静默失效——用户看到的是"搜索框没反应"，而不是报错。

用合成消息直接调 `build_html_page`，不碰数据库。
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import pathlib
import re
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('export_chat_html', SCRIPTS / 'export_chat_html.py')
ech = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ech)


def fake_messages():
    """最小可用消息：build_html_page 只读这四个键。"""
    return [
        {'create_time': 1763000000, 'sender': '联系人A', 'is_self': False,
         'display': '会议纪要发了吗？'},
        {'create_time': 1763003600, 'sender': '我', 'is_self': True,
         'display': '下周整理好发你'},
        {'create_time': 1763090000, 'sender': '联系人A', 'is_self': False,
         'display': '收到'},
    ]


class HtmlSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = ech.build_html_page('wxid_demo', fake_messages(), 1, 1, '联系人A')

    def test_rows_carry_timestamp_and_sender(self):
        # 时间筛选必须靠 data-ts，不能靠解析文本里的日期
        self.assertEqual(self.html.count('data-ts="'), 3)
        self.assertIn('data-sender="联系人A"', self.html)

    def test_toolbar_has_all_controls(self):
        for needle in ('id="q"', 'id="from"', 'id="to"',
                       'onclick="jump(-1)"', 'onclick="jump(1)"', 'resetFilters'):
            self.assertIn(needle, self.html, '缺少控件: %s' % needle)

    def test_old_search_function_is_gone(self):
        self.assertNotIn('function searchMessages', self.html,
                         '旧实现应当被替换掉，避免两个函数争同一个输入框')

    def test_hit_highlight_style_exists(self):
        self.assertIn('.msg-row.hit .msg-bubble', self.html)

    def test_script_is_syntactically_valid_js(self):
        """把页面里的 <script> 抽出来交给 node --check。"""
        scripts = re.findall(r'<script>(.*?)</script>', self.html, re.S)
        self.assertTrue(scripts, '页面里应当有一段脚本')
        node = shutil.which('node')
        if not node:
            self.skipTest('没有 node')
        for body in scripts:
            with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False,
                                             encoding='utf-8') as fh:
                fh.write(body)
                path = fh.name
            try:
                proc = subprocess.run([node, '--check', path],
                                      capture_output=True, text=True, timeout=60)
                self.assertEqual(proc.returncode, 0,
                                 'JS 语法错误:\n%s' % (proc.stderr or '')[:600])
            finally:
                pathlib.Path(path).unlink(missing_ok=True)

    def test_filters_combine_text_and_time(self):
        """脚本里三者是"与"关系：文本 + 起 + 止。"""
        body = re.findall(r'<script>(.*?)</script>', self.html, re.S)[0]
        self.assertIn('okText && okFrom && okTo', body)
        self.assertIn('dataset.ts', body)


if __name__ == '__main__':
    unittest.main()
