/**
 * Jev 建议的强制调用红线（D-202）：Jev 说要查、模型却零工具作答 → 强制回炉补查一次。
 *
 * 用户的取舍："直接强制调用吧，调多调少以后研究，能做正确就OK。"
 * 这是 Jev 建议自带的硬约束，**不走守卫总开关**（那个默认关）。
 * 只强制一次（防无限重跑）；Jev 没建议时不干预。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'jev-force-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantService } = await import('../src/services/assistantService.js')
const { configService } = await import('../src/services/configService.js')
const realGet = configService.get.bind(configService)

/** 判断层回放：所有工具高概率（Jev 建议要查）；低概率版本用于"没建议"对照 */
function decider(allHigh: boolean) {
  return async () => ({
    success: true, model: 'probe',
    answers: Object.fromEntries(
      ['get_messages', 'search_chats', 'list_sessions', 'get_todos', 'get_daily_report',
       'get_sns', 'get_weread', 'get_stats'].map(n => [n, { type: 'noul', noul: allHigh ? 0.9 : 0.05 }])),
  })
}

/** 剧本：第一轮给纯答复（不带工具），后续轮按 given 给 */
function svcWithScript(script: any[], high: boolean) {
  ;(configService as any).get = (k: string) =>
    (k === 'assistantJevDoubleCheck' ? 'on' : realGet(k))
  const svc: any = new AssistantService({ routeDecider: decider(high) })
  const rounds: any[][] = []
  let i = 0
  svc.callLLM = async (messages: any[], tools?: any) => {
    if (!tools) return { choices: [{ message: { content: '[]' } }] }  // 记忆压缩/提取
    rounds.push(messages)
    const r = script[Math.min(i++, script.length - 1)]
    return r instanceof Error ? Promise.reject(r) : r
  }
  return { svc, rounds, roundCount: () => rounds.length }
}

test('Jev 建议 + 零工具作答 → 强制回炉（第二轮才给出答复）', async () => {
  try {
    const { svc, rounds, roundCount } = svcWithScript([
      { choices: [{ message: { content: '我觉得没有文件' } }] },        // 第1轮：零工具
      { choices: [{ message: { content: '' } }] },                       // 第2轮占位（会被工具分支覆盖）
    ], true)
    // 让第二轮真的调工具：覆盖 callLLM 在第二次返回 tool_calls
    let n = 0
    svc.callLLM = async (messages: any[], tools?: any) => {
      if (!tools) return { choices: [{ message: { content: '[]' } }] }
      rounds.push(messages)
      n++
      if (n === 1) return { choices: [{ message: { content: '我觉得没有文件' } }] }
      return { choices: [{ message: { content: '', tool_calls: [
        { id: 'c1', type: 'function', function: { name: 'get_messages', arguments: '{"contact":"x"}' } },
      ] } }] }
    }
    await svc.handleMessage('u-force-1', '查一下文件', 'text')
    assert.ok(roundCount() >= 2, '零工具作答后必须回炉再问一轮')
    const forced = rounds[1].some((m: any) => m.role === 'user' && /没有调用任何工具/.test(m.content || ''))
    assert.ok(forced, '回炉时应该注入"你没调工具"的强约束')
  } finally { ;(configService as any).get = realGet }
})

test('Jev 没建议时，零工具作答不回炉（正常一轮结束）', async () => {
  try {
    const { svc, roundCount } = svcWithScript([
      { choices: [{ message: { content: '这是常识，不用查' } }] },
    ], false)
    await svc.handleMessage('u-force-2', '1+1等于几', 'text')
    assert.equal(roundCount(), 1, 'Jev 没建议就不该强制回炉')
  } finally { ;(configService as any).get = realGet }
})

test.after(() => { ;(configService as any).get = realGet })
