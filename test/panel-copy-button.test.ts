/**
 * 面板里那个复制按钮：**真的把它跑起来**，而不是读一遍源码看它像不像。
 *
 * 为什么不用文本断言（这个仓库里 `panel-packaging.test.ts` 的既有手法）：这条路径的
 * 失败方式全是**静默**的——按钮挂到了错误的那一侧（用户复制到自己发的话）、
 * 复制不到东西却回一句"已复制"（用户以为剪贴板里有，粘出来是上一次的旧内容）、
 * 或者按钮压根没被 append 到节点上。文本断言对这三种都只能"看着像对"。
 *
 * 用 jsdom 起一个真页面，把 `resources/panel/renderer.js` 当经典脚本注进去，
 * 走真实的 `submit → ask() → fetch → addTurn` 那条路，然后 `click()` 那个按钮。
 *
 * **这份测试证明不了什么，也说清楚**：
 *
 * - jsdom 不是 Chromium。它**没法**复现"Windows 上 `-webkit-app-region: drag` 吞掉鼠标
 *   事件"那类故障——上一轮的"点不动"就是被这种合成点击骗过去的（用 CDP 的
 *   `element.click()` 验过"球能点开"，那绕过了真实输入）。所以这里证的是**处理逻辑**，
 *   不是"用真鼠标点得动"。真机上还得人手点一次。
 * - `navigator.clipboard` 在 jsdom 里没有实现，是本文件装的桩；于是它证的是
 *   "成功/失败两条分支各自怎么反应"，不是"系统剪贴板真的被写了"。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { JSDOM } from 'jsdom'

const RENDERER = readFileSync(join(process.cwd(), 'resources', 'panel', 'renderer.js'), 'utf8')

/** renderer.js 顶层要拿的那几个节点（`ball`/`collapse` 只在有外壳时才用到，这里不给） */
const HTML = `<!doctype html><html><body>
  <div id="status"></div>
  <div id="log"></div>
  <div id="foot"></div>
  <form id="composer"><input id="input"><button id="send">发送</button></form>
  <span id="ball"></span><button id="collapse"></button>
</body></html>`

interface Booted {
  dom: JSDOM
  /** 点一下"发送"并等这条回答渲染完 */
  ask: (text: string) => Promise<void>
  copied: string[]
}

/**
 * 起一个页面并把 renderer.js 注进去。
 *
 * `writeText` 由调用方给：成功那条传一个会 resolve 的，失败那条传一个 reject 的
 * （两种都在真机上会遇到——权限被拒、或者老引擎里根本没有这个 API）。
 */
async function boot(writeText: (text: string) => Promise<void>): Promise<Booted> {
  const dom = new JSDOM(HTML, { runScripts: 'dangerously', url: 'http://127.0.0.1:8766/panel' })
  const { window } = dom
  const copied: string[] = []

  window.fetch = (async (url: string) => {
    if (String(url).includes('/api/ask')) {
      return { ok: true, json: async () => ({ ok: true, reply: '建议这样回：\n1. 今天下班前发你' }) }
    }
    return { ok: true, json: async () => ({ channelActive: false, quota: { used: 1, limit: 100 }, memoryBucket: 'u1' }) }
  }) as any
  Object.defineProperty(window.navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async (text: string) => { copied.push(text); return writeText(text) } },
  })
  // 页面自己有一个 30 秒轮询状态（`setInterval(refreshStatus, 30000)`）。在测试里留着它，
  // 关窗之后还会打一次，抛出的是"close 之后还在动"的噪音，把测试结果染成红的。
  window.setInterval = (() => 0) as any

  const script = window.document.createElement('script')
  script.textContent = RENDERER
  window.document.body.appendChild(script)

  const ask = async (text: string): Promise<void> => {
    const input = window.document.getElementById('input') as any
    input.value = text
    window.document.getElementById('composer')!
      .dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
    // `ask()` 是 `void ask(text)` 起的，里面还有 fetch→json→addTurn→`finally` 里的
    // 又一次 refreshStatus。要让这些真的跑完再断言，得让出**宏任务**而不只是微任务。
    for (let i = 0; i < 3; i++) await new Promise((resolve) => setTimeout(resolve, 0))
  }
  return { dom, ask, copied }
}

test('助手回答下面有复制按钮，复制的是那条回答的全文', async () => {
  const { dom, ask, copied } = await boot(async () => {})
  const { window } = dom
  await ask('帮我回老王')

  const button = window.document.querySelector('.turn.it .copy') as any
  assert.ok(button, '助手的回答下面应当有复制按钮')
  assert.equal(button.textContent, '复制')

  button.click()
  for (let i = 0; i < 4; i++) await Promise.resolve()
  assert.deepEqual(copied, ['建议这样回：\n1. 今天下班前发你'], '复制的是整条回答（含换行）')
  assert.equal(button.textContent, '已复制')
  assert.equal(button.textContent === '复制', false)
  window.close()
})

test('复制不成功时**不许**回一句"已复制"', async () => {
  // 假装成功比失败更坏：用户以为剪贴板里有东西，粘出来是上一次的旧内容。
  // 退路是把这段文字选上，让用户自己按 Ctrl+C。
  const { dom, ask, copied } = await boot(async () => { throw new Error('没有剪贴板权限') })
  const { window } = dom
  await ask('帮我回老王')

  const button = window.document.querySelector('.turn.it .copy') as any
  button.click()
  for (let i = 0; i < 4; i++) await Promise.resolve()

  assert.equal(copied.length, 1, '确实试过写剪贴板')
  assert.equal(button.textContent, '按 Ctrl+C', '失败要说失败')
  assert.match(String(window.getSelection()), /建议这样回/, '并且把这段选上，退路能用')

  // 还有一条：那句"没复制成"的话不能只是一闪而过就变回"复制"
  assert.notEqual(button.textContent, '已复制')
  window.close()
})

test('只有助手的回答带复制按钮 —— 自己发的话、"正在输入"、错误提示都不带', async () => {
  const { dom, ask } = await boot(async () => {})
  const { window } = dom
  await ask('帮我回老王')

  assert.equal(window.document.querySelectorAll('.turn.me .copy').length, 0, '自己发的话不用复制')
  assert.equal(window.document.querySelectorAll('.turn .copy').length, 1, '一轮对话只有一个')
  // 出错那一支走的是 addTurn('err', ...)，也得没有按钮
  window.fetch = (async () => ({ ok: false, json: async () => ({ code: 'BUSY', error: '忙' }) })) as any
  await ask('再来一句')
  assert.equal(window.document.querySelectorAll('.turn.err .copy').length, 0)
  assert.equal(window.document.querySelectorAll('.turn .copy').length, 1, '多出来的那个是上一条助手回答的')
  window.close()
})

test('回答里的尖括号是文字，不是标签 —— 复制按钮不改变这条纪律', async () => {
  // 这条是防"为了让复制按钮好写而引入 innerHTML"。renderer.js 里 textContent 那条线
  // 由 `panel-packaging.test.ts` 静态钉着，这里再从**行为**上确认一次：
  // 回答里带 <script> 时，页面上不该多出一个 script 元素。
  const { dom, ask } = await boot(async () => {})
  const { window } = dom
  const before = window.document.querySelectorAll('script').length
  const evil = '<script>window.__pwned = 1</script><img src=x onerror="window.__pwned=2">'
  window.fetch = (async (url: string) => {
    if (String(url).includes('/api/ask')) return { ok: true, json: async () => ({ ok: true, reply: evil }) }
    return { ok: true, json: async () => ({ channelActive: false, quota: { used: 1, limit: 100 }, memoryBucket: 'u1' }) }
  }) as any
  await ask('来点坏东西')

  assert.equal(window.document.querySelectorAll('script').length, before, '回复里的 script 不许被当成标签')
  assert.equal((window as any).__pwned, undefined)
  assert.match(window.document.querySelector('.turn.it .turn-text')!.textContent!, /<script>/,
    '它应当原样显示成文字')
  window.close()
})
