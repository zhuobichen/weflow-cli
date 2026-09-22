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

test('log 模式：不预派发工具，第一轮与关闭时逐字段相同', async () => {
  const { decide } = deciderPicking(0.95, 'list_sessions', 0.95)

  const logged = await ask('最近和谁聊天了', { mode: 'log', decide })
  const baseline = await ask('最近和谁聊天了', { mode: 'off' })

  assert.deepEqual(logged.rounds[0], baseline.rounds[0], '第一轮必须与关闭时逐字段相同')
  assert.match(logged.audit(), /FASTROUTE_WOULD/)
  assert.doesNotMatch(logged.audit(), /TOOL:list_sessions/, '灰度期不预派发工具')
})

test('log 模式下守卫仍然生效 —— 它是安全行为，不是路由行为', async () => {
  // 这里有意的取舍：log 的承诺是"**路由**不改变行为"，而守卫是"模型说查了、其实没查"的
  // 兜底。观测期正是它最该在的时候，所以它不跟着 log 一起关。`off` 才是完全不介入
  // （那时不问路由，也就没有触发守卫的信号）。
  const { decide } = deciderPicking(0.95, 'list_sessions', 0.95)
  const result = await ask('最近和谁聊天了', { mode: 'log', decide })

  assert.equal(result.rounds.length, 2, '模型没调工具而路由说需要查 → 顶回去一次')
  assert.match(result.audit(), /TOOL_GUARD_PUSHBACK/)
})

test('off 模式完全不动：不问路由，也就没有守卫', async () => {
  const { decide, calls } = deciderPicking(0.95, 'list_sessions', 0.95)
  const result = await ask('最近和谁聊天了', { mode: 'off', decide })

  assert.equal(calls.length, 0, 'off 不问判断层')
  assert.equal(result.rounds.length, 1, '没有信号就不会补问')
  assert.doesNotMatch(result.audit(), /TOOL_GUARD_PUSHBACK/)
})

test('快路径落下的工具调用会算进这轮的 tools 计数', async () => {
  const { decide } = deciderPicking(0.95, 'list_sessions', 0.95)
  const result = await ask('最近和谁聊天了', { mode: 'on', decide })

  assert.match(result.audit(), /tools=1/, 'TURN_DONE 里的 tools 数要包含快路径那一次')
})

// ------------------------------------------------- 守卫：路由说需要查，却没调工具

test('守卫：路由说需要查本机数据而模型没调工具时，顶回去一次', async () => {
  // 实测撞到过两次：它回"我确实调了工具查了"，而审计里 tools=0、没有任何 TOOL: 行。
  // 不解释原因，只把矛盾顶回去一次。
  const { decide } = deciderPicking(0.9, 'none', 0.9)   // 需要查本机数据，但没有对应能力
  const script = [
    { choices: [{ message: { content: '我确实查了，但内容被挡住了。' } }] },          // 第一轮：没调工具
    { choices: [{ message: { content: '真正的答复（这轮才去查的）' } }] },              // 补问后
  ]

  const result = await ask('我今天和咸鱼梦想家聊了什么', { mode: 'log', decide, script })

  assert.equal(result.rounds.length, 2, '应当补问一轮')
  const nudge = result.rounds[1].find((m: any) => m.role === 'system' && /没有调用任何工具/.test(m.content))
  assert.ok(nudge, '补问时要把"你手上没有工具结果"这个事实摆给它')
  assert.equal(result.reply, '真正的答复（这轮才去查的）')
  assert.match(result.audit(), /TOOL_GUARD_PUSHBACK/)
})

test('守卫：模型已经调过工具就不顶回去', async () => {
  const { decide } = deciderPicking(0.9, 'none', 0.9)
  const script = [
    { choices: [{ message: { content: '', tool_calls: [{ id: 'c1', type: 'function', function: { name: 'list_sessions', arguments: '{}' } }] } }] },
    { choices: [{ message: { content: '查完了，这是答复' } }] },
  ]

  const result = await ask('我今天和咸鱼梦想家聊了什么', { mode: 'log', decide, script })

  assert.equal(result.rounds.length, 2, '正常的两轮（调工具 + 作答），没有额外补问')
  assert.doesNotMatch(result.audit(), /TOOL_GUARD_PUSHBACK/)
})

test('守卫：路由说不需要查本机数据时不顶回去（闲聊不该被骚扰）', async () => {
  const { decide } = deciderPicking(0.1, 'none', 0.9)   // needs_local_data 没过线
  const result = await ask('你好呀', { mode: 'log', decide })

  assert.equal(result.rounds.length, 1)
  assert.doesNotMatch(result.audit(), /TOOL_GUARD_PUSHBACK/)
})

test('守卫只顶一次：补问后仍然没调工具，就接受并如实收尾', async () => {
  const { decide } = deciderPicking(0.9, 'none', 0.9)
  const script = [
    { choices: [{ message: { content: '第一次没查' } }] },
    { choices: [{ message: { content: '第二次还是没查' } }] },
  ]

  const result = await ask('我今天和咸鱼梦想家聊了什么', { mode: 'log', decide, script })

  assert.equal(result.rounds.length, 2, '只补问一次，不无限顶')
  assert.equal(result.reply, '第二次还是没查')
})

test('守卫：补问那一轮挂了，也要保住第一轮的答复', async () => {
  const { decide } = deciderPicking(0.9, 'none', 0.9)
  const script = [
    { choices: [{ message: { content: '第一轮的答复' } }] },
    new Error('补问时网络断了'),
  ]

  const result = await ask('我今天和咸鱼梦想家聊了什么', { mode: 'log', decide, script })

  assert.equal(result.reply, '第一轮的答复', '守卫失败不许把已经拿到的答复弄丢')
  assert.doesNotMatch(result.reply, /大脑暂时离线/)
})
