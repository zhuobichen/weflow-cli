"""Linux 相关的读取链路：SQLCipher 打开 + passphrase 派生。

为什么单独一个文件：Linux 上"读不到库"曾经被笼统归因于"密钥不行"，
但其实是两件事——
  A. **读取链路**（用 SQLCipher 打开、查表、解析消息）是否在各平台都工作；
  B. 拿不拿得到密钥。
2026-11-01 在 Ubuntu + 微信 Linux 4.1.13.26 上用**已知密钥的合成库**验证过 A 是通的；
本文件把那次验证固化下来，免得以后 A 坏了却继续怪 B。

两个测试都不碰真实微信数据：库是临时造的，密钥是测试专用的固定值。
"""
import hashlib
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

try:
    import sqlcipher3
    HAS_SQLCIPHER = True
except ImportError:          # CI 装的是 sqlcipher3-binary；本地可能没装
    HAS_SQLCIPHER = False


TITLE = 'Linux 合成库验证文章'
URL = 'https://mp.weixin.qq.com/s/SYNTHETIC0000000000000'
GH = 'gh_synthtest0000000000000000'


def build_db(path, raw_key_hex, salt_hex):
    """按 biz_daily 的读法造一个最小的订阅号库。"""
    import sqlcipher3
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlcipher3.connect(path)
    conn.execute(f"PRAGMA key = \"x'{raw_key_hex}{salt_hex}'\"")
    conn.execute('CREATE TABLE Name2Id (user_name TEXT)')
    conn.execute('INSERT INTO Name2Id VALUES (?)', (GH,))
    tbl = 'Msg_' + hashlib.md5(GH.encode()).hexdigest()
    conn.execute(f'CREATE TABLE "{tbl}" (create_time INTEGER, message_content BLOB)')
    xml = ('<msg><appmsg><title><![CDATA[%s]]></title>'
           '<des><![CDATA[合成测试]]></des>'
           '<url><![CDATA[%s]]></url></appmsg></msg>' % (TITLE, URL))
    conn.execute(f'INSERT INTO "{tbl}" VALUES (?, ?)', (1793500000, xml.encode('utf-8')))
    conn.commit()
    conn.close()


def read_articles(db_path, raw_key_hex, salt_hex):
    """照着 biz_daily 的读法把标题读回来（走它自己的解析函数）。"""
    import biz_daily
    conn = sqlcipher3.connect(db_path)
    conn.execute(f'PRAGMA key = "x\'{raw_key_hex}{salt_hex}\'"')
    cur = conn.cursor()
    users = [r[0] for r in cur.execute("SELECT user_name FROM Name2Id WHERE user_name LIKE 'gh_%'")]
    out = []
    for user in users:
        tbl = 'Msg_' + hashlib.md5(user.encode()).hexdigest()
        for _ct, content in cur.execute(f'SELECT create_time, message_content FROM "{tbl}"'):
            info = biz_daily.extract_article_info(content)
            if info['title']:
                out.append(info)
    conn.close()
    return out


@unittest.skipUnless(HAS_SQLCIPHER, 'sqlcipher3 未安装（CI 装 sqlcipher3-binary）')
class ReadPathTests(unittest.TestCase):
    """A：读取链路本身，跨平台必须一致。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wf-biz-')
        self.db = os.path.join(self.tmp, 'message', 'biz_message_0.db')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_synthetic_db_with_raw_key(self):
        key, salt = '11' * 32, '22' * 16
        build_db(self.db, key, salt)
        arts = read_articles(self.db, key, salt)
        self.assertEqual(len(arts), 1, '用已知密钥应能读出一篇')
        self.assertEqual(arts[0]['title'], TITLE)
        self.assertEqual(arts[0]['url'], URL)

    def test_wrong_key_yields_nothing(self):
        """反例：错误密钥不能"看起来也能读"——否则上面的通过说明不了问题。"""
        key, salt = '11' * 32, '22' * 16
        build_db(self.db, key, salt)
        with self.assertRaises(Exception):
            read_articles(self.db, '33' * 32, salt)


@unittest.skipUnless(HAS_SQLCIPHER, 'sqlcipher3 未安装')
class PassphraseDerivationTests(unittest.TestCase):
    """B 的前半：4.1.12.26+ 的「口令 + 每分片 salt 派生」路径。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wf-pass-')
        self.db = os.path.join(self.tmp, 'message', 'biz_message_0.db')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_derived_key_matches_derive_database_key(self):
        """库是用「口令派生的密钥」建的；_utils 必须派生出同一把。"""
        from nt_common import derive_database_key
        passphrase_hex = '44' * 32
        salt = '55' * 16
        derived = hashlib.pbkdf2_hmac('sha512', bytes.fromhex(passphrase_hex),
                                      bytes.fromhex(salt), 256000, 32).hex()
        build_db(self.db, derived, salt)

        key, got_salt = derive_database_key(self.db, '', '', passphrase_hex)
        self.assertEqual(got_salt, salt, 'salt 应取自库头')
        self.assertEqual(key, derived, '派生结果必须与建库时一致')
        arts = read_articles(self.db, key, got_salt)
        self.assertEqual(arts[0]['title'], TITLE)


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(HAS_SQLCIPHER, 'sqlcipher3 未安装')
class VerifyPassphraseAllTests(unittest.TestCase):
    """`nt_decrypt.py verify-passphrase-all` 的判据：口令对不对要能一句话说清。

    Linux 上自动提密钥走不通（4.1.12.26+ 用口令 + 每分片派生），
    用户唯一可行的路径就是自己提供口令——那就必须能立刻验证它对不对，
    而不是等到 `init` 之后读数据时才报"file is not a database"。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wf-vpa-')
        self.db = os.path.join(self.tmp, 'wxid_demo', 'db_storage', 'message',
                               'biz_message_0.db')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _verify(self, passphrase_hex):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'ntd', str(ROOT / 'scripts' / 'nt_decrypt.py'))
        ntd = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ntd)
        return ntd.verify_passphrase_native(passphrase_hex, self.db)

    def test_correct_passphrase_is_recognised(self):
        passphrase = '77' * 32
        salt = '66' * 16
        derived = hashlib.pbkdf2_hmac('sha512', bytes.fromhex(passphrase),
                                      bytes.fromhex(salt), 256000, 32).hex()
        build_db(self.db, derived, salt)
        self.assertIs(self._verify(passphrase), True, '正确口令必须被判为可用')

    def test_wrong_passphrase_is_rejected(self):
        passphrase = '77' * 32
        salt = '66' * 16
        derived = hashlib.pbkdf2_hmac('sha512', bytes.fromhex(passphrase),
                                      bytes.fromhex(salt), 256000, 32).hex()
        build_db(self.db, derived, salt)
        self.assertIs(self._verify('88' * 32), False, '错误口令必须被判为不可用')
