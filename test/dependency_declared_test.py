"""每个第三方导入都必须在 requirements.txt 里声明，或在"刻意不声明"名单里。

为什么要有这条：2026-11-01 做依赖审计时发现 `PIL`（6 个脚本）和 `requests`
一直没写进 `requirements.txt`——装了 `pip install -r requirements.txt` 的用户
会以为自己装齐了，实际到某个命令才 ImportError。这类漂移**没有任何东西会红**。

判据的三条设计：

1. **正向**：真实扫描 `scripts/*.py`，每个第三方顶层导入要么已声明、要么在允许名单里；
2. **反向（正对照）**：喂一段含虚构包的源码，必须被判为"未声明"——
   否则解析器坏了也会报"全部通过"；
3. **名单本身**：允许名单里的每一项都必须**真的被某个脚本导入**，
   以及出现在 requirements 的包不能重复出现在名单里——两边都不许腐化。
"""
import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'

STDLIB = set(sys.stdlib_module_names)
LOCAL_MODULES = {p.stem for p in SCRIPTS.glob('*.py')}

# 导入名 → requirements 里的包名（有的不同名）
IMPORT_TO_PACKAGE = {
    'Crypto': 'pycryptodome',
    'Cryptodome': 'pycryptodome',
    'PIL': 'pillow',
    'fitz': 'pymupdf',
    'yaml': 'pyyaml',
    'bs4': 'beautifulsoup4',
    'dotenv': 'python-dotenv',
    'OpenSSL': 'pyopenssl',
    'win32crypt': 'pywin32',
}

# 刻意**不**声明在 requirements.txt 里的：要么可选（不装也能用主流程），
# 要么只在 Windows 上存在。新增条目必须写清理由。
ALLOWED_UNDECLARED = {
    'faster_whisper': '语音转写，可选',
    'imageio_ffmpeg': '表情/视频抽帧，可选',
    'nt_keys': '第三方 NT 密钥提取，可选',
    'pilk': '语音格式转换，可选',
    'psutil': '3.x 密钥提取辅助，可选',
    'pywxdump': '3.x 密钥提取，可选',
    'win32crypt': 'Windows DPAPI，仅 Windows 有意义',
}

# 已声明但可能被误加进名单的包
def read_requirements() -> set[str]:
    declared = set()
    for line in (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name = line.split('==')[0].split('>=')[0].split('[')[0].strip().lower()
        declared.add(name)
    return declared


def collect_imports(source: str) -> set[str]:
    """返回源码里的顶层第三方导入名（去掉标准库与本地模块）。"""
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split('.')[0])
    return {n for n in names if n not in STDLIB and n not in LOCAL_MODULES and not n.startswith('_')}


def undeclared(names: set[str], declared: set[str]) -> list[str]:
    """哪些导入既没声明、也不在允许名单里。"""
    bad = []
    for name in sorted(names):
        if name in ALLOWED_UNDECLARED:
            continue
        pkg = IMPORT_TO_PACKAGE.get(name, name).lower()
        if pkg in declared or name.lower() in declared:
            continue
        bad.append(name)
    return bad


class DependencyDeclaredTests(unittest.TestCase):
    def setUp(self):
        self.declared = read_requirements()
        self.assertIn('sqlcipher3', self.declared, 'requirements.txt 没读到内容 —— 下面的判据会失去意义')

    def test_control_undeclared_import_is_flagged(self):
        """正对照：解析器必须能抓到"未声明"——否则它坏掉时也会报全部通过。"""
        fake = 'import totally_fake_pkg_xyz\nimport json\n'
        names = collect_imports(fake)
        self.assertIn('totally_fake_pkg_xyz', names, '解析器没取到导入名')
        self.assertEqual(undeclared(names, self.declared), ['totally_fake_pkg_xyz'])

    def test_control_declared_import_is_accepted(self):
        """正对照 2：已声明的不能被误判。"""
        names = collect_imports('import sqlcipher3\nimport PIL.Image\n')
        self.assertEqual(undeclared(names, self.declared), [], 'PIL 已声明为 pillow，不该被判未声明')

    def test_every_script_import_is_declared_or_allowed(self):
        missing = {}
        for path in sorted(SCRIPTS.glob('*.py')):
            bad = undeclared(collect_imports(path.read_text(encoding='utf-8')), self.declared)
            if bad:
                missing[path.name] = bad
        self.assertEqual(
            missing, {},
            '这些第三方导入既没在 requirements.txt 声明、也不在允许名单里：\n' +
            '\n'.join(f'  {k}: {", ".join(v)}' for k, v in missing.items()))

    def test_allowlist_entries_are_actually_used(self):
        """名单不许腐化：写了"刻意不声明"却没人导入，就该删掉。"""
        used = set()
        for path in sorted(SCRIPTS.glob('*.py')):
            used |= collect_imports(path.read_text(encoding='utf-8'))
        stale = [name for name in ALLOWED_UNDECLARED if name not in used]
        self.assertEqual(stale, [], f'允许名单里这些条目已经没人导入了，请删除：{stale}')

    def test_allowlist_does_not_duplicate_requirements(self):
        """同一个包不能既声明又在名单里——那会让"未声明"永远判不出来。"""
        overlap = []
        for name in ALLOWED_UNDECLARED:
            pkg = IMPORT_TO_PACKAGE.get(name, name).lower()
            if pkg in self.declared or name.lower() in self.declared:
                overlap.append(name)
        self.assertEqual(overlap, [], f'这些已经在 requirements.txt 里了，不该再进允许名单：{overlap}')


if __name__ == '__main__':
    unittest.main()
