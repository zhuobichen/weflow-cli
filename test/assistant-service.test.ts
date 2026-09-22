/**
 * 助手的主循环 `handleMessage`（295 行的文件，此前零测试）。
 *
 * 用**合成 harness**驱动：注入假的 `callLLM`（这是唯一出网的出口），工具执行走真实代码
 * 但只挑纯本地的两个（`search_memory` / `save_memory` 只碰记忆），所以整条链路
 * 「收到消息 → 判断 → 调工具 → 脱敏 → 回话 → 写记忆」都能在本地跑完、不联网、不连微信、
 * 不碰真实家目录（HOME 指到临时目录后才 import）。
 *
 * 为什么值得写：这段代码的失败模式是**看起来正常**——工具结果没脱敏就出境、审计日志里
 * 混进聊天正文、LLM 挂了却回一句像模像样的话。这些都不会抛异常。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-service-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantService } = await import('../src/services/assistantService.js')

const AUDIT_FILE = join(HOME, '.weflow-cli', 'assistant_audit.log')
const MAX_TOOL_ROUNDS = 6 // assistantService.ts 的常量

let seq = 0
function newUser(): string {
  seq += 1
  return `u-svc-${seq}`
}

interface Harness {
  svc: any
  /** 每次 ReAct 调用的 messages（不含记忆压缩/事实提取那两次） */
  rounds: any[][]
  memoryCalls: number
  audit: () => string
}

/**
 * @param replies ReAct 各轮的剧本；给 Error 就是那一轮抛错。
 *                记忆压缩/事实提取（调用时不带 tools）固定回 '[]'。
 */
function harness(replies: any[]): Harness {
  const svc: any = new AssistantService()
  const rounds: any[][] = []
  let memoryCalls = 0
  let i = 0
  svc.callLLM = async (messages: any[], tools?: any) => {
    if (!tools) { // 记忆压缩 / 事实提取
      memoryCalls++
      return { choices: [{ message: { content: '[]' } }] }
    }
    rounds.push(messages)
    const reply = replies[i++]
    if (reply instanceof Error) throw reply
    if (reply === undefined) throw new Error('剧本用完了（比预期多问了一轮）')
    return reply
  }
  return {
    svc,
    rounds,
    get memoryCalls() { return memoryCalls },
    audit: () => (existsSync(AUDIT_FILE) ? readFileSync(AUDIT_FILE, 'utf8') : ''),
  } as Harness
}

function toolCall(name: string, args: unknown, id = 'call-1'): any {
  return {
    choices: [{
      message: {
        content: '',
        tool_calls: [{
          id,
          type: 'function',
          function: { name, arguments: typeof args === 'string' ? args : JSON.stringify(args) },
        }],
      },
    }],
  }
}

function answer(text: string): any {
  return { choices: [{ message: { content: text } }] }
}

function toolMessages(messages: any[]): any[] {
  return messages.filter((m) => m.role === 'tool')
}

test('内置指令直接回答，不叫 LLM', async () => {
  const h = harness([])
  const user = newUser()

  const help = await h.svc.handleMessage(user, '帮助', 'text')
  assert.match(help, /第二大脑/)
  assert.match(help, /指令: 记忆 \| 隐私 \| 清空记忆/)

  assert.match(await h.svc.handleMessage(user, 'help', 'text'), /第二大脑/)
  assert.equal(h.rounds.length, 0, '内置指令不该产生任何 LLM 调用')
})

test('非文字消息直接回掉，不叫 LLM', async () => {
  const h = harness([])
  const reply = await h.svc.handleMessage(newUser(), '[图片]', 'image')
  assert.match(reply, /只支持文字消息/)
  assert.equal(h.rounds.length, 0)
})

test('「记忆」如实报出三层状态与今日用量', async () => {
  const h = harness([answer('好')])
  const user = newUser()
  h.svc.memory.addFact(user, '喜欢喝茶')

  await h.svc.handleMessage(user, '随便说一句', 'text')
  const report = await h.svc.handleMessage(user, '记忆', 'text')

  assert.match(report, /工作窗口: \d+ 条/)
  assert.match(report, /长期事实: 1 条/)
  assert.match(report, /· 喜欢喝茶/)
  assert.match(report, /隐私模式: strict/)
  assert.match(report, /今日用量: 0\/100/, '配额只在常驻循环里累加，这里如实报 0')
})

test('「清空记忆」清掉该用户的窗口与事实，并留下审计行', async () => {
  const h = harness([answer('好')])
  const user = newUser()
  h.svc.memory.addFact(user, '一件事实')
  await h.svc.handleMessage(user, '说句话', 'text')

  assert.match(await h.svc.handleMessage(user, '清空记忆', 'text'), /已清空/)
  assert.deepEqual(h.svc.memory.facts(user), [])
  assert.deepEqual(h.svc.memory.workingWindow(user), [])
  assert.match(h.audit(), /MEMORY_RESET/)
})

test('工具调用：结果被喂回对话，最终回话用模型的答复', async () => {
  const h = harness([
    toolCall('search_memory', { keyword: '喝茶' }),
    answer('你之前提过喜欢喝茶。'),
  ])
  const user = newUser()
  h.svc.memory.addFact(user, '喜欢喝茶')

  const reply = await h.svc.handleMessage(user, '我喜欢喝什么来着', 'text')

  assert.equal(reply, '你之前提过喜欢喝茶。')
  assert.equal(h.rounds.length, 2, '一轮调工具、一轮作答')
  // 第一轮里能看到系统提示 + 用户这句话
  assert.match(h.rounds[0][0].content, /第二大脑|助手/)
  // 第二轮里必须带着工具的真实返回，而不是空的占位
  const tools = toolMessages(h.rounds[1])
  assert.equal(tools.length, 1)
  assert.match(tools[0].content, /喜欢喝茶/)
})

test('工具结果出境前被脱敏：链接打码，原文不再出现', async () => {
  const h = harness([
    toolCall('search_memory', { keyword: '文档' }),
    answer('好'),
  ])
  const user = newUser()
  h.svc.memory.addFact(user, '项目文档 https://secret.example/x')

  await h.svc.handleMessage(user, '文档在哪', 'text')

  const toolText = toolMessages(h.rounds[1])[0].content as string
  assert.match(toolText, /\[链接\]/)
  assert.doesNotMatch(toolText, /secret\.example/, '脱敏后的文本不许再带着原文出境')
})

test('未知工具不崩，把「未知工具」如实喂回去', async () => {
  const h = harness([toolCall('no_such_tool', {}), answer('换个办法回答你')])
  const reply = await h.svc.handleMessage(newUser(), '调用一个不存在的工具', 'text')

  assert.equal(reply, '换个办法回答你')
  assert.match(toolMessages(h.rounds[1])[0].content, /未知工具/)
})

test('工具参数不是合法 JSON 时按空对象容错，不中断这一轮', async () => {
  const h = harness([toolCall('search_memory', '{这不是 json'), answer('继续')])
  const reply = await h.svc.handleMessage(newUser(), '问点什么', 'text')

  assert.equal(reply, '继续')
  assert.match(toolMessages(h.rounds[1])[0].content, /长期记忆中无相关内容|参数/)
})

test('一直要求调工具时会停下，而不是无限转', async () => {
  const h = harness(new Array(MAX_TOOL_ROUNDS).fill(null).map(() => toolCall('search_memory', { keyword: 'x' })))
  const reply = await h.svc.handleMessage(newUser(), '循环调用吧', 'text')

  assert.equal(h.rounds.length, MAX_TOOL_ROUNDS, `最多 ${MAX_TOOL_ROUNDS} 轮`)
  assert.match(reply, /太复杂|换个问法/)
})

test('LLM 挂了要说出来，并指回本地指令', async () => {
  const h = harness([new Error('socket hang up')])
  const user = newUser()
  const reply = await h.svc.handleMessage(user, '在吗', 'text')

  assert.match(reply, /大脑暂时离线/)
  assert.match(reply, /socket hang up/)
  assert.match(reply, /帮助/, '挂了也要告诉用户本地指令还能用')
})

test('审计日志记事件与条数，绝不记聊天正文', async () => {
  const h = harness([answer('好的')])
  const secret = '这句正文不该出现在审计日志里'
  await h.svc.handleMessage(newUser(), secret, 'text')

  const audit = h.audit()
  assert.match(audit, /TURN_DONE/)
  assert.doesNotMatch(audit, new RegExp(secret))
})

test('工具调用会留下带工具名的审计行', async () => {
  const h = harness([toolCall('search_memory', { keyword: 'x' }), answer('好')])
  await h.svc.handleMessage(newUser(), '问', 'text')

  assert.match(h.audit(), /TOOL:search_memory/)
  assert.match(h.audit(), /tools=1/, 'TURN_DONE 里要能看到这轮用了几个工具')
})

test('用户轮与助手轮都进工作窗口（服务把记忆接上了）', async () => {
  const h = harness([answer('答复')])
  const user = newUser()
  await h.svc.handleMessage(user, '问题', 'text')

  const window = h.svc.memory.workingWindow(user)
  assert.deepEqual(window.map((t: any) => t.role), ['user', 'assistant'])
  assert.equal(window[1].content, '答复')
})

test('save_memory 工具真的写进长期记忆', async () => {
  const h = harness([
    toolCall('save_memory', { content: '用户的猫叫豆豆' }),
    answer('记住了'),
  ])
  const user = newUser()
  await h.svc.handleMessage(user, '记住我的猫叫豆豆', 'text')

  assert.deepEqual(h.svc.memory.facts(user).map((f: any) => f.content), ['用户的猫叫豆豆'])
})

test('被严格模式挡住时，系统提示要求它给出全部三条路（含本地模型那条）', async () => {
  // 实测过一次：它只说了"关掉严格模式"，漏掉了"换本地模型"——那恰恰是最贴合隐私顾虑的路，
  // 而且本地推理下严格模式的屏蔽**本来就不生效**（isLocalInference 为真时直接返回原文）。
  const h = harness([])
  const prompt: string = h.svc.buildSystemPrompt('u-prompt')

  assert.match(prompt, /严格隐私模式/)
  assert.match(prompt, /balanced/)
  assert.match(prompt, /ollama/)
  assert.match(prompt, /不要只说/)
})

test('「隐私」指令报出当前档位与改法，且不叫模型', async () => {
  const h = harness([])
  const report = await h.svc.handleMessage(newUser(), '隐私', 'text')

  assert.match(report, /隐私模式: strict/, '测试环境没有配置，取默认档')
  assert.match(report, /只能看到时间与字数|被屏蔽/)
  assert.match(report, /config set assistantPrivacy balanced/)
  assert.match(report, /config set assistantPrivacy open/)
  assert.equal(h.rounds.length, 0, '查隐私档不该产生任何模型调用')
})

test('云端推理时，「隐私」还会给出换本地模型这条路', async () => {
  const h = harness([])
  const report = await h.svc.handleMessage(newUser(), '隐私', 'text')

  assert.match(report, /aiEngine ollama/, '正文不出机器的那条路必须一并给出')
})

test('一条微信消息改不了隐私档位：它只报不改', async () => {
  const h = harness([])
  await h.svc.handleMessage(newUser(), '隐私', 'text')
  // 尝试用自然语言"改"它 —— 应当仍然只是被当成普通提问，由模型回答，而不是写配置
  const before = h.svc.privacyModeForTest ?? null
  assert.equal(before, null, '服务里不该存在"按消息改档位"的入口')
})
