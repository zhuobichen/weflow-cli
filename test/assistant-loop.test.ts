/**
 * 助手的常驻循环：**谁会被回复、谁被拒、配额怎么算**。
 *
 * 这是助手最外面的一层判断，也是此前唯一完全没测过的一层（之前判断"需要已登录的通道"）。
 * 但通道是模块级的一个类，把它的 prototype 方法换掉就能在不联网的情况下驱动整条循环，
 * 于是"白名单为空时是不是真的谁都拒"「配额用完是先回一句还是先扣 LLM」这类问题都能直接问。
 *
 * 这些判断的失败方式全是静默的：多回了一条消息、拒了却不留审计行、配额用尽还在叫模型。
 *
 * 打桩：`WechatMessageService.prototype` 的四个方法 + `configService.get` + `callLLM`。
 * 不联网、不连微信、不碰真实家目录（HOME 已指到临时目录）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-loop-'))
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
let polling = false
let llmCalls = 0

/** 换掉通道：只留下"回调 + 发送 + 轮询开关"，别的都不做 */
function installFakeChannel(): void {
  sent = []
  handler = null
  polling = false
  CHANNEL.onMessage = function (cb: (msg: any) => void) { handler = cb }
  CHANNEL.startPolling = async function () { polling = true }
  CHANNEL.sendText = async function (to: string, text: string) { sent.push({ to, text }); return true }
  CHANNEL.stop = async function () { polling = false }
}

function setConfig(overrides: Record<string, string>): void {
  ;(configService as any).get = (key: string) =>
    key in overrides ? overrides[key] : realGet(key)
}

function directMessage(text: string, senderId = 'wxid_me', conversationId = 'wxid_me') {
  return {
    conversationType: 'direct',
    conversationId,
    senderId,
    mentionedBot: false,
    messageKind: 'text',
    messageStr: text,
  }
}

interface Session {
  svc: any
  logs: string[]
  audit: () => string
  deliver: (msg: any) => Promise<void>
}

/** 起一个"已登录"的助手，并把队列排空作为 deliver 的语义 */
async function boot(overrides: Record<string, string> = {}): Promise<Session> {
  installFakeChannel()
  setConfig({ wechatOcToken: 'fake-token', wechatOcAccountId: 'bot-1', ...overrides })
  llmCalls = 0
  const svc: any = new AssistantService()
  boots.push(svc)
  svc.callLLM = async () => { llmCalls++; return { choices: [{ message: { content: '收到' } }] } }
  const logs: string[] = []
  await svc.start((line: string) => logs.push(line))
  return {
    svc,
    logs,
    audit: () => (existsSync(AUDIT_FILE) ? readFileSync(AUDIT_FILE, 'utf8') : ''),
    deliver: async (msg: any) => {
      handler!(msg)
      await svc.queue          // 队列是串行的：等它排空就等于"这条处理完了"
    },
  }
}

/** 每个 boot 出来的实例都要收尾：`start()` 会真的把本机端点起起来，
 *  不 stop 就留下一个活着的监听，这个**文件进程**会因此退不出去
 *  （node:test 会把整个文件报成 failed，而里面每条用例都是绿的——我踩过）。 */
test.beforeEach(installFakeChannel)
test.after(async () => {
  for (const s of boots) s.stop()
  await new Promise(r => setTimeout(r, 100))
  ;(configService as any).get = realGet
})

test('没登录消息通道时**降级启动**：本机入口可用，但不去轮询', async () => {
  // 这条**改过行为**（原断言是"抛错 '未登录消息通道'"）。理由是被实测记录打脸的：
  // 本机的 `assistant start` 从来没成功启动过一次，日志尾部就是那句抛错——也就是
  // "没登录微信" == "助手整个起不来"。现在没有 token 只是没有微信这个入口，
  // 本机入口照样能用；`isChannelActive()` 让 status 能区分"配了 token"与"通道真的接上了"。
  setConfig({})
  const svc: any = new AssistantService()
  boots.push(svc)          // 这条没走 boot()，收尾得自己登记（漏了它就留下一个活着的端点）
  const logs: string[] = []
  await svc.start((line: string) => logs.push(line))

  assert.equal(polling, false, '没有通道就不该去轮询')
  assert.equal(svc.isChannelActive(), false, '通道没接上')
  assert.equal(svc.isRunning(), true, '降级启动了，running 必须是 true——否则每一轮都静默 no-op')
  assert.ok(logs.some(l => /消息通道未登录/.test(l)), `要说清它没接通道：${logs.join(' | ')}`)
  assert.ok(logs.some(l => /本机入口模式/.test(l)), '启动行不该谎报一个 bot 账号')
  assert.doesNotMatch(logs.join(' '), /bot:/, '没有通道时不许报 bot 账号')
})

test('白名单为空：谁都拒，不回复、不调模型、留审计行', async () => {
  const s = await boot({ assistantWhitelist: '' })

  await s.deliver(directMessage('你好'))

  assert.deepEqual(sent, [], '拒绝就是不回复')
  assert.equal(llmCalls, 0, '被拒的消息不该消耗模型调用')
  assert.match(s.audit(), /DENY_DIRECT_NOT_WHITELISTED/)
  assert.ok(s.logs.some(line => line.includes('拒绝')), s.logs.join('\n'))
})

test('白名单里的人：回复、计数、日志里能看到今日用量', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })

  await s.deliver(directMessage('你好'))

  assert.equal(sent.length, 1)
  assert.equal(sent[0].to, 'wxid_me')
  assert.equal(sent[0].text, '收到')
  assert.equal(s.svc.dailyCount, 1)
  assert.ok(s.logs.some(line => line.includes('今日 1/100')), s.logs.join('\n'))
})

test('非文本消息被忽略：不回复、不计数、不调模型', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })

  await s.deliver({ ...directMessage('[图片]'), messageKind: 'image' })

  assert.deepEqual(sent, [])
  assert.equal(llmCalls, 0)
  assert.equal(s.svc.dailyCount, 0)
  assert.ok(s.logs.some(line => line.includes('忽略非文本消息')))
})

test('配额用尽：先回一句额度用完，不叫模型', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  s.svc.dailyCount = 100

  await s.deliver(directMessage('再问一个'))

  assert.equal(sent.length, 1)
  assert.match(sent[0].text, /额度已用完/)
  assert.equal(llmCalls, 0, '额度没了还调模型就是白花钱')
  assert.match(s.audit(), /DENY_DAILY_LIMIT/)
})

test('跨天后配额自动重置', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  s.svc.dailyCount = 100
  s.svc.dailyDate = 'Mon Jan 01 2024'   // 假装上一次计数发生在昨天

  await s.deliver(directMessage('新的一天'))

  assert.deepEqual(sent.map(m => m.text), ['收到'], '跨天应该重新可用')
  assert.equal(s.svc.dailyCount, 1)
})

test('两条消息按到达顺序串行处理', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  s.svc.callLLM = async (messages: any[]) => {
    llmCalls++
    return { choices: [{ message: { content: `回复第${llmCalls}条` } }] }
  }

  const first = s.deliver(directMessage('第一条'))
  const second = s.deliver(directMessage('第二条'))
  await Promise.all([first, second])

  assert.deepEqual(sent.map(m => m.text), ['回复第1条', '回复第2条'], '串行队列是记忆窗口不交错的前提')
})

test('群聊三道门槛：群白名单、成员白名单、@ 门槛，缺一不可', async () => {
  const group = { conversationType: 'group', conversationId: 'room@chatroom', senderId: 'wxid_me' }

  // ① 群不在白名单
  let s = await boot({ assistantWhitelist: 'wxid_me', assistantGroupWhitelist: '' })
  await s.deliver({ ...group, mentionedBot: true, messageKind: 'text', messageStr: 'hi' })
  assert.deepEqual(sent, [])
  assert.match(s.audit(), /DENY_GROUP_NOT_WHITELISTED/)

  // ② 群在白名单，但发送者不在
  s = await boot({ assistantWhitelist: 'wxid_other', assistantGroupWhitelist: 'room@chatroom' })
  await s.deliver({ ...group, mentionedBot: true, messageKind: 'text', messageStr: 'hi' })
  assert.match(s.audit(), /DENY_GROUP_SENDER_NOT_WHITELISTED/)

  // ③ 都满足，但没有 @
  s = await boot({ assistantWhitelist: 'wxid_me', assistantGroupWhitelist: 'room@chatroom' })
  await s.deliver({ ...group, mentionedBot: false, messageKind: 'text', messageStr: 'hi' })
  assert.match(s.audit(), /DENY_GROUP_MENTION_REQUIRED/)

  // ④ 三道都满足才回
  s = await boot({ assistantWhitelist: 'wxid_me', assistantGroupWhitelist: 'room@chatroom' })
  await s.deliver({ ...group, mentionedBot: true, messageKind: 'text', messageStr: 'hi' })
  assert.equal(sent.length, 1)
  assert.equal(sent[0].to, 'room@chatroom')
})

test('处理这一条时抛异常，日志记下来，队列继续能用', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  const boom = async () => { throw new Error('处理炸了') }
  s.svc.handleMessage = boom

  await s.deliver(directMessage('第一条'))
  assert.ok(s.logs.some(line => line.includes('处理异常')), s.logs.join('\n'))

  s.svc.handleMessage = async () => '恢复正常'
  await s.deliver(directMessage('第二条'))
  assert.equal(sent.at(-1)!.text, '恢复正常', '一条坏掉不该让常驻循环瘫掉')
})

test('stop 之后到达的消息不再处理', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  s.svc.stop()

  await s.deliver(directMessage('停之后来的'))

  assert.deepEqual(sent, [])
  assert.equal(llmCalls, 0)
})

test('白名单为空时，第一条被拒的直聊消息会把完整 ID 和该执行的命令打出来', async () => {
  // 首次启用的死结：不配白名单就拒所有人，而填白名单用的那个 ID 只在入站消息里出现
  // （登录响应给的 ilink_user_id 与它是不是同一个值没有被验证过，所以不该拿登录来猜）。
  const s = await boot({ assistantWhitelist: '' })

  await s.deliver(directMessage('你好', 'wxid_from_you'))

  const hint = s.logs.find(line => line.includes('[首次配置]'))
  assert.ok(hint, s.logs.join(' / '))
  assert.match(hint!, /wxid_from_you/, '必须打完整 ID：截断过的前缀填不进白名单')
  assert.match(hint!, /config set assistantWhitelist "wxid_from_you"/)
  assert.deepEqual(sent, [], '提示归提示，这条消息仍然是拒绝、不回复')
})

test('首次配置提示只打一次，不刷屏', async () => {
  const s = await boot({ assistantWhitelist: '' })

  await s.deliver(directMessage('第一条', 'wxid_from_you'))
  await s.deliver(directMessage('第二条', 'wxid_from_you'))

  assert.equal(s.logs.filter(line => line.includes('[首次配置]')).length, 1)
})

test('白名单非空时不再提示（那时人已经配过了）', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_someone_else' })

  await s.deliver(directMessage('你好', 'wxid_from_you'))

  assert.equal(s.logs.some(line => line.includes('[首次配置]')), false)
  assert.match(s.audit(), /DENY_DIRECT_NOT_WHITELISTED/, '照旧拒绝，只是不再提示')
})

test('群聊被拒时不提示：群里的 sender_id 是群成员，不该被加进白名单', async () => {
  const s = await boot({ assistantWhitelist: '', assistantGroupWhitelist: '' })

  await s.deliver({ conversationType: 'group', conversationId: 'room@chatroom',
                    senderId: 'wxid_a_member', mentionedBot: true, messageKind: 'text',
                    messageStr: 'hi' })

  assert.equal(s.logs.some(line => line.includes('[首次配置]')), false)
  assert.match(s.audit(), /DENY_GROUP_NOT_WHITELISTED/)
})

test('a broken memory file is announced at startup instead of looking like amnesia', async () => {
  // 记忆加载出过事（版本不认识 / 文件坏了）时，用户面对的是"它忘了我"——必须说出来。
  // 原文件此时已经留档，所以这句话里带着文件名；审计里也留一行。
  const stateDir = join(HOME, '.weflow-cli')
  mkdirSync(stateDir, { recursive: true })
  writeFileSync(join(stateDir, 'assistant_memory.json'),
    JSON.stringify({ version: 99, users: {} }), 'utf8')

  const s = await boot({ assistantWhitelist: 'wxid_me' })

  assert.ok(s.logs.some(line => line.includes('⚠ 记忆')), s.logs.join(' / '))
  assert.match(s.audit(), /MEMORY_LOAD_ISSUE/)
})

test('a healthy memory file says nothing at startup', async () => {
  const s = await boot({ assistantWhitelist: 'wxid_me' })
  assert.equal(s.logs.some(line => line.includes('⚠ 记忆')), false)
})

