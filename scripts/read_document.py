#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用文档读取 — 把本地文件转成纯文本，给助手的 read_document 工具用。

用法: python scripts/read_document.py --path <文件路径> [--name <文件名> --wechat-dir <微信文件根>] [--json] [--max-chars N]

--name 模式：只给文件名（如 "figure (2).pdf"），在 --wechat-dir 下递归找真实路径。
  这是"这个 pdf"那条路：模型从聊天记录拿到 `[文件] figure (2).pdf` 的文件名，
  但文件体实际落在 <dbPath>/<wxid>/msg/file/<年月>/ 下，模型猜不到完整路径。
  按文件名搜到唯一命中就直接用；多个命中按修改时间取最新的，并把候选列出来。

支持（按扩展名分派，缺依赖如实报错、不假装成功）：
  .txt/.md/.json/.csv/.log/.py/.js/...  纯文本，直接读（utf-8，失败回 gbk）
  .pdf                                  pdftotext（系统命令）
  .doc                                  antiword（老 Word，系统命令）
  .docx                                 python-docx
  .xls                                  xlrd（老 Excel；2.x 只支持 .xls）
  .xlsx/.xlsm                           openpyxl（每个 sheet 逐行）
  .pptx                                 python-pptx
  .html/.htm/.xml                       BeautifulSoup（去脚本/样式，留正文）
  .ppt（老 PowerPoint）                 无转换器，如实报"不支持"

输出: {"ok": bool, "text": str, "format": str, "truncated": bool, "chars": int, "error": str?}
"""
import argparse
import json
import os
import subprocess
import sys

# Windows 控制台默认 GBK，正文里的 emoji 会让 print 抛 UnicodeEncodeError。
# 强制 UTF-8 输出，与 pythonBridge 按 UTF-8 读 stdout 对齐。
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass


def read_plain(path):
    for enc in ('utf-8', 'gbk', 'utf-16'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            continue
    raise RuntimeError('文本编码认不出来（试过 utf-8 / gbk / utf-16）')


def read_pdf(path):
    try:
        r = subprocess.run(['pdftotext', '-layout', path, '-'],
                           capture_output=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError('pdftotext 没装，读不了 PDF')
    except subprocess.TimeoutExpired:
        raise RuntimeError('PDF 解析超时（60s）')
    if r.returncode != 0:
        raise RuntimeError('pdftotext 失败: ' + r.stderr.decode('utf-8', 'replace')[:200])
    return r.stdout.decode('utf-8', 'replace')


def read_doc(path):
    """老 Word .doc：antiword 优先，失败时 fallback 到 catdoc。
    WPS 生成的 .doc 虽是 OLE 复合文档，antiword 认不出（"is not a Word Document"），
    catdoc 对这类兼容性更好——实测两种都要装（服务端 apt install antiword catdoc）。"""
    errs = []
    for cmd in (['antiword', '-m', 'UTF-8.txt', path], ['catdoc', '-d', 'utf-8', path]):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
        except FileNotFoundError:
            errs.append(cmd[0] + ' 没装'); continue
        except subprocess.TimeoutExpired:
            errs.append(cmd[0] + ' 超时'); continue
        if r.returncode == 0:
            text = r.stdout.decode('utf-8', 'replace')
            if text.strip():
                return text
            errs.append(cmd[0] + ' 返回空')
        else:
            errs.append(cmd[0] + ' 失败: ' + r.stderr.decode('utf-8', 'replace')[:80])
    raise RuntimeError('、'.join(errs))


def read_xls(path):
    """老 Excel .xls：xlrd 2.x 只支持 .xls（.xlsx 仍走 openpyxl）。"""
    try:
        import xlrd
    except ImportError:
        raise RuntimeError('xlrd 没装，读不了 .xls（服务端需 pip install xlrd）')
    wb = xlrd.open_workbook(path)
    parts = []
    for ws in wb.sheets():
        parts.append(f'[工作表: {ws.name}]')
        for ri in range(ws.nrows):
            vals = [str(ws.cell_value(ri, ci)) for ci in range(ws.ncols)]
            if any(v.strip() for v in vals):
                parts.append(' | '.join(vals))
    return '\n'.join(parts)


def read_docx(path):
    try:
        import docx
    except ImportError:
        raise RuntimeError('python-docx 没装，读不了 .docx')
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for ti, table in enumerate(d.tables):
        parts.append(f'\n[表格 {ti + 1}]')
        for row in table.rows:
            parts.append(' | '.join(c.text.strip() for c in row.cells))
    return '\n'.join(parts)


def read_xlsx(path):
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError('openpyxl 没装，读不了 .xlsx')
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f'[工作表: {ws.title}]')
        for row in ws.iter_rows(values_only=True):
            vals = ['' if v is None else str(v) for v in row]
            if any(v.strip() for v in vals):
                parts.append(' | '.join(vals))
    wb.close()
    return '\n'.join(parts)


def read_pptx(path):
    try:
        from pptx import Presentation
    except ImportError:
        raise RuntimeError('python-pptx 没装，读不了 .pptx')
    prs = Presentation(path)
    parts = []
    for i, slide in enumerate(prs.slides):
        parts.append(f'[幻灯片 {i + 1}]')
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text)
            if shape.has_table:
                for row in shape.table.rows:
                    parts.append(' | '.join(c.text.strip() for c in row.cells))
    return '\n'.join(parts)


def read_html(path):
    text = read_plain(path)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        return soup.get_text('\n', strip=True)
    except ImportError:
        import re
        return re.sub(r'<[^>]+>', ' ', text)


def find_by_name(name, wechat_dir):
    """在微信文件目录下按文件名找真实路径。精确匹配优先，再退到包含匹配。
    多个命中按修改时间取最新，并把候选列进结果（调用方能看到有几个）。"""
    if not wechat_dir or not os.path.isdir(wechat_dir):
        raise RuntimeError('微信文件目录不可用，无法按文件名定位')
    name = os.path.basename(str(name).strip())
    if not name or name in ('.', '..'):
        raise RuntimeError('文件名不合法')
    exact, partial = [], []
    for root, dirs, files in os.walk(wechat_dir):
        # 跳过隐藏目录与缓存，避免扫到无关副本
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f == name:
                exact.append(os.path.join(root, f))
            elif name.lower() in f.lower():
                partial.append(os.path.join(root, f))
    hits = exact or partial
    if not hits:
        raise RuntimeError(f'在微信文件目录里没找到名为「{name}」的文件')
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return hits[0], hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--path')
    ap.add_argument('--name', help='只给文件名，在 --wechat-dir 下递归定位真实路径')
    ap.add_argument('--wechat-dir', help='微信文件根目录（按文件名搜索时用）')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--max-chars', type=int, default=20000)
    ap.add_argument('--locate-only', action='store_true',
                    help='只按 --name 定位真实路径，不解析内容（给调用方过权限门用）')
    args = ap.parse_args()

    if not args.path and not args.name:
        ap.error('要给 --path 或 --name 之一')
    out = {'ok': False, 'text': '', 'format': '', 'truncated': False,
           'chars': 0, 'found_path': '', 'candidates': []}
    try:
        candidates = []
        if args.name and not (args.path and os.path.isfile(os.path.expanduser(args.path))):
            found, candidates = find_by_name(args.name, args.wechat_dir)
            path = found
            out['found_path'] = found
            out['candidates'] = candidates[:10]
        else:
            path = os.path.abspath(os.path.expanduser(args.path))
        # 只定位：拿到真实路径就返回，不读内容
        if args.locate_only:
            out['ok'] = True
            out['found_path'] = out['found_path'] or path
            if args.json:
                print(json.dumps(out, ensure_ascii=False))
            return
        if not os.path.exists(path):
            raise RuntimeError('文件不存在: ' + path)
        if not os.path.isfile(path):
            raise RuntimeError('不是普通文件: ' + path)
        ext = os.path.splitext(path)[1].lower()
        handlers = {
            '.pdf': read_pdf,
            '.doc': read_doc, '.docx': read_docx,       # 老 Word 走 antiword
            '.xls': read_xls,                            # 老 Excel 走 xlrd
            '.xlsx': read_xlsx, '.xlsm': read_xlsx,
            '.pptx': read_pptx,
            '.html': read_html, '.htm': read_html, '.xml': read_html,
        }
        # .ppt（老 PowerPoint）没有可用转换器，如实报不支持——不假装能读。
        if ext == '.ppt':
            raise RuntimeError('.ppt 是老格式，没有可用转换器；请另存为 .pptx 再读')
        handler = handlers.get(ext, read_plain)
        out['format'] = ext or 'text'
        text = handler(path)
        if len(text) > args.max_chars:
            out['truncated'] = True
            text = text[:args.max_chars]
        out['text'] = text
        out['chars'] = len(text)
        out['ok'] = True
    except Exception as e:
        out['error'] = str(e)[:300]

    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        if out['ok']:
            print(out['text'])
        else:
            print('读取失败: ' + out['error'], file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
