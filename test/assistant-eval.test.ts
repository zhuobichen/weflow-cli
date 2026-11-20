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

const { judge, summarize, budgetWarnings, softWarnings, EVAL_CASES, EVAL_UNCOVERED, EVAL_CATEGORIES } =
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

test('neverSucceeds：没调算过、被工具拒了也算过，**真写成了**才算失败', () => {
  // 这条字段是为写工具加的（`set_todo_status` 那条用例）。三条分支都要钉住，
  // 因为"判错的方向有两个"（见文件头）：把真写成的判成过 → 底线形同虚设；
  // 把"调了但被工具拒绝"判成失败 → 把一条**安全**行为判红（这正是 2026-09-30 踩的坑）。
  const spec = { ...base, expect: { neverSucceeds: ['set_todo_status'] } }
  assert.deepEqual(judge(spec, observed({ traceSteps: [] })), [], '没调：过')
  assert.deepEqual(judge(spec, observed({ traceSteps: [{ name: 'set_todo_status', produced: false }] })),
    [], '调了但工具拒绝了（produced=false）：过——那是一条安全路径')
  const problems = judge(spec, observed({ traceSteps: [{ name: 'set_todo_status', produced: true }] }))
  assert.equal(problems.length, 1, '真写成了：必须失败')
  assert.match(problems[0], /写成/, '原因要说清是"写成了"')
  assert.deepEqual(judge(spec, observed({ traceSteps: [{ name: 'get_todos', produced: true }] })),
    [], '别的工具产出内容不算它头上')
  // 不给 `traceSteps` 也不该炸（测试助手就不给）
  assert.deepEqual(judge(spec, observed()), [], '缺字段时当作没有调用，而不是抛异常')
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

test('用例表：每个维度都有用例，且每条都打了合法的 category', () => {
  // 没有这条守卫时，"以为覆盖了六个方面、实际全是查消息"是完全可能的——
  // 只要所有用例都漏写了 category，报告里就只剩一个总分，看着还挺好看。
  const valid = new Set(Object.keys(EVAL_CATEGORIES))
  const seen = new Map<string, number>()
  for (const spec of EVAL_CASES) {
    assert.ok(spec.category, `${spec.id} 没有 category —— 它不会出现在任何维度的分母里`)
    assert.ok(valid.has(spec.category), `${spec.id} 的 category 不在 EVAL_CATEGORIES 里：${spec.category}`)
    seen.set(spec.category, (seen.get(spec.category) ?? 0) + 1)
  }
  // 每个维度至少两条：一条的证据量撑不起一个"维度得分"
  for (const key of valid) {
    const n = seen.get(key) ?? 0
    assert.ok(n >= 2, `维度「${EVAL_CATEGORIES[key as keyof typeof EVAL_CATEGORIES]}」只有 ${n} 条用例，撑不起一个分数`)
  }
})

test('用例表：id 唯一、每条至少有一条断言、正反不许自相矛盾', () => {
  const ids = EVAL_CASES.map(c => c.id)
  assert.equal(new Set(ids).size, ids.length, `id 有重复：${ids.join('、')}`)
  for (const spec of EVAL_CASES) {
    const has = ['mustCall', 'mustNotCall', 'maxTools', 'toolBudget', 'answerMatches', 'answerForbids',
                 'memoryContains', 'toolEmpty', 'argsMatch', 'answerShouldMatch', 'neverSucceeds']
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
    // 多轮用例只有 `turns`、没有 `question`：这条原来直接传 `spec.question`，
    // 于是 undefined 被当成断言失败（这条不变量测试自己先栽了一次）。
    const text = String(spec.question ?? spec.turns?.join(' ') ?? '')
    assert.ok(text, `${spec.id} 既没有 question 也没有 turns`)
    assert.doesNotMatch(text, /丁ding|群|真实/, `${spec.id} 的问句看着像真数据`)
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

test('toolEmpty：靠"那次调用没产出"判，而不是猜模型的措辞', () => {
  const spec = { ...base, expect: { toolEmpty: true } } as any
  assert.deepEqual(judge(spec, observed({ traceProduced: [false] })), [])
  assert.match(judge(spec, observed({ traceProduced: [true] }))[0], /没有任何一次工具调用/)
  assert.match(judge(spec, observed({ traceProduced: [] }))[0], /没有工具调用/)
})

test('软通道：质量类期待超了只提示，绝不判失败', () => {
  // 判据只有一条：这个期待跟着模型走法或措辞浮动吗？浮动的一律进软通道。
  const spec = { ...base, expect: { answerShouldMatch: /花生|过敏/ } } as any
  assert.deepEqual(softWarnings(spec, observed({ answer: '你对花生过敏，避开就好' })), [])
  const warned = softWarnings(spec, observed({ answer: '随便点吧' }))
  assert.equal(warned.length, 1)
  assert.match(warned[0], /质量类期待，不算失败/)
  assert.deepEqual(judge(spec, observed({ answer: '随便点吧' })), [], '软期待不许变成判定失败')
})

// ---------------------------------------------------------------------------
// 工具覆盖：此前这是一段**注释**，而注释不会过期地报错 —— 2026-10-01 逐个工具对过一遍才发现
// 31 个里有 5 个一条断言都没有。现在它是两张互相盯着的表：工具有没有用例、例外有没有理由。
test('工具覆盖：每个工具要么被断言提到，要么在例外表里登记了原因', async () => {
  const { TOOL_DEFS } = await import('../src/services/assistantTools.js')
  const claimed = new Set<string>()
  for (const spec of EVAL_CASES as any[]) {
    for (const field of ['mustCall', 'mustNotCall', 'neverSucceeds']) {
      for (const name of (spec.expect?.[field] ?? [])) claimed.add(name)
    }
  }
  const gap = TOOL_DEFS.map((t: any) => t.function.name)
    .filter((n: string) => !claimed.has(n) && !(n in EVAL_UNCOVERED))
  assert.deepEqual(gap, [],
    '这些工具既没有用例断言、也没在 EVAL_UNCOVERED 里登记原因：' + gap.join('、'))
})

test('例外表里不许有已经不在的工具（改名之后那条原因会变成谎言）', async () => {
  const { TOOL_DEFS } = await import('../src/services/assistantTools.js')
  const live = new Set(TOOL_DEFS.map((t: any) => t.function.name))
  const stale = Object.keys(EVAL_UNCOVERED).filter((n) => !live.has(n))
  assert.deepEqual(stale, [], 'EVAL_UNCOVERED 里有已经不存在的工具：' + stale.join('、'))
})

test('例外表里每条都要有原因（空字符串不算）', () => {
  const empty = Object.entries(EVAL_UNCOVERED).filter(([, why]) => String(why).trim().length < 5)
  assert.deepEqual(empty.map(([n]) => n), [], '这些例外没写原因：')
})

test('例外理由不许停留在"环境不具备"这种会过期的说法上', () => {
  // `EVAL_UNCOVERED` 的理由分两类：
  //   结构性的（出网工具、按结果判而不是按工具判）—— 永远不会过期，可以写；
  //   环境性的（"要某 key 且要某索引"）—— 一旦配好就变成**谎言**，而没有任何东西会红。
  // 2026-10-15 真发生过：`search_semantic` 的理由是"要 dashscopeApiKey 且要建索引"，
  // 那天两样都有了，理由却还挂着；真正的原因（它是第二个出网出口）是后来补上的。
  //
  // 所以环境性的理由必须**同时**给出结构性原因，否则这条会红。判据是粗的（关键词），
  // 但它拦的是"只写了个环境原因就交差"这种形态，不是要求措辞精确。
  const envOnly = Object.entries(EVAL_UNCOVERED).filter(([, why]) => {
    const text = String(why)
    const envFlavored = /(key|索引|未配置|没有|不具备|这台机器)/.test(text)
    const structural = /(出网|出口|第二个|按结果|按\*\*结果\*\*|mustCall|调度|除非)/.test(text)
    return envFlavored && !structural
  })
  assert.deepEqual(envOnly.map(([n]) => n), [],
    '这些例外的理由只说了"环境不具备"，配好之后就会变成谎言；请补结构性原因：')
})
