#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用文档读取 — 把本地文件转成纯文本，给助手的 read_document 工具用。

用法: python scripts/read_document.py --path <文件路径> [--json] [--max-chars N]

支持（按扩展名分派，缺依赖如实报错、不假装成功）：
  .txt/.md/.json/.csv/.log/.py/.js/...  纯文本，直接读（utf-8，失败回 gbk）
  .pdf                                  pdftotext（系统命令）
  .docx                                 python-docx
  .xlsx/.xlsm                           openpyxl（每个 sheet 逐行）
  .pptx                                 python-pptx
  .html/.htm/.xml                       BeautifulSoup（去脚本/样式，留正文）
  .doc/.xls/.ppt（老格式）              无转换器，如实报"不支持"

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--path', required=True)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--max-chars', type=int, default=20000)
    args = ap.parse_args()

    path = os.path.abspath(os.path.expanduser(args.path))
    out = {'ok': False, 'text': '', 'format': '', 'truncated': False, 'chars': 0}
    try:
        if not os.path.exists(path):
            raise RuntimeError('文件不存在: ' + path)
        if not os.path.isfile(path):
            raise RuntimeError('不是普通文件: ' + path)
        ext = os.path.splitext(path)[1].lower()
        handlers = {
            '.pdf': read_pdf, '.docx': read_docx,
            '.xlsx': read_xlsx, '.xlsm': read_xlsx,
            '.pptx': read_pptx,
            '.html': read_html, '.htm': read_html, '.xml': read_html,
        }
        if ext in ('.doc', '.xls', '.ppt'):
            raise RuntimeError(f'{ext} 是老格式，需要先用 Office 转成 {ext}x 再读')
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
