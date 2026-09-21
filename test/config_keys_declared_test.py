"""脚本读的配置键，得是 `CliConfig` 里声明过的。

**这条检查是从一次真实审计里长出来的**：扫了一遍发现 `dashscopeApiKey` 被两个脚本读，
却根本不在 `CliConfig` 里——于是 `config set dashscopeApiKey` 拒绝执行，而脚本的报错
只能让用户手改 config.json。**一个存在、被读、却无法通过正规途径设置的配置项**，
就是这类问题的形状。

允许例外，但每一个例外都要写清为什么——名单本身就是这份检查最有价值的部分。
"""
import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

# 这些键确实被读，但**不来自** ~/.weflow-cli/config.json，或者缺少写入方。
# 每一项都要说明理由，否则这份名单会变成"把误报堆起来"。
NOT_FROM_WEFLOW_CONFIG = {
    # health_check 的 config 是 `check --json` 的输出，不是配置文件。
    'initialized': 'check --json 的输出，不是配置文件',
    'messageDatabase': 'check --json 的输出，不是配置文件',
    # watch_issues 有它自己的配置文件（见该脚本 docstring）。
    'smtp_auth': 'watch_issues 自己的配置文件',
    # 这两个被三个脚本读，但**没有任何东西写它们**——所以那条分支永远走不到，
    # 实际生效的是"从 passphrase 派生"。记在这里而不是假装它可用。
    'bizKey': '只被读、没人写；实际走的是从 passphrase 派生',
    'bizSalt': '只被读、没人写；实际走的是从 passphrase 派生',
}

READ_PATTERN = re.compile(r"(?:config|cfg|conf)\.get\(\s*['\"]([A-Za-z0-9_]+)['\"]")


def declared_keys():
    ts = (ROOT / 'src' / 'services' / 'configService.ts').read_text(encoding='utf-8')
    iface = ts.split('interface CliConfig {', 1)[1].split('\n}', 1)[0]
    return set(re.findall(r'^\s*([A-Za-z0-9_]+)\??:', iface, re.M))


class DeclaredConfigKeyTests(unittest.TestCase):
    def test_every_key_a_script_reads_is_declared(self):
        declared = declared_keys()
        offenders = {}
        for path in sorted(SCRIPTS.glob('*.py')):
            text = path.read_text(encoding='utf-8', errors='replace')
            for name in set(READ_PATTERN.findall(text)):
                if name in declared or name in NOT_FROM_WEFLOW_CONFIG:
                    continue
                offenders.setdefault(name, []).append(path.name)
        self.assertEqual(
            offenders, {},
            '这些键被脚本读，但 CliConfig 里没有声明——`config set` 会拒绝设置它们，\n'
            '用户只能手改 config.json：\n  '
            + '\n  '.join('%s <- %s' % (k, ', '.join(v))
                          for k, v in sorted(offenders.items())))

    def test_the_exception_list_is_not_stale(self):
        # 例外名单会腐烂：那些键可能已经被声明了，那就该从名单里删掉。
        declared = declared_keys()
        stale = sorted(k for k in NOT_FROM_WEFLOW_CONFIG if k in declared)
        self.assertEqual(stale, [],
                         '这些键已经声明了，请从 NOT_FROM_WEFLOW_CONFIG 里删掉：%s' % stale)

    def test_every_exception_carries_a_reason(self):
        for key, reason in NOT_FROM_WEFLOW_CONFIG.items():
            with self.subTest(key=key):
                self.assertTrue(reason and len(reason) > 6,
                                '%s 的例外没有写清理由' % key)

    def test_the_check_would_catch_a_regression(self):
        # 拿一个确实读过、但没声明的键验证它有牙齿。用当时那个真实的例子。
        declared = declared_keys()
        self.assertIn('dashscopeApiKey', declared,
                      'dashscopeApiKey 现在应当是声明过的——它正是这条检查的由来')
        self.assertNotIn('dashscopeApiKey', NOT_FROM_WEFLOW_CONFIG,
                         '它不该躺在例外名单里')


if __name__ == '__main__':
    unittest.main()
