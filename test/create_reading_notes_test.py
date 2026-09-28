"""`create_reading_notes.py`：笔记模板里的 **dataview 查询**与它的重写。

这一支盯的是一类**看不见的坏**：查询是写在笔记正文里的一段代码，它错了不会让任何
代码报错——Obsidian 那边只是显示一块代码、或者显示一张空表。2026-09-27 实测到三处：

1. 阅读笔记 `SORT date DESC`，而笔记里**没有 `date` 字段**（是 `published`）→ 排不出来；
2. 阅读笔记的相关文章里**会列出它自己**（没有排除 this.file）；
3. 日记 `WHERE created = date(...)`，而 `created` 是**生成日**——一次回填把 25,676 篇的
   `created` 全写成 2026-09-26（实测），于是 175 天的日记查出来是空的、
   唯独生成那天把两万多篇一次列出来。

前两条影响 25,676 篇阅读笔记，第三条影响 176 篇日记，全部 100% 命中。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location('create_reading_notes', SCRIPTS / 'create_reading_notes.py')
crn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crn)

ARTICLE = ('---\ntitle: "某篇"\nsource: "某号"\ndate: 2026-05-20\ntopic: AI\n'
           'relevance: 中\ntags: [AI]\nurl: "http://x"\n---\n\n## AI 摘要\n\n一段摘要。\n')


def dataview_block(text: str) -> str:
    start = text.find('```dataview')
    end = text.find('```', start + 3)
    return text[start:end + 3] if start >= 0 else ''


class GeneratedNoteTests(unittest.TestCase):
    def make(self, tmp):
        src = Path(tmp) / 'biz-daily' / '2026-05-20' / 'AI'
        src.mkdir(parents=True)
        (src / '某号-某篇.md').write_text(ARTICLE, encoding='utf-8')
        vault = Path(tmp) / 'vault'
        crn.create_reading_note(src / '某号-某篇.md', vault, '2026-05-20')
        return vault

    def test_相关文章按_published_排序_不引用不存在的_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self.make(tmp)
            note = next((vault / '002_Literature').rglob('*.md'))
            block = dataview_block(note.read_text(encoding='utf-8'))
        self.assertIn('SORT published DESC', block)
        self.assertNotIn('SORT date', block, '笔记里没有 date 字段，按它排是排不出来的')

    def test_相关文章不列出自己(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self.make(tmp)
            note = next((vault / '002_Literature').rglob('*.md'))
            block = dataview_block(note.read_text(encoding='utf-8'))
        self.assertIn('this.file.name', block, '不排除自己的话，每篇的相关文章第一条都是它自己')

    def test_查询里的主题是替换过的_没有大括号残留(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self.make(tmp)
            note = next((vault / '002_Literature').rglob('*.md'))
            text = note.read_text(encoding='utf-8')
        self.assertNotIn('{topic}', text, 'str.format 不会回头扫替换进去的值，得先自己 format 好')
        self.assertIn('contains(string(hasTopic), "AI")', dataview_block(text))


class RefreshTests(unittest.TestCase):
    """重写已有笔记：模板改了以后，老笔记不会自己更新（`create_reading_note` 遇到已存在就 skip）。"""

    def build(self, tmp):
        vault = Path(tmp) / 'vault'
        (vault / '001_Daily').mkdir(parents=True)
        (vault / '002_Literature' / 'WeChat' / '2026-05-20').mkdir(parents=True)
        note = vault / '002_Literature' / 'WeChat' / '2026-05-20' / '2026-05-20-某篇.md'
        note.write_text(
            '---\ntitle: "某篇"\nhasTopic: [[AI]]\npublished: 2026-05-20\n---\n\n'
            '## 正文\n\n这段是文章内容，重写查询不许动它。\n\n'
            '## 📊 相关文章\n\n```dataview\nTABLE rating\nFROM "002_Literature"\n'
            'WHERE contains(hasTopic, "AI")\nSORT date DESC\nLIMIT 10\n```\n',
            encoding='utf-8')
        (vault / '001_Daily' / '2026-05-20.md').write_text(
            '---\ntitle: 日记\n---\n\n## 🔗 概念连接\n\n```dataview\nLIST\n'
            'FROM "002_Literature"\nWHERE created = date(2026-05-20)\n```\n',
            encoding='utf-8')
        return vault, note

    def test_把旧查询重写成当前那份(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, note = self.build(tmp)
            result = crn.refresh_dataview_blocks(str(vault))
            self.assertEqual(len(result['rewritten']), 2, '阅读笔记与日记各一份')
            self.assertIn('SORT published DESC', note.read_text(encoding='utf-8'))

    def test_日记用_published_判日期_而不是生成日(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ = self.build(tmp)
            crn.refresh_dataview_blocks(str(vault))
            daily = (vault / '001_Daily' / '2026-05-20.md').read_text(encoding='utf-8')
            self.assertIn('published = date("2026-05-20")', daily)
            self.assertNotIn('created = date', daily,
                             'created 是生成日；一次回填会让全库都是同一天')

    def test_hasTopic_是嵌套列表时_主题值不许带引号和方括号(self):
        """**这条是补的，因为第一版漏了它。**

        `hasTopic: [[AI]]` 被 YAML 解析成**嵌套列表** `[['AI']]`。第一版取值写的是
        `str(整个列表).strip('[]')`，得到的主题值是 `"'[AI]'"`——带着引号和方括号被塞进
        查询，**不报错**，表格永远空着。实测：25,669 / 25,676 篇被写坏。

        当时的验收只查了"旧查询没了"（`SORT date` 不在），**没查新查询里的值对不对**。
        这条就是那个缺口。
        """
        with tempfile.TemporaryDirectory() as tmp:
            vault, note = self.build(tmp)
            crn.refresh_dataview_blocks(str(vault))
            text = note.read_text(encoding='utf-8')
        self.assertIn('contains(string(hasTopic), "AI")', text)
        for junk in ("'[", "]'", "[['", '"['):
            self.assertNotIn(junk, text, '主题值里混进了 %r' % junk)

    def test_只动那个代码块_别的一个字不改(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, note = self.build(tmp)
            before = note.read_text(encoding='utf-8')
            crn.refresh_dataview_blocks(str(vault))
            after = note.read_text(encoding='utf-8')
            self.assertIn('这段是文章内容，重写查询不许动它。', after)
            head_before = before.split('```dataview')[0]
            head_after = after.split('```dataview')[0]
            self.assertEqual(head_before, head_after, '代码块之前的内容必须逐字不变')

    def test_没有查询块的不动_并且计数报出来(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ = self.build(tmp)
            orphan = vault / '002_Literature' / 'WeChat' / '2026-05-20' / '没有块.md'
            orphan.write_text('---\nhasTopic: [[AI]]\npublished: 2026-05-20\n---\n\n正文\n',
                              encoding='utf-8')
            result = crn.refresh_dataview_blocks(str(vault))
            self.assertIn('没有块.md', result['noBlock'])
            self.assertNotIn('没有块.md', result['rewritten'])
            self.assertNotIn('dataview', orphan.read_text(encoding='utf-8'))

    def test_主题为空的也照改_只是单独计数(self):
        """空主题**不是**"判不出来"——那 7 篇本来就空，查询里写着 `hasTopic, ""`。

        跳过它们，等于让它们继续留着 `SORT date`（排不出来的那个写法）。
        "语法对、结果如实为空"比"语法错、看不出为什么"要好。
        """
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / 'vault'
            (vault / '002_Literature').mkdir(parents=True)
            odd = vault / '002_Literature' / '无主题.md'
            odd.write_text(
                '---\ntitle: 无主题\n---\n\n```dataview\nTABLE rating\n'
                'FROM "002_Literature"\nWHERE contains(hasTopic, "")\nSORT date DESC\n```\n',
                encoding='utf-8')
            result = crn.refresh_dataview_blocks(str(vault))
            text = odd.read_text(encoding='utf-8')
        self.assertIn('无主题.md', result['topicless'], '要单独报出来，让人看得见这几篇没有主题')
        self.assertIn('SORT published DESC', text, '查询本身要修好')
        self.assertNotIn('SORT date', text)
        self.assertIn('contains(string(hasTopic), "")', text, '主题是空的，查询里就该是空的')

    def test_读不出来的文件不静默(self):
        # 读失败（编码/权限）要计数报出来，不能悄悄少一篇
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / 'vault'
            lit = vault / '002_Literature'
            lit.mkdir(parents=True)
            bad = lit / '坏文件.md'
            bad.write_bytes(b'---\n\xff\xfe\ntitle: x\n---\n')
            result = crn.refresh_dataview_blocks(str(vault))
            self.assertEqual(len(result['skipped']) + len(result['rewritten']) + len(result['noBlock']), 1)

    def test_再跑一次不会重复改(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, note = self.build(tmp)
            crn.refresh_dataview_blocks(str(vault))
            first = note.read_text(encoding='utf-8')
            result = crn.refresh_dataview_blocks(str(vault))
            self.assertEqual(note.read_text(encoding='utf-8'), first, '幂等：第二次不该再动')
            self.assertEqual(len(result['rewritten']), 0)


class VaultLayoutAgreementTests(unittest.TestCase):
    """`vault init`（CLI 里的 `dirs`）与 `create_reading_notes.VAULT_DIRS` 必须对得上。

    **这个形状在本仓库出现过两次。** 第一次是 `007_Wiki/Concepts`：模板声明了它、实际写在
    顶层的 `Wiki/Concepts`，于是每次 init 都建出一个永远空着的目录。第二次是
    `Wiki/Entities` 与 `Wiki/Topics`：CLI 声明了、README 还把它们当成已有的介绍给用户，
    而没有任何代码写它们。两边的共同点是**声明与写入分居两处，而且不报错**。
    """

    ROOT = Path(__file__).resolve().parents[1]

    def cli_dirs(self):
        text = (self.ROOT / 'bin' / 'weflow-cli.ts').read_text(encoding='utf-8')
        start = text.index('const dirs = [')
        end = text.index(']', start)
        import re as _re
        return set(_re.findall(r"'([^']+)'", text[start:end]))

    def test_模板里声明的目录_cli_都必须建(self):
        missing = sorted(d for d in crn.VAULT_DIRS if d not in self.cli_dirs())
        self.assertEqual(missing, [], 'CLI 的 init 必须把笔记写入方期望的目录都建出来')

    def test_cli_不许声明没有人写的_Wiki_子目录(self):
        extra = sorted(d for d in self.cli_dirs()
                       if d.startswith('Wiki/') and d != 'Wiki/Concepts')
        self.assertEqual(extra, [],
                         '声明了就要有东西往里写；否则就是又一个永远空着的目录')

    def test_附件目录只有一个名字(self):
        # `app.json` 的 attachmentFolderPath 曾经写 `Assets`，而布局里建的是 `_attachments`
        # ——附件会被放进一个不存在的目录。这个字面量当时只出现一次，是个孤例。
        text = (self.ROOT / 'bin' / 'weflow-cli.ts').read_text(encoding='utf-8')
        import re as _re
        declared = _re.search(r"attachmentFolderPath:\s*'([^']+)'", text)
        self.assertIsNotNone(declared, '读不到 attachmentFolderPath')
        self.assertIn(declared.group(1), self.cli_dirs(),
                      '附件目录必须是布局里真的会建出来的那个')


class CleanSummariesTests(unittest.TestCase):
    """清洗已有笔记摘要里的微信界面残留。本地，不调模型。"""

    RESIDUE = '在小说阅读器读本章'

    def build(self, tmp):
        vault = Path(tmp)
        (vault / '002_Literature' / 'WeChat').mkdir(parents=True)
        (vault / 'Sources' / 'WeChat').mkdir(parents=True)
        note = '---\ntitle: "a"\n---\n\n## 📋 摘要\n\n正文。%s\n\n## 💡 核心观点\n\n- 别的。\n' % self.RESIDUE
        raw = '---\ntitle: "b"\n---\n\n## 📋 摘要\n\n原文。%s\n\n## 正文\n\n- 别的。\n' % self.RESIDUE
        (vault / '002_Literature' / 'WeChat' / 'a.md').write_text(note, encoding='utf-8')
        (vault / 'Sources' / 'WeChat' / 'b.md').write_text(raw, encoding='utf-8')
        return vault, note, raw

    def test_只扫阅读笔记那一层_素材一个字都不动(self):
        """**这是踩过的坑**：第一版写的是 `Path(vault).rglob('*.md')`，那会扫**整个库** ——
        实测把 `Sources/` 的 10,949 篇**原文**也洗了，而那些是素材，不是这条命令的职责。
        """
        with tempfile.TemporaryDirectory() as tmp:
            vault, _, _ = self.build(tmp)
            result = crn.clean_summaries(str(vault))
            note = (vault / '002_Literature' / 'WeChat' / 'a.md').read_text(encoding='utf-8')
            raw = (vault / 'Sources' / 'WeChat' / 'b.md').read_text(encoding='utf-8')
        self.assertEqual(len(result['cleaned']), 1, '只有笔记那一篇该被清')
        self.assertNotIn(self.RESIDUE, note, '笔记摘要里的残留要清掉')
        self.assertIn(self.RESIDUE, raw, '**素材一个字都不能动**')

    def test_只动摘要段_后面的章节不受影响(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _, _ = self.build(tmp)
            crn.clean_summaries(str(vault))
            note = (vault / '002_Literature' / 'WeChat' / 'a.md').read_text(encoding='utf-8')
        self.assertIn('## 💡 核心观点', note, '摘要段之后的章节要原样留着')
        self.assertIn('- 别的。', note)
        self.assertIn('---', note, 'frontmatter 的分隔线不能被吃掉')

    def test_判据是含界面短语而不是文本变了(self):
        """`strip_wx_ads` 末尾还有空白规整与 `.strip()`，所以**它几乎总会改点什么** ——
        拿"文本变了"当判据会把全库重写一遍（我测量时正好踩过：报出 25,676 篇全部命中）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / '002_Literature').mkdir(parents=True)
            (vault / '002_Literature' / 'clean.md').write_text(
                '---\ntitle: "c"\n---\n\n## 📋 摘要\n\n\n干净的一段。\n\n## 别的\n\n- x\n',
                encoding='utf-8')
            result = crn.clean_summaries(str(vault))
        self.assertEqual(result['cleaned'], [], '本来就干净的不许动')
        self.assertEqual(len(result['untouched']), 1)

    def test_幂等(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _, _ = self.build(tmp)
            crn.clean_summaries(str(vault))
            first = (vault / '002_Literature' / 'WeChat' / 'a.md').read_text(encoding='utf-8')
            again = crn.clean_summaries(str(vault))
            second = (vault / '002_Literature' / 'WeChat' / 'a.md').read_text(encoding='utf-8')
        self.assertEqual(again['cleaned'], [], '第二遍没有可清的了')
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
