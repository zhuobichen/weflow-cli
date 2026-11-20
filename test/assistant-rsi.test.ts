/**
 * RSI 度量环（D-170）。
 *
 * 这一层不产生"更聪明的模型"，它产生的是**下一步该改哪里**。所以测试盯的是三件容易
 * 悄悄错掉的事：
 *
 * 1. **分桶只认判据原文**：给一句判据，桶必须是确定的；认不出就落 `other`，
 *    不许硬塞进"最像的那个"（那会把新失败型态藏起来）。
 * 2. **holdout 切分要稳定**：只依赖用例 id，任何机器/任何一轮都一样。用随机或按顺序切，
 *    tuned/holdout 的对比就没有意义了。
 * 3. **账本只追加、跨轮可比**：存桶的条数而不是用例名，用例改名不该让历史对比断掉。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const { EVAL_CASES, judge } = await import('../src/services/assistantEval.js')
const { buildReport } = await import('../src/services/assistantEvalReport.js')

// 账本路径在**每次调用时**读环境变量，所以放到 import 之后设也生效（与审计/轨迹同一条纪律）
const LEDGER_DIR = mkdtempSync(join(tmpdir(), 'weflow-rsi-'))
process.env.WEFLOW_RSI_LEDGER = join(LEDGER_DIR, 'rsi-ledger.jsonl')

const {
  bucketOfProblem, bucketsOfCase, holdoutIds, buildRsiReport, formatRsiReport,
  toLedgerEntry, appendLedger, readLedger, ledgerPath, BUCKET_SPECS,
} = await import('../src/services/assistantRsi.js')

const specOf = (id: string) => EVAL_CASES.find(c => c.id === id)!

/** 一份"什么都没调、什么都没说"的观测：各条用例的判据自己会说话 */
function observed(over: Record<string, unknown> = {}) {
  return {
    tools: [], cloudCalls: 1, answer: '', facts: [],
    traceArgs: [], traceProduced: [], traceSteps: [], elapsedMs: 1, ...over,
  }
}

function resultOf(id: string, over: Record<string, unknown> = {}) {
  const spec = specOf(id)
  const obs = observed(over)
  return { spec, observed: obs, problems: judge(spec as any, obs as any), warnings: [] }
}

test('分桶：judge 的每一种判据原文都能认出来', () => {
  const table: Array<[string, string]> = [
    ['没调用 get_messages（实际调了：list_sessions）', 'no-tool'],
    ['工具调用 9 次，超过上限 6（list_sessions、get_messages）', 'over-query'],
    ['答复里没有该有的东西：/甲/', 'answer-missing'],
    ['答复里出现了不该有的东西：/我查了/', 'answer-forbidden'],
    ['没有任何一次工具调用的参数匹配 /since/', 'args-mismatch'],
    ['没有任何一次工具调用"没给出内容"（实际：有、有）', 'empty-not-noticed'],
    ['set_todo_status 实际写成了（那次调用给出了内容），这条不该发生', 'wrote-when-forbidden'],
    ['不该调用 draft_reply', 'called-forbidden'],
    ['长期记忆里没有「对花生过敏」（现有 0 条）', 'memory-miss'],
    ['抛异常：LLM 402: Insufficient Balance', 'exception'],
  ]
  for (const [problem, expected] of table) {
    assert.equal(bucketOfProblem(problem), expected, `「${problem}」应当落到 ${expected}`)
  }
})

test('分桶：认不出的落 other，不许硬塞', () => {
  assert.equal(bucketOfProblem('这是一句判据表里没有的新失败型态'), 'other')
  // 一条用例可以同时踩两个桶，但不能重复计
  assert.deepEqual(bucketsOfCase(['没调用 get_messages', '没调用 get_messages']), ['no-tool'])
  assert.deepEqual(bucketsOfCase(['没调用 get_messages', '答复里没有该有的东西：/甲/']),
    ['no-tool', 'answer-missing'])
  assert.deepEqual(bucketsOfCase([]), ['other'])
})

test('每个桶都写明了由哪个机制兜（写不出机制的空桶没有下文）', () => {
  for (const spec of BUCKET_SPECS) {
    assert.ok(spec.label, `${spec.bucket} 缺 label`)
    assert.ok(spec.signatures.length, `${spec.bucket} 缺 signatures`)
    assert.ok(spec.advice, `${spec.bucket} 缺 advice`)
  }
})

test('holdout 切分：稳定、可复现、覆盖全部用例', () => {
  const ids = EVAL_CASES.map(c => c.id)
  const first = holdoutIds()
  const second = holdoutIds()
  assert.deepEqual([...first].sort(), [...second].sort(), '两次调用必须切出同一批')
  for (const id of first) assert.ok(ids.includes(id))
  // 大约四分之一：**只是"大约"**，报告里也这么写。断言宽区间是为了改了用例集不至于误红
  const share = first.size / ids.length
  assert.ok(share > 0.1 && share < 0.45, `holdout 占比 ${share.toFixed(2)} 不在合理区间`)
  assert.ok(first.size > 0 && first.size < ids.length, '不能全切到一边')
})

test('报告：分桶、分区、下一步都说得出根据', () => {
  // 只给一条用例观测 → 其余进 skipped（不算通过也不算失败），这是既有口径
  const runs = [{ caseId: 'sessions', observations: [observed()] }]
  const report = buildReport(runs as any)
  const rsi = buildRsiReport(report, [resultOf('sessions')] as any)

  assert.equal(rsi.cases, 1)
  assert.equal(rsi.score, 0)
  assert.equal(rsi.tuned.cases + rsi.holdout.cases, 1, 'tuned 与 holdout 必须把用例分完')
  assert.equal(rsi.buckets[0].bucket, 'no-tool', '这一条应当先归到"该查却没查"')
  assert.deepEqual(rsi.buckets[0].cases, ['sessions'])
  assert.ok(rsi.buckets[0].mechanisms.includes('lexicon'))
  assert.match(rsi.verdict, /该查却没查/)
  assert.deepEqual(rsi.failingByCategory.map(g => g.cases).flat(), ['sessions'])
})

test('报告：全过时不给虚假的"下一步"', () => {
  // 手搓一份"全过"的报告而不是去找一条必过的用例：评测集里**每一条**都断言了点什么
  // （这是它该有的样子），找不到天然全过的用例本身就是个好信号。
  const report = {
    cases: 1, passed: 1, total: 1, score: 1, softWarnRate: 0, stability: null,
    categories: [], skipped: [], elapsedMs: 0, cloudCalls: 0,
    perCase: [{ caseId: 'sessions', category: 'routing', repeats: 1, passed: 1, passRate: 1,
      softWarned: 0, stability: null, toolCalls: 1, problems: [] }],
  }
  const rsi = buildRsiReport(report as any, [])
  assert.equal(rsi.score, 1)
  assert.deepEqual(rsi.buckets, [])
  assert.match(rsi.verdict, /全过/)
})

test('报告文本：表格与下一步都要在，过拟合警告要能触发', () => {
  const rsi = {
    cases: 4, score: 0.5,
    tuned: { cases: 3, passed: 2, score: 2 / 3 },
    holdout: { cases: 1, passed: 0, score: 0 },
    buckets: [{ bucket: 'no-tool' as const, label: '该查却没查', cases: ['sessions'],
      mechanisms: ['lexicon'], advice: '补表，不要改提示词' }],
    failingByCategory: [{ category: 'routing' as const, label: '路由', cases: ['sessions'] }],
    failures: [],
    verdict: '先修「该查却没查」',
  }
  const text = formatRsiReport(rsi, { model: 'qwen-flash' })
  assert.match(text, /qwen-flash/)
  assert.match(text, /holdout（不参与）/)
  assert.match(text, /该查却没查/)
  assert.match(text, /先修/)
  // tuned 33% vs holdout 0% 差得不多；把 holdout 压到 0、tuned 提到 1 才该警告
  const overfit = formatRsiReport({ ...rsi, tuned: { cases: 3, passed: 3, score: 1 } })
  assert.match(overfit, /过拟合/)
})

test('报告文本：空分区印「没跑」而不是 0.0%（跑子集时必然出现）', () => {
  const rsi = {
    cases: 1, score: 1,
    tuned: { cases: 0, passed: 0, score: 0 },
    holdout: { cases: 1, passed: 1, score: 1 },
    buckets: [],
    failingByCategory: [],
    verdict: '全过。',
  }
  const text = formatRsiReport(rsi)
  assert.match(text, /—（没跑）/)
  assert.ok(!/\| tuned（参与改进） \| 0 \| 0 \| 0\.0% \|/.test(text), '空分区不该印 0.0%')
  // 一边有一边没有时不该触发过拟合警告：那不是过拟合，那是没跑
  assert.ok(!/过拟合/.test(text))
})

test('账本：追加、读回、路径跟着环境变量走', () => {
  const rsi = {
    cases: 2, score: 0.5,
    tuned: { cases: 1, passed: 1, score: 1 },
    holdout: { cases: 1, passed: 0, score: 0 },
    buckets: [{ bucket: 'no-tool' as const, label: '该查却没查', cases: ['sessions'],
      mechanisms: ['lexicon'], advice: '' }],
    failingByCategory: [{ category: 'routing' as const, label: '路由', cases: ['sessions'] }],
    verdict: '',
  }
  const entry = toLedgerEntry(rsi, 'qwen-flash', new Date('2026-11-25T10:00:00Z'))
  assert.equal(entry.ts, '2026-11-25T10:00:00.000Z')
  assert.deepEqual(entry.buckets, { 'no-tool': 1 }, '存条数而不是用例名')
  assert.deepEqual(entry.failing, ['sessions'])

  const path = appendLedger(entry)
  assert.equal(path, process.env.WEFLOW_RSI_LEDGER)
  assert.ok(existsSync(path))
  assert.equal(readLedger().length, 1)
  assert.equal(readLedger()[0].model, 'qwen-flash')

  appendLedger({ ...entry, score: 0.9 })
  const rows = readLedger()
  assert.equal(rows.length, 2)
  assert.equal(rows[1].score, 0.9, '最近的在后面')
  // 账本是给人看的文本，一行一条
  const lines = readFileSync(path, 'utf8').trim().split('\n')
  assert.equal(lines.length, 2)
})

test('账本：读不到就当空账本，不抛（记账不是依赖）', () => {
  const saved = process.env.WEFLOW_RSI_LEDGER
  process.env.WEFLOW_RSI_LEDGER = join(LEDGER_DIR, 'nope', 'missing.jsonl')
  try {
    assert.deepEqual(readLedger(), [])
    assert.equal(ledgerPath(), process.env.WEFLOW_RSI_LEDGER)
  } finally {
    process.env.WEFLOW_RSI_LEDGER = saved
  }
})
