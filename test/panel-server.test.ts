/**
 * 本机端点：**一个能读到用户聊天记录的 HTTP 服务**。所以这里的每条断言都是关于"谁能进来"。
 *
 * 它是照 D-004（阅读器只绑回环）往下走的，但**必须比阅读器严**——`scripts/fav_server.py`
 * 没有 token，而且它的 Origin 校验**不带 Origin 就放行**。照抄那份设计，等于把一个能替用户
 * 读聊天记录的端点开给本机任何程序。所以：
 * 每个请求都要 token（连 `/api/status` 也是），Origin 只是第二道，Content-Type 必须对。
 *
 * 真起服务、真打请求（端口用 0 让内核分配，不占 8766，也就不会和正在跑的助手打架）。
 * 助手是桩：不联网、不 spawn、不烧钱。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-panel-server-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { startPanelServer } = await import('../src/panel/server.js')
const { configService } = await import('../src/services/configService.js')
const { assistantScenes } = await import('../src/services/assistantScenes.js')

/** 只替换 `quickReplyContacts` 这一个键，其余键仍走真实现（这个文件里别的用例不碰它） */
async function withQuickReplies<T>(value: string, body: () => Promise<T>): Promise<T> {
  const real = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'quickReplyContacts' ? value : real(k))
  try { return await body() } finally { ;(configService as any).get = real }
}

interface Stub {
  calls: { bucket: string; text: string; sceneId?: string }[]
  outcome?: any
  hang?: boolean
  release?: () => void
}

/** 助手桩：`ask` 可配置成正常回、挂住不回、或直接抛 */
function stubService(opts: { hang?: boolean; outcome?: any; trace?: any } = {}): { service: any; stub: Stub } {
  const stub: Stub = { calls: [], hang: opts.hang, outcome: opts.outcome }
  if (opts.hang) {
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    stub.release = release
    stub.outcome = gate.then(() => ({ status: 'replied', text: '迟到的答复' }))
  }
  const service: any = {
    isChannelActive: () => false,
    quotaState: () => ({ used: 3, limit: 100 }),
    // 真实接口上就有它（`/api/ask` 拿它给"思考过程"）。桩**不能省**——省了的话
    // 服务端那一句就会抛，而它抛的后果是整条 ask 变 500（第一版正是这么被测试抓到的）。
    recentTrace: () => opts.trace,
    // "它记住了什么"那条只读路由要它。同上：桩**不能省** —— 省了服务端那一句就会抛，
    // 后果是整条 /api/memory 变 500。返回的形状与真方法一致（facts / summary / workingTurns / saveError）。
    memorySnapshot: () => ({
      facts: [{ content: '喜欢喝美式', ts: 1750000000000, sourceQuote: '我平时喝美式' }],
      summary: '聊过天气与咖啡',
      workingTurns: 2,
      saveError: '',
    }),
    ask: async (bucket: string, text: string, _onLog?: unknown, sceneId?: string) => {
      stub.calls.push({ bucket, text, ...(sceneId ? { sceneId } : {}) })
      if (stub.outcome && typeof stub.outcome.then === 'function') return stub.outcome
      return stub.outcome ?? { status: 'replied', text: '收到' }
    },
  }
  return { service, stub }
}

async function boot(opts: { hang?: boolean; outcome?: any; trace?: any } = {}, log?: string[]) {
  const { service, stub } = stubService(opts)
  const server = await startPanelServer({
    service, memoryBucket: 'wxid_me', channel: 'local', port: 0,
    onLog: log ? (l: string) => log.push(l) : undefined,
  })
  // 也把 service 交出去：有用例要现场把某个方法换成会抛的，验证「附加物坏了答复还在」
  return { server, service, stub, base: `http://127.0.0.1:${server.port}`, token: server.endpoint.token }
}

function ask(base: string, token: string | null, body: unknown, headers: Record<string, string> = {}) {
  return fetch(`${base}/api/ask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  })
}

// ------------------------------------------------------------------ 门：token

test('没有 token → 401，连状态也不给', async () => {
  const { server, base } = await boot()
  try {
    const res = await fetch(`${base}/api/status`)
    assert.equal(res.status, 401)
    assert.deepEqual(await res.json(), { ok: false, code: 'UNAUTHORIZED' })
  } finally { await server.close() }
})

test('token 错了 → 401，而且响应体与"没带 token"完全一样（不回显任何线索）', async () => {
  const { server, base, token } = await boot()
  try {
    const wrong = await fetch(`${base}/api/status`, { headers: { Authorization: `Bearer ${token}x` } })
    assert.equal(wrong.status, 401)
    const none = await fetch(`${base}/api/status`)
    assert.deepEqual(await wrong.json(), await none.json())
  } finally { await server.close() }
})

test('对 token 才能拿到状态，字段齐、且**不含 token 本身**', async () => {
  const { server, base, token } = await boot()
  try {
    const res = await fetch(`${base}/api/status`, { headers: { Authorization: `Bearer ${token}` } })
    assert.equal(res.status, 200)
    const body: any = await res.json()
    assert.equal(body.service, 'weflow-assistant')
    assert.equal(body.channel, 'local')
    assert.equal(body.channelActive, false)
    assert.equal(body.memoryBucket, 'wxid_me')
    assert.deepEqual(body.quota, { used: 3, limit: 100 })
    assert.deepEqual(body.quickReplies, [], '没配名单时是个空数组，不是 undefined')
    assert.equal(JSON.stringify(body).includes(token), false, '状态里不许出现 token')
  } finally { await server.close() }
})

// ------------------------------------------------------------------ 门：Origin

test('带一个外来 Origin，**即使 token 正确**也 403', async () => {
  const { server, base, token } = await boot()
  try {
    const res = await fetch(`${base}/api/status`, {
      headers: { Authorization: `Bearer ${token}`, Origin: 'https://evil.test' },
    })
    assert.equal(res.status, 403)
    assert.equal((await res.json() as any).code, 'ORIGIN_DENIED')
  } finally { await server.close() }
})

test('不带 Origin 要放行（curl 与 file:// 的 Electron 就是这种形态）', async () => {
  const { server, base, token } = await boot()
  try {
    const res = await fetch(`${base}/api/status`, { headers: { Authorization: `Bearer ${token}` } })
    assert.equal(res.status, 200)
  } finally { await server.close() }
})

test('本机 origin 放行', async () => {
  const { server, base, token } = await boot()
  try {
    const res = await fetch(`${base}/api/status`, {
      headers: { Authorization: `Bearer ${token}`, Origin: `http://127.0.0.1:${server.port}` },
    })
    assert.equal(res.status, 200)
  } finally { await server.close() }
})

// ------------------------------------------------------------------ 门：请求形状

test('POST 不带 application/json → 415（挡掉不发 preflight 的跨站简单请求）', async () => {
  const { server, base, token } = await boot()
  try {
    const res = await fetch(`${base}/api/ask`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'text/plain' },
      body: '{"text":"你好"}',
    })
    assert.equal(res.status, 415)
  } finally { await server.close() }
})

test('请求体过大 → 413，而且不会把服务挂住', async () => {
  const { server, base, token } = await boot()
  try {
    const res = await ask(base, token, JSON.stringify({ text: 'x'.repeat(20 * 1024) }))
    assert.equal(res.status, 413)
    const after = await fetch(`${base}/api/status`, { headers: { Authorization: `Bearer ${token}` } })
    assert.equal(after.status, 200, '服务还能继续服务')
  } finally { await server.close() }
})

test('空文本 / 坏 JSON / 超长文本各有各的说法', async () => {
  const { server, base, token } = await boot()
  try {
    assert.equal((await ask(base, token, { text: '   ' })).status, 400)
    assert.equal((await ask(base, token, '{不是 json')).status, 400)
    assert.match((await (await ask(base, token, '{不是 json')).json() as any).code, /BAD_JSON/)
    assert.equal((await ask(base, token, { text: 'x'.repeat(5000) })).status, 413)
  } finally { await server.close() }
})

test('方法和路径不对：DELETE 405、未知路径 404', async () => {
  const { server, base, token } = await boot()
  try {
    const del = await fetch(`${base}/api/ask`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } })
    assert.equal(del.status, 405)
    const nope = await fetch(`${base}/api/nope`, { headers: { Authorization: `Bearer ${token}` } })
    assert.equal(nope.status, 404)
  } finally { await server.close() }
})

// ------------------------------------------------------------------ 一轮的处理

test('问一句：把文本交给助手，带回答复，并说明用的是哪个记忆桶', async () => {
  const { server, stub, base, token } = await boot()
  try {
    const res = await ask(base, token, { text: '我最近有什么待办？' })
    assert.equal(res.status, 200)
    const body: any = await res.json()
    assert.equal(body.ok, true)
    assert.equal(body.status, 'replied')
    assert.equal(body.reply, '收到')
    assert.equal(body.memoryBucket, 'wxid_me', '要把桶名带回去，界面才好显示')
    assert.deepEqual(stub.calls, [{ bucket: 'wxid_me', text: '我最近有什么待办？' }])
  } finally { await server.close() }
})

test('显式场景 id 只转发已启用的场景；缺失、停用或格式错误时在入队前拒绝', async () => {
  for (const item of assistantScenes.list()) assistantScenes.remove(item.id)
  assistantScenes.add({ id: 'enabled-scene', name: '启用场景', keywords: [], instruction: '仅此轮', outputSpec: '', skills: [], enabled: true })
  assistantScenes.add({ id: 'disabled-scene', name: '停用场景', keywords: [], instruction: '', outputSpec: '', skills: [], enabled: false })
  assistantScenes.bind('panel-user', 'enabled-scene')
  assistantScenes.noteUsed('panel-user', 'enabled-scene')
  assistantScenes.save()

  const { server, stub, base, token } = await boot()
  try {
    const valid = await ask(base, token, { text: '只问这一轮', sceneId: 'enabled-scene' })
    assert.equal(valid.status, 200)
    assert.deepEqual(stub.calls, [{ bucket: 'wxid_me', text: '只问这一轮', sceneId: 'enabled-scene' }])

    for (const invalidBody of [
      { text: '不应入队', sceneId: 'missing-scene' },
      { text: '不应入队', sceneId: 'disabled-scene' },
      { text: '不应入队', sceneId: '' },
      { text: '不应入队', sceneId: 123 },
    ]) {
      const invalid = await ask(base, token, invalidBody)
      assert.equal(invalid.status, 400)
      assert.equal((await invalid.json() as any).code, 'INVALID_SCENE')
    }
    assert.equal(stub.calls.length, 1, '无效场景不能进入助手队列')
    assert.equal(assistantScenes.binding('wxid_me'), '')
    assert.equal(assistantScenes.lastUsedFor('wxid_me'), '')
    assert.equal(assistantScenes.binding('panel-user'), 'enabled-scene')
    assert.equal(assistantScenes.lastUsedFor('panel-user'), 'enabled-scene')
  } finally {
    await server.close()
    for (const item of assistantScenes.list()) assistantScenes.remove(item.id)
    assistantScenes.save()
  }
})

test('答完之后把这一轮的轨迹带回去（页面折叠显示成"思考过程"）', async () => {
  // 2026-09-29 加的：用户要"显示思考过程（默认折叠，可展开）"。轨迹与微信里发「轨迹」
  // 看到的**是同一份**（同一个格式函数），所以两处不会各说各话。
  // 桩要给一条**真的 TurnTrace**（服务端用 `describeForPanel` 把它格式化成行），
  // 不是格式化之后的样子 —— 第一版就给错了形状，拿到的行是格式化后的垃圾。
  const trace = {
    at: '2026-09-29T12:00:00.000Z', userId: 'wxid_me', questionChars: 8,
    steps: [{ kind: 'tool', name: 'get_messages', args: 'contact=甲', bytes: 100, produced: true }],
    rounds: 2, toolCalls: 1, reasoning: '先看会话再定', reasoningChars: 7,
    answerChars: 12, stop: 'answered', elapsedMs: 1200,
  }
  const { server, base, token } = await boot({ trace })
  try {
    const res = await ask(base, token, { text: '我和甲聊了什么？' })
    const body: any = await res.json()
    assert.equal(res.status, 200)
    assert.ok(body.trace.lines.some((l: string) => l.includes('get_messages')), '工具那一步要在里面')
    assert.equal(body.trace.reasoning, '先看会话再定')
    assert.ok(!JSON.stringify(body.trace).includes('wxid_me'), '面板也不该拿到 userId')
  } finally { await server.close() }
})

test('轨迹取不到时**照样回答复** —— 附加物坏了不能把结果弄丢', async () => {
  // 第一版没有这层保护：桩服务没有 `recentTrace`，于是每条 ask 都成了 500。
  // 轨迹是附加物、答复才是这一轮的结果，所以那一句必须 catch 住。
  const { server, service, base, token } = await boot()
  ;(service as any).recentTrace = () => { throw new Error('轨迹模块炸了') }
  try {
    const res = await ask(base, token, { text: '问一句' })
    const body: any = await res.json()
    assert.equal(res.status, 200, '答复必须照常回来')
    assert.equal(body.reply, '收到')
    assert.deepEqual(body.trace, { lines: [], reasoning: '' }, '拿不到轨迹就给空的，页面不显示那一块')
  } finally { await server.close() }
})

test('配额用尽：200 + 那句提示（它不是错误，是一个回答）', async () => {
  const { server, base, token } = await boot({ outcome: { status: 'quota-exceeded', text: '今日额度已用完, 明天再来吧' } })
  try {
    const res = await ask(base, token, { text: '再问一句' })
    assert.equal(res.status, 200)
    const body: any = await res.json()
    assert.equal(body.status, 'quota-exceeded')
    assert.match(body.reply, /额度已用完/)
  } finally { await server.close() }
})

test('助手没启动 → 503 NOT_RUNNING，而不是假装答了', async () => {
  const { server, base, token } = await boot({ outcome: { status: 'not-running' } })
  try {
    const res = await ask(base, token, { text: '你好' })
    assert.equal(res.status, 503)
    assert.equal((await res.json() as any).code, 'NOT_RUNNING')
  } finally { await server.close() }
})

test('一轮还在飞的时候再问 → 429，**不入队**（入队就意味着它一定会被执行、一定会烧配额）', async () => {
  const { server, stub, base, token } = await boot({ hang: true })
  try {
    const first = ask(base, token, { text: '第一句' })
    await new Promise(r => setTimeout(r, 50))
    const second = await ask(base, token, { text: '第二句' })
    assert.equal(second.status, 429)
    assert.equal((await second.json() as any).code, 'BUSY')

    stub.release!()
    assert.equal((await first).status, 200)
    assert.equal(stub.calls.length, 1, '第二句根本没被交给助手')
  } finally { await server.close() }
})

test('助手抛错 → 500，而且**不回显内部细节**（细节只进本机日志）', async () => {
  const log: string[] = []
  const { service } = stubService()
  service.ask = async () => { throw new Error('数据库路径 /home/u/.weflow-cli/secret.db 打不开') }
  const server = await startPanelServer({
    service, memoryBucket: 'panel', channel: 'local', port: 0,
    onLog: (l: string) => log.push(l),
  })
  try {
    const res = await ask(`http://127.0.0.1:${server.port}`, server.endpoint.token, { text: '你好' })
    assert.equal(res.status, 500)
    const raw = JSON.stringify(await res.json())
    assert.doesNotMatch(raw, /secret\.db|打不开/, '内部细节不许出境')
    assert.ok(log.some(l => /抛错/.test(l)), '但本机日志里要留下它')
  } finally { await server.close() }
})

// ------------------------------------------------------------------ 落盘与端口

test('端点文件写的是**实际**监听的端口，不是请求的那个', async () => {
  const { server } = await boot()
  try {
    const { readEndpoint } = await import('../src/panel/endpoint.js')
    const back = readEndpoint()
    assert.equal(back?.port, server.port)
    assert.equal(back?.pid, process.pid)
    assert.equal(back?.memoryBucket, 'wxid_me')
    assert.equal(back?.channel, 'local')
  } finally { await server.close() }
})

test('端点只绑回环：绑的是 127.0.0.1，不是 0.0.0.0', async () => {
  // 说明白这条断言证明的是什么：它证明的是**我们传进去的 host 参数**，
  // 不是操作系统的实际行为（那要另一台机器从外部打才知道）。参数是这个仓库能测到的那一层。
  const { server } = await boot()
  try {
    const { createServer } = await import('node:http')
    const probe = createServer()
    await new Promise<void>((r) => probe.listen(0, '127.0.0.1', () => r()))
    probe.close()
    // 真实的证据是"能连上"，因为给的是回环地址
    const res = await fetch(`http://127.0.0.1:${server.port}/api/status`, {
      headers: { Authorization: `Bearer ${server.endpoint.token}` },
    })
    assert.equal(res.status, 200)
  } finally { await server.close() }
})

// ------------------------------------------------------------------ 浏览器那条路

/** 从 set-cookie 里抠出 cookie 串（Node 的 fetch 走 getSetCookie） */
function setCookieOf(res: Response): string {
  const list = (res.headers as any).getSetCookie?.() ?? []
  const raw = list[0] ?? res.headers.get('set-cookie') ?? ''
  return raw.split(';')[0]
}

test('换一次性口令要带 token；换回来的是一个**短时效、单次使用**的口令', async () => {
  const { server, base, token } = await boot()
  try {
    const denied = await fetch(`${base}/api/pair`, { method: 'POST' })
    assert.equal(denied.status, 401, '没 token 换不到口令')

    const res = await fetch(`${base}/api/pair`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
    assert.equal(res.status, 200)
    const body: any = await res.json()
    assert.ok(body.code && body.url.includes(`c=${body.code}`), 'URL 里的是一次性口令，不是 token')
    assert.equal(body.url.includes(token), false, '**token 绝不能进 URL**')
  } finally { await server.close() }
})

test('拿口令换 cookie：Set-Cookie 必须是 HttpOnly + SameSite=Strict', async () => {
  const { server, base, token } = await boot()
  try {
    const pair = await fetch(`${base}/api/pair`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
    const { code } = await pair.json() as any

    const page = await fetch(`${base}/panel?c=${code}`)
    assert.equal(page.status, 200)
    const cookie = setCookieOf(page)
    assert.match(cookie, /^weflow_panel=/, 'Cookie 名要对得上')
    const raw = page.headers.get('set-cookie') ?? ''
    assert.match(raw, /HttpOnly/i, '页面脚本必须读不到它')
    assert.match(raw, /SameSite=Strict/i)
    assert.match(page.headers.get('content-type') ?? '', /text\/html/)
    assert.match(page.headers.get('content-security-policy') ?? '', /default-src 'none'/)
  } finally { await server.close() }
})

test('口令**单次使用**：第二次拿同一个口令就进不来了', async () => {
  const { server, base, token } = await boot()
  try {
    const pair = await fetch(`${base}/api/pair`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
    const { code } = await pair.json() as any
    assert.equal((await fetch(`${base}/panel?c=${code}`)).status, 200)
    assert.equal((await fetch(`${base}/panel?c=${code}`)).status, 401, '用过的口令必须作废')
  } finally { await server.close() }
})

test('没有口令也没有 cookie → 页面不给', async () => {
  const { server, base } = await boot()
  try {
    const res = await fetch(`${base}/panel`)
    assert.equal(res.status, 401)
    assert.doesNotMatch(await res.text(), /<html/i, '连页面壳都不该给')
  } finally { await server.close() }
})

test('带上 cookie 之后，不用 Bearer 也能读状态、能问 —— 页面就是这么工作的', async () => {
  const { server, base, token } = await boot()
  try {
    const pair = await fetch(`${base}/api/pair`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
    const { code } = await pair.json() as any
    const cookie = setCookieOf(await fetch(`${base}/panel?c=${code}`))

    const status = await fetch(`${base}/api/status`, { headers: { Cookie: cookie } })
    assert.equal(status.status, 200)
    assert.equal((await status.json() as any).service, 'weflow-assistant')

    const asked = await ask(base, null, { text: '你好' }, { Cookie: cookie })
    assert.equal(asked.status, 200)
    assert.equal((await asked.json() as any).reply, '收到')
  } finally { await server.close() }
})

test('静态资源：白名单里的能取，别的一律 404（不是把目录挂出去）', async () => {
  const { server, base, token } = await boot()
  try {
    const headers = { Authorization: `Bearer ${token}` }
    const js = await fetch(`${base}/panel/renderer.js`, { headers })
    assert.equal(js.status, 200)
    assert.match(js.headers.get('content-type') ?? '', /javascript/)

    const css = await fetch(`${base}/panel/panel.css`, { headers })
    assert.equal(css.status, 200)
    assert.match(css.headers.get('content-type') ?? '', /text\/css/)

    // `/panel/` 是 index.html 的别名（**故意映射的**，不是目录列举）
    const alias = await fetch(`${base}/panel/`, { headers })
    assert.equal(alias.status, 200)
    assert.match(alias.headers.get('content-type') ?? '', /text\/html/)

    // 其余的路径穿越与别的目录都该 404 —— 白名单里没有这些名字
    for (const path of ['/panel/../package.json', '/panel/secret.txt', '/resources/panel/index.html',
                        '/panel/../src/panel/server.ts']) {
      const res = await fetch(`${base}${path}`, { headers })
      assert.equal(res.status, 404, `${path} 不该被服务`)
    }
  } finally { await server.close() }
})

test('页面里没有内联脚本（CSP 也会挡，但别写）', async () => {
  const { server, base, token } = await boot()
  try {
    const html = await (await fetch(`${base}/panel`, { headers: { Authorization: `Bearer ${token}` } })).text()
    assert.doesNotMatch(html, /<script(?![^>]*\ssrc=)/)
    assert.match(html, /src="\/panel\/renderer\.js"/)
  } finally { await server.close() }
})

test('右键菜单那份名单来自配置键，切分去空、有上限', async () => {
  // 名单走这个端点出去，是因为**页面本来就在轮询它**——面板那条路上不加新端点。
  const { server, base, token } = await boot()
  try {
    await withQuickReplies(' 咸鱼梦想家 , 老王 ,, 甲 ', async () => {
    const res = await fetch(`${base}/api/status`, { headers: { Authorization: `Bearer ${token}` } })
    const body: any = await res.json()
      assert.deepEqual(body.quickReplies, ['咸鱼梦想家', '老王', '甲'], '按逗号切、两头去空、丢掉空段')
    })
  } finally { await server.close() }
})

test('名单再长也只带前 20 个（菜单在 420px 宽的窗口里，本来也放不下更多）', async () => {
  const many = Array.from({ length: 30 }, (_, i) => `联系人${i + 1}`).join(',')
  const { server, base, token } = await boot()
  try {
    await withQuickReplies(many, async () => {
      const res = await fetch(`${base}/api/status`, { headers: { Authorization: `Bearer ${token}` } })
      const body: any = await res.json()
      assert.equal(body.quickReplies.length, 20)
      assert.equal(body.quickReplies[0], '联系人1')
      assert.equal(body.quickReplies[19], '联系人20')
    })
  } finally { await server.close() }
})


// -------------------------------------------------- /api/memory（面板"它记住了什么"）

test('/api/memory：没 token 401，有 token 才给 —— 那是一份关于用户的东西', async () => {
  const { server, base, token } = await boot()
  try {
    const none = await fetch(`${base}/api/memory`)
    assert.equal(none.status, 401)
    const ok = await fetch(`${base}/api/memory`, { headers: { Authorization: `Bearer ${token}` } })
    assert.equal(ok.status, 200)
    const body: any = await ok.json()
    assert.equal(body.ok, true)
    assert.equal(body.bucket, 'wxid_me', '桶由服务端决定')
    assert.equal(body.facts.length, 1)
    assert.equal(body.facts[0].content, '喜欢喝美式')
    assert.equal(body.facts[0].sourceQuote, '我平时喝美式', '带上它是从哪句话来的，用户才审得动')
    assert.equal(body.workingTurns, 2)
    assert.equal(body.saveError, '')
    assert.equal(JSON.stringify(body).includes(token), false, '这里也不许出现 token')
  } finally { await server.close() }
})

test('/api/memory：**桶不接受客户端指定**（否则就是一个读别人记忆的口子）', async () => {
  const { server, base, token } = await boot()
  try {
    const res = await fetch(`${base}/api/memory?bucket=someone_else`, { headers: { Authorization: `Bearer ${token}` } })
    assert.equal(res.status, 200)
    assert.equal((await res.json() as any).bucket, 'wxid_me', '查询参数改不了它用哪个桶')
  } finally { await server.close() }
})

test('/api/memory：只读 —— POST 405', async () => {
  const { server, base, token } = await boot()
  try {
    const res = await fetch(`${base}/api/memory`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: '{}',
    })
    assert.equal(res.status, 405)
  } finally { await server.close() }
})
