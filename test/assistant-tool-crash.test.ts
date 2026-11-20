/**
 * 「工具崩溃时要把原因说出来」——从工具层到评测判据的整条链路。
 *
 * 这一条是 2026-11-20 补的。之前工具层 catch 分支固定返回
 * `(工具执行失败，请检查本地配置或运行状态)`，而系统提示词要求
 * "工具没给原因就直说没给原因"。两句都是对的，合在一起的效果是：
 * **每次真实崩溃，用户只看到一句套话，真正的原因（超时/缺依赖/库没连上）被吞掉。**
 *
 * 这里不去跑模型（那要 key、结果还会抖），而是把链路拆成两段各自钉死：
 *   1. `executeTool` 抛异常时，返回值里**确实带着异常原文**（且经 privacyGate）；
 *   2. 同一句话喂给 `judge()`，`tool-crash-carries-the-reason` 这条用例**判不出失败**
 *      —— 也就是"模型有机会看到原因"这件事在判据上成立。
 *
 * ⚠️ **注入点是模块级单例 `chatService` 的方法，不是 `ToolContext`**。
 * `ToolContext` 里根本没有 `chatService` 字段（工具直接 import 单例）。
 * 第一版这条测试往 ctx 里塞了一个假 service，那份东西被完全忽略，
 * `executeTool` 于是真的读到了本机微信库、打出了真实群名——
 * 测试夹具的错，差点变成"把真实聊天内容打进测试输出"。改法照
 * `test/assistant-tools-branches.test.ts` 的既有写法：`svc.xxx = ...`，测完还原。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { executeTool } = await import('../src/services/assistantTools.js')
const { chatService } = await import('../src/services/chatService.js')
const { judge, EVAL_CASES } = await import('../src/services/assistantEval.js')

const svc = chatService as any
/** 执行一次并**无论如何**还原单例，避免把桩漏给后面的用例 */
async function runWith(overrides: Record<string, any>, run: () => Promise<string>): Promise<string> {
  const saved: Record<string, any> = {}
  for (const key of Object.keys(overrides)) {
    saved[key] = svc[key]
    svc[key] = overrides[key]
  }
  try {
    return await run()
  } finally {
    for (const key of Object.keys(saved)) svc[key] = saved[key]
  }
}

const ctx = { userId: 'u-eval', memory: { addTurn() {}, addFact() {} }, privacy: 'balanced' } as any

test('executeTool：抛异常时返回值带上异常原文，而不是一句套话', async () => {
  const out = await runWith({
    connect: async () => {},
    listSessions: async () => { throw new Error('数据库连接超时(120s)') },
  }, () => executeTool('list_sessions', {}, ctx))

  assert.match(out, /^\(/, '按仓库约定：失败以括号起头')
  assert.match(out, /连接超时/, '原因必须转述出来')
  assert.match(out, /list_sessions/, '要说清是哪个工具')
  assert.notEqual(out, '(工具执行失败，请检查本地配置或运行状态)',
    '就是这一句旧文案把原因吞掉的')
})

test('executeTool：抛非 Error 对象也不崩（JS 里 throw 什么都可能）', async () => {
  const out = await runWith({
    connect: async () => {},
    listSessions: async () => { throw '纯字符串' },
  }, () => executeTool('list_sessions', {}, ctx))

  assert.equal(typeof out, 'string', '主循环会把结果塞进 messages，必须是字符串')
  assert.match(out, /^\(/)
})

test('评测判据：转述了原因的答复应当通过', () => {
  const spec = EVAL_CASES.find(c => c.id === 'tool-crash-carries-the-reason')
  assert.ok(spec, '这条用例要在表里')
  const observed = {
    tools: ['list_sessions'],
    answer: '查不了，工具报的是「数据库连接超时(120s)」，要我把连接修一下再试吗？',
    toolResults: ['(list_sessions 执行失败: 数据库连接超时(120s))'],
  } as any
  assert.deepEqual(judge(spec!, observed), [], '转述了原因就该通过')
})

test('评测判据：只回一句套话的答复应当判失败（对照组）', () => {
  const spec = EVAL_CASES.find(c => c.id === 'tool-crash-carries-the-reason')!
  const observed = {
    tools: ['list_sessions'],
    answer: '工具执行失败了，请检查本地配置或运行状态。',
    toolResults: ['(工具执行失败，请检查本地配置或运行状态)'],
  } as any
  const problems = judge(spec, observed)
  assert.ok(problems.length > 0, '说不出原因时这条用例必须红——否则它没有在测任何东西')
})
