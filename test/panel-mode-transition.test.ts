/**
 * 球的展开/收起：**形态只能有一个来源**。
 *
 * 从前是两条路：球的点击在页面里自己 `classList.replace`，托盘那条靠主进程发
 * `panel:mode`。两条路行为不同（一边球是瞬间消失的、一边等主进程），而"展开那一下太突兀"
 * 正是从这个形状里长出来的。现在页面只负责**请求**，形态由主进程说——这份测试盯的就是
 * "页面不再自己换 class"这件事，以及"收起时先淡掉内容、再换形态"那个顺序。
 *
 * 两条说明（免得把这里的结果当成了不起的证据）：
 *
 * - jsdom 不是 Chromium。CSS 过渡/动画在这里**不会真的跑**，所以这里验的是"加没加那个类、
 *   调用参数对不对"，不是"看起来顺不顺"。好看只能人看。
 * - `matchMedia` 在 jsdom 里根本不存在（本文件自己装了个桩）。装它不是为了迁就实现，
 *   是因为那条分支决定了窗口那半做不做动画——它必须有测试。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { JSDOM } from 'jsdom'

const RENDERER = readFileSync(join(process.cwd(), 'resources', 'panel', 'renderer.js'), 'utf8')
const CLOSE_FADE_MS = 130   // 与 main.cjs / CSS 里那个值一致：气泡淡出多久

const HTML = `<!doctype html><html><body>
  <button id="ball" hidden><span class="glow"></span><span class="face"></span></button>
  <header><span id="status"></span><button id="collapse" hidden>收起</button></header>
  <main id="log"></main>
  <form id="composer"><textarea id="input"></textarea><button id="send">发送</button></form>
  <footer id="foot"></footer>
</body></html>`

interface Booted {
  window: any
  calls: { mode: string; opts: any }[]
  /** 展开：主进程把窗口一次放到位、通知页面（含气泡方位） */
  notifyChat: (side?: string, anchorY?: string, bubbleHeight?: number) => void
  /** 收起第一步：让页面把气泡淡掉（窗口还是大的） */
  notifyFade: (fadeMs?: number) => void
  /** 收起第二步：主进程已经把窗口缩回去了 */
  notifyShrunk: () => void
  /** 点一下球（按下、抬起，指针没动 = 一次点击） */
  clickBall: () => void
  clickCollapse: () => void
  dragBall: () => void
  bodyHas: (cls: string) => boolean
}

async function boot(options: { reducedMotion?: boolean } = {}): Promise<Booted> {
  const dom = new JSDOM(HTML, { runScripts: 'dangerously', url: 'http://127.0.0.1:8766/panel' })
  const { window } = dom
  const calls: { mode: string; opts: any }[] = []
  let modeListener: ((mode: string) => void) | null = null

  // 页面里那个 30 秒状态轮询在测试里只会制造噪音（关窗之后还会打一次）
  window.setInterval = () => 0
  window.matchMedia = (query: string) => ({
    matches: query.includes('prefers-reduced-motion') && options.reducedMotion === true,
    media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
  })
  window.fetch = (async () => ({ ok: true, json: async () => ({ channelActive: false, quota: { used: 0, limit: 100 } }) })) as any
  // 外壳：preload 暴露的那几个方法（这里只需要 setMode 与 onMode）
  window.weflowPanel = {
    // `{ ...opts }` 是有意的：`opts` 是**页面那个 realm** 建的对象字面量，原型不是这个
    // 测试 realm 的 `Object.prototype`，`assert.deepEqual`（严格版）会因此判不等——
    // 两个对象打印出来一模一样却报失败，就是它。摊平一次换成这边 realm 的普通对象。
    setMode: (mode: string, opts: any) => { calls.push({ mode, opts: { ...opts } }); return Promise.resolve({ mode }) },
    onMode: (cb: (mode: string) => void) => { modeListener = cb },
    dragStart: () => Promise.resolve(), dragMove: () => Promise.resolve(), dragEnd: () => Promise.resolve(),
    info: () => Promise.resolve({}), quit: () => Promise.resolve(),
  }

  const script = window.document.createElement('script')
  script.textContent = RENDERER
  window.document.body.appendChild(script)

  const pointer = (type: string, x: number, y: number) => {
    const event = new window.MouseEvent(type, { button: 0, screenX: x, screenY: y, bubbles: true })
    return event
  }
  const ball = window.document.getElementById('ball') as any
  const collapse = window.document.getElementById('collapse') as any

  // 让加载时那几件事（`void refreshStatus()` 的 fetch → 写状态条）跑完再交给用例。
  // 用**宏任务**而不是 `await Promise.resolve()`：只让微任务的话，用例可能在它还没落地时
  // 就 `window.close()`，那条续作会在关窗之后去读 `document.body`，抛成 unhandledRejection，
  // 把整个文件判红（而每条用例本身都是过的）。
  await wait(0)
  await wait(0)
  return {
    window,
    calls,
    notifyChat: (side = 'left', anchorY = 'bottom', bubbleHeight = 560) => {
      modeListener?.({ mode: 'chat', side, anchorY, bubbleHeight })
    },
    notifyFade: (fadeMs = CLOSE_FADE_MS) => { modeListener?.({ mode: 'ball', fadeMs }) },
    notifyShrunk: () => { modeListener?.({ mode: 'ball', done: true, fadeMs: 0 }) },
    clickBall: () => {
      ball.dispatchEvent(pointer('pointerdown', 1000, 800))
      window.dispatchEvent(pointer('pointerup', 1000, 800))
    },
    clickCollapse: () => { collapse.dispatchEvent(new window.MouseEvent('click', { bubbles: true })) },
    dragBall: () => {
      ball.dispatchEvent(pointer('pointerdown', 1000, 800))
      window.dispatchEvent(pointer('pointermove', 1060, 830))     // 动了 60px：这是拖，不是点
      window.dispatchEvent(pointer('pointerup', 1060, 830))
    },
    bodyHas: (cls) => window.document.body.classList.contains(cls),
  }
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

test('有外壳时球才在场：body 上挂 shell，初始是球形态', async () => {
  const app = await boot()
  assert.ok(app.bodyHas('shell'), '球一直在场这件事靠 shell 这个类（浏览器那条路没有它）')
  assert.ok(app.bodyHas('mode-ball'), '初始形态是球')
  app.window.close()
})

test('没有外壳（浏览器降级）时球仍然不许露出来', async () => {
  // 球现在靠 `body.shell` 决定显不显示（从前的 `hidden` 属性会被 author 样式盖掉：
  // UA 的 `[hidden] { display: none }` 拗不过作者写的 `#ball { display: grid }`）。
  // 这条盯的就是那个坑：降级那条路没有 shell 这个类，球必须还是看不见的。
  const dom = new JSDOM(HTML, { runScripts: 'dangerously', url: 'http://127.0.0.1:8766/panel' })
  const { window } = dom
  window.setInterval = () => 0
  window.fetch = (async () => ({ ok: true, json: async () => ({ quota: { used: 0, limit: 100 } }) })) as any
  const script = window.document.createElement('script')
  script.textContent = RENDERER
  window.document.body.appendChild(script)
  await wait(0)

  assert.equal(window.document.body.classList.contains('shell'), false)
  assert.equal(window.document.body.classList.contains('mode-ball'), false)
  assert.equal((window.document.getElementById('ball') as any).hidden, true, '浏览器那条路的球必须还是 hidden')
  window.close()
})

test('点球只**请求**换形态，页面自己不先换 —— 形态只有一个来源', async () => {
  const app = await boot()
  app.clickBall()
  assert.deepEqual(app.calls, [{ mode: 'chat', opts: { animate: true } }])
  assert.ok(app.bodyHas('mode-ball'), '主进程还没说切，页面不许自己先换')
  assert.equal(app.bodyHas('mode-chat'), false)

  // 主进程决定完了才通知页面
  app.notifyChat()
  assert.ok(app.bodyHas('mode-chat'))
  assert.equal(app.bodyHas('mode-ball'), false, '两个形态类不许同时挂着')
  app.window.close()
})

test('球是开关：展开状态下再点一下就是收起', async () => {
  const app = await boot()
  app.clickBall()
  app.notifyChat()

  app.clickBall()
  assert.deepEqual(app.calls[1], { mode: 'ball', opts: { animate: true } },
    '展开时点球 = 收起（用户要的"点击收起"）')
  app.notifyFade()
  app.notifyShrunk()
  app.window.close()
})

test('气泡方位照主进程说的挂类，翻边后不留旧类', async () => {
  // `side`/`anchorY` 是主进程用 `bubbleLayout` 算的（页面算不出来：它不知道自己
  // 在屏幕上的位置，也不知道工作区多大）。球钉在窗口的哪个角就靠这几个类。
  const app = await boot()
  app.notifyChat('left', 'bottom')
  assert.ok(app.bodyHas('bubble-left'))
  assert.equal(app.bodyHas('bubble-right'), false)
  assert.equal(app.bodyHas('anchor-top'), false, '底对齐是默认，不需要类')

  app.notifyChat('right', 'top')
  assert.ok(app.bodyHas('bubble-right'), '球被拖到屏幕左缘 → 气泡翻到右边')
  assert.equal(app.bodyHas('bubble-left'), false, '旧的方位类必须摘掉')
  assert.ok(app.bodyHas('anchor-top'), '顶上放不下 → 改成顶对齐')

  // 回到球形态时，方位类不该留在身上（否则下次展开会带着上一次的方位）。
  // 顺序：淡出期间还在对话形态（方位留着，气泡还看得见），主进程缩完窗口才换。
  app.clickCollapse()
  app.notifyFade()
  assert.ok(app.bodyHas('closing'), '第一步只淡出，先别换')
  assert.ok(app.bodyHas('bubble-right'), '淡出期间方位还得留着')
  app.notifyShrunk()
  assert.equal(app.bodyHas('bubble-right'), false)
  assert.equal(app.bodyHas('anchor-top'), false)
  assert.ok(app.bodyHas('mode-ball'))
  app.window.close()
})

test('气泡高度由主进程给（屏幕不够高时它变矮，球才不用动）', async () => {
  const app = await boot()
  app.notifyChat('left', 'top', 378)
  assert.equal(app.window.document.body.style.getPropertyValue('--bubble-height'), '378px')
  app.notifyChat('left', 'bottom', 560)
  assert.equal(app.window.document.body.style.getPropertyValue('--bubble-height'), '560px')
  // 载荷里给了个不三不四的值时退回默认，而不是写一个 NaN 进样式
  app.notifyChat('left', 'bottom', 0)
  assert.equal(app.window.document.body.style.getPropertyValue('--bubble-height'), '560px')
  app.window.close()
})

test('拖球不是点球 —— 拖完不许展开', async () => {
  const app = await boot()
  app.dragBall()
  assert.deepEqual(app.calls, [], '拖过了就不算点击（阈值 4px，这里动了 60px）')
  assert.ok(app.bodyHas('mode-ball'))
  app.window.close()
})

test('收起：先把内容淡掉，再换形态（不是"啪"地消失）', async () => {
  const app = await boot()
  app.clickBall()
  app.notifyChat()

  app.clickCollapse()
  assert.deepEqual(app.calls[1], { mode: 'ball', opts: { animate: true } })
  app.notifyFade()
  assert.ok(app.bodyHas('closing'), '先进入淡出状态')
  assert.ok(app.bodyHas('mode-chat'), '这会儿还是对话形态——气泡的 display:none 要等窗口缩完')
  assert.equal(app.bodyHas('mode-ball'), false)

  // 主进程那边等这 130ms 过去、缩了窗口，才发第二步
  app.notifyShrunk()
  assert.ok(app.bodyHas('mode-ball'), '窗口缩回去之后才换成球')
  assert.equal(app.bodyHas('closing'), false, '过渡类要摘掉，否则下次展开带着它')
  assert.equal(app.bodyHas('mode-chat'), false)
  app.window.close()
})

test('系统开了"减少动态效果"：窗口那半别补间，收起也别等淡出', async () => {
  const app = await boot({ reducedMotion: true })
  app.clickBall()
  assert.deepEqual(app.calls[0], { mode: 'chat', opts: { animate: false } },
    '窗口几何的动画在主进程，CSS 管不着——这个标记是唯一能让它停下来的东西')
  app.notifyChat()
  app.notifyFade(0)
  assert.equal(app.bodyHas('closing'), false, '不做动画时直接换，别白等 130ms')
  app.notifyShrunk()
  assert.ok(app.bodyHas('mode-ball'))
  app.window.close()
})

test('托盘那条路（主进程先动、页面后知道）也走同一套交接', async () => {
  // 这条是这次改动的重点之一：从前托盘展开是"主进程换完再告诉页面"，球是瞬间消失的。
  const app = await boot()
  app.notifyChat()
  assert.ok(app.bodyHas('mode-chat'))
  assert.deepEqual(app.calls, [], '页面没有请求过——是主进程自己切的（托盘/快捷键那条路）')
  app.notifyFade()
  assert.ok(app.bodyHas('closing'))
  app.notifyShrunk()
  assert.ok(app.bodyHas('mode-ball'))
  app.window.close()
})
