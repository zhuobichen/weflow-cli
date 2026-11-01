"""`pdf_notes.py`：PDF → 本机 OCR → 摘要与概念 → 知识卡（纯函数，不联网、不调 OCR）。

重点是**对下游的契约**：产出的卡要能被 `compile_wiki` 读出概念与描述，
格式与 `article-card` 对齐（`## AI 摘要` + `## 概念`）。

另外三条纪律在这里也要成立：
- `[[…]]` 只由我们的代码渲染（模型写的会被拆掉）；
- 摘要**由模型写**（PDF 没有原笔记可抄），所以 frontmatter 标 `summary_by: model`；
- OCR 引擎缺失要**如实报错**，不静默降级成"没正文"。
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pn = load('pdf_notes')
cw = load('compile_wiki')


class ParseTests(unittest.TestCase):
    def test_正常_JSON(self):
        raw = json.dumps({'summary': '这是一份讲 MCP 的文档。',
                          'concepts': [{'name': 'MCP 协议', 'desc': '把时间线开放给 Agent'}]},
                         ensure_ascii=False)
        note = pn.parse_note(raw)
        self.assertEqual(note['summary'], '这是一份讲 MCP 的文档。')
        self.assertEqual(note['concepts'],
                         [{'name': 'MCP 协议', 'desc': '把时间线开放给 Agent'}])

    def test_容忍代码块与前言(self):
        raw = '好：\n```json\n' + json.dumps(
            {'summary': '摘要', 'concepts': [{'name': '甲', 'desc': '在说甲'}]},
            ensure_ascii=False) + '\n```'
        self.assertIsNotNone(pn.parse_note(raw))

    def test_解不出来就_None_不猜(self):
        for raw in ['', '  ', '没有概念', '[]', '{"concepts": {}}', None, '{坏']:
            self.assertIsNone(pn.parse_note(raw), repr(raw)[:30])

    def test_没有摘要就当没解出来(self):
        # 摘要这一节是卡片正文的一部分，缺了卡片就是空壳
        raw = json.dumps({'concepts': [{'name': '甲', 'desc': '在说甲'}]}, ensure_ascii=False)
        self.assertIsNone(pn.parse_note(raw))

    def test_空概念列表是合法的(self):
        # "这份文档确实没有概念"与"模型没按形状答"是两件事，不许混
        note = pn.parse_note('{"summary": "短文", "concepts": []}')
        self.assertEqual(note['concepts'], [])

    def test_概念最多六条(self):
        many = [{'name': '概念%d' % i, 'desc': '描述%d' % i} for i in range(10)]
        raw = json.dumps({'summary': '摘要', 'concepts': many}, ensure_ascii=False)
        self.assertEqual(len(pn.parse_note(raw)['concepts']), 6)


class CardTests(unittest.TestCase):
    """卡片格式要对齐 `article-card`，`compile_wiki` 才吃得下。"""

    def _card(self, **kw):
        note = {'summary': '一份讲 MCP 的文档。',
                'concepts': [{'name': 'MCP 协议', 'desc': '把时间线开放给 Agent'}]}
        params = dict(title='某文档.pdf', note=note, source_name='某文档.pdf', pages=12,
                      body_len=4000, saved='2026-10-27 18:00', updated='2026-10-27 18:00',
                      via='local-ocr')
        params.update(kw)
        return pn.build_card(**params)

    def test_摘要与概念两节都在(self):
        _, body = self._card()
        self.assertIn('## AI 摘要', body)
        self.assertIn('## 概念', body)

    def test_概念链接是_我们渲染的(self):
        _, body = self._card()
        self.assertIn('- [[MCP 协议]] — 把时间线开放给 Agent', body)

    def test_摘要标成模型写的(self):
        # PDF 没有"原笔记"可抄，摘要必须重新生成——标出来免得它看起来像原文
        frontmatter, _ = self._card()
        self.assertEqual(frontmatter['summary_by'], 'model')

    def test_正文来源被记下来(self):
        frontmatter, _ = self._card(via='local-ocr')
        self.assertEqual(frontmatter['text_via'], 'local-ocr')
        self.assertEqual(frontmatter['pages'], 12)

    def test_下游能读出概念与描述(self):
        """**对下游的契约**：`compile_wiki` 要能读出概念名和描述。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'card.md'
            frontmatter, body = self._card()
            pn.write_with_frontmatter(str(path), frontmatter, body)
            _, card_body = pn.parse_frontmatter(path.read_text(encoding='utf-8'))
            links = cw.concept_links(card_body, topic='文档')
            self.assertTrue(links, 'compile_wiki 没读出概念')
            names = [name for name, _ in links]
            self.assertIn('MCP 协议', names)

    def test_没有概念时卡里不出现概念节(self):
        note = {'summary': '短文', 'concepts': []}
        _, body = self._card(note=note)
        self.assertNotIn('## 概念', body)
        self.assertIn('## AI 摘要', body)


class OcrContractTests(unittest.TestCase):
    """OCR 引擎那一侧：缺了要**如实报错**，不静默降级。"""

    def test_引擎缺失时报错(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(pn.ocr_available(tmp))

    def test_引擎齐全时认得出来(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / '.venv' / 'Scripts').mkdir(parents=True)
            (base / '.venv' / 'Scripts' / 'python.exe').write_text('', encoding='utf-8')
            (base / 'ocr2_pdf.py').write_text('', encoding='utf-8')
            (base / 'model-00001-of-00002.safetensors').write_text('x', encoding='utf-8')
            self.assertTrue(pn.ocr_available(tmp))

    def test_缺权重不算_可用(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / '.venv' / 'Scripts').mkdir(parents=True)
            (base / '.venv' / 'Scripts' / 'python.exe').write_text('', encoding='utf-8')
            (base / 'ocr2_pdf.py').write_text('', encoding='utf-8')
            self.assertFalse(pn.ocr_available(tmp), '缺权重却报可用')


class OcrCacheTests(unittest.TestCase):
    """OCR 缓存：**OCR 比模型调用贵几百倍**（一页约 1.5 秒，300 页的书七八分钟），
    所以重出卡（换提示词、某篇调用失败重跑）不该把识别重做一遍。

    这里钉住三件事：路径**跨进程稳定**（内置 `hash()` 对 str 每进程不同——那个 bug 让缓存
    永远命中不了、还每次留一个新目录）、参数变了算另一份、PDF 改了就重做。
    """

    def _engine(self, tmp) -> str:
        """造一个"目录结构齐全"的假引擎；脚本是空的，所以真跑必失败——正好用来区分有无命中。"""
        root = Path(tmp) / 'ocr2'
        (root / '.venv' / 'Scripts').mkdir(parents=True)
        (root / '.venv' / 'Scripts' / 'python.exe').write_text('', encoding='utf-8')
        (root / 'ocr2_pdf.py').write_text('', encoding='utf-8')
        return str(root)

    def _pdf(self, tmp, mtime=1000, name='doc.pdf') -> Path:
        path = Path(tmp) / name
        path.write_bytes(b'%PDF-1.4')
        os.utime(path, (mtime, mtime))
        return path

    def test_路径跨进程稳定(self):
        import subprocess
        code = ('import sys; sys.path.insert(0, %r); import pdf_notes as p, pathlib; '
                'print(p.ocr_cache_path(pathlib.Path(%r), %r, 200, 10, ""))'
                % (str(SCRIPTS), 'D:/x/doc.pdf', 'D:/root'))
        runs = [subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                               env={**os.environ, 'PYTHONHASHSEED': seed})
                for seed in ('1', '2')]
        self.assertTrue(runs[0].stdout.strip(), runs[0].stderr[-400:])
        self.assertEqual(runs[0].stdout.strip(), runs[1].stdout.strip(),
                         '两个进程算出的缓存路径不同 —— 又用了内置 hash()？')

    def test_参数不同算两份(self):
        pdf = Path('D:/x/doc.pdf')
        a = pn.ocr_cache_path(pdf, 'D:/root', dpi=200, window=10, pages='')
        b = pn.ocr_cache_path(pdf, 'D:/root', dpi=300, window=10, pages='')
        c = pn.ocr_cache_path(pdf, 'D:/root', dpi=200, window=5, pages='1-20')
        self.assertNotEqual(a, b, 'dpi 变了却复用同一份缓存')
        self.assertNotEqual(a, c, '窗口/页范围变了却复用同一份缓存')

    def test_缓存命中时不再跑_OCR(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._engine(tmp)
            pdf = self._pdf(tmp, mtime=1000)
            cached = pn.ocr_cache_path(pdf, root, 200, 10, '')
            cached.parent.mkdir(parents=True)
            cached.write_text('缓存里的正文', encoding='utf-8')
            os.utime(cached, (2000, 2000))
            body, via = pn.run_ocr(pdf, root, '', 200, 10, reuse=True)
            self.assertEqual(body, '缓存里的正文')
            self.assertEqual(via, 'local-ocr-cached')

    def test_PDF_改过就要重做(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._engine(tmp)
            pdf = self._pdf(tmp, mtime=3000)
            cached = pn.ocr_cache_path(pdf, root, 200, 10, '')
            cached.parent.mkdir(parents=True)
            cached.write_text('旧正文', encoding='utf-8')
            os.utime(cached, (1000, 1000))
            body, via = pn.run_ocr(pdf, root, '', 200, 10, reuse=True)
            self.assertNotEqual(body, '旧正文', 'PDF 更新了却还在用旧缓存')
            self.assertNotEqual(via, 'local-ocr-cached')

    def test_关掉复用就一定重跑(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._engine(tmp)
            pdf = self._pdf(tmp, mtime=1000)
            cached = pn.ocr_cache_path(pdf, root, 200, 10, '')
            cached.parent.mkdir(parents=True)
            cached.write_text('缓存里的正文', encoding='utf-8')
            os.utime(cached, (2000, 2000))
            _, via = pn.run_ocr(pdf, root, '', 200, 10, reuse=False)
            self.assertNotEqual(via, 'local-ocr-cached', '--no-reuse-ocr 没生效')


class OcrOnlyTests(unittest.TestCase):
    """`--ocr-only`：只识别、只写文字。**零出境、零花费**，所以不需要 --yes 与 API key。

    这条线的意义是把贵的、慢的、不出网的那一步单独跑完（可以挂着过夜），白天再出卡。
    """

    def test_文字目录与卡片目录分开(self):
        # 纯文本没有 [[概念]]，混进 output/*-notes 会让 compile_wiki 扫到一份"坏卡"
        self.assertNotEqual(pn.TEXT_ROOT, pn.OUTPUT_ROOT)
        self.assertTrue(pn.TEXT_ROOT.startswith('output/'))

    def test_ocr_only_分支在确认门之前(self):
        """必须在 `if not args.yes` **之前**返回，否则没有 key 就跑不了。"""
        src = (SCRIPTS / 'pdf_notes.py').read_text(encoding='utf-8')
        self.assertIn("'--ocr-only'", src)
        # 只看 main() 里的执行段：`args.ocr_only:` 那个分支的位置
        run_part = src[src.index('if args.ocr_only:'):]
        gate_at = src.index('if not args.yes:', src.index('def main'))
        self.assertLess(src.index('if args.ocr_only:'), gate_at,
                        '--ocr-only 分支排在 --yes 闸门之后，没有 key 时会先被拦住')

    def test_ocr_only_不需要_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / 'x.pdf'
            pdf.write_bytes(b'%PDF-1.4')
            env = {k: v for k, v in os.environ.items()
                   if k not in ('DEEPSEEK_API_KEY', 'WEFLOW_API_KEY')}
            r = subprocess.run(
                [sys.executable, str(SCRIPTS / 'pdf_notes.py'), '--pdf', str(pdf),
                 '--ocr', 'none', '--ocr-only', '--json'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                env={**env, 'PYTHONIOENCODING': 'utf-8'},
                cwd=str(Path(tmp)))
            self.assertNotIn('deepseekApiKey', (r.stdout + r.stderr),
                             '--ocr-only 竟然要求配置 key')
            self.assertNotIn('加 --yes 确认', (r.stdout + r.stderr),
                             '--ocr-only 竟然被 --yes 拦住')


try:
    import fitz  # noqa: F401  PyMuPDF：渲染用
    HAVE_FITZ = True
except Exception:
    HAVE_FITZ = False


@unittest.skipUnless(HAVE_FITZ, 'PyMuPDF(fitz) 未安装——渲染用例跳过，不是通过')
class RenderTests(unittest.TestCase):
    """页面渲染：**必须封顶 + 用 JPEG**。

    实测事故：聊天里一份 17 MB 宣传资料，页面 200 dpi 下是 13978×9548 px → PNG 26 MB、
    base64 35 MB，请求直接超限，三页全挂 `URLError`（而错误信息只说 URLError，
    完全看不出是"图太大"）。封顶到 3000 px + JPEG 后同一份 26 秒跑完。
    """

    def _pdf_with_huge_page(self, path):
        import fitz
        doc = fitz.open()
        # 2000×2000 pt 的页面 → 200dpi 下是 5555×5555 px，远超上限
        doc.new_page(width=2000, height=2000)
        doc.save(str(path))
        doc.close()

    def test_超大页面被压到上限内(self):
        import base64
        import io
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / 'huge.pdf'
            self._pdf_with_huge_page(pdf)
            pages = pn._render_pages(pdf, [0], dpi=200)
            self.assertEqual(len(pages), 1)
            raw = base64.b64decode(pages[0][1])
            self.assertLess(len(raw), 3 * 1024 * 1024, '压完了还是太大，请求会超限')
            img = Image.open(io.BytesIO(raw))
            self.assertLessEqual(max(img.size), pn.MAX_PAGE_PX, '最长边没封顶到 MAX_PAGE_PX')
            self.assertEqual(img.format, 'JPEG')

    def test_普通_A4_按_dpi_渲染(self):
        import base64
        import io
        from PIL import Image
        import fitz
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / 'a4.pdf'
            doc = fitz.open()
            doc.new_page(width=595, height=842)      # A4 in points
            doc.save(str(pdf))
            doc.close()
            pages = pn._render_pages(pdf, [0], dpi=200)
            img = Image.open(io.BytesIO(base64.b64decode(pages[0][1])))
            # A4 200dpi ≈ 1654×2339，不该被压
            self.assertGreater(img.size[0], 1600)
            self.assertLessEqual(max(img.size), pn.MAX_PAGE_PX)

    def test_页号从_1_开始(self):
        import fitz
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / 'two.pdf'
            doc = fitz.open()
            doc.new_page(); doc.new_page()
            doc.save(str(pdf))
            doc.close()
            self.assertEqual([n for n, _ in pn._render_pages(pdf, [0, 1], 150)], [1, 2])


class ChatPdfTests(unittest.TestCase):
    """`--from-chat`：聊天里别人发过的 PDF。

    实测本机 hardlink.db：1,978 条文件记录、**279 个 .pdf**，其中 **199 个**本地还在
    （80 个被微信清理）。这功能就是给这 199 个用的。

    这里只钉**纯逻辑**（过滤/排序/跳过已清理），不碰真库——真库要密钥，CI 里没有。
    """

    def test_缓存目录不在卡片目录里(self):
        # `compile_wiki --source output/pdf-notes` 会把那个目录下的 .md 当卡片扫；
        # 缓存落进去就成了一张"没有概念的坏卡"（实测踩过）
        self.assertFalse(pn.CACHE_ROOT.startswith(pn.OUTPUT_ROOT),
                         '云端 OCR 缓存落在了卡片目录里')
        self.assertFalse(pn.CACHE_ROOT.startswith(pn.TEXT_ROOT))

    def test_缓存路径含模型(self):
        a = pn.cloud_cache_path(Path('x.pdf'), 200, '', 'qwen3.5-ocr')
        b = pn.cloud_cache_path(Path('x.pdf'), 200, '', 'qwen-vl-ocr')
        self.assertNotEqual(a, b, '换模型却复用同一份缓存')

    def test_没有配置时报出原因而不是抛(self):
        # 没有 ntDbPath 时返回 (空, 说明)，调用方据此给用户一句人话
        files, note = pn.list_chat_pdfs()
        self.assertIsInstance(files, list)
        self.assertIsInstance(note, str)
        if not files:
            self.assertTrue(note, '取不到文件却什么也没说')

    def test_按_md5_去重(self):
        """微信给同一份文件存多个副本（文件名带 (1)(2)），必须按 md5 去重。

        实测本机：199 个本地 PDF 里 **23 个是重复副本**（22 组）。不去重就白花 23 次 OCR，
        还会生成 23 张内容一样的卡，概念页里同一来源出现两遍。
        """
        import collections
        src = (SCRIPTS / 'pdf_notes.py').read_text(encoding='utf-8')
        body = src[src.index('def list_chat_pdfs'):]
        body = body[:body.index('\ndef ')]
        self.assertIn('seen_md5', body, '没有按 md5 去重的痕迹')
        self.assertIn('duplicates', body, '去掉了却说不出跳过了多少')
        # 反空扫：md5 字段确实从 hardlink 行里取（字段名写错就永远不去重）
        self.assertIn("row.get('md5')", body)


class SampleBodyTests(unittest.TestCase):
    """长文档采样：**头 + 尾**，不硬截前 N 字。

    动机是实测的：一份 96 页论文，硬截 3000 字只覆盖前 3 页——摘要与方法在开头还行，
    但**结论、讨论、审稿回复全在最后**，被整段丢掉。
    """

    def test_短文档原样返回(self):
        body = '短文档' * 50
        self.assertEqual(pn.sample_body(body), body)

    def test_长文档头尾都在(self):
        body = 'HEAD' * 300 + 'MIDDLE' * 900 + 'TAIL' * 300
        out = pn.sample_body(body, limit=1000)
        self.assertIn('HEAD', out, '开头丢了')
        self.assertIn('TAIL', out, '结尾丢了 —— 结论就在结尾')
        self.assertNotIn('MIDDLE' * 50, out, '中间没被省略')

    def test_省略处写明多少字(self):
        body = 'x' * 5000
        out = pn.sample_body(body, limit=1000)
        self.assertIn('省略', out)
        self.assertRegex(out, r'省略 \d+ 字')

    def test_不超出上限太多(self):
        body = 'y' * 8000
        out = pn.sample_body(body, limit=1000)
        self.assertLessEqual(len(out), 1000 + 40, '采样后比上限长太多')

    def test_提示词用的是采样后的正文(self):
        body = 'HEAD' * 300 + 'X' * 9000 + 'TAIL' * 300
        prompt = pn.build_prompt('标题', body)
        self.assertIn('HEAD', prompt)
        self.assertIn('TAIL', prompt)
        self.assertLess(len(prompt), pn.MAX_PDF_CHARS + 2000)


class TopicTests(unittest.TestCase):
    """PDF 卡**不参与**公众号那六类主题（2026-10-27 定）。

    那六类（AI/学术/新闻/文学/投资/政治）是**文章**的分类口径。实测拿它去分 177 张聊天
    PDF 的结果：第一版把 145 张判成「学术」（志愿者招募、发票、备考手册全在内），
    加「其他」出口后又有 99 张落到「其他」——两个结果都不比"统一写文档"更有信息量。
    所以提示词**不问主题**，卡片统一 `topic: 文档`。
    """

    def test_提示词不问主题(self):
        prompt = pn.build_prompt('标题', '正文')
        self.assertNotIn('topic', prompt, '又把文章的分类塞回 PDF 的提示词了')

    def test_模型就算多给了主题也不采纳(self):
        # 模型偶尔会自作多情多给一个 topic；解出来只留摘要与概念
        raw = json.dumps({'topic': '学术', 'summary': '一份文档。',
                          'concepts': [{'name': '甲', 'desc': '在说甲'}]}, ensure_ascii=False)
        note = pn.parse_note(raw)
        self.assertNotIn('topic', note)
        self.assertEqual(note['summary'], '一份文档。')
        self.assertEqual(len(note['concepts']), 1)

    def test_卡片主题固定为文档(self):
        note = {'summary': '一份论文。', 'concepts': [{'name': '甲', 'desc': '在说甲'}]}
        fm, body = pn.build_card('某论文', note, '某论文.pdf', pages=3, body_len=900,
                                 saved='2026-10-27 20:00', updated='2026-10-27 20:00',
                                 via='local-ocr')
        self.assertEqual(fm['topic'], '文档')
        self.assertIn('## AI 摘要', body)
        self.assertIn('- [[甲]] — 在说甲', body)

    def test_空概念也能出卡(self):
        note = {'summary': '一份文档。', 'concepts': []}
        fm, body = pn.build_card('x', note, 'x.pdf', pages=1, body_len=300,
                                 saved='2026-10-27 20:00', updated='2026-10-27 20:00',
                                 via='embedded-text')
        self.assertEqual(fm['topic'], '文档')
        self.assertNotIn('## 概念', body)


class ChooseSourceTests(unittest.TestCase):
    """选路：**云端优先、本地兜底**（2026-10-27 按实测改过一次，原来本地优先）。

    依据：同一份 5 页 PDF，本地 270 s（54 s/页）vs 云端 6.1 s（1.2 s/页），44 倍，
    云端还多读出 608 字。顺序改在这里，一条条钉住：
    `auto` 的三种组合、强制模式**不偷偷换路**、以及换到云端时理由必须写明图片会出境。
    """

    def test_云端可用就用云端(self):
        source, why = pn.choose_source('auto', True, True)
        self.assertEqual(source, 'cloud', '云端可用却没优先，回到本地优先了？')
        self.assertIn('出境', why, '换到云端时理由里要写明图片会出境')

    def test_没有_key_才退回本地(self):
        source, why = pn.choose_source('auto', True, False)
        self.assertEqual(source, 'local')
        self.assertIn('不出网', why)

    def test_都没有退回内嵌文本(self):
        source, why = pn.choose_source('auto', False, False)
        self.assertEqual(source, 'embedded')
        self.assertIn('扫描件', why, '降级时要提示扫描件读不出')

    def test_强制_local_但没有引擎时报错而不是换云端(self):
        source, why = pn.choose_source('local', False, True)
        self.assertEqual(source, '', '指定 local 却悄悄用了云端')
        self.assertIn('--ocr local', why)

    def test_强制_cloud_但没有_key_时报错而不是回本地(self):
        source, why = pn.choose_source('cloud', True, False)
        self.assertEqual(source, '', '指定 cloud 却悄悄用了本地')
        self.assertIn('DashScope', why)

    def test_none_只抽内嵌文本(self):
        self.assertEqual(pn.choose_source('none', True, True)[0], 'embedded')

    def test_默认模型是_ocr_专用模型(self):
        self.assertIn('ocr', pn.DEFAULT_CLOUD_MODEL.lower())


class PageSpecTests(unittest.TestCase):
    def test_空表示全部(self):
        self.assertEqual(pn.parse_page_spec('', 3), [0, 1, 2])

    def test_区间(self):
        self.assertEqual(pn.parse_page_spec('2-3', 5), [1, 2])

    def test_单页与列表(self):
        self.assertEqual(pn.parse_page_spec('3', 5), [2])
        self.assertEqual(pn.parse_page_spec('1,3', 5), [0, 2])

    def test_越界被丢掉(self):
        self.assertEqual(pn.parse_page_spec('9-12', 5), [])


class ListTests(unittest.TestCase):
    def test_目录递归找_PDF(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'sub').mkdir()
            (base / 'a.pdf').write_bytes(b'%PDF-1.4')
            (base / 'sub' / 'b.pdf').write_bytes(b'%PDF-1.4')
            (base / 'note.txt').write_text('x', encoding='utf-8')
            found = pn.list_pdfs(str(base), '')
            self.assertEqual([p.name for p in found], ['a.pdf', 'b.pdf'])

    def test_单文件优先(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.pdf'
            path.write_bytes(b'%PDF-1.4')
            self.assertEqual(pn.list_pdfs('', str(path)), [path])

    def test_文件不存在时是空列表(self):
        self.assertEqual(pn.list_pdfs('', '/no/such/file.pdf'), [])


class PromptTests(unittest.TestCase):
    def test_正文超长会被截断(self):
        body = '字' * (pn.MAX_PDF_CHARS + 5000)
        prompt = pn.build_prompt('标题', body)
        self.assertLess(len(prompt), pn.MAX_PDF_CHARS + 2000)

    def test_提示词里同时要摘要和概念(self):
        prompt = pn.build_prompt('标题', '正文')
        self.assertIn('summary', prompt)
        self.assertIn('concepts', prompt)


if __name__ == '__main__':
    unittest.main()
