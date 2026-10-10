# -*- coding: utf-8 -*-
"""把知识库导成一张**2D** 图谱页面（更像 Obsidian 那种平面图；同样自包含、不联网）。

与 3D 那份（`graph_3d.py`）的分工：**图怎么建**是同一套（点 = 概念页，边 = 正文里的 `[[链接]]`，
排除 `## 来源` 那一段，也共用 `--min-degree` / `--line` 的过滤），直接 import 过来；
这里只换两件事：

1. **布局按 2D 算** —— 不是把 3D 的 x/y 拍扁。z 那一维也承载结构，拍扁会把两团本不相干的点叠在一起；
   2D 的解也快得多，所以同一张图重算得起。
2. **渲染换成 canvas**（`resources/js/graph3d/viewer2d.js`）——不需要 three.js，这页一张图都不引。

为什么单独一个文件、而不是给 3D 那份加个开关：两套渲染器各两百来行，塞进一个文件就没人看得完了；
共用的部分留在 `graph_3d.py`，两个入口都从那里取。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'scripts'))
sys.path.insert(0, str(REPO))
# 先切到仓库根（`wiki_lint.CARD_DIRS` 是导入时按 cwd 算的，见 graph_3d.py 里那段）
os.chdir(REPO)
import graph_3d as g  # noqa: E402

VIEWER2D = g.LIB_DIR / 'viewer2d.js'
LAYOUT = g.LIB_DIR / 'layout.mjs'
DEFAULT_OUT = REPO / 'output' / 'knowledge-graph-2d.html'


def _parse_args(argv):
    ap = argparse.ArgumentParser(description='把知识库导成一张自包含的 2D 图谱页面（只读本地、不联网）')
    ap.add_argument('--vault', default=str(g.DEFAULT_VAULT), help='Vault 根目录（默认 output/wechat-vault）')
    ap.add_argument('--out', default=str(DEFAULT_OUT), help='页面写到哪（默认 output/knowledge-graph-2d.html）')
    ap.add_argument('--min-degree', dest='min_degree', metavar='N', type=int, default=0,
                    help='只画在原图里连接数 ≥N 的概念（去掉细枝，看骨架）；0 = 全画')
    ap.add_argument('--line', dest='line', metavar='WHICH', choices=['all', 'wiki', 'chat'], default='all',
                    help='只画某一条线：文章线的概念页 / 聊天线的概念页 / 两条都画')
    ap.add_argument('--ticks', type=int, default=250, help='力导向迭代次数（2D 比 3D 快得多）')
    ap.add_argument('--cache', default=str(g.CACHE_DIR), help='布局缓存目录（派生数据；测试请指到临时目录）')
    ap.add_argument('--dry-run', action='store_true', help='只报概念与链接数：不写文件、也不算布局')
    ap.add_argument('--json', action='store_true', help='输出机器可读结果')
    return ap.parse_args(argv)


def _missing():
    # 2D 页面**不需要 three.js**：布局用 d3，渲染是裸 canvas。缺件要在跑布局之前说清楚，
    # 否则报错会长得像"库坏了"。
    wanted = [VIEWER2D, LAYOUT] + [g.LIB_DIR / f for f in g.LIB_D3]
    return [p.name for p in wanted if not p.exists()]


def main(argv=None):
    # 手跑时 stdout 是本机编码（这台机器是 GBK），中文会乱码、非 GBK 字符直接崩掉整次生成
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    vault = Path(args.vault)
    # 与 `graph_3d.build_graph` 一样，清单从 `_utils.KNOWLEDGE_LINES` 读（经 `wiki_lint` 透出），
    # 不在这里硬编码——硬编码的地方不会跟着清单变，只会静默少算。
    if not any((vault / concepts).is_dir() for _line, _root, concepts in g.wl.KNOWLEDGE_LINES):
        return g._fail('Vault 里没有概念目录：%s（先跑 `wiki compile`，或用 --vault 指定）' % vault, args.json)

    nodes, links = g.build_graph(vault, min_degree=args.min_degree, line=args.line)
    if args.dry_run:
        if args.json:
            print(json.dumps({'success': True, 'action': 'graph-2d', 'dryRun': True, 'dims': 2,
                              'nodes': len(nodes), 'links': len(links), 'vault': str(vault),
                              'minDegree': args.min_degree, 'line': args.line,
                              'readsLocalData': True, 'invokesAI': False, 'sendsNothing': True},
                             ensure_ascii=False))
        else:
            print('概念 %d 个、链接 %d 条（--dry-run：没有写文件）' % (len(nodes), len(links)))
        return 0

    missing = _missing()
    if missing:
        return g._fail('缺件：%s（应当在 %s 下随包发）' % ('、'.join(missing), g.LIB_DIR), args.json)

    data = json.dumps({'nodes': nodes, 'links': links}, ensure_ascii=False, separators=(',', ':'))
    # 与 3D 分开分槽：同一张图的 2D 与 3D 坐标不通用，各存一份（见 graph_3d.py 里那段注释）
    digest = g.layout_key(data, 2, args.ticks)
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    graph_file = cache / ('graph-%s.json' % digest)
    pos_file = cache / ('positions-%s.json' % digest)
    same = graph_file.exists() and graph_file.read_text(encoding='utf-8') == data
    if same and pos_file.exists():
        print('图没变，沿用已有布局')
    else:
        graph_file.write_text(data, encoding='utf-8')
        g.run_layout(cache, digest, 2, args.ticks)
    pos = pos_file.read_text(encoding='utf-8')

    viewer = VIEWER2D.read_text(encoding='utf-8')
    page = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>知识库 2D（{len(nodes)} 个概念）</title>
<style>
  html,body {{ margin:0; height:100%; background:#05070e; color:#c9d3e6;
    font:13px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
    overflow:hidden; user-select:none; }}
  /* canvas 是**替换元素**：`inset:0` 不会让它铺满（right/bottom 被忽略，它保持固有的 300x150），
     所以必须显式给 width/height。少了这一条，视图按 300x150 算，整张图画在左上角一小块里。 */
  #cv {{ position:fixed; inset:0; width:100%; height:100%; display:block; cursor:grab; touch-action:none; }}
  #hud {{ position:fixed; left:14px; top:12px; pointer-events:none; max-width:62vw; }}
  #stats {{ color:#6d7a93; }}
  #info {{ margin-top:6px; font-size:15px; min-height:22px; }}
  #q {{ position:fixed; right:14px; top:12px; width:220px; padding:6px 10px;
    background:#121826; color:#c9d3e6; border:1px solid #26304a; border-radius:8px; outline:none; }}
  #q:focus {{ border-color:#3f6ad8; }}
  #tag {{ position:fixed; right:14px; bottom:10px; color:#4d5a72; text-align:right; }}
  #boot {{ position:fixed; inset:0; display:flex; align-items:center; justify-content:center;
    background:#05070e; color:#6d7a93; }}
</style></head>
<body>
<canvas id="cv"></canvas>
<div id="hud"><div id="stats"></div><div id="info">点一个概念看它连着谁 · 拖动平移 · 滚轮缩放</div></div>
<input id="q" placeholder="搜概念，回车跳过去">
<div id="tag">2D · 坐标构建期算好 · 双击空白复位</div>
<div id="boot">正在画…</div>
<script>window.__DATA__ = {data};</script>
<script>window.__POS__ = "{pos}";</script>
<script>{viewer}</script>
</body></html>
"""
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding='utf-8')
    mb = out.stat().st_size / 1048576
    if args.json:
        print(json.dumps({'success': True, 'action': 'graph-2d', 'dims': 2,
                          'nodes': len(nodes), 'links': len(links), 'out': str(out),
                          'vault': str(vault), 'mb': round(mb, 2),
                          'minDegree': args.min_degree, 'line': args.line,
                          'selfContained': True, 'readsLocalData': True, 'invokesAI': False,
                          'sendsNothing': True}, ensure_ascii=False))
    else:
        print('已生成 %s' % out)
        print('  概念 %d / 链接 %d / 文件 %.1f MB · 2D' % (len(nodes), len(links), mb))
    return 0


if __name__ == '__main__':
    sys.exit(main())
