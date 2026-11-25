/**
 * Jev 多工具判断（D-191）的判据测试。
 *
 * 核心能力：**一次调用选多个工具**（choice 做不到，noul 可以）。
 * 测试不联网：钉住请求形状（N 个独立 noul）、解析（多选/全低/异常）、阈值边界。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { buildMultiRequest, parseMultiResponse, decideMultiTools, MULTI_TOOLS,
         DEFAULT_MULTI_THRESHOLD } from '../src/services/assistantJevMulti.js'

/** 造一个 8 个工具的回答，只有传进来的过线 */
const answers = (high: string[], value = 0.9) => {
  const out: Record<string, any> = {}
  for (const t of MULTI_TOOLS) {
    out[t.name] = { type: 'noul', noul: high.includes(t.name) ? value : 0.1 }
  }
  return { success: true, model: 'jev-1.14', answers: out }
}

test('请求形状：每个工具一道独立 noul 问题（多选的前提）', () => {
  const req: any = buildMultiRequest('测试消息')
  const qs = Object.values(req.questions) as any[]
  assert.equal(qs.length, MULTI_TOOLS.length)
  assert.ok(qs.every(q => q.type === 'noul'), '必须全用 noul——choice 是单选，选不了多个')
  assert.match(req.state, /测试消息/)
})

test('多选：三个工具同时过线（这是 choice 做不到的）', () => {
  const r = parseMultiResponse(answers(['get_messages', 'get_todos', 'get_daily_report']))
  assert.equal(r.ok, true)
  assert.deepEqual(r.selected.sort(), ['get_daily_report', 'get_messages', 'get_todos'].sort())
  assert.equal(r.scores.get_todos, 0.9)
  assert.equal(r.model, 'jev-1.14', '模型版本要落下来，不写死 jev-latest')
})

test('全低：一个都不选（闲聊不该乱伸手）', () => {
  const r = parseMultiResponse(answers([]))
  assert.equal(r.ok, true)
  assert.deepEqual(r.selected, [])
})

test('阈值边界：0.49 不选，0.5 选（默认值改了测试会红）', () => {
  assert.equal(DEFAULT_MULTI_THRESHOLD, 0.5)
  assert.deepEqual(parseMultiResponse(answers(['get_messages'], 0.49)).selected, [])
  assert.deepEqual(parseMultiResponse(answers(['get_messages'], 0.5)).selected, ['get_messages'])
})

test('自定义阈值：放宽到 0.3 后 0.4 的工具也入选', () => {
  const r = parseMultiResponse(answers(['get_stats'], 0.4), 0.3)
  assert.deepEqual(r.selected, ['get_stats'])
})

test('契约异常：少一个工具的答案就整体回退，不猜（默认站在安全一边）', () => {
  const broken = answers(['get_messages'])
  delete broken.answers.get_stats
  const r = parseMultiResponse(broken)
  assert.equal(r.ok, false)
  assert.deepEqual(r.selected, [])
  assert.match(r.reason, /get_stats/)
})

test('概率不是数：回退（noul 必须是 0~1 浮点，不是布尔）', () => {
  const broken = answers(['get_messages'])
  broken.answers.get_messages.noul = true
  const r = parseMultiResponse(broken)
  assert.equal(r.ok, false)
  assert.deepEqual(r.selected, [])
})

test('判断层抛异常：吞掉按原样回退（保底坏了不让助手停摆）', async () => {
  let called = 0
  const r = await decideMultiTools({ message: '任意', runDecide: async () => {
    called++; throw new Error('connection refused')
  } })
  assert.equal(called, 1, '正对照：确实调用了判断层')
  assert.equal(r.ok, false)
  assert.deepEqual(r.selected, [])
})

test('端到端解析：一次调用返回多选（保底信号一次给全）', async () => {
  const r = await decideMultiTools({ message: '整理聊天 + 待办 + 日报',
    runDecide: async () => answers(['get_messages', 'get_todos', 'get_daily_report']) })
  assert.equal(r.ok, true)
  assert.equal(r.selected.length, 3)
})
