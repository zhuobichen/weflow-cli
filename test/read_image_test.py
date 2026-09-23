"""取图脚本（`scripts/read_image.py`）里会**静默退化**的那几条规则。

这条路真正难的部分（`.dat` v2 解密、媒体索引、`MessageResourceInfo` 的 md5 映射）在
`export_chat_html` 里，已经被真实数据验证过（380/380 张图取到）。这个文件盯的是这层薄壳
自己会不会出错，而它出错的方式都是不报错的：

1. **调用顺序**：`resource_md5s` 要传给 `resolve_media`。少了它取到率会掉（群聊实测 0/50），
   而失败长得和"本机没有这张图"一模一样——于是助手会说"我看不到"，没人知道是取错了；
2. **只缓存取到的**：取不到可能是"微信没在运行"这种会变的状态，缓存住就永远取不到了；
3. 三种结果（拿到图 / 取不到但说清原因 / 配置层的错）不能混成一种。

不接数据库：`fetch_messages_from_shards` 与整条媒体链都打桩。CI 的 python job 里没有
sqlcipher3，所以按仓库里既有的做法把它换成 stdlib sqlite3（这些用例不碰真实加密库）。
"""
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('read_image', SCRIPTS / 'read_image.py')
ri = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(ri)

# 行序与导出侧一致：local_id, server_id, local_type, sort_seq, real_sender_id,
# create_time, status, source, message_content, compress_content
TALKER = 'wxid_example'
IMAGE_ROW = (44, 9001, 3, 0, 1, 1758000000, 0, '', '<img md5="abc"/>', b'')
TEXT_ROW = (45, 9002, 1, 0, 1, 1758000060, 0, '', '一句正文', b'')


def run_main(argv, *, rows=(IMAGE_ROW,), resolve=('QUJD', 'image/jpeg', 'md5:abc'),
             v2_key='a-key', cache_dir=None):
    """跑一次 main()，返回 (退出码, 解析后的 JSON, 被调用的桩记录)。"""
    calls = {'order': []}

    def fake_scan_nt_cache(*a, **kw):
        calls['order'].append('scan_nt_cache')
        return {'md5:abc': ('QUJD', 'image/jpeg')}

    def fake_scan_account_media(*a, **kw):
        calls['order'].append('scan_account_media')
        return {}

    def fake_load_resource(*a, **kw):
        calls['order'].append('load_resource_media_map')
        calls['resource_map_arg'] = a[3] if len(a) > 3 else kw.get('messages')
        return {'server:9001': ['abc']}

    def fake_resolve(image_map, local_id, create_time, content, resource_md5s, server_id):
        calls['order'].append('resolve_media')
        calls['resolve_args'] = dict(local_id=local_id, create_time=create_time,
                                     content=content, resource_md5s=resource_md5s,
                                     server_id=server_id)
        return resolve

    def fake_shrink(media, max_side=720, force=False):
        calls['order'].append('shrink_embedded')
        calls['shrink_force'] = force
        return media

    cache_dir = cache_dir or tempfile.mkdtemp()
    db_path = os.path.join(cache_dir, 'fake.db')
    with open(db_path, 'w', encoding='utf-8') as fh:
        fh.write('x')          # main() 只检查它存在
    cfg = {'ntDbPath': db_path, 'ntKey': '', 'ntSalt': '', 'wxid': 'wxid_example_4c8e'}

    out = io.StringIO()
    with patch.object(ri, 'CACHE_DIR', cache_dir), \
         patch.object(ri, '_utils') as fake_utils, \
         patch.object(ri, 'E') as fake_e, \
         patch.object(sys, 'argv', ['read_image.py'] + argv), \
         contextlib.redirect_stdout(out):
        fake_utils.load_config.return_value = cfg
        fake_utils.decrypt_lock.side_effect = lambda v: v
        fake_e.fetch_messages_from_shards.return_value = list(rows)
        fake_e.scan_nt_cache.side_effect = fake_scan_nt_cache
        fake_e.scan_account_media.side_effect = fake_scan_account_media
        fake_e.load_resource_media_map.side_effect = fake_load_resource
        fake_e.resolve_media.side_effect = fake_resolve
        fake_e.shrink_embedded.side_effect = fake_shrink
        fake_e.resolve_v2_media_key.return_value = v2_key
        fake_e.EMBED_SHRINK_THRESHOLD = 400 * 1024
        fake_e.clean_account_wxid.side_effect = lambda v: v
        code = ri.main()

    lines = [l for l in out.getvalue().splitlines() if l.strip().startswith('{')]
    parsed = json.loads(lines[-1]) if lines else None
    return code, parsed, calls


class ReadImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_takes_the_image_and_reports_its_size(self):
        code, data, calls = run_main(['--talker', TALKER, '--local-id', '44'],
                                     cache_dir=self.tmp)
        self.assertEqual(code, 0)
        self.assertTrue(data['success'])
        self.assertEqual(data['b64'], 'QUJD')
        self.assertEqual(data['mime'], 'image/jpeg')
        self.assertEqual(data['bytes'], 4)
        self.assertFalse(data['cached'])

    def test_resource_md5s_are_passed_to_resolve_media(self):
        # 少了这一项，群聊取到率会掉到 0，而失败长得和"本机没有"一样
        _, _, calls = run_main(['--talker', TALKER, '--local-id', '44'], cache_dir=self.tmp)
        self.assertEqual(calls['resolve_args']['resource_md5s'], ['abc'])
        self.assertEqual(calls['resolve_args']['server_id'], 9001)
        self.assertEqual(calls['resolve_args']['create_time'], 1758000000)

    def test_index_is_built_before_resolving(self):
        # 顺序错了会用到空索引：取到率静默归零
        _, _, calls = run_main(['--talker', TALKER, '--local-id', '44'], cache_dir=self.tmp)
        order = [name for name in calls['order'] if name != 'shrink_embedded']
        self.assertEqual(order, ['scan_nt_cache', 'scan_account_media',
                                 'load_resource_media_map', 'resolve_media'])

    def test_the_shrink_is_forced(self):
        # `shrink_embedded` 不传 force=True 是空操作——图上去了但是原尺寸
        _, _, calls = run_main(['--talker', TALKER, '--local-id', '44'], cache_dir=self.tmp)
        self.assertTrue(calls['shrink_force'], 'shrink_embedded 必须带 force=True')

    def test_a_second_call_comes_from_cache_and_skips_the_index(self):
        run_main(['--talker', TALKER, '--local-id', '44'], cache_dir=self.tmp)
        _, data, calls = run_main(['--talker', TALKER, '--local-id', '44'], cache_dir=self.tmp)
        self.assertTrue(data['cached'])
        self.assertEqual(calls['order'], [], '命中缓存就不该再建索引')

    def test_a_miss_is_reported_as_a_reason_with_a_hint_when_wechat_is_closed(self):
        code, data, _ = run_main(['--talker', TALKER, '--local-id', '44'],
                                 resolve=None, v2_key=None, cache_dir=self.tmp)
        self.assertEqual(code, 0, '取不到不是错误，是"取不到"')
        self.assertTrue(data['success'])
        self.assertIn('本机没有这张图的副本', data['reason'])
        self.assertIn('微信', data['hint'], '要区分"本机没有"与"我现在看不到"')

    def test_a_miss_is_not_cached(self):
        # 微信关着时取不到，开机后应该能取到——缓存住就永远取不到了
        run_main(['--talker', TALKER, '--local-id', '44'], resolve=None, cache_dir=self.tmp)
        _, _, calls = run_main(['--talker', TALKER, '--local-id', '44'], cache_dir=self.tmp)
        self.assertIn('scan_nt_cache', calls['order'], '上一次没取到，这一次要重新找')

    def test_a_non_image_message_says_so_instead_of_fetching(self):
        _, data, calls = run_main(['--talker', TALKER, '--local-id', '45'],
                                  rows=(TEXT_ROW,), cache_dir=self.tmp)
        self.assertIn('不是图片', data['reason'])
        self.assertEqual(calls['order'], [], '不是图片就不该建索引')

    def test_an_unknown_id_says_the_message_is_not_there(self):
        _, data, calls = run_main(['--talker', TALKER, '--local-id', '9999'], cache_dir=self.tmp)
        self.assertIn('没有编号 #9999', data['reason'])
        self.assertEqual(calls['order'], [])

    def test_a_bad_config_is_an_error_not_a_reason(self):
        # "配置错了"与"这张图取不到"要分开：前者要用户去修，后者不用
        out = io.StringIO()
        with patch.object(ri, '_utils') as fake_utils, \
             patch.object(sys, 'argv', ['read_image.py', '--talker', TALKER, '--local-id', '44']), \
             contextlib.redirect_stdout(out):
            fake_utils.load_config.return_value = {}
            fake_utils.decrypt_lock.side_effect = lambda v: v
            code = ri.main()
        data = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(code, 1)
        self.assertFalse(data['success'])
        self.assertIn('ntDbPath', data['error'])

    def test_cache_is_per_conversation(self):
        a = ri._cache_path('wxid_a', 7)
        b = ri._cache_path('wxid_b', 7)
        self.assertNotEqual(a, b, '不同会话的 local_id 会重号')
        self.assertNotEqual(ri._cache_path('wxid_a', 7), ri._cache_path('wxid_a', 8))

    def test_a_cached_file_without_bytes_counts_as_absent(self):
        path = ri._cache_path(TALKER, 1)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'success': True, 'reason': '没取到'}, fh)
        self.assertIsNone(ri._read_cache(path), '没取到的记录不该被当成命中')


if __name__ == '__main__':
    unittest.main()
