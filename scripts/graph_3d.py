# -*- coding: utf-8 -*-
"""把知识库导成一张**自包含**的 3D 图谱页面（用来"逛"，不是用来分析）。

为什么内联而不是让页面去读 json：`file://` 下 `fetch` 被 CORS 挡死，要读外部 json 就得
起本地服务器或给浏览器加 `--allow-file-access-from-files`。内联一次，双击就能开。

图的口径（与助手那边同一个）：
- 节点 = 概念页（两个目录都在内）
- 边   = 页面**正文**里的 `[[链接]]`，且指向另一个概念页
- **排除 `## 来源` 那一段**：它指向的是卡片，而卡片不在库里，算进去会多出一堆悬空边。

为什么第一版用 `3d-force-graph` 而这一版自己拼 three + d3-force-3d（实测数据）：
第一版在 RTX 5060 上只有 **2.2 fps** —— 25,431 个球和 70,092 条线各自是一个独立对象，
每帧发出 **27,108 次 draw call**（关掉线 9.9 fps、关掉球 6.7 fps，瓶颈就在这个数量上）。
现在节点合成**一个** `THREE.Points`、线合成**一个** `LineSegments`：每帧 3~4 次绘制。
顺带三个好处：`sizeAttenuation:false` 让点在任何缩放下都是同样大小（第一版缩到 0.2 像素
就等于消失了）；布局算完即冻结，逛的时候不再吃 CPU；不再依赖 5.8 MB 的捆绑包。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# 仓库根 = 这个文件的上两级。**不许按 cwd 找**：CLI 从哪儿调用都要能跑，
# 而 `output/` 是用户数据、`resources/` 是随包发的静态件，两者的家在仓库里是固定的。
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'scripts'))
sys.path.insert(0, str(REPO))
# **先切到仓库根**：`wiki_lint.CARD_DIRS` 是导入时按 cwd 通配 `output/*-notes` 算出来的，
# 在别的目录里调用会静默换成另一套口径（而 --out / --vault 都是绝对路径，不该被 cwd 影响）。
os.chdir(REPO)
import wiki_lint as wl

# 页面要用的三个库随包发（`resources/` 整个进 npm 包），布局助手跟它们的库待在一起：
# 它 `new Function` 跑的就是这几个 UMD，放一起就不会一个在包外、一个在包内。
LIB_DIR = REPO / 'resources' / 'js' / 'graph3d'
# 布局缓存是**派生数据**（几千个坐标），跟 graph.json 一起放 output/ 下，不进仓库。
CACHE_DIR = REPO / 'output' / '.graph3d-cache'
DEFAULT_VAULT = REPO / 'output' / 'wechat-vault'
DEFAULT_OUT = REPO / 'output' / 'knowledge-graph-3d.html'
LIB_THREE = LIB_DIR / 'three.min.js'
# d3-force-3d 的 UMD 包**不带依赖**：它从全局 `d3` 命名空间里取 dispatch/timer/quadtree/
# octree/binarytree，所以这五个必须按顺序先引。少一个的报错是 "u.timer is not a function"，
# 看着像库坏了，其实只是缺件。
LIB_D3 = ['d3-dispatch.min.js', 'd3-timer.min.js', 'd3-quadtree.min.js',
          'd3-binarytree.min.js', 'd3-octree.min.js', 'd3-force-3d.min.js']


# 布局的力参数**只在这里定义**（`layout.mjs` 只负责照做），而且**要计入缓存 key**：
# 参数改了而 key 不变，就会一直读到用旧参数算的坐标 —— 这个坑刚踩过（改了 2D 的斥力、
# 重跑却打印"图没变，沿用已有布局"，图上一点变化都没有）。
#
# 同一个力参数在 2D 与 3D **不是同一张图**：少一维可摊开，同样的斥力会把 5 万个点压成一坨
# 密实的圆盘（实测：2D 沿用 3D 的参数，出来是一块没有任何结构的"饼"）。
LAYOUT_PARAMS = {
    '3': {'charge': -22, 'chargeMax': 600, 'linkDist': 22, 'linkStrength': 0.6},
    '2': {'charge': -70, 'chargeMax': 1600, 'linkDist': 55, 'linkStrength': 0.5},
}


def layout_key(data, dims, ticks):
    """缓存 key：**数据 + 维度 + tick 数 + 力参数**。少任何一项都会读到过期的坐标。"""
    params = LAYOUT_PARAMS[str(dims)]
    payload = '%s|dims=%s|ticks=%s|%s' % (data, dims, ticks, json.dumps(params, sort_keys=True))
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]


def run_layout(cache_dir, digest, dims, ticks):
    """让 `layout.mjs` 把坐标算好写进缓存（参数由这里给，助手只照做）。"""
    params = json.dumps(LAYOUT_PARAMS[str(dims)], sort_keys=True)
    subprocess.run(['node', str(LIB_DIR / 'layout.mjs'), str(cache_dir), str(LIB_DIR),
                    str(ticks), digest, str(dims), params], check=True)


def build_graph(vault, min_degree=0, line='all'):
    """`min_degree` / `line` 只在**产出**这一步过滤，改动的是给页面看的点集。

    `min_degree` 用的是**原图里**的度数（"它总共连了 ≥N 个概念"），不是子图里重算的 ——
    否则每放大一次门槛，度数就变一次，同一个数在不同页面上含义不同。
    """
    pages = []
    # 概念目录清单来自 `_utils.KNOWLEDGE_LINES`（经 `wiki_lint` 透出）。**别再在这里硬编码**：
    # 这两行曾经把两条线的相对路径直接拼在 `vault` 上，而它们不在任何一致性清单里
    # （`test/concept-dirs-agreement.test.ts` 只管那五处声明）⇒ 清单改了图也不会变，
    # 症状是"图里少一半节点"，而且不报错。
    for label, _root, concepts in wl.KNOWLEDGE_LINES:
        d = vault / concepts
        if line != 'all' and label != line:
            continue
        for p in wl.collect(str(d), wl.CARD_DIRS):
            p['line'] = label
            pages.append(p)
    names = {p['stem'] for p in pages}
    edges = set()
    for p in pages:
        body = p['body']
        at = body.find('## 来源')
        if at != -1:
            body = body[:at]
        for _sec, name in wl.links_by_section(body):
            t = name.replace('.md', '').strip()
            if t in names and t != p['stem']:
                edges.add((p['stem'], t))
    degree = {p['stem']: 0 for p in pages}
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    nodes = [{'id': p['stem'], 'deg': degree[p['stem']], 'line': p['line']} for p in pages]
    # 边写成二元数组而不是 {source,target} 对象：7 万条边能省掉 1 MB 的重复键名
    links = [[a, b] for a, b in sorted(edges)]
    if min_degree > 0:
        keep = {n['id'] for n in nodes if n['deg'] >= min_degree}
        # **删点必须连边一起删**：布局按 id 连边，留着指向已删点的边，力导向会直接报错
        # （error: node not found），而且只在跑布局时才炸 —— 页面那边看着像"库坏了"。
        links = [pair for pair in links if pair[0] in keep and pair[1] in keep]
        nodes = [n for n in nodes if n['id'] in keep]
    return nodes, links


VIEWER = r'''
(function () {
  window.__t0 = performance.now();
  const NODES = window.__DATA__.nodes;
  const LINKS = window.__DATA__.links.map(([s, t]) => ({ source: s, target: t }));
  const N = NODES.length;
  const hud = document.getElementById('stats');
  const info = document.getElementById('info');
  const bar = document.getElementById('bar');
  const boottext = document.getElementById('boottext');
  const tag = document.getElementById('tag');

  // 度 → 暖色：一眼看出哪几个是枢纽（与第一版同一套映射）
  const maxDeg = Math.max(1, ...NODES.map((n) => n.deg));
  const lg = Math.log10(1 + maxDeg);
  const warm = (d) => Math.min(1, Math.log10(1 + d) / lg);

  // 声明统统提到最前面：resize / 渲染回调里要用，写在后面会踩
  // "Cannot access 'dirty' before initialization"（TDZ），而且只在真的 resize 时才炸
  let points, lines, hubPoints, marker, selLines, hubIdx = [];
  let target, radius, sph, autoRotate = true, dirty = true, fly = null, selected = null;
  // 邻接表**按 id 建键**。一开始这里用 l.source 直接当键，而那时它还是字符串 id，
  // 查询却传节点对象 —— 永远查不到，邻居列表和高亮边静默为空（不报错，只是没有）。
  const byId = new Map(NODES.map((n) => [n.id, n]));
  const neighbors = new Map();
  for (const l of LINKS) {
    if (!neighbors.has(l.source)) neighbors.set(l.source, []);
    if (!neighbors.has(l.target)) neighbors.set(l.target, []);
    neighbors.get(l.source).push(l.target);
    neighbors.get(l.target).push(l.source);
  }
  const nbrs = (id) => (neighbors.get(id) || []).map((k) => byId.get(k)).filter(Boolean);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x05070e);
  const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 1, 5e5);
  const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.setSize(innerWidth, innerHeight);
  document.getElementById('view').appendChild(renderer.domElement);
  addEventListener('resize', () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
    dirty = true;
  });

  // ---------- 布局：分块跑，跑完就停 ----------
  // 25,431 个点跑满收敛要几百 tick（每个 tick 几十毫秒），对"逛"没必要 —— 90 tick 已经
  // 能看出结构，剩下的时间不如让页面早点能用。跑完 sim.stop()，之后逛的时候 CPU 是 0。
  let sim = null;
  if (!window.__POS__) {
    sim = d3.forceSimulation(NODES, 3)
      .force('charge', d3.forceManyBody().strength(-22).distanceMax(600).theta(1.1))
      .force('link', d3.forceLink(LINKS).id((d) => d.id).distance(22).strength(0.6))
      .force('center', d3.forceCenter(0, 0, 0))
      .stop();
  }
  const TICKS = 90;
  let done = 0;
  function layout() {
    const t0 = performance.now();
    while (done < TICKS && performance.now() - t0 < 24) { sim.tick(); done += 1; }
    bar.style.width = ((100 * done) / TICKS).toFixed(0) + '%';
    boottext.textContent = '正在布局 ' + N.toLocaleString() + ' 个概念… ' + ((100 * done) / TICKS).toFixed(0) + '%';
    if (done < TICKS) { requestAnimationFrame(layout); return; }
    sim.stop();
    start();
  }

  // ---------- 建几何 ----------
  function start() {
    // 坐标由 build.py 在 Node 里预先算好内联进来（见 layout.mjs）：打开即用，
    // 不用在浏览器里现跑十几秒。没有内联坐标时下面 layout() 会在页面里现算 —— 兜底。
    if (window.__POS__) {
      const p = window.__POS__.split(',').map(Number);
      NODES.forEach((n, i) => { n.x = p[3 * i]; n.y = p[3 * i + 1]; n.z = p[3 * i + 2]; });
    }
    const pos = new Float32Array(N * 3);
    const col = new Float32Array(N * 3);
    NODES.forEach((n, i) => {
      pos[3 * i] = n.x; pos[3 * i + 1] = n.y; pos[3 * i + 2] = n.z;
      const k = warm(n.deg);
      col[3 * i] = (90 + 165 * k) / 255;
      col[3 * i + 1] = (120 - 60 * k) / 255;
      col[3 * i + 2] = (220 - 160 * k) / 255;
    });
    const pgeo = new THREE.BufferGeometry();
    pgeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    pgeo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    // sizeAttenuation:false ⇒ 不管缩放到哪一层，点永远是 2.2 像素。这是"能逛"的关键：
    // 打开时看整团云是密密麻麻的星点，飞近了星点不会变成糊住的巨球。
    // three 的点**不加贴图就是方片** —— 小点上看不出来，但选中标记放大到 15/40 px 时
    // 明显是个方块。给一张径向渐变的贴图，点就是圆的，还自带柔光。
    const c = document.createElement('canvas');
    c.width = 64; c.height = 64;
    const g2 = c.getContext('2d');
    const rg = g2.createRadialGradient(32, 32, 0, 32, 32, 32);
    rg.addColorStop(0, 'rgba(255,255,255,1)');
    rg.addColorStop(0.4, 'rgba(255,255,255,0.9)');
    rg.addColorStop(1, 'rgba(255,255,255,0)');
    g2.fillStyle = rg; g2.fillRect(0, 0, 64, 64);
    const DOT = new THREE.CanvasTexture(c);

    points = new THREE.Points(pgeo, new THREE.PointsMaterial({
      size: 3.2, sizeAttenuation: false, vertexColors: true, transparent: true,
      opacity: 0.95, map: DOT, depthWrite: false,
    }));
    scene.add(points);

    // 枢纽单独一层，点更大：它是"故事的枢纽"，得在一堆星点里认出来
    hubIdx = NODES.filter((n) => n.deg >= 40);
    if (hubIdx.length) {
      const hpos = new Float32Array(hubIdx.length * 3);
      const hcol = new Float32Array(hubIdx.length * 3);
      hubIdx.forEach((n, j) => {
        hpos[3 * j] = n.x; hpos[3 * j + 1] = n.y; hpos[3 * j + 2] = n.z;
        hcol[3 * j] = 1; hcol[3 * j + 1] = 0.72; hcol[3 * j + 2] = 0.45;
      });
      const hgeo = new THREE.BufferGeometry();
      hgeo.setAttribute('position', new THREE.BufferAttribute(hpos, 3));
      hgeo.setAttribute('color', new THREE.BufferAttribute(hcol, 3));
      hubPoints = new THREE.Points(hgeo, new THREE.PointsMaterial({
        size: 7, sizeAttenuation: false, vertexColors: true, transparent: true,
        opacity: 0.95, map: DOT, depthWrite: false,
      }));
      scene.add(hubPoints);
    }

    const lpos = new Float32Array(LINKS.length * 6);
    LINKS.forEach((l, i) => {
      lpos.set([l.source.x, l.source.y, l.source.z, l.target.x, l.target.y, l.target.z], i * 6);
    });
    const lgeo = new THREE.BufferGeometry();
    lgeo.setAttribute('position', new THREE.BufferAttribute(lpos, 3));
    lines = new THREE.LineSegments(lgeo, new THREE.LineBasicMaterial({
      color: 0x35507f, transparent: true, opacity: 0.15,
    }));
    scene.add(lines);

    // 选中标记（白点 + 光晕两层）与"它连着的边"（另一次绘制）
    const mgeo = new THREE.BufferGeometry();
    mgeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3), 3));
    marker = new THREE.Points(mgeo, new THREE.PointsMaterial({
      size: 16, sizeAttenuation: false, color: 0xffffff, transparent: true,
      opacity: 0.95, map: DOT, depthWrite: false,
    }));
    marker.visible = false;
    scene.add(marker);
    const halo = new THREE.Points(mgeo.clone(), new THREE.PointsMaterial({
      size: 46, sizeAttenuation: false, color: 0x7fb0ff, transparent: true,
      opacity: 0.25, map: DOT, depthWrite: false,
    }));
    halo.visible = false;
    scene.add(halo);
    marker.userData.halo = halo;
    selLines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({
      color: 0xffb060, transparent: true, opacity: 0.6,
    }));
    selLines.visible = false;
    scene.add(selLines);

    // 取景：把整团云框进画面
    let cx = 0, cy = 0, cz = 0;
    for (const n of NODES) { cx += n.x; cy += n.y; cz += n.z; }
    cx /= N; cy /= N; cz /= N;
    radius = 0;
    for (const n of NODES) {
      const d = Math.hypot(n.x - cx, n.y - cy, n.z - cz);
      if (d > radius) radius = d;
    }
    target = new THREE.Vector3(cx, cy, cz);
    const dist = (radius / Math.sin((camera.fov / 2) * Math.PI / 180)) * 1.02;
    sph = { r: dist, theta: 0.7, phi: Math.PI / 2 - 0.22 };
    applyCam();

    hud.textContent = N.toLocaleString() + ' 个概念 · ' +
      LINKS.length.toLocaleString() + ' 条关系 · ' + (hubIdx.length) + ' 个枢纽';
    tag.textContent = '拖 = 转 · 滚轮 = 缩放 · 点一个概念看它连着谁 · 双击空白 = 停/开自转';
    // 有内联坐标就没什么可等的，直接撤掉加载遮罩
    setTimeout(() => { document.getElementById('boot').style.display = 'none'; },
      window.__POS__ ? 0 : 600);
    // 自检用（探针靠它判断"到底建好了没有"，以及量布局花了多久）
    window.__layoutMs = Math.round(performance.now() - window.__t0);
    window.__viz = { nodes: NODES, camera, renderer, scene, select: select_, pick, sph, radius,
      // 给自检用：不然按需渲染下"帧率"量到的是空转的 rAF（144 fps 那种假数字）
      rotate: (v) => { autoRotate = v; dirty = true; },
      info: () => ({ 自动旋转: autoRotate, 脏: dirty, 半径: Math.round(sph.r) }) };
    requestAnimationFrame(loop);
  }

  function applyCam() {
    camera.position.set(
      target.x + sph.r * Math.sin(sph.phi) * Math.cos(sph.theta),
      target.y + sph.r * Math.cos(sph.phi),
      target.z + sph.r * Math.sin(sph.phi) * Math.sin(sph.theta),
    );
    camera.lookAt(target);
    camera.updateMatrixWorld();
    dirty = true;
  }

  // ---------- 自己写的轨道控制（够用，且不用再引一个 UMD 包） ----------
  const canvas = () => renderer.domElement;
  let drag = null, moved = 0;
  canvas().addEventListener('mousedown', (e) => {
    drag = { x: e.clientX, y: e.clientY, btn: e.button, shift: e.shiftKey };
    moved = 0;
  });
  addEventListener('mouseup', (e) => {
    if (drag && moved < 5 && drag.btn === 0 && !drag.shift) pick(e.clientX, e.clientY, 16, true);
    drag = null;
  });
  addEventListener('mousemove', (e) => {
    if (!drag) { hover(e.clientX, e.clientY); return; }
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX; drag.y = e.clientY;
    moved += Math.abs(dx) + Math.abs(dy);
    autoRotate = false;
    if (drag.btn === 2 || drag.shift) {
      // 平移：沿相机的右/上方向挪 target
      const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
      const up = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 1);
      const k = sph.r * 0.0016;
      target.addScaledVector(right, -dx * k).addScaledVector(up, dy * k);
    } else {
      sph.theta -= dx * 0.005;
      sph.phi = Math.max(0.05, Math.min(Math.PI - 0.05, sph.phi - dy * 0.005));
    }
    fly = null;
    applyCam();
  });
  canvas().addEventListener('wheel', (e) => {
    e.preventDefault();
    fly = null;
    sph.r = Math.max(20, Math.min(sph.r * Math.exp(e.deltaY * 0.0011), radius * 6));
    applyCam();
  }, { passive: false });
  canvas().addEventListener('contextmenu', (e) => e.preventDefault());
  canvas().addEventListener('dblclick', () => { autoRotate = !autoRotate; dirty = true; });

  // ---------- 选中 / 悬停：把点投到屏幕上找最近的 ----------
  // 不做射线求交：2.5 万个点每次投影一遍也就零点几毫秒，比给每个点挂可拾取对象便宜得多。
  const v = new THREE.Vector3();
  function pick(x, y, tol, select) {
    const W = innerWidth, H = innerHeight;
    let best = null, bestD = tol * tol;
    for (let i = 0; i < N; i += 1) {
      const n = NODES[i];
      v.set(n.x, n.y, n.z).project(camera);
      if (v.z > 1) continue;
      const sx = (v.x * 0.5 + 0.5) * W, sy = (-v.y * 0.5 + 0.5) * H;
      const d = (sx - x) * (sx - x) + (sy - y) * (sy - y);
      if (d < bestD) { bestD = d; best = n; }
    }
    if (best && select) select_(best);
    return best;
  }

  function select_(n) {
    selected = n;
    if (marker) {
      marker.geometry.attributes.position.setXYZ(0, n.x, n.y, n.z);
      marker.geometry.attributes.position.needsUpdate = true;
      marker.visible = true;
      const halo = marker.userData.halo;
      halo.geometry.attributes.position.setXYZ(0, n.x, n.y, n.z);
      halo.geometry.attributes.position.needsUpdate = true;
      halo.visible = true;
      const nb = nbrs(n.id).slice(0, 400);
      const arr = new Float32Array(nb.length * 6);
      nb.forEach((m, i) => {
        arr.set([n.x, n.y, n.z, m.x, m.y, m.z], i * 6);
      });
      selLines.geometry.dispose();
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(arr, 3));
      selLines.geometry = g;
      selLines.visible = nb.length > 0;
    }
    const nb = nbrs(n.id).slice().sort((a, b) => b.deg - a.deg);
    const head = nb.slice(0, 18).map((m) => m.id).join('、');
    info.innerHTML = '<b>' + esc(n.id) + '</b> · ' + n.deg + ' 条连接' +
      (nb.length ? '<div class="nb">' + esc(head) + (nb.length > 18 ? ' …（共 ' + nb.length + '）' : '') + '</div>' : '');
    // 取景要让「它 + 它的邻居」一起进画面。固定拉近到 60 单位是错的：枢纽的邻居散在
    // 整团云里（Codex 的 603 个邻居横跨全图），那样屏幕上全是不相干的点。
    // 取邻居距离的 80 分位再乘 2.2 —— 大多数邻居入画，又不会为了几个远亲把镜头拉到天上。
    let framR = 60;
    if (nb.length) {
      const ds = nb.map((m) => Math.hypot(m.x - n.x, m.y - n.y, m.z - n.z)).sort((a, b) => a - b);
      framR = Math.min(radius * 2.5, Math.max(60, ds[Math.floor(ds.length * 0.8)] * 2.2));
    }
    fly = { t: 0, dur: 0.7,
      from: { x: target.x, y: target.y, z: target.z, r: sph.r },
      to: { x: n.x, y: n.y, z: n.z, r: framR } };
    autoRotate = false;
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  let hoverAt = 0;
  function hover(x, y) {
    const now = performance.now();
    if (now - hoverAt < 60) return;   // 2.5 万次投影不必每像素都做
    hoverAt = now;
    const n = pick(x, y, 9, false);
    if (!n) { tag.textContent = '拖 = 转 · 滚轮 = 缩放 · 点一个概念看它连着谁 · 双击空白 = 停/开自转'; return; }
    tag.textContent = n.id + ' · ' + n.deg + ' 条连接';
  }

  // 搜索：25,431 个点里靠拖是找不到的，直接跳
  const box = document.getElementById('q');
  box.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const q = box.value.trim().toLowerCase();
    if (!q) return;
    const hit = NODES.find((n) => n.id.toLowerCase() === q) || NODES.find((n) => n.id.toLowerCase().includes(q));
    if (hit) select_(hit);
    else info.textContent = '没找到「' + box.value.trim() + '」';
  });

  // ---------- 渲染循环：按需渲染 ----------
  let last = performance.now();
  function loop(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (fly) {
      fly.t = Math.min(1, fly.t + dt / fly.dur);
      const e = fly.t < 0.5 ? 2 * fly.t * fly.t : 1 - 2 * (1 - fly.t) * (1 - fly.t);
      target.x = fly.from.x + (fly.to.x - fly.from.x) * e;
      target.y = fly.from.y + (fly.to.y - fly.from.y) * e;
      target.z = fly.from.z + (fly.to.z - fly.from.z) * e;
      sph.r = fly.from.r + (fly.to.r - fly.from.r) * e;
      applyCam();
      if (fly.t >= 1) fly = null;
    } else if (autoRotate) {
      sph.theta += 0.16 * dt;
      applyCam();
    }
    if (dirty) { renderer.render(scene, camera); dirty = false; }
    requestAnimationFrame(loop);
  }

  // 渲染循环**只能**在 start() 里启动：如果在这里就 rAF(loop)，而布局还没跑完，
  // loop 第一帧就会读还没赋值的 sph → 抛异常 → 那行 rAF(loop) 不再执行 → 循环死掉，
  // 页面永远是黑的。这个 bug 的表现是"帧率 144 但 draw call 是 0"。
  if (window.__POS__) start(); else layout();
})();
'''


def _parse_args(argv):
    ap = argparse.ArgumentParser(description='把知识库导成一张自包含的 3D 图谱页面（只读本地、不联网）')
    ap.add_argument('--vault', default=str(DEFAULT_VAULT), help='Vault 根目录（默认 output/wechat-vault）')
    ap.add_argument('--out', default=str(DEFAULT_OUT), help='页面写到哪（默认 output/knowledge-graph-3d.html）')
    # argparse 的短名要写在 metavar 里（写成 '--min-degree <n>' 是 commander 的语法，
    # dest 会变成别的名字，运行时报 AttributeError）
    ap.add_argument('--min-degree', dest='min_degree', metavar='N', type=int, default=0,
                    help='只画在原图里连接数 ≥N 的概念（去掉细枝，看骨架）；0 = 全画')
    ap.add_argument('--line', dest='line', metavar='WHICH', choices=['all', 'wiki', 'chat'], default='all',
                    help='只画某一条线：文章线的概念页 / 聊天线的概念页 / 两条都画')
    ap.add_argument('--ticks', type=int, default=250, help='力导向迭代次数（默认 250，越多越舒展也越慢）')
    ap.add_argument('--dry-run', action='store_true', help='只报概念与链接数：不写文件、也不算布局')
    ap.add_argument('--cache', default=str(CACHE_DIR),
                    help='布局缓存目录（派生数据；测试请指到临时目录，别踩真实的那份）')
    ap.add_argument('--json', action='store_true', help='输出机器可读结果')
    return ap.parse_args(argv)


def _fail(message, as_json):
    if as_json:
        print(json.dumps({'success': False, 'action': 'graph-3d', 'error': message}, ensure_ascii=False))
    else:
        print(message, file=sys.stderr)
    return 1


def _missing_libs():
    return [p.name for p in [LIB_THREE] + [LIB_DIR / f for f in LIB_D3] if not p.exists()]


def main(argv=None):
    # 手跑这份脚本时（不走 CLI 的 pythonBridge）stdout 是本机编码（这台机器是 GBK）：
    # 中文会变乱码，遇到非 GBK 字符（`✓` 之类）直接 UnicodeEncodeError 把整次生成挂掉 ——
    # 而它**在写文件之后**打印，所以现象是"生成失败"其实产物已经落盘。
    # 仓库里三十多份脚本都在 main() 里做同一行（见 docs/EXTENDING.md 的 Recipe E）。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    vault = Path(args.vault)
    if not any((vault / concepts).is_dir() for _line, _root, concepts in wl.KNOWLEDGE_LINES):
        return _fail('Vault 里没有概念目录：%s（先跑 `wiki compile`，或用 --vault 指定）' % vault, args.json)

    nodes, links = build_graph(vault, min_degree=args.min_degree, line=args.line)
    if args.dry_run:
        # 只报数：不写文件、也不算布局（布局是最慢的一步，预览不该等它）。
        # 库的存在性检查也放在这之后 —— 预览不内联任何库，缺库不该挡住"先看看有多少个点"。
        if args.json:
            print(json.dumps({'success': True, 'action': 'graph-3d', 'dryRun': True,
                              'nodes': len(nodes), 'links': len(links), 'vault': str(vault),
                              'minDegree': args.min_degree, 'line': args.line,
                              'readsLocalData': True, 'invokesAI': False, 'sendsNothing': True},
                             ensure_ascii=False))
        else:
            print('概念 %d 个、链接 %d 条（--dry-run：没有写文件）' % (len(nodes), len(links)))
        return 0
    # 库缺了就说清楚缺哪个 —— 少了它会跑到 new Function 里才炸，报错长得像"库坏了"。
    missing = _missing_libs()
    if missing:
        return _fail('缺库：%s（应当在 %s 下随包发）' % ('、'.join(missing), LIB_DIR), args.json)
    three = LIB_THREE.read_text(encoding='utf-8')
    d3lib = '\n'.join((LIB_DIR / f).read_text(encoding='utf-8') for f in LIB_D3)
    data = json.dumps({'nodes': nodes, 'links': links}, ensure_ascii=False, separators=(',', ':'))
    # 布局在 Node 里算（250 tick 实测 55 秒），留到构建期；页面打开就是现成坐标。
    # 图没变就沿用缓存 —— 只改渲染的时候不该每次都等一分钟。
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    # **按图的内容分槽**：核心图与全量图是两张不同的图，坐标不通用。共用一份文件名的话，
    # 建完核心图就把 5 万点的坐标挤掉了，切回去要重算 55 秒。
    digest = layout_key(data, 3, args.ticks)
    graph_file = cache / ('graph-%s.json' % digest)
    pos_file = cache / ('positions-%s.json' % digest)
    same = graph_file.exists() and graph_file.read_text(encoding='utf-8') == data
    if same and pos_file.exists():
        print('图没变，沿用已有布局')
    else:
        graph_file.write_text(data, encoding='utf-8')
        run_layout(cache, digest, 3, args.ticks)
    pos = pos_file.read_text(encoding='utf-8')
    page = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>知识库 3D（{len(nodes)} 个概念）</title>
<style>
  html,body {{ margin:0; height:100%; background:#05070e; color:#c9d3e6;
    font:13px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
    overflow:hidden; user-select:none; }}
  #view {{ position:fixed; inset:0; }}
  #view canvas {{ display:block; cursor:crosshair; }}
  #hud {{ position:fixed; left:14px; top:12px; pointer-events:none; max-width:60vw; }}
  #stats {{ color:#6d7a93; }}
  #info {{ margin-top:6px; font-size:15px; min-height:22px; }}
  #info .nb {{ color:#8b98b3; font-size:12px; margin-top:3px; max-width:52vw; }}
  #q {{ position:fixed; right:14px; top:12px; width:220px; padding:6px 10px;
    background:#121826; color:#c9d3e6; border:1px solid #26304a; border-radius:8px; outline:none; }}
  #q:focus {{ border-color:#3f6ad8; }}
  #tag {{ position:fixed; right:14px; bottom:10px; color:#4d5a72; text-align:right; }}
  #boot {{ position:fixed; inset:0; display:flex; flex-direction:column; align-items:center;
    justify-content:center; gap:14px; background:#05070e; }}
  #boot .b {{ width:260px; height:3px; background:#161d2e; border-radius:2px; overflow:hidden; }}
  #bar {{ width:0; height:100%; background:#4a7fe0; transition:width .1s linear; }}
</style></head>
<body>
<div id="view"></div>
<div id="boot"><div id="boottext">正在布局…</div><div class="b"><div id="bar"></div></div></div>
<div id="hud"><div id="stats"></div><div id="info">点一个概念看它连着谁 · 拖动旋转 · 滚轮缩放</div></div>
<input id="q" placeholder="搜概念，回车跳过去">
<div id="tag">拖 = 转 · 滚轮 = 缩放 · 点一个概念看它连着谁 · 双击空白 = 停/开自转</div>
<script>{three}</script>
<script>{d3lib}</script>
<script>window.__DATA__ = {data};</script>
<script>window.__POS__ = "{pos}";</script>
<script>{VIEWER}</script>
</body></html>
"""
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding='utf-8')
    mb = out.stat().st_size / 1048576
    if args.json:
        print(json.dumps({'success': True, 'action': 'graph-3d', 'nodes': len(nodes), 'links': len(links),
                          'out': str(out), 'vault': str(vault), 'mb': round(mb, 2),
                          'minDegree': args.min_degree, 'line': args.line,
                          'selfContained': True, 'readsLocalData': True, 'invokesAI': False,
                          'sendsNothing': True}, ensure_ascii=False))
    else:
        print('已生成 %s' % out)
        print('  节点 %d / 边 %d / 文件 %.1f MB' % (len(nodes), len(links), mb))
    return 0


if __name__ == '__main__':
    sys.exit(main())
