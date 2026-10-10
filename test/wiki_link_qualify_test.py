# -*- coding: utf-8 -*-
"""**同库内消歧**：跨线同名的概念链接按名字带路径。

用户 2026-10-10 选的修法是"留在同一个 Obsidian 库里、给指向同名的链接加路径"
（另一条路是把两条线拆成两个库，不选）。所以：`[[DeepSeek]]` →
`[[Chat/Concepts/DeepSeek|DeepSeek]]`。

这一条失效的样子全是**静默**的：路径少拼一级 → "另一条线没有这个名字"永远成立 →
链接不带路径 → Obsidian 里那条裸链接又变成二义的，而没有任何东西会红。所以这里钉四件事：

1. 解析（`link_target_name`）—— 带路径的写法必须能被读回成同一个概念名，否则图谱少边；
2. 认兄弟目录（`sibling_concepts_dir`）—— **两级**路径那段逻辑，写成一级也要能被抓出来；
3. 重写（`qualify_links`）—— 只改跨线同名的、不碰 `## 来源`、可重跑、`--dry-run` 不写；
4. 体检（`ambiguousLinks`）—— 漏掉的那些要能被报出来（这是唯一的兜底）。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


u = load('_utils')
cw = load('compile_wiki')
wl = load('wiki_lint')


class ParseTests(unittest.TestCase):
    """**解析只此一份**：带路径的链接读回成同一个概念名。"""

    def test_带路径的链接读回概念名(self):
        for raw, expected in (
            ('Chat/Concepts/DeepSeek|DeepSeek', 'DeepSeek'),
            ('Chat/Concepts/DeepSeek', 'DeepSeek'),
            ('Wiki/Concepts/AI 工具|AI 工具', 'AI 工具'),
            ('DeepSeek', 'DeepSeek'),
            ('DeepSeek|显示成别的', 'DeepSeek'),
            ('Chat/Concepts/DeepSeek.md', 'DeepSeek'),
        ):
            self.assertEqual(u.link_target_name(raw), expected, raw)

    def test_带井号的概念名不许被当锚点切掉(self):
        """这个库里**真有**叫 `#GPT6` / `Erdős#1026` 的概念。

        顺手切 `#`（当 Obsidian 的锚点）会把它们变成另一个名字，指向它们的边**整条消失**
        ——实测少了 4 条边，而图里少一条边不报错（2026-10-10 当场量出来的）。
        """
        self.assertEqual(u.link_target_name('#GPT6'), '#GPT6')
        self.assertEqual(u.link_target_name('Erdős#1026'), 'Erdős#1026')
        self.assertEqual(u.link_target_name('Chat/Concepts/#GPT6|#GPT6'), '#GPT6')

    def test_只有带线路径的才算已消歧(self):
        self.assertTrue(u.is_line_qualified('Chat/Concepts/DeepSeek|DeepSeek'))
        self.assertTrue(u.is_line_qualified('Wiki/Concepts/DeepSeek'))
        self.assertFalse(u.is_line_qualified('DeepSeek'))
        # 卡片链接带路径但**不是概念目录**：那不是"已消歧"，走的是另一条路（`## 来源`）
        self.assertFalse(u.is_line_qualified('Sources/Chat/会话-甲.md'))

    def test_渲染只在另一条线也有这个名字时才带路径(self):
        self.assertEqual(u.concept_link_target('chat', '甲', {'甲'}),
                         'Chat/Concepts/甲|甲')
        self.assertEqual(u.concept_link_target('wiki', '甲', {'甲'}),
                         'Wiki/Concepts/甲|甲')
        # 只属于一条线 → 一个字节都不改（库里 4.9 万张页因此不用动）
        self.assertEqual(u.concept_link_target('chat', '只有聊天线有', set()), '只有聊天线有')

    def test_图谱读到的名字与裸链接一致(self):
        # 图谱的边靠这条：`[[Chat/Concepts/甲|甲]]` 必须与 `[[甲]]` 解析成同一个名字
        body = '## 相关概念\n\n- [[甲]]\n- [[Chat/Concepts/甲|甲]]\n- [[Wiki/Concepts/乙]]\n'
        self.assertEqual([n for _s, n in wl.links_by_section(body)], ['甲', '甲', '乙'])


class SiblingDirTests(unittest.TestCase):
    """**两级路径**：`<库>/<线>/Concepts` 的兄弟是 `<库>/<另一线>/Concepts`。"""

    def build(self, tmp):
        root = Path(tmp) / 'vault'
        (root / 'Wiki' / 'Concepts').mkdir(parents=True)
        (root / 'Chat' / 'Concepts').mkdir(parents=True)
        return root

    def test_两个方向都认(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build(tmp)
            # 返回的是**路径字符串**（`_utils` 通篇 os.path 风格）
            self.assertEqual(Path(u.sibling_concepts_dir(root / 'Wiki' / 'Concepts')),
                             root / 'Chat' / 'Concepts')
            self.assertEqual(Path(u.sibling_concepts_dir(root / 'Chat' / 'Concepts')),
                             root / 'Wiki' / 'Concepts')

    def test_路径不认识的目录没有兄弟(self):
        # `--output` 指到库外时，别凭空猜一个兄弟目录出来
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / 'elsewhere'
            outside.mkdir()
            self.assertIsNone(u.sibling_concepts_dir(outside))

    def test_另一条线不存在时回None而不是拼一个不存在的路径(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build(tmp)
            (root / 'Chat' / 'Concepts').rmdir()
            self.assertIsNone(u.sibling_concepts_dir(root / 'Wiki' / 'Concepts'))

    def test_写成一级路径会被认出来_不会静默当成没有另一条线(self):
        """**这条钉的是那个坑本身**：`<库>/Chat/Wiki/Concepts` 是拼错一级的产物。

        旧实现（2026-09-27 那次）就是把库根写成了一级，于是候选目录恒不存在、
        "另一条线没有这个名字"永远成立，而**没有任何东西会红**。这里要求：
        认不出来的路径必须回 `None`（= 调用方拒绝改），而不是回一个错的目录。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'vault'
            # 故意造一个"一级错"的结构：库根下直接就是 Chat/Concepts（没有 Wiki 那一层）
            (root / 'Chat' / 'Concepts').mkdir(parents=True)
            self.assertIsNone(u.sibling_concepts_dir(root / 'Chat' / 'Concepts'),
                              '库根下只有一条线时，另一条线不存在 —— 必须回 None')


class QualifyTests(unittest.TestCase):
    """`qualify_links`：只改跨线同名的、不碰 `## 来源`、可重跑。"""

    COLLIDE = '共有'
    ONLY_CHAT = '只在聊天线'

    def build(self, tmp):
        root = Path(tmp) / 'vault'
        wiki = root / 'Wiki' / 'Concepts'
        chat = root / 'Chat' / 'Concepts'
        for d in (wiki, chat):
            d.mkdir(parents=True)
        # 两条线都有 `共有`；`只在聊天线` 只有聊天线有
        (wiki / '共有.md').write_text(
            '# 共有\n\n## 相关概念\n\n- [[只在聊天线]]\n- [[另一个]]\n\n## 来源\n\n- [[某张卡]] — 标题\n',
            encoding='utf-8')
        (wiki / '另一个.md').write_text('# 另一个\n\n## 相关概念\n\n- [[共有]]\n', encoding='utf-8')
        (chat / '共有.md').write_text('# 共有\n\n## 相关概念\n\n- [[只在聊天线]]\n', encoding='utf-8')
        # 聊天线里**指向** `共有` 的那张页：它该被改成聊天线的路径
        (chat / f'{self.ONLY_CHAT}.md').write_text(
            '# x\n\n## 相关概念\n\n- [[共有]]\n', encoding='utf-8')
        (root / 'Wiki' / '00-Overview.md').write_text(
            '| # | 概念 | 篇 |\n|---|---|---|\n| 1 | [[共有]] | 3 |\n', encoding='utf-8')
        (root / 'Chat' / '00-Overview.md').write_text(
            '| # | 概念 | 篇 |\n|---|---|---|\n| 1 | [[共有]] | 1 |\n', encoding='utf-8')
        (root / 'Sources' / 'Chat').mkdir(parents=True)
        (root / 'Sources' / 'Chat' / '会话-甲.md').write_text(
            '## 主题与人物\n\n### 话题\n\n- [[共有]] — 说明\n', encoding='utf-8')
        return root, wiki, chat

    def test_两条线各按自己的线限定(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, wiki, chat = self.build(tmp)
            result = cw.qualify_links(wiki)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['ambiguous'], 1, '只有 `共有` 是两条线都有的')
            self.assertIn('[[Wiki/Concepts/共有|共有]]', (wiki / '另一个.md').read_text(encoding='utf-8'))
            self.assertIn('[[Chat/Concepts/共有|共有]]',
                          (chat / f'{self.ONLY_CHAT}.md').read_text(encoding='utf-8'))
            self.assertIn('[[Wiki/Concepts/共有|共有]]',
                          (root / 'Wiki' / '00-Overview.md').read_text(encoding='utf-8'))
            self.assertIn('[[Chat/Concepts/共有|共有]]',
                          (root / 'Chat' / '00-Overview.md').read_text(encoding='utf-8'))
            self.assertIn('[[Chat/Concepts/共有|共有]]',
                          (root / 'Sources' / 'Chat' / '会话-甲.md').read_text(encoding='utf-8'))

    def test_只属于一条线的名字一个字节都不改(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, wiki, _chat = self.build(tmp)
            cw.qualify_links(wiki)
            text = (wiki / '共有.md').read_text(encoding='utf-8')
            self.assertIn('- [[只在聊天线]]', text, '只有一条线有的名字，裸写法本来就确定')
            self.assertIn('- [[另一个]]', text)

    def test_来源段不许动(self):
        """`## 来源` 的链接指的是**卡片**，改成概念页是另一回事。"""
        with tempfile.TemporaryDirectory() as tmp:
            _root, wiki, _chat = self.build(tmp)
            # 把来源行改成跨线同名，看它会不会被动
            page = wiki / '共有.md'
            page.write_text(page.read_text(encoding='utf-8').replace('- [[某张卡]]', '- [[共有]]'),
                            encoding='utf-8')
            cw.qualify_links(wiki)
            text = page.read_text(encoding='utf-8')
            head, _, tail = text.partition('## 来源')
            self.assertIn('[[共有]]', tail, '来源段里那条不该被改')
            self.assertNotIn('Concepts/共有', tail)

    def test_幂等(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, wiki, _chat = self.build(tmp)
            first = cw.qualify_links(wiki)
            second = cw.qualify_links(wiki)
            self.assertGreater(first['changed'], 0)
            self.assertEqual(second['changed'], 0, '已经带路径的不该再改一遍')

    def test_空跑不写文件(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, wiki, _chat = self.build(tmp)
            before = (wiki / '另一个.md').read_text(encoding='utf-8')
            result = cw.qualify_links(wiki, dry_run=True)
            self.assertTrue(result['dryRun'])
            self.assertGreater(result['changed'], 0, '空跑也要报出会改几条')
            self.assertEqual((wiki / '另一个.md').read_text(encoding='utf-8'), before)

    def test_库外目录拒绝改_并说明原因(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / 'elsewhere'
            outside.mkdir()
            result = cw.qualify_links(outside)
            self.assertFalse(result['ok'], '认不出线就别改 —— 猜出来的线会把链接指到错的目录')
            self.assertIn('不猜', result['reason'])


class LintReportsAmbiguousTests(unittest.TestCase):
    """体检报"二义的链接" —— 这是漏改时的唯一兜底。"""

    def page(self, stem, body, dir_='Wiki/Concepts', title=None):
        return {'stem': stem, 'title': title or stem, 'dir': dir_,
                'body': body, 'aliases': [], 'topic': '', 'links': []}

    def both_lines(self, wiki_body):
        """两条线各一张 `共有`，外加文章线上指向它的 `甲`。"""
        return [self.page('甲', wiki_body, 'Wiki/Concepts'),
                self.page('共有', '# 共有\n', 'Wiki/Concepts', title='共有'),
                self.page('共有', '# 共有\n', 'Chat/Concepts', title='共有')]

    def test_裸的二义链接被报出来(self):
        report = wl.inspect(self.both_lines('## 相关概念\n\n- [[共有]]\n'),
                            lambda name: True)
        self.assertEqual(report['crossLineSameName'], ['共有'])
        self.assertEqual([r['target'] for r in report['ambiguousLinks']], ['共有'])

    def test_带了路径就不报(self):
        report = wl.inspect(self.both_lines('## 相关概念\n\n- [[Chat/Concepts/共有|共有]]\n'),
                            lambda name: True)
        self.assertEqual(report['ambiguousLinks'], [], '已经消歧的链接不是问题')

    def test_只属于一条线的名字不报(self):
        pages = [self.page('甲', '## 相关概念\n\n- [[只有一条线有]]\n'),
                 self.page('只有一条线有', '# x\n')]
        report = wl.inspect(pages, lambda name: True)
        self.assertEqual(report['crossLineSameName'], [])
        self.assertEqual(report['ambiguousLinks'], [])


class LinkNamerTests(unittest.TestCase):
    def test_认得出线就带路径_认不出就裸名字(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'vault'
            (root / 'Wiki' / 'Concepts').mkdir(parents=True)
            (root / 'Chat' / 'Concepts').mkdir(parents=True)
            (root / 'Wiki' / 'Concepts' / '共有.md').write_text('# x\n', encoding='utf-8')
            (root / 'Chat' / 'Concepts' / '共有.md').write_text('# x\n', encoding='utf-8')
            (root / 'Chat' / 'Concepts' / '只在聊天线.md').write_text('# x\n', encoding='utf-8')

            chat = u.LinkNamer.for_out_dir(root / 'Chat' / 'Concepts')
            self.assertEqual(chat('共有'), 'Chat/Concepts/共有|共有')
            self.assertEqual(chat('只在聊天线'), '只在聊天线')
            outside = u.LinkNamer.for_out_dir(Path(tmp) / 'elsewhere')
            self.assertEqual(outside('共有'), '共有', '认不出线就按老行为渲染裸名字')


if __name__ == '__main__':
    unittest.main()
