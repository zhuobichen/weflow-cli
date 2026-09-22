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
import { existsSync, mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-loop-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

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

test.beforeEach(installFakeChannel)
test.after(() => { ;(configService as any).get = realGet })

test('没登录消息通道时立刻报错，而且不开始轮询', async () => {
  setConfig({})
  try {
    const svc: any = new AssistantService()
    await svc.start()
    assert.fail('应该抛错')
  } catch (error: any) {
    assert.match(error.message, /未登录消息通道/)
  }
  assert.equal(polling, false)
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
