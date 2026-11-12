"""日报的 `images/` 默认删除（`dailyKeepImages` / `--keep-images` 才保留）。

背景（2026-11-12 实测）：`output/biz-daily/<日期>/images/` 每天 0.6–1.3G
（3,600+ 个文件，含 9M 的 GIF），11 天吃掉 9G，在 59G 的机器上把根分区填满过一次
（100%，日报中途 EXIT=1）。而 `index.html` 与所有 md **都不引用**它——md 里的图片
是 `mmbiz.qpic.cn` 的远程 URL；只有单篇阅读视图 `article.html` 经 `.image_map.json`
用到。所以默认删、留个开关。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'test'))

from _tmpenv import mkdtemp  # noqa: E402
import _utils as U  # noqa: E402


class KeepDailyImagesTests(unittest.TestCase):
    def test_default_is_delete(self):
        self.assertFalse(U.keep_daily_images(config={}, cli_flag=False))
        self.assertFalse(U.keep_daily_images(config={'dailyKeepImages': 'false'}, cli_flag=False))

    def test_config_true_keeps(self):
        for value in ('true', 'True', '1', 'yes', 'on'):
            self.assertTrue(U.keep_daily_images(config={'dailyKeepImages': value}),
                            f'dailyKeepImages={value!r} 应当被认成"保留"')

    def test_config_false_or_garbage_deletes(self):
        for value in ('false', '', None, '0', 'nope'):
            self.assertFalse(U.keep_daily_images(config={'dailyKeepImages': value}),
                             f'dailyKeepImages={value!r} 应当被认成"删"')

    def test_cli_flag_wins_over_config(self):
        self.assertTrue(U.keep_daily_images(config={'dailyKeepImages': 'false'}, cli_flag=True))


class CleanupDayImagesTests(unittest.TestCase):
    def setUp(self):
        self.day = pathlib.Path(mkdtemp('wf-images-'))
        self.images = self.day / 'images'
        self.images.mkdir(parents=True)
        for i in range(3):
            (self.images / f'{i}.png').write_bytes(b'x' * 1024)
        (self.day / '.image_map.json').write_text('{"a": "images/0.png"}', encoding='utf-8')
        (self.day / 'article.html').write_text('<img src="images/0.png">', encoding='utf-8')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.day, ignore_errors=True)

    def test_deletes_images_but_keeps_map_and_html(self):
        stats = U.cleanup_day_images(self.day, config={'dailyKeepImages': 'false'})
        self.assertEqual(stats['removed'], 3)
        self.assertFalse((self.day / 'images').exists(), 'images/ 应当被删掉')
        self.assertTrue((self.day / '.image_map.json').exists(), '映射表要留着（很小）')
        self.assertTrue((self.day / 'article.html').exists(), '阅读视图本身要留着')

    def test_reports_bytes_freed(self):
        stats = U.cleanup_day_images(self.day, config={})
        self.assertEqual(stats['bytes'], 3 * 1024, '要能报出释放了多少，否则这件事永远无声')

    def test_keep_flag_leaves_everything(self):
        stats = U.cleanup_day_images(self.day, cli_flag=True, config={})
        self.assertTrue(stats['kept'])
        self.assertTrue((self.day / 'images').exists())
        self.assertEqual(len(list(self.images.iterdir())), 3)

    def test_missing_dir_is_not_an_error(self):
        import shutil
        shutil.rmtree(self.images)
        stats = U.cleanup_day_images(self.day, config={})
        self.assertTrue(stats.get('missing'))
        self.assertEqual(stats['removed'], 0)


if __name__ == '__main__':
    unittest.main()
