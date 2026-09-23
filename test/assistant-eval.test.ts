/**
 * 助手评测的**判定逻辑**（`judge`）与用例表本身。
 *
 * 评测跑起来要真模型、要花钱，所以它不进 `npm test`；但"一条用例过不过"的判定是纯函数，
 * 必须离线被测——判错的方向有两个，都很难看：把越了底线的一轮判成通过（于是评测形同
 * 虚设），或者把答得好的一轮判成失败。**两个方向都真的发生过**：写这条用例时连着两次
 * 用"查不到"去卡模型说的"查不了"，报告里就多了两条假失败。
 *
 * 另有一组用例表自身的不变量：期望表写漏了（比如一条用例什么断言都没有）不该靠肉眼发现。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { judge, summarize, budgetWarnings, EVAL_CASES } =
  await import('../src/services/assistantEval.js')

function observed(patch: Record<string, unknown> = {}) {
  return {
    tools: [], cloudCalls: 1, answer: '', facts: [], traceArgs: [], elapsedMs: 1, ...patch,
  } as any
}

const base = { id: 'x', question: 'q', expect: {} } as any

test('什么都没要求时通过', () => {
  assert.deepEqual(judge(base, observed()), [])
})

test('该调的工具没调：失败，并把实际调了什么写进原因里', () => {
  const problems = judge({ ...base, expect: { mustCall: ['get_messages'] } },
                         observed({ tools: ['list_sessions'] }))
  assert.equal(problems.length, 1)
  assert.match(problems[0], /没调用 get_messages/)
  assert.match(problems[0], /list_sessions/, '要说清实际调了什么，不然没法查')
})

test('不该调的工具调了：失败', () => {
  const problems = judge({ ...base, expect: { mustNotCall: ['look_at_image'] } },
                         observed({ tools: ['look_at_image'] }))
  assert.match(problems[0], /不该调用 look_at_image/)
})

test('工具调用超上限：把调用序列一起报出来', () => {
  const problems = judge({ ...base, expect: { maxTools: 2 } },
                         observed({ tools: ['a', 'b', 'c'] }))
  assert.match(problems[0], /3 次，超过上限 2/)
  assert.match(problems[0], /a、b、c/)
})

test('该有的内容没有 / 不该有的内容有了', () => {
  assert.match(judge({ ...base, expect: { answerMatches: /查不了/ } },
                     observed({ answer: '我查不到' }))[0], /没有该有的东西/)
  assert.match(judge({ ...base, expect: { answerForbids: /没人在等/ } },
                     observed({ answer: '没人在等你' }))[0], /出现了不该有的东西/)
})

test('记忆是按结果判的，不是按调没调 save_memory', () => {
  const spec = { ...base, expect: { memoryContains: '豆豆' } }
  assert.match(judge(spec, observed({ facts: ['用户养了只猫'] }))[0], /没有「豆豆」/)
  assert.deepEqual(judge(spec, observed({ facts: ['用户养了只猫叫豆豆'] })), [])
})

test('抛异常时只报异常本身，不再逐条刷其它断言', () => {
  const problems = judge({ ...base, expect: { mustCall: ['get_messages'], answerMatches: /x/ } },
                         observed({ error: 'boom' }))
  assert.equal(problems.length, 1)
  assert.match(problems[0], /抛异常：boom/)
})

test('一条用例可以同时踩多条：全部报出来', () => {
  const problems = judge({ ...base, expect: { mustCall: ['a'], maxTools: 0, answerMatches: /z/ } },
                         observed({ tools: ['b'], answer: 'y' }))
  assert.equal(problems.length, 3)
})

test('summarize 数的是"问题为空的条数"，跳过的不算', () => {
  const report = summarize([
    { spec: base, observed: observed(), problems: [], warnings: [] },
    { spec: base, observed: observed(), problems: ['x'], warnings: [] },
    { spec: base, observed: observed(), problems: ['y'], warnings: [], skipped: '没有日报归档' },
  ] as any)
  assert.equal(report.passed, 1)
  assert.equal(report.failed, 1)
  assert.equal(report.skipped, 1)
})

test('用例表：id 唯一、每条至少有一条断言、正反不许自相矛盾', () => {
  const ids = EVAL_CASES.map(c => c.id)
  assert.equal(new Set(ids).size, ids.length, `id 有重复：${ids.join('、')}`)
  for (const spec of EVAL_CASES) {
    const has = ['mustCall', 'mustNotCall', 'maxTools', 'toolBudget', 'answerMatches', 'answerForbids', 'memoryContains']
      .some(key => (spec.expect as any)[key] !== undefined)
    assert.ok(has, `${spec.id} 什么断言都没有——这种用例只会让报告好看`)
    const both = (spec.expect.mustCall ?? []).filter(t => (spec.expect.mustNotCall ?? []).includes(t))
    assert.deepEqual(both, [], `${spec.id} 同一个工具既要又不要`)
  }
})

test('用例表：回放只按脚本文件名给，且都得是 .py', () => {
  for (const spec of EVAL_CASES) {
    for (const name of Object.keys(spec.scripts ?? {})) {
      assert.match(name, /\.py$/, `${spec.id} 的回放键应该是脚本文件名：${name}`)
    }
  }
})

test('用例表：问句里不许出现真实会话名（合成数据是这套评测的前提）', () => {
  // 期望值里不写死名字，问句里也不该带——不然"合成数据"这个前提就漏了
  for (const spec of EVAL_CASES) {
    assert.doesNotMatch(spec.question, /丁ding|群|真实/, `${spec.id} 的问句看着像真数据`)
  }
})

test('效率预算：超了只报提示，绝不变成失败', () => {
  // 同一句话的调用次数实测在 2~7 之间波动，所以"5 次"不构成底线；当硬上限会造出假失败，
  // 而假失败会训练人忽略评测。硬的是 maxTools，软的是这个。
  const spec = { ...base, expect: { toolBudget: 3 } } as any
  assert.deepEqual(budgetWarnings(spec, ['a', 'b', 'c']), [])
  const warned = budgetWarnings(spec, ['a', 'b', 'c', 'd'])
  assert.equal(warned.length, 1)
  assert.match(warned[0], /超出效率预算 3/)
  assert.deepEqual(judge(spec, observed({ tools: ['a', 'b', 'c', 'd'] })), [],
                   '软预算超了不许让它变成判定失败')
  assert.deepEqual(budgetWarnings({ ...base, expect: {} } as any, ['a', 'b', 'c', 'd', 'e']), [],
                   '没设预算就不提示')
})
