"""待办列表要分得清两件事：**没有待办** 和 **从没提取过待办**。

脚本里 `load_todos()` 在文件不存在和文件内容是空数组两种情况下都返回 `[]`，
于是 `list` 的输出一模一样。而这个区别是有后果的：提取是用户显式跑
`weflow-cli todos extract --yes` 才发生的，所以"从没提取过"时那份清单一直是空的，
把这种空说成"你没有事要做"就是**报了一个没查证过的状态**——本机的实际情况正是
`~/.weflow-cli/todos.json` 根本不存在（实测）。

`--json` 的裸数组形状有别的调用方在用（`bin/weflow-cli.ts` 的 `todos: { cli: ... }`
能力声明），所以区分信号走新开关 `--meta`，老形状不动。
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('extract_todos', SCRIPTS / 'extract_todos.py')
et = importlib.util.module_from_spec(spec)
spec.loader.exec_module(et)


class _Capture(io.StringIO):
    """`main()` 开头会对 stdout 调 `reconfigure`，StringIO 没有这个方法。"""

    def reconfigure(self, **kwargs):  # noqa: D102
        return None


def run_cli(argv):
    """跑一次真正的 `main()`，返回它打印到 stdout 的文本。"""
    buf = _Capture()
    with patch.object(sys, 'argv', ['extract_todos.py'] + argv), contextlib.redirect_stdout(buf):
        et.main()
    return buf.getvalue()


class MetaFlagTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        os.unlink(self.path)                      # 默认：文件不存在（= 从没提取过）
        self._patch = patch.object(et, 'TODOS_FILE', self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def write(self, todos):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(todos, f, ensure_ascii=False)

    def meta(self, *extra):
        return json.loads(run_cli(['list', '--json', '--meta'] + list(extra)))

    # ---------------------------------------------------------------- 区分两种空

    def test_no_file_says_not_extracted(self):
        out = self.meta()
        self.assertEqual(out['items'], [])
        self.assertIs(out['extracted'], False, '文件不存在就是没提取过')

    def test_an_extracted_but_empty_file_is_not_the_same_thing(self):
        self.write([])
        out = self.meta()
        self.assertEqual(out['items'], [])
        self.assertIs(out['extracted'], True, '跑过 extract 了，只是确实没有待办')

    def test_items_come_back_with_a_count(self):
        self.write([{'id': 1, 'task': '交报表', 'urgency': '高', 'deadline': '周五', 'status': 'pending'}])
        out = self.meta()
        self.assertEqual(out['count'], 1)
        self.assertEqual(out['items'][0]['task'], '交报表')
        self.assertIs(out['extracted'], True)

    def test_status_filter_still_applies_under_meta(self):
        self.write([
            {'id': 1, 'task': '待办的事', 'urgency': '中', 'deadline': '未提及', 'status': 'pending'},
            {'id': 2, 'task': '办完的事', 'urgency': '中', 'deadline': '未提及', 'status': 'done'},
        ])
        self.assertEqual([t['task'] for t in self.meta('--status', 'pending')['items']], ['待办的事'])
        self.assertEqual([t['task'] for t in self.meta('--status', 'done')['items']], ['办完的事'])

    # ---------------------------------------------------------------- 老形状不许变

    def test_plain_json_stays_a_bare_array(self):
        """有调用方按数组消费，加开关不能顺手改了它。"""
        self.assertEqual(json.loads(run_cli(['list', '--json'])), [], '没有文件时是空数组，不是对象')

        self.write([{'id': 1, 'task': '交报表', 'urgency': '高', 'deadline': '未提及', 'status': 'pending'}])
        out = json.loads(run_cli(['list', '--json']))
        self.assertIsInstance(out, list)
        self.assertEqual([t['task'] for t in out], ['交报表'])

    # ---------------------------------------------------------------- 给人看的那句

    def test_human_output_distinguishes_them_too(self):
        self.assertIn('还没提取过待办', run_cli(['list']))
        self.assertNotIn('暂无待办', run_cli(['list']))

        self.write([])
        self.assertIn('暂无待办', run_cli(['list']))

    def test_remind_does_not_congratulate_an_empty_never_extracted_list(self):
        out = run_cli(['remind'])
        self.assertIn('还没提取过待办', out)
        self.assertNotIn('干得好', out)

        self.write([])
        self.assertIn('干得好', run_cli(['remind']))


if __name__ == '__main__':
    unittest.main()
