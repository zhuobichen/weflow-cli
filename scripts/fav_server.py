#!/usr/bin/env python3
"""
本地收藏服务器 — 浏览器点击收藏实时同步到磁盘 收藏/ 文件夹。

用法:
  python scripts/fav_server.py --date 2026-05-19
  python scripts/fav_server.py --date 2026-05-19 --port 8765

打开 http://localhost:8765 即可使用，点击 ☆ 收藏按钮实时写入磁盘。
"""

import sys, os, json
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
SOURCE_ROOT = os.path.join(PROJECT_ROOT, 'output', 'biz-daily')


class FavHandler(SimpleHTTPRequestHandler):
    """处理静态文件 + /api/fav/* 端点。"""
    date_str = None
    date_dir = None

    # 确保文本文件以 UTF-8 编码发送，避免浏览器乱码
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
        '.md': 'text/markdown; charset=utf-8',
        '.html': 'text/html; charset=utf-8',
        '.css': 'text/css; charset=utf-8',
        '.js': 'text/javascript; charset=utf-8',
        '.json': 'application/json; charset=utf-8',
        '.txt': 'text/plain; charset=utf-8',
    }

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        if self.path.startswith('/api/fav/list'):
            self._handle_fav_list()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/fav/toggle':
            self._handle_fav_toggle()
        else:
            self.send_error(404)

    def _handle_fav_list(self):
        favs = self._read_favs()
        self._send_json(favs)

    def _handle_fav_toggle(self):
        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len)
        try:
            data = json.loads(body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_error(400, 'Invalid JSON')
            return
        article_id = data.get('id', '')
        if not article_id:
            self.send_error(400, 'Missing id')
            return

        favs = self._read_favs()
        if article_id in favs:
            favs.remove(article_id)
            action = 'removed'
        else:
            favs.append(article_id)
            action = 'added'

        self._write_favs(favs)
        self._sync_folder(favs)
        self._send_json({'ok': True, 'action': action, 'id': article_id, 'count': len(favs)})

    def _read_favs(self):
        fav_file = Path(self.date_dir) / '.fav_state.json'
        if fav_file.exists():
            try:
                with open(fav_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _write_favs(self, favs):
        fav_file = Path(self.date_dir) / '.fav_state.json'
        with open(fav_file, 'w', encoding='utf-8') as f:
            json.dump(favs, f, ensure_ascii=False, indent=2)

    def _sync_folder(self, favs):
        """实时同步 收藏/ 文件夹 — 添加/移除文章文件。"""
        fav_dir = Path(self.date_dir) / '收藏'
        fav_dir.mkdir(parents=True, exist_ok=True)

        desired_names = set()
        for rel_path in favs:
            src = Path(self.date_dir) / rel_path
            if src.exists():
                desired_names.add(src.name)
                link = fav_dir / src.name
                if not link.exists():
                    try:
                        link.symlink_to(os.path.relpath(src, fav_dir))
                    except OSError:
                        import shutil
                        shutil.copy2(src, link)

        # 移除不在收藏列表中的文件
        for item in fav_dir.iterdir():
            if item.name not in desired_names:
                if item.is_symlink() or item.is_file():
                    item.unlink()

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        # 简洁日志
        if '/api/' in str(args[0]):
            print(f'  {args[0]}')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='本地收藏服务器')
    parser.add_argument('--date', required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--port', type=int, default=8765, help='端口 (默认 8765)')
    args = parser.parse_args()

    date_dir = os.path.join(SOURCE_ROOT, args.date)
    if not os.path.isdir(date_dir):
        print(f'[ERROR] 目录不存在: {date_dir}')
        sys.exit(1)

    # 配置 Handler
    FavHandler.date_str = args.date
    FavHandler.date_dir = date_dir

    # 切换到日报目录以提供静态文件服务
    os.chdir(date_dir)

    server = HTTPServer(('127.0.0.1', args.port), FavHandler)
    print(f'⭐ 收藏服务器已启动')
    print(f'   打开: http://localhost:{args.port}')
    print(f'   日期: {args.date}')
    print(f'   收藏文件夹: {date_dir}/收藏/')
    print(f'   Ctrl+C 停止')
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
        server.server_close()


if __name__ == '__main__':
    main()
