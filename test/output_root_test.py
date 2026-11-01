"""输出目录必须可写：`_utils.output_root()` 的契约。

背景（2026-11-01 实测）：全局安装时包目录属于 root，输出写进包里会直接
`PermissionError: [Errno 13] Permission denied: '/usr/lib/node_modules/weflow-cli/output'`。
Windows 上全局包在用户目录，所以只在类 Unix 暴露——"本地通过、装到 Linux 就崩"的典型。
"""
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import _utils  # noqa: E402


class OutputRootTests(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get('WEFLOW_OUTPUT_DIR')

    def tearDown(self):
        if self.saved is None:
            os.environ.pop('WEFLOW_OUTPUT_DIR', None)
        else:
            os.environ['WEFLOW_OUTPUT_DIR'] = self.saved

    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'nested', 'out')
            os.environ['WEFLOW_OUTPUT_DIR'] = target
            got = _utils.output_root()
            self.assertEqual(got, target, '显式指定的目录必须原样使用')
            self.assertTrue(os.path.isdir(got), '目录应被创建')

    def test_default_is_writable(self):
        os.environ.pop('WEFLOW_OUTPUT_DIR', None)
        got = _utils.output_root()
        self.assertTrue(os.path.isdir(got), '默认目录应存在')
        probe = os.path.join(got, '.write_probe_test')
        with open(probe, 'w', encoding='utf-8') as fh:
            fh.write('x')
        os.unlink(probe)

    def test_biz_daily_output_root_uses_it(self):
        """biz_daily 的 OUTPUT_ROOT 必须落在 output_root() 之下（而不是脚本目录）。"""
        import biz_daily
        base = pathlib.Path(_utils.output_root())
        self.assertEqual(pathlib.Path(biz_daily.OUTPUT_ROOT).parent, base,
                         'OUTPUT_ROOT 应是 <output_root>/biz-daily')
        # 抓取缓存同理：不能落在 scripts/ 下
        self.assertNotIn('scripts', pathlib.Path(biz_daily.FETCH_CACHE_DIR).parts)

    def test_pipeline_scripts_share_the_same_root(self):
        """整条日报流水线的脚本必须用**同一个**可写根，否则会出现
        "biz_daily 写到用户目录、generate_html 去包里找" 的分裂
        （2026-11-01 在 Linux 上实测：generate_html / enrich_backlinks /
        create_reading_notes 三个步骤全部 exit=1，只打一句 [FAIL]）。"""
        import generate_html
        import enrich_backlinks
        import create_reading_notes
        import auto_tag

        base = pathlib.Path(_utils.output_root())
        for name, value in (
            ('generate_html.SOURCE_ROOT', generate_html.SOURCE_ROOT),
            ('enrich_backlinks.DEFAULT_SOURCE', enrich_backlinks.DEFAULT_SOURCE),
            ('create_reading_notes.DEFAULT_SOURCE', create_reading_notes.DEFAULT_SOURCE),
            ('auto_tag.DEFAULT_SOURCE', auto_tag.DEFAULT_SOURCE),
        ):
            self.assertEqual(pathlib.Path(value), base / 'biz-daily', f'{name} 应在 output_root() 之下')
        self.assertEqual(pathlib.Path(create_reading_notes.DEFAULT_VAULT), base / 'wechat-vault')

    def test_imported_chats_uses_the_same_root(self):
        """导入存储的**读取方**必须和写入方同根。

        TS 侧写 `resolveOutputRoot()/imports/qq`，Python 侧之前写死 cwd 相对的
        `output/imports/qq/sessions`——装到 Linux 后写入在用户目录、读取在 cwd，
        导入的会话会"看不见"（2026-11-01 实测）。
        """
        import imported_chats
        os.environ.pop('WEFLOW_IMPORT_ROOT', None)
        got = pathlib.Path(imported_chats.sessions_dir())
        base = pathlib.Path(_utils.output_root())
        self.assertEqual(got, base / 'imports' / 'qq' / 'sessions')

    def test_import_root_env_still_wins(self):
        """WEFLOW_IMPORT_ROOT 仍然是显式覆盖（测试与自定义布局靠它）。"""
        import imported_chats
        os.environ['WEFLOW_IMPORT_ROOT'] = '/tmp/explicit-import-root'
        try:
            self.assertEqual(pathlib.Path(imported_chats.sessions_dir()),
                             pathlib.Path('/tmp/explicit-import-root') / 'sessions')
        finally:
            os.environ.pop('WEFLOW_IMPORT_ROOT', None)


if __name__ == '__main__':
    unittest.main()
