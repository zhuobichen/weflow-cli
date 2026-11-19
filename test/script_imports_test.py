"""守卫：脚本里用到的模块名必须真的 import 了。

**为什么需要这条**：`promises.py` / `on_this_day.py` 的 `--talker` 默认值改成读
`WEFLOW_TALKER` 环境变量时（86e4afa 那次，会话标识不进 argv），用了 `os.environ`
却忘了 `import os`。`argparse.add_argument` 在**注册时**就求值默认值，所以
`weflow-cli promises` 和 `weflow-cli today` **每一次运行**都抛 NameError——
而这四个命令当时没有任何测试覆盖，`npm test` / `python -m unittest` 全绿。

这类错误的共同特征：它不是某个函数的边界情况，而是**整个命令不可用**，
并且 syntax/AST 层面完全合法，只有真正执行到那一行才会炸。

判据：模块级的 `import` / `from ... import` 名 + 函数参数 + 赋值目标，构成"已绑定名字"；
再收集所有 `Load` 上下文的 `Name`。凡是"被使用但从未绑定"的**标准库常见模块名**
就报错。只查一张小名单（os/sys/re/json/math/time/datetime/pathlib/subprocess/hashlib/
base64/struct/zlib），避免把 `__import__` 动态注入的、或条件导入的名字误报。
"""
from __future__ import annotations

import ast
import pathlib
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'

# 只放"忘了 import 就等于整条命令不可用"的那批常用模块
WATCHED = {
    'os', 'sys', 're', 'json', 'math', 'time', 'datetime', 'pathlib',
    'subprocess', 'hashlib', 'base64', 'struct', 'zlib', 'shutil',
    'argparse', 'csv', 'sqlite3', 'tempfile', 'glob', 'random', 'uuid',
}


def bound_names(tree: ast.AST) -> set[str]:
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != '*':
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for a in list(args.args) + list(args.kwonlyargs):
                bound.add(a.arg)
            if args.vararg:
                bound.add(args.vararg.arg)
            if args.kwarg:
                bound.add(args.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            if isinstance(node.target, ast.Name):
                bound.add(node.target.id)
    return bound


def loaded_names(tree: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


class UndefinedModuleNameTest(unittest.TestCase):
    def test_no_script_uses_an_unimported_stdlib_module(self):
        broken = []
        for path in sorted(SCRIPTS.glob('*.py')):
            try:
                tree = ast.parse(path.read_text(encoding='utf-8'))
            except SyntaxError:      # 语法错误由别的检查负责，这里不重复报
                continue
            missing = (loaded_names(tree) & WATCHED) - bound_names(tree)
            if missing:
                broken.append('%s 用了 %s 却没 import'
                              % (path.name, ', '.join(sorted(missing))))
        self.assertEqual(
            broken, [],
            '以下脚本会在运行时抛 NameError（整条命令不可用）：\n  ' + '\n  '.join(broken)
            if broken else None)

    def test_the_checker_itself_catches_a_planted_case(self):
        """正对照：把我的判据本身放进一个已知会红的样本里，确认它真的会报。"""
        planted = ast.parse('import json\nprint(os.environ)\n')
        missing = (loaded_names(planted) & WATCHED) - bound_names(planted)
        self.assertIn('os', missing, '判据失效：连植入的 os 都查不出来')

        clean = ast.parse('import os\nprint(os.environ)\n')
        self.assertEqual((loaded_names(clean) & WATCHED) - bound_names(clean), set(),
                         '判据误报：已 import 的 os 也被判成缺失')


if __name__ == '__main__':
    unittest.main()
