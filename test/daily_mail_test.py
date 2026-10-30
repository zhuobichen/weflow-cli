"""Regression tests for daily-mail rendering and incremental delivery."""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))

import daily_mail as dm


class CleanSummaryTests(unittest.TestCase):
    def test_只保留核心观点并去掉标签(self):
        raw = '【核心观点】Caldera 把仓库整理成主页。【关键细节】1. 前 Myspace CEO；2. 城市可视化。'
        self.assertEqual(dm.clean_summary(raw), 'Caldera 把仓库整理成主页。')

    def test_没有标签时整段保留(self):
        self.assertEqual(dm.clean_summary('普通摘要，没有标签。'), '普通摘要，没有标签。')

    def test_空输入不炸(self):
        self.assertEqual(dm.clean_summary(''), '')
        self.assertEqual(dm.clean_summary(None), '')

    def test_超长才截断并加省略号(self):
        long_text = '【核心观点】' + ('啊' * 300)
        out = dm.clean_summary(long_text, limit=50)
        self.assertEqual(len(out), 51)      # 50 + 省略号
        self.assertTrue(out.endswith('…'))

    def test_截断不留下悬空的标点(self):
        text = '【核心观点】' + ('词句，' * 40)
        out = dm.clean_summary(text, limit=21)
        self.assertFalse(out.rstrip('…')[-1] in '，；、')


class GithubLinkTests(unittest.TestCase):
    def test_提取并按仓库去重(self):
        body = '见 https://github.com/foo/bar 与 https://github.com/foo/bar/ ，还有 https://github.com/baz/qux?tab=readme'
        self.assertEqual(dm.github_links(body),
                         ['https://github.com/foo/bar', 'https://github.com/baz/qux'])

    def test_去掉句末标点(self):
        self.assertEqual(dm.github_links('地址 https://github.com/a/b.'), ['https://github.com/a/b'])

    def test_没有链接时返回空(self):
        self.assertEqual(dm.github_links('这里没有仓库地址'), [])

    def test_最多三个(self):
        body = ' '.join(f'https://github.com/o/r{i}' for i in range(6))
        self.assertEqual(len(dm.github_links(body)), 3)


class IncrementalMailTests(unittest.TestCase):
    def _article(self, title, url, topic='AI'):
        return {'title': title, 'url': url, 'source': '示例来源', 'time': '10:00', 'topic': topic}

    def test_scheduled_noon_and_night_jobs_enable_incremental_mail(self):
        scripts = pathlib.Path(__file__).resolve().parents[1] / 'scripts'
        task_source = (scripts / 'daily_task.ps1').read_text(encoding='utf-8')
        install_source = (scripts / 'install_tasks.ps1').read_text(encoding='utf-8')
        self.assertIn("$mailArgs = @($mailScript, '--date', $targetDay, '--incremental')", task_source)
        self.assertIn("if ($mode -eq 'noon') { $mailArgs += @('--label', 'noon') }", task_source)
        self.assertIn("@('noon', 'night', 'catchup', 'day')", task_source)
        self.assertIn("$targetDay = if ($mode -in @('noon', 'night')) { $today } else { $yesterday }", task_source)
        self.assertIn("$modeArgs = @('--date', $targetDay)", task_source)
        # A missed 23:30 run must not relabel the next morning as a night pass.
        self.assertIn("if ($mode -eq 'night' -and (Get-Date).Hour -lt 20) { $mode = 'catchup' }", task_source)
        self.assertIn("New-ScheduledTaskTrigger -Daily -At '13:00'", install_source)
        self.assertIn("New-ScheduledTaskTrigger -Daily -At '23:30'", install_source)
        self.assertIn("$action + ' night'", install_source)
        self.assertIn("$action + ' catchup'", install_source)

    def test_url_tracking_parameters_do_not_change_fingerprint(self):
        first = self._article('标题', 'https://mp.weixin.qq.com/s/example?utm_source=noon&scene=1')
        second = self._article('标题改写', 'https://mp.weixin.qq.com/s/example?utm_source=day&scene=2')
        self.assertEqual(dm.article_fingerprint(first), dm.article_fingerprint(second))

    def test_missing_url_uses_article_metadata(self):
        first = self._article('标题', '', topic='AI')
        same = self._article('标题', '', topic='AI')
        different = self._article('标题', '', topic='学术')
        self.assertEqual(dm.article_fingerprint(first), dm.article_fingerprint(same))
        changed_title = self._article('另一个标题', '', topic='AI')
        self.assertNotEqual(dm.article_fingerprint(first), dm.article_fingerprint(changed_title))

    def test_query_only_url_keeps_trailing_question_mark_out_of_fingerprint(self):
        first = self._article('标题', 'https://mp.weixin.qq.com/s/example?')
        second = self._article('标题', 'https://mp.weixin.qq.com/s/example')
        self.assertEqual(dm.article_fingerprint(first), dm.article_fingerprint(second))

    def test_sent_state_contains_only_fingerprints_and_round_trips(self):
        article = self._article('不可写入状态文件的标题', 'https://example.invalid/private-path')
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / '.mail-sent.json'
            fingerprint = dm.article_fingerprint(article)
            dm.save_sent_fingerprints(path, {fingerprint})
            self.assertEqual(dm.load_sent_fingerprints(path), {fingerprint})
            raw = path.read_text(encoding='utf-8')
            self.assertNotIn(article['title'], raw)
            self.assertNotIn(article['url'], raw)

    def test_corrupt_state_fails_open_to_full_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / '.mail-sent.json'
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(dm.load_sent_fingerprints(path), set())
            path.write_text(json.dumps({'version': 2, 'fingerprints': []}), encoding='utf-8')
            self.assertEqual(dm.load_sent_fingerprints(path), set())

    def test_tracking_only_wechat_query_is_removed(self):
        first = self._article('标题', 'https://mp.weixin.qq.com/s/example?scene=1&ascene=1')
        second = self._article('标题', 'https://mp.weixin.qq.com/s/example?chksm=private&clicktime=123')
        self.assertEqual(dm.article_fingerprint(first), dm.article_fingerprint(second))

    def test_incremental_filter_keeps_only_unseen_articles(self):
        first = self._article('午间', 'https://example.invalid/a')
        second = self._article('新增', 'https://example.invalid/b')
        sent = {dm.article_fingerprint(first)}
        self.assertEqual(dm.only_unsent_articles([first, second], sent), [second])

    def test_failed_smtp_does_not_mark_articles_as_sent(self):
        article = self._article('仅本地测试', 'https://example.invalid/failed')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
                dm, 'send', side_effect=RuntimeError('synthetic SMTP failure')):
            path = pathlib.Path(tmp) / '.mail-sent.json'
            with self.assertRaisesRegex(RuntimeError, 'synthetic SMTP failure'):
                dm.deliver_mail('user', 'auth', 'to', 'subject', 'text', 'html',
                                incremental=True, state_path=path, sent=set(), articles=[article])
            self.assertFalse(path.exists())

    def test_successful_smtp_marks_only_delivered_articles(self):
        article = self._article('仅本地测试', 'https://example.invalid/success')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(dm, 'send'):
            path = pathlib.Path(tmp) / '.mail-sent.json'
            dm.deliver_mail('user', 'auth', 'to', 'subject', 'text', 'html',
                            incremental=True, state_path=path, sent=set(), articles=[article])
            self.assertEqual(dm.load_sent_fingerprints(path), {dm.article_fingerprint(article)})

    def test_dry_run_uses_incremental_filter_without_smtp_or_state_write(self):
        first = self._article('午间内容', 'https://example.invalid/noon')
        second = self._article('晚间新增', 'https://example.invalid/evening')
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / 'output').mkdir()
            state_path = pathlib.Path(tmp) / '.articles.json'
            state_path.with_name('.mail-sent.json').write_text(
                json.dumps({'version': 1, 'fingerprints': [dm.article_fingerprint(first)]}), encoding='utf-8')
            patches = [
                mock.patch.object(dm, 'ROOT', pathlib.Path(tmp)),
                mock.patch.object(dm, 'articles_path', return_value=state_path),
                mock.patch.object(dm, 'load_articles', return_value=({}, [first, second])),
                mock.patch.object(dm, 'load_config', return_value={}),
                mock.patch.object(dm, 'get_api_key', return_value=''),
                mock.patch.object(dm, 'body_index', return_value={}),
                mock.patch.object(dm, 'smtp_settings', return_value=('user', 'auth')),
                mock.patch.object(dm, 'send'),
            ]
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7] as send_mock:
                with mock.patch.object(sys, 'argv', [
                        'daily_mail.py', '--date', '2026-10-29', '--incremental', '--no-digest', '--dry-run']):
                    dm.main()
            send_mock.assert_not_called()
            state = dm.load_sent_fingerprints(state_path.with_name('.mail-sent.json'))
            self.assertEqual(state, {dm.article_fingerprint(first)})

    def test_second_incremental_run_sends_only_new_article(self):
        first = self._article('午间内容', 'https://example.invalid/noon')
        second = self._article('晚间新增', 'https://example.invalid/evening')
        with tempfile.TemporaryDirectory() as tmp:
            state_path = pathlib.Path(tmp) / '2026-10-29' / '.articles.json'
            sent_bodies = []
            patches = [
                mock.patch.object(dm, 'articles_path', return_value=state_path),
                mock.patch.object(dm, 'load_articles', side_effect=[({}, [first]), ({}, [first, second])]),
                mock.patch.object(dm, 'load_config', return_value={}),
                mock.patch.object(dm, 'get_api_key', return_value=''),
                mock.patch.object(dm, 'body_index', return_value={}),
                mock.patch.object(dm, 'smtp_settings', return_value=('user', 'auth')),
                mock.patch.object(dm, 'send', side_effect=lambda *args: sent_bodies.append(args[4])),
            ]
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                for _ in range(2):
                    with mock.patch.object(sys, 'argv', [
                            'daily_mail.py', '--date', '2026-10-29', '--incremental', '--no-digest']):
                        dm.main()
            self.assertEqual(len(sent_bodies), 2)
            self.assertIn(first['title'], sent_bodies[0])
            self.assertIn(second['title'], sent_bodies[1])
            self.assertNotIn(first['title'], sent_bodies[1])

    def test_only_rendered_compact_topic_articles_are_marked_sent(self):
        articles = [self._article(f'文章{i}', f'https://example.invalid/{i}', topic='新闻')
                    for i in range(dm.OTHER_TOPIC_LIMIT + 2)]
        groups = {'新闻': articles}
        mailed = dm.articles_in_email(groups)
        self.assertEqual(len(mailed), dm.OTHER_TOPIC_LIMIT)
        self.assertEqual(mailed, articles[:dm.OTHER_TOPIC_LIMIT])

    def test_digest_topics_are_not_truncated_by_compact_limit(self):
        articles = [self._article(f'文章{i}', f'https://example.invalid/{i}', topic='AI')
                    for i in range(dm.OTHER_TOPIC_LIMIT + 2)]
        self.assertEqual(len(dm.articles_in_email({'AI': articles})), len(articles))


class RenderSectionsTests(unittest.TestCase):
    """**每个主题都必须真的出现在邮件里。**

    2026-10-20 出过一次：`render` 里「主题分区」那一段缩进错位（掉到 `for topic` 循环外），
    结果只有最后一个主题被渲染——邮件里只有学术、AI 整段消失，而且**不报错、篇数也对**
    （因为 total 是另一个变量）。用户报的"新邮件只有学术没有 AI"就是这个。
    """

    def _article(self, title, topic_source='某号'):
        return {'title': title, 'source': topic_source, 'time': '10:00', 'relevance': '中',
                'url': 'https://mp.weixin.qq.com/s/x', 'summary': '【核心观点】摘要。', 'tags': []}

    def test_two_topics_both_render(self):
        groups = {
            'AI': [self._article('AI 的文章甲'), self._article('AI 的文章乙')],
            '学术': [self._article('学术的文章丙')],
        }
        body, page, total = dm.render('2026-10-18', groups, {}, {}, {})
        self.assertEqual(total, 3)
        self.assertIn('>AI<', page, 'AI 主题头必须在')
        self.assertIn('>学术<', page, '学术主题头必须在')
        for title in ('AI 的文章甲', 'AI 的文章乙', '学术的文章丙'):
            self.assertIn(title, page, f'{title} 丢了')

    def test_every_article_becomes_one_card(self):
        groups = {'AI': [self._article(f'甲{i}') for i in range(5)],
                  '学术': [self._article(f'乙{i}') for i in range(4)]}
        body, page, total = dm.render('2026-10-18', groups, {}, {}, {})
        self.assertEqual(page.count('border:1px solid #eef0f3'), 9,
                         '卡片数必须等于篇数——少一张就说明有一篇没渲染')

    def test_empty_topic_is_skipped_not_rendered(self):
        groups = {'AI': [self._article('甲')], '学术': []}
        body, page, total = dm.render('2026-10-18', groups, {}, {}, {})
        self.assertIn('>AI<', page)
        self.assertNotIn('>学术<', page)
        self.assertEqual(total, 1)


if __name__ == '__main__':
    unittest.main()
