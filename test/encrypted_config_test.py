"""加密存储的配置项，读的时候必须解密。

`configService.ENCRYPTED_KEYS` 里那些键在磁盘上是 `lock:<密文>`。`config set` 写进去
就是这个形态——**包括 `deepseekApiKey`**。但仓库里曾有四处直接 `config.get('deepseekApiKey')`，
把密文当 API key 发出去，换来一个说不清原因的 401。

它没暴露，只因为那台机器上这把 key 恰好是**明文**（被绕过 `config set` 写进去的）。
换句话说：**这是一颗等你去轮换密钥就会踩到的雷**——那一刻四个脚本会同时失败，
而错误信息只会说"密钥无效"。

所以这里做静态检查：凡是读加密项的，要么经过 `decrypt_lock(`，要么用 `_utils` 里
专门的取键函数。这个检查不需要运行任何东西，它看的是代码。
"""
import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'

sys.path.insert(0, str(SCRIPTS))

# 与 src/services/configService.ts 的 ENCRYPTED_KEYS 对应。**故意重复一份**：
# 从 TS 里读那份要跨语言解析，而漂移的代价是漏检；所以这里再加一条测试
# （test_…same_as_the_ts_list）比对两边集合相等。
ENCRYPTED_KEYS = {
    'decryptKey', 'decryptKey3x', 'ntKey', 'contactKey', 'wechatOcToken',
    'wereadApiKey', 'deepseekApiKey', 'typesafeApiKey', 'dashscopeApiKey', 'snsKey',
    'favKey', 'favPassphrase',
}
# 允许直接读的文件：解密助手就住在这里。
SANCTIONED = {'_utils.py'}

PATTERN = re.compile(r"\.get\(\s*['\"](%s)['\"]" % '|'.join(sorted(ENCRYPTED_KEYS)))


def offending_lines(path: Path):
    """挑出「读了加密项但没有解密」的行。

    只有两种情况算合格：
      1. 同一条语句里就解密（`decrypt_lock(...)` 或 `_utils` 里那几个取键函数）；
      2. 赋值目标以 `_enc` 结尾——这是仓库里既有的约定：**先把密文拿在手里，
         稍后再解**（`msg_key_enc` / `contact_key_enc` / `pass_enc` 都这样用）。
         不认这条约定，检查会把这七处正确代码判成错的，然后被人整体忽略。
    """
    hits = []
    text = path.read_text(encoding='utf-8', errors='replace')
    for number, line in enumerate(text.splitlines(), 1):
        if not PATTERN.search(line):
            continue
        if any(marker in line for marker in
               ('decrypt_lock(', 'get_api_key(', 'get_typesafe_key(')):
            continue
        target = line.split('=', 1)[0].strip() if '=' in line else ''
        if target.endswith('_enc'):
            continue
        hits.append((number, line.strip()))
    return hits


class EncryptedConfigReadTests(unittest.TestCase):
    def test_no_script_reads_an_encrypted_key_without_decrypting(self):
        offenders = []
        for path in sorted(SCRIPTS.glob('*.py')):
            if path.name in SANCTIONED:
                continue
            for number, line in offending_lines(path):
                offenders.append('%s:%d  %s' % (path.name, number, line[:90]))
        self.assertEqual(offenders, [],
                         '这些地方直接读了加密存储的配置项，会把密文当明文用：\n  '
                         + '\n  '.join(offenders))

    def test_the_sanctioned_helper_does_decrypt(self):
        # 上一条测试把 `_utils` 排除在外，所以它自己必须确实解密——否则整条链断了
        # 而没有任何东西会发现。
        text = (SCRIPTS / '_utils.py').read_text(encoding='utf-8')
        for key in ('deepseekApiKey', 'typesafeApiKey', 'dashscopeApiKey'):
            self.assertIn("decrypt_lock(config.get('%s'" % key, text,
                          '%s 的读取没有经过 decrypt_lock' % key)

    def test_the_check_would_actually_catch_a_regression(self):
        # 一条永远不会失败的检查比没有检查更糟。用一段**假的**代码验证它有牙齿。
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'fake.py'
            fake.write_text("key = config.get('deepseekApiKey', '')\n", encoding='utf-8')
            self.assertEqual(len(offending_lines(fake)), 1)
            fake.write_text("key = decrypt_lock(config.get('deepseekApiKey', ''))\n",
                            encoding='utf-8')
            self.assertEqual(offending_lines(fake), [])

    def test_the_hold_the_ciphertext_convention_is_accepted(self):
        # `msg_key_enc = config.get('ntKey')` 之后才 decrypt_lock，是既有且正确的写法。
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'fake.py'
            fake.write_text("msg_key_enc = config.get('ntKey', '')\n", encoding='utf-8')
            self.assertEqual(offending_lines(fake), [])

    def test_the_python_list_matches_the_typescript_one(self):
        # 两边各有一份名单，漂了就会漏检。这里比对集合相等。
        ts = (ROOT / 'src' / 'services' / 'configService.ts').read_text(encoding='utf-8')
        block = ts.split('ENCRYPTED_KEYS', 1)[1].split('])', 1)[0]
        found = set(re.findall(r"'([A-Za-z0-9_]+)'", block))
        self.assertEqual(found, ENCRYPTED_KEYS,
                         '两边的加密键名单不一致：%s' % (found ^ ENCRYPTED_KEYS,))


if __name__ == '__main__':
    unittest.main()
