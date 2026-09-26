/**
 * 右键"快速回复"：**真的派一次右键事件**，看菜单出没出来、点下去发了什么。
 *
 * 为什么不是文本断言：这条链路的失败方式全是静默的——菜单弹在窗口外面（球形态那个 76x76 的
 * 窗口里根本放不下）、点了一个人却发出"帮我回老王"（名单没接上）、或者菜单压根没绑。
 * 这些在源码里都"看着像对的"。
 *
 * 两条说明：
 * - 有外壳时菜单是**主进程弹的原生菜单**（道具在 `quick-menu.cjs`，那一条单独有测试）：
 *   这里验的是"右键之后页面做了什么"——把名单交给主进程、拿到选择、发出那句话。
 *   页内菜单那条路**已经删了**：降级那条路不接管右键（原生菜单里的"复制"要留给用户），
 *   留着它就是没人走的死代码。
 * - `/api/status` 是桩：真机上那份名单来自守护进程（`quickReplyContacts` 配置键）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { JSDOM } from 'jsdom'

const RENDERER = readFileSync(join(process.cwd(), 'resources', 'panel', 'renderer.js'), 'utf8')

const HTML = `<!doctype html><html><body>
  <button id="ball" hidden><span class="glow"></span><span class="face"></span></button>
  <header><span id="status"></span><button id="collapse" hidden>收起</button></header>
  <main id="log"></main>
  <form id="composer"><textarea id="input"></textarea><button id="send">发送</button></form>
  <footer id="foot"></footer>
</body></html>`

interface Booted {
  window: any
  /** 页面向 /api/ask 发出去的那些请求体 */
  asked: string[]
  /** 交给主进程弹原生菜单的那些名单（有外壳时走这条路） */
  nativeMenus: string[][]
  /** setMode 被怎么调过（用来钉"右键不许顺手把窗口弹出来"） */
  modeCalls: string[]
  rightClick: (x?: number, y?: number) => void
  menuItems: () => string[]
  clickItem: (label: string) => void
  setNativePick: (value: {kind: string, name?: string, label?: string, prompt?: string} | null) => void
  /** 主进程那一侧的通知（形态由它说了算） */
  notifyMode: (payload: Record<string, unknown>) => void
  bodyHas: (cls: string) => boolean
}

async function boot(options: { quickReplies?: string[]; shell?: boolean; stayInBall?: boolean } = {}): Promise<Booted> {
  const dom = new JSDOM(HTML, { runScripts: 'dangerously', url: 'http://127.0.0.1:8766/panel' })
  const { window } = dom
  const asked: string[] = []
  const nativeMenus: string[][] = []
  const modeCalls: string[] = []
  let modeListener: ((payload: any) => void) | null = null
  // 原生菜单那条路：桩里让它返回名单里的第一个（或者 options.nativePick 指定的），
  // `null` 表示"用户把菜单关掉了、没选"
  let nativePick: {kind: string, name?: string, label?: string, prompt?: string} | null | undefined = undefined

  window.setInterval = () => 0
  window.matchMedia = () => ({ matches: false, media: '', addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  window.fetch = (async (url: string, init: any) => {
    if (String(url).includes('/api/ask')) {
      asked.push(JSON.parse(init?.body || '{}').text)
      return { ok: true, json: async () => ({ ok: true, reply: '嗯' }) }
    }
    return {
      ok: true,
      json: async () => ({
        channelActive: false, quota: { used: 0, limit: 100 }, memoryBucket: 'u1',
        aiConfigured: true, memoryNote: '记忆桶：u1',
        quickReplies: options.quickReplies ?? [],
      }),
    }
  }) as any
  if (options.shell !== false) {
    window.weflowPanel = {
      setMode: (mode: string) => { modeCalls.push(mode); return Promise.resolve({}) },
      openQuickMenu: (labels: string[]) => {
        nativeMenus.push(labels)
        // 菜单回传的是**结构化的一项**（`{kind:'contact'|'action', ...}`），不是光一个名字：
        // 菜单有两段，页面得知道点的是哪一段。没指定就当作"点了名单里的第一个人"。
        const fallback = labels[0] ? { kind: 'contact', name: labels[0] } : null
        return Promise.resolve(nativePick === undefined ? fallback : nativePick)
      },
      onMode: (cb: any) => { modeListener = cb },
      dragStart: () => Promise.resolve(), dragMove: () => Promise.resolve(), dragEnd: () => Promise.resolve(),
      info: () => Promise.resolve({}), quit: () => Promise.resolve(),
    }
  }

  const script = window.document.createElement('script')
  script.textContent = RENDERER
  window.document.body.appendChild(script)
  await wait(0)
  await wait(0)

  const menuItems = () => [...window.document.querySelectorAll('.quick-item')].map((n: any) => n.textContent)
  const notifyMode = (payload: Record<string, unknown>) => { modeListener?.(payload) }
  // 后面的用例默认在**对话形态**下右键（球形态那条是单独一个用例，它自己从球形态起步）
  if (options.shell !== false && options.stayInBall !== true) {
    notifyMode({ mode: 'chat', side: 'left', anchorY: 'bottom', bubbleHeight: 560 })
  }
  return {
    window,
    asked,
    nativeMenus,
    modeCalls,
    setNativePick: (value: {kind: string, name?: string} | null) => { nativePick = value },
    rightClick: (x = 30, y = 40) => {
      // 右键是个 MouseEvent（`contextmenu`），要带上坐标——菜单就在那个点上弹
      window.document.body.dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y }))
    },
    menuItems,
    notifyMode,
    clickItem: (label) => {
      const item = [...window.document.querySelectorAll('.quick-item')].find((n: any) => n.textContent === label) as any
      assert.ok(item, `菜单里应当有「${label}」`)
      item.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
    },
    bodyHas: (cls) => window.document.body.classList.contains(cls),
  }
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

test('有外壳时右键走**原生菜单**：窗口不动，也不去展开（用户踩到的那一下）', async () => {
  // 用户的原话是"右键的话，我看第二大脑窗口也弹出来了，右键应该算是快速功能"。
  // 上一版在球形态下先 requestMode('chat') 再弹，于是右键 = 把窗口弹出来 ✗。
  // 现在菜单由主进程画在窗口外面，窗口一个尺寸都不用改。
  const app = await boot({ quickReplies: ['咸鱼梦想家'], stayInBall: true })
  app.setNativePick(null)          // 只看了一眼、把菜单关掉（没选人）
  app.rightClick()
  await wait(30)

  assert.deepEqual(app.nativeMenus, [['咸鱼梦想家']], '名单交给主进程去弹原生菜单')
  assert.equal(app.window.document.querySelectorAll('.quick-menu').length, 0, '页内菜单不该在这条路上出现')
  assert.deepEqual(app.modeCalls, [], '**不许顺手切形态**——那正是"窗口弹出来"的来头')
  assert.ok(app.bodyHas('mode-ball'), '还在球形态')
  app.window.close()
})

test('球形态下**选中一个人**才展开窗口 —— 候选得有地方看', async () => {
  // 与上一条成对：右键本身不许动窗口；但选完人以后必须展开，
  // 否则候选出在一个 76x76 的球里，谁也看不到。
  const app = await boot({ quickReplies: ['甲'], stayInBall: true })
  app.rightClick()
  await wait(30)
  assert.deepEqual(app.modeCalls, ['chat'], '选中之后才切形态')
  assert.equal(app.asked.length, 1)
  app.window.close()
})

test('原生菜单里选了一个人 → 照样发出那句带名字的请求', async () => {
  const app = await boot({ quickReplies: ['咸鱼梦想家', '老王'] })
  app.setNativePick({ kind: 'contact', name: '老王' })
  app.rightClick()
  await wait(30)
  assert.equal(app.asked.length, 1)
  assert.match(app.asked[0], /老王/)
  assert.match(app.asked[0], /draft_reply/, '发出去的仍是点名工具的请求')
  const meTurn = app.window.document.querySelector('.turn.me')
  assert.equal(meTurn.textContent, '快速回复：老王', '对话里显示的是人话，不是那句机器指令')
  app.window.close()
})

test('原生菜单里选了一个快捷功能 → 发出它自己的话术，而不是起草', async () => {
  // 菜单第二段（快捷功能）。**话术由菜单项带回来**，页面不按 id 查表——所以这里断言的是
  // "原样发出"，而不是"发出了某个我们认识的 id 对应的话"。
  const action = {
    kind: 'action', label: '我的待办',
    prompt: '用 get_todos 把我的待办列出来，按紧急程度排。',
  }
  const app = await boot({ quickReplies: ['甲'], stayInBall: true })
  app.setNativePick(action)
  app.rightClick()
  await wait(30)
  assert.equal(app.asked.length, 1)
  assert.equal(app.asked[0], action.prompt, '原样发出菜单带回来的话术')
  assert.doesNotMatch(app.asked[0], /draft_reply/, '选功能不该走起草那条路')
  assert.deepEqual(app.modeCalls, ['chat'], '功能也要展开——球那个 76x76 里看不到回答')
  const meTurn = app.window.document.querySelector('.turn.me')
  assert.equal(meTurn.textContent, '我的待办', '对话里显示标签，不是那句机器指令')
  app.window.close()
})

test('回传的形状不对（比如上一版的字符串契约）→ 忽略，不是当成起草发出去', async () => {
  // 菜单与页面是两个进程里的两份代码，升级过程中"一边新一边旧"是会发生的。
  // 旧契约回的是一个裸名字，若把它当联系人用，发出去的就是一次**没人让它发的**起草。
  const app = await boot({ quickReplies: ['甲'] })
  app.setNativePick('老王' as any)
  app.rightClick()
  await wait(30)
  assert.equal(app.asked.length, 0, '不认识的东西不该被当成请求发出去')
  assert.deepEqual(app.modeCalls, [])
  app.window.close()
})

test('原生菜单被关掉（没选 / 选了「关闭悬浮球」）→ 页面什么都不发', async () => {
  // "看了一眼就关掉"与"点了关闭悬浮球"回到页面时都是 null：后者主进程那一侧已经把窗口
  // 收起来了（那一步在 `panel-quick-menu.test.ts` 里钉着）。页面这两个都不该有动作，
  // 尤其**不许**顺手切形态——那正是"右键把窗口弹出来"的来头。
  const app = await boot({ quickReplies: ['甲'] })
  app.setNativePick(null)
  app.rightClick()
  await wait(30)
  assert.equal(app.asked.length, 0, '取消不该触发任何请求')
  assert.deepEqual(app.modeCalls, [], '也不该动窗口')
  app.window.close()
})

test('浏览器降级那条路不接管右键（原生菜单里的"复制"要留给用户）', async () => {
  const app = await boot({ quickReplies: ['甲'], shell: false })
  const event = new app.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true })
  app.window.document.body.dispatchEvent(event)
  assert.equal(app.window.document.querySelectorAll('.quick-menu').length, 0, '降级那条路不弹我们的菜单')
  assert.equal(event.defaultPrevented, false, '也别拦掉浏览器自己的菜单')
  app.window.close()
})
