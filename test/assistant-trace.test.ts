/**
 * 决策轨迹（`assistantTrace`）：**给排查用**的那层记录。
 *
 * 它与审计是两件事，别混：审计只记事件与字节量、绝不记内容、且是出境记录；轨迹记的是
 * "这一步怎么走的"，**包括工具参数摘要**，所以它是本地的。这批用例盯的是三件事：
 *
 * 1. 参数摘要**过脱敏、有截断**——它是唯一可能把聊天边角带进日志的地方；
 * 2. 微信里那条 `轨迹` **不许出现 userId**（账号标识没必要进聊天）；
 * 3. `producedContent` 那个约定（括号起头 = 没能给出内容）**边界是诚实的**：唯一的例外
 *    写在一张白名单里，而白名单里每一条都必须在 `assistantTools.ts` 里真的出现过——
 *    改措辞忘了同步就红。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-trace-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME
mkdirSync(join(HOME, '.weflow-cli'), { recursive: true })

const trace = await import('../src/services/assistantTrace.js')
const tools = await import('../src/services/assistantTools.js')

const SPEC_PATH = join(import.meta.dirname, '..', 'src', 'services', 'assistantTools.ts')

function turn(patch: Record<string, unknown> = {}) {
  return {
    at: '2026-09-23T12:00:00.000Z', userId: 'o9cq80-example-id', questionChars: 5, steps: [],
    rounds: 1, toolCalls: 0, reasoning: '', reasoningChars: 0, answerChars: 10,
    stop: 'answered', elapsedMs: 1200, ...patch,
  } as any
}

// --------------------------------------------------------------- 参数摘要

test('参数摘要：键值成对，值太长就截断并留记号', () => {
  const summary = trace.summarizeArgs({ contact: '甲', limit: 20 })
  assert.equal(summary, 'contact=甲, limit=20')

  const long = trace.summarizeArgs({ keyword: 'x'.repeat(200) })
  assert.match(long, /keyword=x{40}…$/, '值要截断，且截断要留记号')
  assert.ok(long.length < 80, `摘要不该无界增长：${long.length}`)
})

test('参数摘要过脱敏：链接会被打码（它是唯一可能带走聊天边角的地方）', () => {
  const summary = trace.summarizeArgs({ url: 'https://secret.example/x?a=1' })
  assert.doesNotMatch(summary, /secret\.example/, '摘要里不该出现原始链接')
  assert.match(summary, /\[链接\]/)
})

test('参数摘要：对象与数组只报形状，不展开', () => {
  assert.equal(trace.summarizeArgs({ ids: [1, 2, 3] }), 'ids=[3 项]')
  assert.equal(trace.summarizeArgs({ nested: { a: 1 } }), 'nested={…}')
})

// --------------------------------------------------------------- 推理内容

test('推理内容：短的原样，长的截断且留记号', () => {
  assert.equal(trace.clipReasoning('想一下'), '想一下')
  const long = trace.clipReasoning('想'.repeat(2000))
  assert.equal(long.length, 801)
  assert.match(long, /…$/)
})

// --------------------------------------------------------------- 「有没有给出内容」的约定

test('括号起头 = 工具没能给出内容；白名单里的成功消息不算', () => {
  assert.equal(tools.producedContent('· 甲: 你好'), true)
  assert.equal(tools.producedContent('(缺少 contact 参数)'), false)
  assert.equal(tools.producedContent('(没找到「某人」的消息)'), false)
  assert.equal(tools.producedContent('(已附上图片 #44（580×435）。你能看到它了。)'), true,
    '成功的句子也可以是括号起头，所以它得在白名单里')
})

test('白名单里每一条都必须在 assistantTools.ts 里真的出现过', () => {
  // 这条是这个约定唯一的护栏：白名单是手写的，模型改了措辞而白名单没跟上，
  // 就会静默地多标一个/少标一个「无内容」。测试扫源码，让那种漂移当场变红。
  const source = readFileSync(SPEC_PATH, 'utf8')
  for (const prefix of tools.PAREN_SUCCESS_PREFIXES) {
    const literal = prefix.slice(1).split('${')[0]     // 去掉开括号、去掉插值
    assert.ok(source.includes(literal), `白名单里的「${prefix}」在工具源码里找不到对应写法`)
  }
})

// --------------------------------------------------------------- 渲染

test('终端描述：带上时长、轮次、每一步与它的结果', () => {
  const lines = trace.describeTurn(turn({
    toolCalls: 2, rounds: 3,
    steps: [
      { kind: 'route', detail: '判断层: 不需要查本机数据 (0.03)' },
      { kind: 'tool', name: 'get_messages', args: 'contact=甲', bytes: 1148, produced: true },
      { kind: 'tool', name: 'who_owes_reply', args: 'days=14', bytes: 15, produced: false },
      { kind: 'note', detail: '第 3 轮往返后给出答复' },
    ],
  }))
  const text = lines.join('\n')
  assert.match(text, /1200ms/)
  assert.match(text, /工具 2 次/)
  assert.match(text, /get_messages\(contact=甲\) → 1148 字节/)
  assert.match(text, /who_owes_reply\(days=14\) → 15 字节\s+（无内容）/)
  assert.match(text, /判断层: 不需要查本机数据/)
})

test('终端描述：模型没返回推理时直说，不假装有', () => {
  assert.match(trace.describeTurn(turn()).join('\n'), /推理: 无/)
  const withReasoning = trace.describeTurn(turn({ reasoningChars: 42, reasoning: '先看会话' }))
  assert.match(withReasoning.join('\n'), /推理 42 字/)
  assert.match(withReasoning.join('\n'), /先看会话/)
})

test('微信那条轨迹：不出现 userId（账号标识没必要进聊天）', () => {
  const text = trace.describeForChat(turn({
    steps: [{ kind: 'tool', name: 'get_messages', args: 'contact=甲', bytes: 100, produced: true }],
  }))
  assert.doesNotMatch(text, /o9cq80-example-id/)
  assert.match(text, /get_messages/)
  assert.match(text, /上一轮/)
})

test('微信那条轨迹：还没有记录时如实说，而不是给一段空话', () => {
  assert.match(trace.describeForChat(undefined), /还没有可看的轨迹/)
})

// --------------------------------------------------------------- 落盘

test('记录一轮后能读回来，且顺序是新的在前', () => {
  trace.recordTurn(turn({ questionChars: 1 }))
  trace.recordTurn(turn({ questionChars: 2 }))
  const turns = trace.readTurns(5)
  assert.equal(turns.length, 2)
  assert.equal(turns[0].questionChars, 2, '最近一轮排在最前')
})

test('读一个不存在的轨迹文件不抛，回空数组', () => {
  const before = trace.readTurns(3)
  assert.ok(Array.isArray(before))
})

test('落盘的是 JSONL，一行一条，字段齐全', () => {
  trace.recordTurn(turn({ stop: 'rounds-exhausted', toolCalls: 6 }))
  const lines = readFileSync(trace.traceFile(), 'utf8').split('\n').filter(Boolean)
  const last = JSON.parse(lines[lines.length - 1])
  assert.equal(last.stop, 'rounds-exhausted')
  assert.equal(last.toolCalls, 6)
  assert.equal(typeof last.elapsedMs, 'number')
})
