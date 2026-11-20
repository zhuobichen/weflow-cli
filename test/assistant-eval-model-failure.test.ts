/**
 * 「模型根本没通」必须报成"没跑"，不是低分。
 *
 * `handleMessage` 把 LLM 异常兜成给用户看的一句「❌ 大脑暂时离线: LLM 401 …」——对人这样是
 * 对的。但评测拿到的就是那条"答复"：`tools=0`、答复里没有该有的东西，于是判据把
 * **key 无效 / 余额为 0 / 限流**读成"模型不会用工具"。
 *
 * 2026-11-20 真的踩了一次：`dashscopeApiKey` 在 config.json 里是 `lock:` 加密的，把密文直接
 * 当 `WEFLOW_AI_KEY` 用，整轮 44 条全部这样失败，报出来 27.3%（真值 70%+）。那份报告看起来
 * 像"模型退化"，而真正的原因是一个 key 的形态。
 *
 * 所以这里钉三件事：
 *   ① `runCase` 把它还原成 `observed.error`，判据落进「抛异常」；
 *   ② `fatalModelError` 只认"再跑也不会有变化"的那几类；
 *   ③ `runEvalSuite` 认出就中止，返回 `report: null`（= 没跑，退出码 2）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-eval-modelfail-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME
process.env.WEFLOW_AUDIT_DIR = mkdtempSync(join(tmpdir(), 'weflow-eval-modelfail-audit-'))
// 端点指到一个**不存在的主机**没有意义（会走 DNS），这里指到本地假地址：桩会拦住它
process.env.WEFLOW_AI_BASE_URL = 'https://stub.invalid/v1'
process.env.WEFLOW_AI_MODEL = 'stub-model'
process.env.WEFLOW_AI_KEY = 'sk-not-a-real-key'

const { runCase, EVAL_CASES } = await import('../src/services/assistantEval.js')
const { runEvalSuite, fatalModelError } = await import('../src/services/assistantEvalRunner.js')

/** 把模型那一个出口换成"永远 401"，其余照旧由 `installStubs` 管 */
function withFailingModel(): () => void {
  const saved = globalThis.fetch
  ;(globalThis as any).fetch = async () =>
    new Response(JSON.stringify({ error: { message: 'Incorrect API key provided' } }), { status: 401 })
  return () => { (globalThis as any).fetch = saved }
}

const carrier = EVAL_CASES.find(c => c.id === 'sessions')!

test('401 会被还原成异常，而不是一条"答复"', async () => {
  const restore = withFailingModel()
  let result
  try {
    result = await runCase(carrier, 'u-modelfail-1')
  } finally {
    restore()
  }
  assert.match(result.observed.error ?? '', /大脑暂时离线/, '整段兜底话要留下来，不然查不动')
  assert.match(result.observed.error ?? '', /401/)
  assert.ok(result.problems.length > 0)
  assert.match(result.problems[0], /^抛异常：/, '判据应当是"抛异常"，不是"没调工具"')
})

test('fatalModelError：只认再跑也不会有变化的那几类', () => {
  const offline = (status: string) => `❌ 大脑暂时离线: LLM ${status}: {"error":"x"}`
  for (const status of ['401', '402', '403', '404', '429']) {
    assert.ok(fatalModelError(offline(status)), `LLM ${status} 应当被认成致命`)
  }
  // 单条崩、单条超时：让那一条失败，其余照跑
  assert.equal(fatalModelError('❌ 大脑暂时离线: LLM 500: boom'), null)
  assert.equal(fatalModelError('fetch failed'), null)
  assert.equal(fatalModelError(undefined), null)
  assert.equal(fatalModelError(''), null)
})

test('runEvalSuite：模型没通就中止，返回"没跑"而不是低分', async () => {
  const restore = withFailingModel()
  let outcome
  try {
    outcome = await runEvalSuite({ only: ['sessions'], repeats: 1 })
  } finally {
    restore()
  }
  assert.equal(outcome.report, null, '不该产出分数：那份分数会被当成模型能力')
  assert.match(outcome.unavailable ?? '', /模型调用失败/)
  assert.match(outcome.unavailable ?? '', /401/)
})
