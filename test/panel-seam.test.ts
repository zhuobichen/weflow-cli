/**
 * 本机面板接进助手的那条缝：**一个大脑、两个入口、一条队列**。
 *
 * 面板（屏幕角落的悬浮窗）与微信通道共用同一个 `AssistantService` 实例——配额计数器、
 * 串行队列、被那条队列保护的实例字段都是**进程内状态**，所以两个入口绝不能各建一个实例。
 * 这里的断言问的就是这件事成不成立。
 *
 * 三条具体的失败方式，都是静默的：
 * 1. **本机入口被微信白名单挡住**（把"谁能在微信里跟我说话"套到坐在机器前的人身上），
 *    顺带打出一条教用户把自己的面板 id 加进微信白名单的错提示；
 * 2. **配额用尽时抛 TypeError 而不是回话**——泵里原先是 `this.svc!.sendText(...)`，
 *    那个 `!` 只是给 TS 看的；没有微信通道时 `svc` 是 `null`，于是调用方永远收不到响应（挂到超时）；
 * 3. **两个入口各自起一轮导致记忆窗口交错**——`turnCalls`/`lastReasoning` 都是实例字段，
 *    它们的正确性只依赖"handleMessage 被串行调用"。
 *
 * 打桩：`WechatMessageService.prototype` 的四个方法 + `configService.get` + `callLLM`。
 * 不联网、不连微信、不碰真实家目录。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-panel-seam-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME
// 这些用例会真的把助手的本机端点起起来（`start()` 里就会起）。
// 端口给 0 让内核分配：否则每个用例都去抢生产端口 8766，用例之间也互相抢。
process.env.WEFLOW_PANEL_PORT = '0'

/** 收尾用：所有 `start()` 过的实例都要 stop，否则文件进程退不出去 */
const boots: any[] = []

const { AssistantService } = await import('../src/services/assistantService.js')
const { WechatMessageService } = await import('../src/services/wechatMessageService.js')
const { configService } = await import('../src/services/configService.js')

const CHANNEL = WechatMessageService.prototype as any
const AUDIT_FILE = join(HOME, '.weflow-cli', 'assistant_audit.log')
const realGet = configService.get.bind(configService)

let sent: { to: string; text: string }[] = []
let handler: ((msg: any) => void) | null = null

function installFakeChannel(): void {
  sent = []
  handler = null
  CHANNEL.onMessage = function (cb: (msg: any) => void) { handler = cb }
  CHANNEL.startPolling = async function () {}
  CHANNEL.sendText = async function (to: string, text: string) { sent.push({ to, text }); return true }
  CHANNEL.stop = async function () {}
}

function setConfig(overrides: Record<string, string>): void {
  ;(configService as any).get = (key: string) =>
    key in overrides ? overrides[key] : realGet(key)
}

function directMessage(text: string, senderId = 'wxid_me') {
  return {
    conversationType: 'direct', conversationId: senderId, senderId,
    mentionedBot: false, messageKind: 'text', messageStr: text,
  }
}

/** 起一个助手。`hold` 给出时，第一次 LLM 调用会挂住，直到 `release()` */
async function boot(overrides: Record<string, string> = {}, opts: { hold?: boolean } = {}) {
  installFakeChannel()
  setConfig({ wechatOcToken: 'fake-token', wechatOcAccountId: 'bot-1', ...overrides })
  const svc: any = new AssistantService()
  boots.push(svc)

  let release!: () => void
  const gate = new Promise<void>((r) => { release = r })
  let held = false
  let concurrent = 0
  let maxConcurrent = 0
  let llmCalls = 0

  svc.callLLM = async () => {
    llmCalls++
    concurrent++
    maxConcurrent = Math.max(maxConcurrent, concurrent)
    if (opts.hold && !held) { held = true; await gate }
    concurrent--
    return { choices: [{ message: { content: '收到' } }] }
  }

  const logs: string[] = []
  await svc.start((line: string) => logs.push(line))
  return {
    svc, logs, release,
    stats: () => ({ llmCalls, maxConcurrent }),
    audit: () => (existsSync(AUDIT_FILE) ? readFileSync(AUDIT_FILE, 'utf8') : ''),
    /** 推一条微信消息并等队列排空——"排空"就是"这条处理完了" */
    deliver: async (msg: any) => { handler!(msg); await svc.queue },
    quotaUsed: () => svc.dailyCount as number,
  }
}

/** 每个 boot 出来的实例都要收尾：`start()` 会把本机端点真的起起来，
 *  不 stop 就留下一个活着的监听，这个**文件进程**因此退不出去——
 *  node:test 会把整个文件报成 failed，而里面每条用例都是绿的。 */
test.beforeEach(installFakeChannel)
test.after(async () => {
  for (const s of boots) s.stop()
  await new Promise(r => setTimeout(r, 100))
  ;(configService as any).get = realGet
})

// ------------------------------------------------------ 白名单只管微信，不管本机

test('本机入口不看微信白名单；同一时刻微信那边仍然谁都拒', async () => {
  // 最要紧的一条。白名单回答的是"谁能在微信里跟我说话"；把它套到坐在机器前的人身上，
  // 空名单时会把面板自己拒掉——而面板的门是端点 token，不是这个。
  const s = await boot({ assistantWhitelist: '' })

  const panel = await s.svc.ask('wxid_panel', '你好')
  assert.equal(panel.status, 'replied')
  assert.equal(panel.text, '收到')

  await s.deliver(directMessage('你好'))
  assert.equal(sent.length, 0, '微信那边白名单为空，仍然一句都不回')
})

test('白名单里的本人走微信照旧畅通（别为了放行面板把微信那道门拆了）', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  await s.deliver(directMessage('你好', 'wxid_me'))
  assert.equal(sent.length, 1)
  assert.equal(sent[0].text, '收到')
})

test('白名单里没有的陌生人，微信进来仍被拒并留审计', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  await s.deliver(directMessage('你好', 'wxid_stranger'))
  assert.equal(sent.length, 0, '不回复陌生人是既有行为')
  assert.match(s.audit(), /DENY_DIRECT_NOT_WHITELISTED/)
})

// ------------------------------------------------------ 配额：一个计数器，两个来源

test('配额用尽时本机入口拿到的是回话，不是 TypeError —— 没有通道时也一样', async () => {
  // **这就是那个真 bug 的回归测试**：泵里原先写的是 `this.svc!.sendText(...)`，
  // 而没有微信通道时 `svc` 是 `null`。所以这里刻意用一个**没有通道**的助手
  // （`wechatOcToken: ''` → 降级启动），把配额耗光，再问一次。
  // 旧代码在这条上会抛 TypeError，面板那边永远收不到响应。
  const s = await boot({ wechatOcToken: '', assistantWhitelist: 'wxid_me' })
  assert.equal(s.svc.isChannelActive(), false, '前提：这个实例没有微信通道')

  s.svc.dailyCount = 100          // DAILY_LIMIT
  const out = await s.svc.ask('wxid_panel', '再问一句')
  assert.equal(out.status, 'quota-exceeded')
  assert.match(out.text, /额度已用完/)
})

test('配额用尽时微信那边照样收到那句话说清楚（不是静默丢弃）', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  s.svc.dailyCount = 100
  await s.deliver(directMessage('你好'))
  assert.equal(sent.length, 1)
  assert.match(sent[0].text, /额度已用完/)
})

test('面板与微信共用一个计数器：面板花掉一条，微信那边就少一条', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  assert.equal(s.quotaUsed(), 0)

  await s.svc.ask('wxid_panel', '面板问一句')
  assert.equal(s.quotaUsed(), 1, '面板的轮次要计数')

  await s.deliver(directMessage('微信问一句'))
  assert.equal(s.quotaUsed(), 2, '同一个计数器，不是各算各的')

  const last = s.logs.filter(l => /已回复/.test(l)).pop()!
  assert.match(last, /今日 2\/100/)
})

test('额度拒绝也计入来源，日志里能看出是哪条入口', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  s.svc.dailyCount = 100
  await s.svc.ask('wxid_panel', '问一句')
  const log = readFileSync(join(HOME, '.weflow-cli', 'assistant.log'), 'utf8')
  assert.match(log, /拒绝 \(via=panel\)/)
})

// ------------------------------------------------------ 没起来 / 重入 / 串行

test('没启动的实例问它，如实回 not-running，而不是装作答了', async () => {
  const svc: any = new AssistantService()
  const out = await svc.ask('wxid_panel', '你好')
  assert.equal(out.status, 'not-running')
})

test('一轮还在飞的时候直接调 handleMessage 会抛 —— 不许悄悄重入', async () => {
  // 第二个入口出现之后，早晚有人想加一条直连 handleMessage 的快速路径。
  // 那条路会让 turnCalls / lastReasoning 这两份实例状态互相踩，而且是静默的。
  const s = await boot({ assistantWhitelist: 'wxid_me' }, { hold: true })
  const running = s.svc.ask('wxid_panel', '第一句')       // 挂住第一次 LLM 调用
  await new Promise(r => setTimeout(r, 10))

  // `handleMessage` 是 async：被拒的是一个 Promise，所以用 `rejects` 而不是 `throws`
  // （第一版用错了，结果那条错误变成了 unhandledRejection —— node:test 把它单独报了出来）
  await assert.rejects(() => s.svc.handleMessage('wxid_panel', '插队', 'text'), /重入/)

  s.release()
  await running
})

test('两个入口同时来，轮次是串行的（记忆窗口不交错）', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' }, { hold: true })

  const fromPanel = s.svc.ask('wxid_panel', '面板这句')
  const fromWechat = s.deliver(directMessage('微信这句'))
  await new Promise(r => setTimeout(r, 10))
  s.release()
  await Promise.all([fromPanel, fromWechat])

  assert.equal(s.stats().maxConcurrent, 1, '同一时刻只许有一轮在叫模型')
  assert.equal(s.quotaUsed(), 2, '两条都处理了，没被漏掉')
})

test('面板那条路的日志不写内容，微信那条照旧写', async () => {
  // 有意的不对称：微信没有别的界面，日志是唯一线索；面板有界面，留长度就够查问题。
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  await s.svc.ask('wxid_panel', '这是面板里的原话')
  await s.deliver(directMessage('这是微信里的原话'))
  const joined = s.logs.join('\n')
  assert.match(joined, /\[panel\] 8字/, '面板那条只留长度')
  assert.doesNotMatch(joined, /这是面板里的原话/, '面板的正文不该进日志')
  assert.match(joined, /这是微信里的原话/, '微信那边保持原样，别顺手改了')
})
