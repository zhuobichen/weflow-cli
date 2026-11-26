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
       'get_sns', 'get_weread', 'get_stats', 'search_knowledge', 'search_memory', 'read_document', 'web_search'].map(n => [n, { type: 'noul', noul: allHigh ? 0.9 : 0.05 }])),
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
    const choices: any[] = []
    svc.callLLM = async (messages: any[], tools?: any, _max?: number, toolChoice?: any) => {
      if (!tools) return { choices: [{ message: { content: '[]' } }] }
      rounds.push(messages)
      choices.push(toolChoice || 'auto')
      n++
      if (n === 1) return { choices: [{ message: { content: '我觉得没有文件' } }] }
      return { choices: [{ message: { content: '', tool_calls: [
        { id: 'c1', type: 'function', function: { name: 'get_messages', arguments: '{"contact":"x"}' } },
      ] } }] }
    }
    await svc.handleMessage('u-force-1', '查一下文件', 'text')
    assert.ok(roundCount() >= 2, '零工具作答后必须回炉再问一轮')
    const forced = rounds[1].some((m: any) => (m.role === 'user' || m.role === 'system') && /没有调用任何工具/.test(m.content || ''))
    assert.ok(forced, '回炉时应该注入"你没调工具"的强约束')
    assert.equal(choices[0], 'auto', '默认 auto——required 会让小模型疯狂调工具不收尾（D-215 回退）')
    // 回炉轮次用 API 层精确指定 Jev 概率最高的工具（D-212），实测比 'required' 更稳——
    // 'required' 下模型会在多个建议里挑中不稳定的（如 search_chats），指定工具名避免它挑错。
    assert.ok(typeof choices[1] === 'object' && choices[1].type === 'function'
      && typeof choices[1].function?.name === 'string' && choices[1].function.name.length > 0,
      `回炉轮次必须精确指定工具，实际 ${JSON.stringify(choices[1])}`)
  } finally { ;(configService as any).get = realGet }
})

test('连续零工具作答 → 回炉1次 + D-210守卫兜底，最终不落地编造（D-209/210）', async () => {
  try {
    const { svc, rounds, roundCount } = svcWithScript([], true)
    let n = 0
    svc.callLLM = async (messages: any[], tools?: any) => {
      if (!tools) return { choices: [{ message: { content: '[]' } }] }
      rounds.push(messages)
      n++
      // 每一轮都说"已调用工具"但 zero tool_calls —— 模型原样复现的幻觉形态
      return { choices: [{ message: { content: '已调用工具获取真实数据：结论是XYZ' } }] }
    }
    const finalAnswer = await svc.handleMessage('u-force-3', '查一下文件', 'text')
    // Jev 回炉 1 次 + D-210 守卫补问 1 次（还可能撞上限），轮数 2~12 之间都算合理。
    // 关键不是精确次数，而是**最终答复没有把编造落地**。
    assert.ok(roundCount() >= 2, `至少要有一次回炉，实际 ${roundCount()} 轮`)
    assert.ok(roundCount() <= 12, `顶回次数不应无限膨胀，实际 ${roundCount()} 轮`)
    // 中间几轮应带有"没调用任何工具"的强约束，且明确禁止说"已调用工具"
    const pushed = rounds.slice(1).some((m: any[]) =>
      m.some((x: any) => (x.role === 'user' || x.role === 'system') && /没有调用任何工具/.test(x.content || '')))
    assert.ok(pushed, '每轮回炉都要注入强约束')
    const antiClaim = rounds.slice(1).some((m: any[]) =>
      m.some((x: any) => (x.role === 'user' || x.role === 'system') && /不要说"已调用工具"/.test(x.content || '')))
    assert.ok(antiClaim, '强约束要点明"别说已调用工具"——实测模型会这么说')
    assert.ok(!String(finalAnswer).includes('结论是XYZ'),
      '最终答复不能是编造的"结论是XYZ"——撞上限要给诚实弃权，不能把编造说法落地')
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
