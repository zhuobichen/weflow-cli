/**
 * 评测报告：分维度打分、稳定性、与基线对比。
 *
 * 这套指标的**三条硬规矩**在这里逐条钉住（它们都不是"好看"的问题，是"会不会骗人"）：
 * 1. 没跑的用例不算通过也不算失败 —— 记进 skipped；
 * 2. 单次跑的稳定性是"未测"，不是 100%；
 * 3. 对比基线要同时报变好和变坏，且**只比两边都跑过的**。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { EVAL_CASES, EVAL_CATEGORIES } = await import('../src/services/assistantEval.js')
const { buildReport, scoreCase, diffAgainstBaseline, formatReport } =
  await import('../src/services/assistantEvalReport.js')

const okCase = EVAL_CASES.find(c => c.id === 'sessions')!
const otherCase = EVAL_CASES.find(c => c.id === 'no-tool')!

/** 让一条用例通过的最小观测（按它的 expect 造） */
function passObs(spec: any = okCase) {
  const mustCall: string[] = spec.expect.mustCall ?? []
  // 答复要把 `answerMatches` 满足掉：不同用例要的内容不同（`no-tool` 要英文、别的要中文名），
  // 用一条同时含中英的答复覆盖这两种，免得构造夹具本身成为负担。
  const answer = 'The weather is nice today. 甲、乙 都对得上。'
  return {
    tools: mustCall.length ? mustCall : [],
    cloudCalls: 1,
    answer,
    facts: [],
    traceArgs: [],
    traceProduced: [],
    traceSteps: mustCall.map((name: string) => ({ name, produced: true })),
    elapsedMs: 100,
  }
}

/** 让一条用例失败的最小观测 */
function failObs(spec: any = okCase) {
  // 不能简单给 `tools: []`：`no-tool` 的 expect 是 `maxTools: 0`，空列表恰好满足它。
  // 这里按用例的 expect 造一个**必然不满足**的观测。
  const mustNot = spec.expect.mustNotCall ?? []
  if (mustNot.length) {
    // 有"不该调"的工具：就调它，必然失败
    return { ...passObs(spec), tools: [mustNot[0]], answer: '嗯', elapsedMs: 100 }
  }
  const mustCall = spec.expect.mustCall ?? []
  if (mustCall.length) {
    return { ...passObs(spec), tools: [], traceSteps: [], answer: '不知道', elapsedMs: 100 }
  }
  // 既没有 mustCall 也没有 mustNotCall：用超预算制造失败
  return { ...passObs(spec), tools: ['a', 'b', 'c', 'd', 'e', 'f', 'g'], answer: '嗯', elapsedMs: 100 }
}

/** 把若干次观测包成 `CaseRun`（`scoreCase` 要的是这个形状，不是裸数组） */
function run(id: string, ...observations: any[]) {
  return { caseId: id, observations }
}

test('通过的用例：passRate=1、稳定性未测（单次）', () => {
  const score = scoreCase(okCase, run(okCase.id, passObs()))
  assert.equal(score.passRate, 1)
  assert.equal(score.stability, null, '单次跑不能给出稳定性——那会是假的 100%')
  assert.deepEqual(score.problems, [])
})

test('失败：problems 里带得上原话，便于报告里直接读', () => {
  const score = scoreCase(okCase, run(okCase.id, failObs()))
  assert.equal(score.passRate, 0)
  assert.ok(score.problems.some(p => p.includes('list_sessions')))
})

test('稳定性：两次全过 = 1；一次过一次不过 = 不稳定', () => {
  assert.equal(scoreCase(okCase, run(okCase.id, passObs(), passObs())).stability, 1)
  const flaky = scoreCase(okCase, run(okCase.id, passObs(), failObs()))
  assert.equal(flaky.stability, 0.5)
  assert.equal(flaky.passRate, 0.5)
})

test('报告：没跑的用例进 skipped，不进分母', () => {
  const report = buildReport([{ caseId: okCase.id, observations: [passObs()] }])
  assert.equal(report.cases, 1, '只算跑过的')
  assert.ok(report.skipped.includes(otherCase.id), '没跑的要点名')
  // 分数按用例等权：一条通过 = 100%，不是"1/41"
  assert.equal(report.score, 1)
})

test('报告：一条都没跑时分数是 0 而不是崩溃', () => {
  const report = buildReport([])
  assert.equal(report.cases, 0)
  assert.equal(report.score, 0)
  assert.equal(report.skipped.length, EVAL_CASES.length)
})

test('报告：维度分只统计该维度里的用例', () => {
  const report = buildReport([
    { caseId: okCase.id, observations: [passObs()] },
    { caseId: otherCase.id, observations: [failObs()] },
  ])
  const routing = report.categories.find(c => c.category === 'routing')!
  assert.ok(routing.cases >= 2)
  assert.ok(routing.passRate < 1, '有一条失败，该维度不该是 100%')
})

test('对比基线：同时报变好与变坏', () => {
  // 基线：ok 失败、other 通过；现在：ok 通过、other 失败 —— 一升一降，两边都要报出来。
  // 只报提升的那一半，就是在挑对自己有利的数据。
  const base = buildReport([
    run(okCase.id, failObs(okCase)),
    run(otherCase.id, passObs(otherCase)),   // no-tool：不调工具即通过
  ])
  const now = buildReport([
    run(okCase.id, passObs(okCase)),
    run(otherCase.id, failObs(otherCase)),
  ])
  const d = diffAgainstBaseline(now, base)
  assert.deepEqual(d.improved, [okCase.id])
  assert.deepEqual(d.regressed, [otherCase.id])
})

test('对比基线：一边没跑的算"无法比较"，不算退步', () => {
  const base = buildReport([{ caseId: okCase.id, observations: [passObs()] }])
  const now = buildReport([{ caseId: otherCase.id, observations: [passObs(otherCase)] }])
  const d = diffAgainstBaseline(now, base)
  assert.deepEqual(d.regressed, [], '跳过的用例不能被说成退步')
  assert.ok(d.incomparable.includes(okCase.id))
})

test('文本报告：该有的段落都在，且不吹牛', () => {
  const report = buildReport([
    { caseId: okCase.id, observations: [passObs()] },
    { caseId: otherCase.id, observations: [failObs()] },
  ])
  const text = formatReport(report, { title: '自测' })
  assert.match(text, /自测/)
  assert.match(text, /总分/)
  assert.match(text, /未通过/, '有失败就要有"未通过"一节')
  assert.match(text, /未测（需 --repeats≥2）/, '单次跑必须显式说稳定性未测')
  // 每个维度都该出现在表里（此刻是有用例的那些）
  for (const label of Object.values(EVAL_CATEGORIES)) {
    if (report.categories.some(c => c.label === label)) assert.match(text, new RegExp(label))
  }
})
