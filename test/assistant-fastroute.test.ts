/**
 * 单轮快路径接进 `handleMessage` 之后的行为（D-035 的验收清单）。
 *
 * 清单要求覆盖四件事：**路由对**、**路由错（必须降级）**、**路由不确定（必须降级）**、
 * **没有审计行就不得派发工具**。外加一条它自己写的：默认关闭，且回退路径与今天**逐字段一致**。
 *
 * 这四条里最容易做错的是"降级"的含义：不是"降级到一个更差的新兜底"，而是**原样回到今天的
 * ReAct 循环**。所以这里用"同一问题、开与不开快路径"的对照来断言：不开是两轮，开了是一轮，
 * 而回退时两边送去模型的消息逐字段相同。
 *
 * 打桩：注入的假判断层（构造参数）、`callLLM`、`chatService` 的读方法。不联网、不 spawn
 * Python、不碰真实家目录（HOME 已指到临时目录）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-fastroute-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantService } = await import('../src/services/assistantService.js')
const { chatService } = await import('../src/services/chatService.js')
const { configService } = await import('../src/services/configService.js')

const AUDIT_FILE = join(HOME, '.weflow-cli', 'assistant_audit.log')
const SESSIONS = '· 甲: 最近还好吗\n· 乙: 明天见'
const realGet = configService.get.bind(configService)

function setConfig(mode: string | undefined): void {
  ;(configService as any).get = (key: string) =>
    key === 'assistantFastRoute' ? (mode ?? '') : realGet(key)
}

function audit(): string {
  return existsSync(AUDIT_FILE) ? readFileSync(AUDIT_FILE, 'utf8') : ''
}

/** 判断层的剧本：needs_local_data 概率 + 选中的能力 + 置信度 */
function deciderPicking(needsTool: number, capability: string, confidence: number) {
  const calls: any[] = []
  const decide = async (request: any) => {
    calls.push(request)
    return {
      success: true,
      model: 'jev-test-1.0',
      answers: {
        needs_local_data: { noul: needsTool },
        capability: { choice: capability, confidence, probabilities: { [capability]: confidence } },
      },
    }
  }
  return { decide, calls }
}

let seq = 0

interface Harness {
  svc: any
  rounds: any[][]
  reply: string
  audit: () => string
}

/** 跑一条消息，返回模型看到的每一轮消息（**深拷贝**：原数组会被继续 push）。
 *
 *  每次用一个全新的 userId：记忆是持久化的，两次 run 共用一个 id 的话第二次的窗口里会多出
 *  上一轮的话，两条转录就没法逐字段比了——这正是"基线对照"的前提。 */
async function ask(message: string, opts: {
  mode?: string
  decide?: (request: any) => Promise<any>
  script?: any[]
  before?: () => void
} = {}): Promise<Harness> {
  setConfig(opts.mode)
  ;(chatService as any).connect = async () => {}
  ;(chatService as any).listSessions = async () => ([
    { displayName: '甲', username: 'wxid_a', summary: '最近还好吗' },
    { displayName: '乙', username: 'wxid_b', summary: '明天见' },
  ])
  ;(chatService as any).getFavorites = async () => ({ success: true, total: 0, favorites: [] })
  opts.before?.()

  // 审计文件是整个测试共用的：清空它，`audit()` 才是"这一次 run 写了什么"
  mkdirSync(join(HOME, '.weflow-cli'), { recursive: true })
  writeFileSync(AUDIT_FILE, '')

  const svc: any = new AssistantService({ routeDecider: opts.decide })
  const rounds: any[][] = []
  const script = opts.script ?? [{ choices: [{ message: { content: '给你的答复' } }] }]
  let i = 0
  svc.callLLM = async (messages: any[]) => {
    rounds.push(JSON.parse(JSON.stringify(messages)))
    return script[Math.min(i++, script.length - 1)]
  }
  seq += 1
  const reply = await svc.handleMessage(`u-fast-${seq}`, message, 'text')
  // 审计也要**当场取快照**：同一个用例里跑两次 ask 时，第二次的清理会把第一次的行冲掉，
  // 而比较的对象正是"第一次跑出来的那几行"。
  const snapshot = existsSync(AUDIT_FILE) ? readFileSync(AUDIT_FILE, 'utf8') : ''
  return { svc, rounds, reply, audit: () => snapshot }
}

const toolMessages = (messages: any[]) => messages.filter(m => m.role === 'tool')

test.after(() => { ;(configService as any).get = realGet })

// ------------------------------------------------------------------ 默认关闭

test('默认关闭：判断层一次都不叫，送去模型的消息与基线逐字段相同', async () => {
  const { decide, calls } = deciderPicking(0.95, 'list_sessions', 0.95)

  // 默认（配置里没有这个键）
  const off = await ask('最近和谁聊天了', { mode: undefined, decide })
  // 显式 off
  const explicitOff = await ask('最近和谁聊天了', { mode: 'off', decide })

  assert.equal(calls.length, 0, '默认关就不该问判断层')
  assert.deepEqual(off.rounds, explicitOff.rounds)
  assert.equal(off.rounds.length, 1, '模型自己一句就答完了，这里只走了一轮')
})

// ------------------------------------------------------------------ 路由对

test('路由对：工具在循环之前就派发了，两轮往返变一轮', async () => {
  const { decide } = deciderPicking(0.95, 'list_sessions', 0.93)

  const fast = await ask('最近和谁聊天了', { mode: 'on', decide })
  assert.equal(fast.rounds.length, 1, '循环第一轮就已经拿到工具结果——这正是快路径的全部意义')

  const first = fast.rounds[0]
  const tools = toolMessages(first)
  assert.equal(tools.length, 1)
  assert.match(tools[0].content, /甲/)
  const call = first.find(m => m.tool_calls)
  assert.equal(call.tool_calls[0].function.name, 'list_sessions')
  assert.match(fast.audit(), /FASTROUTE_HIT/, '命中要留审计行')
  assert.match(fast.audit(), /list_sessions/)
  assert.match(fast.audit(), /jev-test-1\.0/, '审计里要能看出是哪个版本的模型判的')
})

test('对照组：同一问题不开快路径，模型要两轮才答完', async () => {
  const script = [
    { choices: [{ message: { content: '', tool_calls: [{ id: 'c1', type: 'function', function: { name: 'list_sessions', arguments: '{}' } }] } }] },
    { choices: [{ message: { content: '给你的答复' } }] },
  ]
  const baseline = await ask('最近和谁聊天了', { mode: 'off', script })
  assert.equal(baseline.rounds.length, 2, '第一轮挑工具、第二轮作答')
  assert.equal(toolMessages(baseline.rounds[0]).length, 0, '这一轮里还没有工具结果')
  assert.equal(toolMessages(baseline.rounds[1]).length, 1)
})

// ------------------------------------------------------------------ 路由错

test('路由错：模型仍能在循环里再要一个工具，不会被错结果卡死', async () => {
  // 判断层挑错了能力（问的是待办，它挑了统计），模型看到不相关的结果后会再要一次
  const { decide } = deciderPicking(0.9, 'get_stats', 0.9)
  const script = [
    { choices: [{ message: { content: '', tool_calls: [{ id: 'c2', type: 'function', function: { name: 'list_sessions', arguments: '{}' } }] } }] },
    { choices: [{ message: { content: '这才是对的答复' } }] },
  ]

  const result = await ask('我有什么事要做', { mode: 'on', decide, script })

  assert.equal(result.rounds.length, 2)
  assert.equal(toolMessages(result.rounds[0]).length, 1, '错的那一个先派发了')
  assert.equal(toolMessages(result.rounds[1]).length, 2, '模型自己又要了一个，循环没有被卡住')
  assert.equal(result.reply, '这才是对的答复')
})

// ------------------------------------------------------------------ 路由不确定

test('置信度不足：不派发任何工具，回退成与基线一致的两轮', async () => {
  const { decide } = deciderPicking(0.9, 'list_sessions', 0.2)
  const script = [
    { choices: [{ message: { content: '', tool_calls: [{ id: 'c1', type: 'function', function: { name: 'list_sessions', arguments: '{}' } }] } }] },
    { choices: [{ message: { content: '答案是这个' } }] },
  ]

  const uncertain = await ask('最近和谁聊天了', { mode: 'on', decide, script })
  const baseline = await ask('最近和谁聊天了', { mode: 'off', script })

  assert.equal(toolMessages(uncertain.rounds[0]).length, 0, '拿不准就不许预派发')
  assert.deepEqual(uncertain.rounds, baseline.rounds, '回退路径必须与今天逐字段一致')
  assert.match(uncertain.audit(), /FASTROUTE_SKIP/)
})

test('判断层说不需要查本机数据时，也回退成基线', async () => {
  const { decide } = deciderPicking(0.1, 'list_sessions', 0.99)
  const result = await ask('你好呀', { mode: 'on', decide })

  assert.equal(result.rounds.length, 1)
  assert.equal(toolMessages(result.rounds[0]).length, 0)
  assert.match(result.audit(), /FASTROUTE_SKIP/)
})

test('判断层抛错：这条消息照常回答，且回退到基线', async () => {
  const boom = async () => { throw new Error('判断层挂了') }
  const broken = await ask('最近和谁聊天了', { mode: 'on', decide: boom })
  const baseline = await ask('最近和谁聊天了', { mode: 'off' })

  assert.deepEqual(broken.rounds, baseline.rounds)
  assert.doesNotMatch(broken.reply, /大脑暂时离线/, '判断层坏了不该让这条消息失败')
  assert.match(broken.audit(), /FASTROUTE_SKIP/)
})

// ------------------------------------------------------------------ 审计与灰度

test('没有审计行就不得派发工具：快路径派发的工具同样留下 TOOL 行', async () => {
  const { decide } = deciderPicking(0.95, 'list_sessions', 0.95)
  const result = await ask('最近和谁聊天了', { mode: 'on', decide })

  assert.match(result.audit(), /TOOL:list_sessions/, '派发前必须写审计行，两条路共用同一份实现')
  assert.equal(toolMessages(result.rounds[0]).length, 1)
})

test('下发工具与审计行是同一份实现（循环不再自己内联一份）', () => {
  const source = readFileSync(join(process.cwd(), 'src', 'services', 'assistantService.ts'), 'utf8')
  const loop = source.slice(source.indexOf('for (const tc of msg.tool_calls)'),
    source.indexOf('continue', source.indexOf('for (const tc of msg.tool_calls)')))

  assert.match(loop, /this\.runToolCall\(/, '循环必须走共用的 runToolCall')
  assert.doesNotMatch(loop, /privacyGate\.audit\(/, '循环里不该再内联一份审计')
})

test('log 模式：只记「本来会走哪条」，行为一个字不改', async () => {
  const { decide } = deciderPicking(0.95, 'list_sessions', 0.95)

  const logged = await ask('最近和谁聊天了', { mode: 'log', decide })
  const baseline = await ask('最近和谁聊天了', { mode: 'off' })

  assert.deepEqual(logged.rounds, baseline.rounds, '只记不改：送去模型的消息必须与关闭时相同')
  assert.match(logged.audit(), /FASTROUTE_WOULD/)
  assert.doesNotMatch(logged.audit(), /TOOL:list_sessions/, '灰度期不派发工具')
})

test('快路径落下的工具调用会算进这轮的 tools 计数', async () => {
  const { decide } = deciderPicking(0.95, 'list_sessions', 0.95)
  const result = await ask('最近和谁聊天了', { mode: 'on', decide })

  assert.match(result.audit(), /tools=1/, 'TURN_DONE 里的 tools 数要包含快路径那一次')
})
