/**
 * 三个新机制接进 `handleMessage` 之后的行为（D-168 / D-169）。
 *
 * 单测各自的纯函数只能证明"表写得对""判断写得对"，证明不了**它们真的被调用了**——
 * 这个仓库在这件事上付过学费：守卫写对了、但站在一个默认不成立的信号上（判断层只在
 * `assistantFastRoute=on` 时跑），于是默认配置下**永远不会触发**，而测试全绿。
 *
 * 所以这里用注入的 `callLLM` + 假数据把整条链跑起来，断言的是"模型看到的每一轮消息"。
 *
 * 两台开关是**独立**的：`assistantLexiconRoute` 默认 on，`assistantFastRoute` 默认 off
 * （D-035 那条"默认与今天逐字段一致"被 D-168 改写成"关掉词法之后就与今天一致"）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-lexicon-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantService } = await import('../src/services/assistantService.js')
const { chatService } = await import('../src/services/chatService.js')
const { configService } = await import('../src/services/configService.js')

const AUDIT_FILE = join(HOME, '.weflow-cli', 'assistant_audit.log')
const realGet = configService.get.bind(configService)

function setConfig(values: { lexicon?: string; fastRoute?: string }): void {
  ;(configService as any).get = (key: string) => {
    if (key === 'assistantLexiconRoute') return values.lexicon ?? ''
    if (key === 'assistantFastRoute') return values.fastRoute ?? ''
    return realGet(key)
  }
}

let seq = 0
interface Harness { rounds: any[][]; reply: string; audit: string; deciderCalls: number }

async function ask(message: string, opts: {
  lexicon?: string
  fastRoute?: string
  script?: any[]
  decider?: (request: any) => Promise<any>
} = {}): Promise<Harness> {
  let deciderCalls = 0
  const decider = opts.decider ?? (async () => { deciderCalls += 1; throw new Error('不该问判断层') })

  setConfig({ lexicon: opts.lexicon, fastRoute: opts.fastRoute })
  ;(chatService as any).connect = async () => {}
  ;(chatService as any).listSessions = async () => ([
    { displayName: '甲', username: 'wxid_a', summary: '最近还好吗' },
    { displayName: '乙', username: 'wxid_b', summary: '明天见' },
  ])

  mkdirSync(join(HOME, '.weflow-cli'), { recursive: true })
  writeFileSync(AUDIT_FILE, '')

  const svc: any = new AssistantService({ routeDecider: opts.decider ? decider : undefined })
  const rounds: any[][] = []
  const script = opts.script ?? [{ choices: [{ message: { content: '给你的答复' } }] }]
  let i = 0
  svc.callLLM = async (messages: any[]) => {
    rounds.push(JSON.parse(JSON.stringify(messages)))
    return script[Math.min(i++, script.length - 1)]
  }
  seq += 1
  const reply = await svc.handleMessage(`u-lex-${seq}`, message, 'text')
  const audit = existsSync(AUDIT_FILE) ? readFileSync(AUDIT_FILE, 'utf8') : ''
  return { rounds, reply, audit, deciderCalls }
}

const toolMessages = (messages: any[]) => messages.filter(m => m.role === 'tool')
const systemText = (messages: any[]) => messages
  .filter(m => m.role === 'system' && typeof m.content === 'string')
  .map(m => m.content).join('\n')

test.after(() => { ;(configService as any).get = realGet })

// --------------------------------------------------------------- 词法快路径

test('词法默认开：一句话就被认出来，工具结果在第一轮之前就到位', async () => {
  const { rounds, audit, deciderCalls } = await ask('我最近都在和谁聊天？')
  assert.equal(deciderCalls, 0, '词法命中就不该再去问判断层')
  assert.equal(rounds.length, 1, '预派发之后模型一轮就答完')
  const tools = toolMessages(rounds[0])
  assert.equal(tools.length, 1)
  assert.match(tools[0].content, /甲/)
  const call = rounds[0].find((m: any) => m.tool_calls)
  assert.equal(call.tool_calls[0].function.name, 'list_sessions')
  assert.match(audit, /LEXICON_HIT/)
  assert.match(audit, /list_sessions/)
})

test('关掉词法：同一句话什么都不派发（与 D-035 的旧行为一致）', async () => {
  const { rounds, audit } = await ask('我最近都在和谁聊天？', { lexicon: 'off' })
  assert.equal(rounds.length, 1)
  assert.equal(toolMessages(rounds[0]).length, 0)
  assert.ok(!rounds[0].some((m: any) => m.tool_calls), '关掉之后不该有预派发')
  assert.ok(!/LEXICON_HIT/.test(audit))
})

test('灰度模式 log：只记账，不派发', async () => {
  const { rounds, audit } = await ask('我最近都在和谁聊天？', { lexicon: 'log' })
  assert.equal(toolMessages(rounds[0]).length, 0)
  assert.match(audit, /LEXICON_WOULD/)
  assert.ok(!/LEXICON_HIT/.test(audit))
})

test('抽参数那一档：「甲最近跟我说了什么」直接派发 get_messages', async () => {
  const { rounds } = await ask('甲最近跟我说了什么？', { lexicon: 'on' })
  assert.equal(rounds.length, 1, '预派发之后模型一轮就答完')
  const call = rounds[0].find((m: any) => m.tool_calls)
  assert.equal(call.tool_calls[0].function.name, 'get_messages')
  const args = JSON.parse(call.tool_calls[0].function.arguments)
  assert.equal(args.contact, '甲')
})

test('抽参数那一档：时间词也一起带上（「前天」→ since）', async () => {
  const { rounds } = await ask('前天甲跟我说了什么？', { lexicon: 'on' })
  const call = rounds[0].find((m: any) => m.tool_calls)
  const args = JSON.parse(call.tool_calls[0].function.arguments)
  assert.equal(args.contact, '甲')
  assert.equal(args.since, '前天', '原样传下去：`resolveSince` 现在认得中文相对说法')
})

test('表里没有的说法：老老实实交给循环，不瞎猜', async () => {
  // 既不是固定表里的意图，也不是"某人跟我说了什么"的句形
  const { rounds } = await ask('那个文档的事我怎么回他比较好？', { lexicon: 'on' })
  assert.equal(rounds.length, 1)
  assert.ok(!rounds[0].some((m: any) => m.tool_calls), '这句话表里没有，词法不该碰')
})

// --------------------------------------------------------------- 自述守卫

test('守卫：一次工具没调却说"已查" → 顶回去一次，并保留补问结果', async () => {
  const script = [
    { choices: [{ message: { content: '已查：甲在3天前发来项目文档更新，提到「等你确认」。' } }] },
    { choices: [{ message: { content: '我没有查到，需要调用工具。' } }] },
  ]
  // 用一句**表里没有**的问法：被预派发过就不该再触发守卫（那条另有断言）
  const { rounds, reply, audit } = await ask('那个文档的事我怎么回他比较好？',
    { lexicon: 'on', script })
  assert.equal(rounds.length, 2, '应当补问一次')
  const pushed = systemText(rounds[1])
  assert.match(pushed, /没有调用任何工具/)
  assert.match(pushed, /已查/, '顶回去的话要引用它自己的原话')
  assert.equal(reply, '我没有查到，需要调用工具。', '最终答复取补问那一轮的')
  assert.match(audit, /TOOL_GUARD_PUSHBACK/, '顶回去要留审计行')
  assert.match(audit, /claimed-query-without-tool/, '审计里要能看出是哪个机制')
})

test('守卫不误伤：真调过工具，答复里说"已查"是正常的', async () => {
  const script = [
    { choices: [{ message: { content: '', tool_calls: [{ id: 'c1', type: 'function',
      function: { name: 'list_sessions', arguments: '{}' } }] } }] },
    { choices: [{ message: { content: '已查：最近与你聊天的是甲和乙。' } }] },
  ]
  const { rounds, reply } = await ask('甲最近跟我说了什么？', { lexicon: 'on', script })
  assert.equal(rounds.length, 2, '两轮：挑工具 + 作答')
  assert.ok(!/没有调用任何工具/.test(systemText(rounds[1])), '调过工具就不该被顶回去')
  assert.match(reply, /甲和乙/)
})

test('守卫：闲聊不触发（不会把每一轮都顶一次）', async () => {
  const { rounds } = await ask('今天天气不错', { lexicon: 'on' })
  assert.equal(rounds.length, 1)
  assert.ok(!/没有调用任何工具/.test(systemText(rounds[0])))
})

// --------------------------------------------------------------- 工具失败提醒

test('工具没拿出内容：下一轮看得到一句能接住的提醒，且带工具名', async () => {
  const script = [
    { choices: [{ message: { content: '', tool_calls: [{ id: 'c1', type: 'function',
      function: { name: 'get_messages', arguments: '{}' } }] } }] },
    { choices: [{ message: { content: '这一步失败了：缺 contact 参数。' } }] },
  ]
  const { rounds } = await ask('把和甲的聊天导出成 txt 给我。', { lexicon: 'on', script })
  assert.equal(rounds.length, 2)
  const reminder = systemText(rounds[1])
  assert.match(reminder, /get_messages/)
  assert.match(reminder, /没能取到内容/)
  // 工具自己给的原因在 `tool` 消息里（提醒负责把它翻译成指令，不重复原文）
  assert.match(toolMessages(rounds[1]).map(m => m.content).join('\n'), /缺少 contact 参数/)
  // 提醒紧跟在工具结果之后：同一批消息里，不是另开一轮
  const tail = rounds[1].slice(-2).map((m: any) => m.role)
  assert.deepEqual(tail, ['tool', 'system'], '提醒要插在工具结果后面')
})
