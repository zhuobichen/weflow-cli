// 2D 图谱视图（canvas）。数据与坐标都是构建期内联进来的，这里只负责画与交互。
//
// 为什么是 canvas 而不是 SVG/DOM：5 万个点用元素画，光建元素就要几秒 —— 这和 Obsidian 全局图谱
// 在十万级上吃力的原因同类。这里每帧只做三件事：一次批量描线、按线分三批批量填点、一小批文字。
// 视口外的点与线直接跳过（所以缩放到任何一级，画的都只是眼前这些）。
//
// 坐标分两套，别混：**世界坐标**（构建期算出来的那套，成千上万）用来画点线，套在 context 变换里；
// **屏幕坐标**用来写字与命中判断。第一版把两套混着用，图形会被画到屏幕外。
//
// 标签只给度数最高的一小批：5 万个名字全画上去就是一团墨，什么也读不出来。
(() => {
  'use strict'

  const data = window.__DATA__ || { nodes: [], links: [] }
  const nodes = data.nodes
  const N = nodes.length
  const flat = String(window.__POS__ || '').split(',')
  const X = new Float64Array(N)
  const Y = new Float64Array(N)
  for (let i = 0; i < N; i += 1) {
    X[i] = Number(flat[2 * i]) || 0
    Y[i] = Number(flat[2 * i + 1]) || 0
  }

  const index = new Map()
  for (let i = 0; i < N; i += 1) index.set(nodes[i].id, i)

  // **画给人看的一律用 `name`**：主键是 `线:名字`（两条线各有一张同名页时要分开），
  // 直接把主键印在画布上，两个同名概念会像叫「wiki:DeepSeek」「chat:DeepSeek」。
  const LINE_LABELS = data.lineLabels || {}
  const label = (n) => n.name || n.id
  const lineLabel = (n) => LINE_LABELS[n.line] || n.line || ''

  const edges = []
  for (const pair of data.links) {
    const a = index.get(pair[0])
    const b = index.get(pair[1])
    if (a === undefined || b === undefined) continue // 悬空边（上游过滤过，这里再兜一次）
    edges.push(a, b)
  }
  const E = Int32Array.from(edges)
  const adj = new Array(N)
  for (let i = 0; i < N; i += 1) adj[i] = []
  for (let k = 0; k < E.length; k += 2) {
    adj[E[k]].push(E[k + 1])
    adj[E[k + 1]].push(E[k])
  }

  const bucketOf = (line) => (line === 'wiki' || line === 'chat' ? line : 'other')
  const LINE_COLOR = { wiki: '#6ea8fe', chat: '#f0a35e', other: '#8b98b3' }

  // **按线预排一次**：每帧就能一趟扫完。不为分色把 5 万个点扫三遍 —— 省下的是纯遍历时间。
  const ORDER = new Int32Array(N)
  for (let i = 0; i < N; i += 1) ORDER[i] = i
  ORDER.sort((a, b) => bucketOf(nodes[a].line).localeCompare(bucketOf(nodes[b].line)))
  // 排序后同一条线的点连成一段，扫一遍就能得到三个桶的边界（顺序与上面的字典序一致）
  const BUCKET_LINE = ['chat', 'other', 'wiki']
  const BUCKET = [0, 0, 0, N]
  {
    let cursor = 0
    for (let b = 0; b < 3; b += 1) {
      BUCKET[b] = cursor
      while (cursor < N && bucketOf(nodes[ORDER[cursor]].line) === BUCKET_LINE[b]) cursor += 1
    }
    BUCKET[3] = N
  }

  // 边的**世界坐标**中位长度：乘上当前缩放就是屏幕上的长度。缩到看不见时直接不画边 ——
  // 一屏 14 万条线糊成一片，没有信息量，却要花掉每帧四成的时间。
  const MEDIAN_EDGE = (() => {
    if (E.length === 0) return 0
    const total = E.length / 2
    const step = Math.max(1, Math.floor(total / 2000))
    const lens = []
    for (let k = 0; k < E.length; k += 2 * step) {
      lens.push(Math.hypot(X[E[k]] - X[E[k + 1]], Y[E[k]] - Y[E[k + 1]]))
    }
    lens.sort((a, b) => a - b)
    return lens[lens.length >> 1] || 0
  })()
  const EDGE_MIN_PX = 2 // 屏幕上短于这么多像素的边就别画了

  // 度数最高的一批画名字（其余只在鼠标指着或点开时显示）
  const labelled = nodes
    .map((_n, i) => i)
    .sort((a, b) => (nodes[b].deg || 0) - (nodes[a].deg || 0))
    .slice(0, 80)
  const labelledSet = new Set(labelled)
  const LABEL_MAX = 40 // 一屏最多这么多名字，超了就前面的优先（还得互相避让，见 draw 里那段）

  const canvas = document.getElementById('cv')
  const hudStats = document.getElementById('stats')
  const hudInfo = document.getElementById('info')
  const boot = document.getElementById('boot')
  const q = document.getElementById('q')
  const HINT = '拖动平移 · 滚轮缩放 · 点一个概念看它连谁 · 双击空白复位'

  let ctx = null
  try {
    ctx = canvas && canvas.getContext ? canvas.getContext('2d') : null
  } catch (_error) {
    ctx = null
  }
  if (!ctx) {
    if (boot) boot.textContent = '这个浏览器给不出 2D 画布，图谱无法渲染'
    return
  }

  const dpr = Math.min(2, window.devicePixelRatio || 1)
  const view = { scale: 1, x: 0, y: 0 } // 世界 → 屏幕：sx = view.x + X * scale
  let hovered = -1
  let focus = -1
  let neighbours = null

  const cssW = () => canvas.clientWidth || window.innerWidth || 800
  const cssH = () => canvas.clientHeight || window.innerHeight || 600

  function escapeHtml(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  }

  function resize() {
    const w = cssW()
    const h = cssH()
    // **画布尺寸与 CSS 尺寸必须一起设**：canvas 是替换元素，`position:fixed; inset:0` 不会
    // 让它铺满（它保持固有的 300x150）—— 只设 width/height（后备缓冲）而 CSS 盒还是 300x150 的话，
    // `clientWidth` 就一直是 300，整张图会被算进左上角那一小块。第一版就是这么错的。
    canvas.style.width = `${w}px`
    canvas.style.height = `${h}px`
    canvas.width = Math.floor(w * dpr)
    canvas.height = Math.floor(h * dpr)
    draw()
  }

  function fit() {
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    for (let i = 0; i < N; i += 1) {
      if (X[i] < minX) minX = X[i]
      if (X[i] > maxX) maxX = X[i]
      if (Y[i] < minY) minY = Y[i]
      if (Y[i] > maxY) maxY = Y[i]
    }
    if (!Number.isFinite(minX)) {
      view.scale = 1
      view.x = cssW() / 2
      view.y = cssH() / 2
      draw()
      return
    }
    const pad = 28
    view.scale = Math.max(1e-6, Math.min((cssW() - pad * 2) / Math.max(1, maxX - minX),
                                         (cssH() - pad * 2) / Math.max(1, maxY - minY)))
    view.x = cssW() / 2 - ((minX + maxX) / 2) * view.scale
    view.y = cssH() / 2 - ((minY + maxY) / 2) * view.scale
    draw()
  }

  /** 屏幕坐标 → 命中的节点下标（找不到是 -1）。 */
  function pick(px, py) {
    const s = view.scale
    const wx = (px - view.x) / s
    const wy = (py - view.y) / s
    const tol = 7 / s
    let best = -1
    let bestD = tol * tol
    for (let i = 0; i < N; i += 1) {
      const dx = X[i] - wx
      const dy = Y[i] - wy
      const d = dx * dx + dy * dy
      if (d <= bestD) {
        bestD = d
        best = i
      }
    }
    return best
  }

  function draw() {
    const w = cssW()
    const h = cssH()
    const s = view.scale
    const ox = view.x
    const oy = view.y

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)

    // 视口在世界坐标里的范围（多留一点余量，免得边缘的点被切掉半个）
    const pad = 4 / s
    const x0 = -ox / s - pad
    const y0 = -oy / s - pad
    const x1 = (w - ox) / s + pad
    const y1 = (h - oy) / s + pad

    // ---- 世界坐标：点与线 ----
    ctx.setTransform(dpr * s, 0, 0, dpr * s, dpr * ox, dpr * oy)

    // 聚焦时把无关的点线压暗（不是藏起来）：结构还在，但一眼看得出它连的是谁
    const dimOthers = focus >= 0
    const near = (i) => !dimOthers || i === focus || (neighbours !== null && neighbours.has(i))

    // 屏幕上的边短到看不清时，整批不画（见 MEDIAN_EDGE 那段）
    const drawEdges = MEDIAN_EDGE * s >= EDGE_MIN_PX
    ctx.lineWidth = 0.7 / s
    ctx.strokeStyle = dimOthers ? 'rgba(96,116,152,0.10)' : 'rgba(96,116,152,0.26)'
    ctx.beginPath()
    for (let k = 0; drawEdges && k < E.length; k += 2) {
      const i = E[k]
      const j = E[k + 1]
      if (dimOthers && !(near(i) && near(j))) continue
      const xi = X[i]
      const yi = Y[i]
      const xj = X[j]
      const yj = Y[j]
      if ((xi < x0 && xj < x0) || (xi > x1 && xj > x1)) continue
      if ((yi < y0 && yj < y0) || (yi > y1 && yj > y1)) continue
      ctx.moveTo(xi, yi)
      ctx.lineTo(xj, yj)
    }
    ctx.stroke()

    // 一趟扫完（ORDER 已按线排好，每段一个桶一次 fill），而不是为了分色把全部点扫三遍
    for (let b = 0; b < 3; b += 1) {
      const from = BUCKET[b]
      const to = BUCKET[b + 1]
      if (from === to) continue
      ctx.beginPath()
      ctx.fillStyle = LINE_COLOR[BUCKET_LINE[b]]
      let any = false
      for (let k = from; k < to; k += 1) {
        const i = ORDER[k]
        if (X[i] < x0 || X[i] > x1 || Y[i] < y0 || Y[i] > y1) continue
        if (dimOthers && !near(i)) continue
        const deg = nodes[i].deg || 0
        const r = (i === focus ? 4 : deg >= 50 ? 2.6 : deg >= 10 ? 1.9 : 1.4) / s
        // **屏幕上小到看不出圆角时用方块**：实测 5 万个方块 3.4ms、5 万个圆 9.0ms。
        // 缩到最远时每个点不到 2px，方块与圆在这里没有任何视觉差别。
        if (r * s < 2) ctx.rect(X[i] - r, Y[i] - r, r * 2, r * 2)
        else {
          ctx.moveTo(X[i] + r, Y[i])
          ctx.arc(X[i], Y[i], r, 0, Math.PI * 2)
        }
        any = true
      }
      if (any) ctx.fill()
    }

    // ---- 屏幕坐标：名字 ----
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.font = '11px "Segoe UI","Microsoft YaHei",system-ui,sans-serif'
    ctx.textAlign = 'left'
    ctx.textBaseline = 'middle'
    // 只遍历"要画名字的那一小批"，而不是全部 5 万个（以前每帧都要扫 N 次 Set 查询）
    const extra = []
    if (hovered >= 0 && !labelledSet.has(hovered)) extra.push(hovered)
    if (focus >= 0 && focus !== hovered && !labelledSet.has(focus)) extra.push(focus)
    // **互相压住的名字谁也别画**（贪心避让）：度数最高的那批全是枢纽，它们本来就挤在中心，
    // 早先那版把它们全画出来，正中间是一团白字，等于没有标签。鼠标指着/点开的那两个永远画。
    const placed = []
    let shownLabels = 0
    for (const i of dimOthers ? extra : labelled.concat(extra)) {
      const sx = ox + X[i] * s
      const sy = oy + Y[i] * s
      if (sx < -40 || sy < -20 || sx > w + 40 || sy > h + 20) continue
      const forced = i === hovered || i === focus
      if (!forced) {
        if (shownLabels >= LABEL_MAX) continue
        let clash = false
        for (let p = 0; p < placed.length; p += 1) {
          if (Math.abs(placed[p][0] - sx) < 52 && Math.abs(placed[p][1] - sy) < 13) {
            clash = true
            break
          }
        }
        if (clash) continue
      }
      placed.push([sx, sy])
      shownLabels += 1
      ctx.fillStyle = forced ? '#ffffff' : 'rgba(201,211,230,0.78)'
      ctx.fillText(label(nodes[i]), sx + 6, sy)
    }
  }

  // **每帧最多画一次。** 拖动、滚轮、悬停原本是每个事件都直接 draw()；事件比一帧还密时
  // （拖动时一秒几百个 pointermove）画布就一直在重画，一次 12.8ms —— 操作会"粘住"，这才是
  // 它最初卡的主因。合并成每帧一次之后，帧时间就是那一次 draw 的时间，交互立刻顺。
  const RAF = (typeof window.requestAnimationFrame === 'function')
    ? (fn) => window.requestAnimationFrame(fn)
    : (fn) => setTimeout(fn, 16)
  let scheduled = false
  function scheduleDraw() {
    if (scheduled) return
    scheduled = true
    RAF(() => {
      scheduled = false
      draw()
    })
  }

  function setFocus(i) {
    focus = i
    if (i >= 0) {
      neighbours = new Set(adj[i])
      if (hudInfo) {
        const names = [...neighbours].slice(0, 6).map((j) => label(nodes[j])).join('、')
        const more = neighbours.size > 6 ? ' 等' : ''
        hudInfo.innerHTML = `<b>${escapeHtml(label(nodes[i]))}</b> · ${escapeHtml(lineLabel(nodes[i]))} · 连 ${neighbours.size} 个（${escapeHtml(names)}${more}）`
      }
    } else {
      neighbours = null
      if (hudInfo) hudInfo.textContent = HINT
    }
    scheduleDraw()
  }

  // ---- 交互 ----
  let dragging = false
  let lastX = 0
  let lastY = 0
  let moved = 0

  canvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return
    dragging = true
    moved = 0
    lastX = event.clientX
    lastY = event.clientY
    if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId)
  })
  canvas.addEventListener('pointermove', (event) => {
    if (dragging) {
      const dx = event.clientX - lastX
      const dy = event.clientY - lastY
      moved += Math.abs(dx) + Math.abs(dy)
      lastX = event.clientX
      lastY = event.clientY
      view.x += dx
      view.y += dy
      scheduleDraw()
      return
    }
    const rect = canvas.getBoundingClientRect()
    const hit = pick(event.clientX - rect.left, event.clientY - rect.top)
    if (hit !== hovered) {
      hovered = hit
      canvas.style.cursor = hit >= 0 ? 'pointer' : 'grab'
      scheduleDraw()
    }
  })
  canvas.addEventListener('pointerup', (event) => {
    if (!dragging) return
    dragging = false
    if (moved > 3) return // 拖过就是平移，不算点选
    const rect = canvas.getBoundingClientRect()
    setFocus(pick(event.clientX - rect.left, event.clientY - rect.top))
  })
  canvas.addEventListener('dblclick', () => setFocus(-1))
  canvas.addEventListener('pointerleave', () => {
    if (hovered >= 0) {
      hovered = -1
      scheduleDraw()
    }
  })
  canvas.addEventListener('wheel', (event) => {
    event.preventDefault()
    const rect = canvas.getBoundingClientRect()
    const px = event.clientX - rect.left
    const py = event.clientY - rect.top
    const next = Math.max(0.02, Math.min(60, view.scale * Math.exp(-event.deltaY * 0.0015)))
    const k = next / view.scale
    view.x = px - (px - view.x) * k
    view.y = py - (py - view.y) * k
    view.scale = next
    scheduleDraw()
  }, { passive: false })

  if (q) {
    q.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return
      const needle = q.value.trim().toLowerCase()
      if (!needle) return
      // 搜 `name`（主键也认，方便精确指定某一条线）。同名两页都在时**两个都算命中**，
      // 跳连接更多的那张并把另一条线也说出来 —— 合并视图里这正是要看见的东西。
      const exact = []
      for (let i = 0; i < N; i += 1) {
        if (label(nodes[i]).toLowerCase() === needle || nodes[i].id.toLowerCase() === needle) exact.push(i)
      }
      let pool = exact
      if (!pool.length) {
        pool = []
        for (let i = 0; i < N; i += 1) {
          if (label(nodes[i]).toLowerCase().includes(needle)) pool.push(i)
        }
      }
      if (!pool.length) {
        if (hudInfo) hudInfo.textContent = `没找到「${q.value.trim()}」`
        return
      }
      pool.sort((a, b) => (nodes[b].deg || 0) - (nodes[a].deg || 0))
      const hit = pool[0]
      view.scale = Math.max(view.scale, 2.5)
      view.x = cssW() / 2 - X[hit] * view.scale
      view.y = cssH() / 2 - Y[hit] * view.scale
      setFocus(hit)
      if (pool.length > 1 && hudInfo) {
        hudInfo.innerHTML += `<div>两条线都有「${escapeHtml(label(nodes[hit]))}」：${escapeHtml(pool.map((i) => lineLabel(nodes[i])).join('、'))}</div>`
      }
    })
  }

  if (hudStats) {
    let wiki = 0
    let chat = 0
    for (let i = 0; i < N; i += 1) {
      if (nodes[i].line === 'wiki') wiki += 1
      else if (nodes[i].line === 'chat') chat += 1
    }
    hudStats.textContent = `${N.toLocaleString('en-US')} 个概念 · ${(E.length / 2).toLocaleString('en-US')} 条链接`
      + ` · ${LINE_LABELS.wiki || 'wiki'} ${wiki.toLocaleString('en-US')} / ${LINE_LABELS.chat || 'chat'} ${chat.toLocaleString('en-US')}`
  }
  if (hudInfo) hudInfo.textContent = HINT
  if (boot) boot.style.display = 'none'

  window.addEventListener('resize', resize)
  resize()
  fit()
  // 给冒烟测试一个把手：能问它"画了多少个点、多少条线"
  window.__GRAPH2D__ = { nodes: N, edges: E.length / 2, draw, fit, view }
})()
