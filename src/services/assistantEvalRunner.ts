/**
 * 跑评测：把 `EVAL_CASES` 真正喂给助手，产出 `EvalReport`。
 *
 * 与 `runCase()` 的分工：那条负责"跑一条并给观测"，这里负责
 * **批量的编排**——重复、并发、跳过、聚合、落基线。分开是为了让"跑一条"的
 * 语义保持简单（它已经被 19 条不变量测试盯着）。
 *
 * ## 三条编排规矩
 *
 * 1. **默认串行**。并发会让多个用例同时改 `chatService` 上的桩（`installStubs` 是
 *    模块级的），而那种串味是**静默**的——观测混了，判据照样给出结论。
 *    要并发必须显式 `--concurrency`，且那样只对纯网络等待有收益。
 * 2. **没有 key 就整体跳过，逐条报原因**，不产出"0 分"这种看起来像失败的分数。
 * 3. **重复跑（`--repeats`）之间不共享 userId**：同一用户的记忆会跨轮累积，
 *    第二轮起点带着第一轮的事实，"记忆"这个维度就测不出东西了。
 */
import { EVAL_CASES, runCase, type EvalCase, type CaseResult } from './assistantEval.js'
import { buildReport, type CaseRun, type EvalReport } from './assistantEvalReport.js'

export interface RunOptions {
  /** 只跑这些用例 id（省略=全部） */
  only?: string[]
  /** 只跑这些维度 */
  categories?: string[]
  /** 每条重复几次（默认 1；≥2 才有稳定性指标） */
  repeats?: number
  /** 每条跑完回调一次（做进度输出） */
  onCaseDone?: (result: CaseResult, index: number, total: number) => void
  /** 取当前是否可用（没有 key 时返回原因）。返回 null 表示可用。 */
  precheck?: () => string | null | Promise<string | null>
}

export interface RunOutcome {
  report: EvalReport | null
  /** precheck 不通过时的原因 */
  unavailable?: string
  /** 逐条的原始结果（含 problems，方便细看） */
  results: CaseResult[]
}

function pick(options: RunOptions): EvalCase[] {
  let cases = EVAL_CASES
  if (options.only?.length) {
    const wanted = new Set(options.only)
    const known = new Set(EVAL_CASES.map(c => c.id))
    const missing = [...wanted].filter(id => !known.has(id))
    if (missing.length) throw new Error(`没有这些用例：${missing.join('、')}`)
    cases = cases.filter(c => wanted.has(c.id))
  }
  if (options.categories?.length) {
    const wanted = new Set(options.categories)
    cases = cases.filter(c => wanted.has(c.category))
  }
  return cases
}

export async function runEvalSuite(options: RunOptions = {}): Promise<RunOutcome> {
  const unavailable = (await options.precheck?.()) ?? null
  if (unavailable) return { report: null, unavailable, results: [] }

  const cases = pick(options)
  const repeats = Math.max(1, options.repeats ?? 1)
  const runs: CaseRun[] = []
  const results: CaseResult[] = []

  let index = 0
  for (const spec of cases) {
    const observations = []
    for (let round = 0; round < repeats; round++) {
      // 每轮一个新 userId：同一用户的长期记忆跨轮累积，第二轮起点会带着第一轮的事实，
      // 那样"记忆"维度测的就是累积效应而不是记忆本身。
      const userId = `eval-${spec.id}-${round}-${Date.now().toString(36)}`
      const result = await runCase(spec, userId)
      observations.push(result.observed)
      results.push(result)
      index += 1
      options.onCaseDone?.(result, index, cases.length * repeats)
    }
    runs.push({ caseId: spec.id, observations })
  }

  return { report: buildReport(runs), results }
}

/**
 * 当前能不能跑：需要模型 key。`async` 是因为要动态 `import` 配置服务
 * （这个包是 ESM，`require` 运行时不存在——编译期不报，只有真跑才炸）。
 *
 * 查两处，顺序与助手运行时一致：环境变量 `DEEPSEEK_API_KEY` → 配置里的
 * `deepseekApiKey` / `aiApiKey`。**只读不改**，评测不该改变本机状态。
 */
export async function precheckModel(): Promise<string | null> {
  const envKey = (process.env.DEEPSEEK_API_KEY ?? '').trim()
  if (envKey) return null
  try {
    const { configService } = await import('./configService.js')
    for (const key of ['deepseekApiKey', 'aiApiKey'] as const) {
      if (String(configService.get(key) ?? '').trim()) return null
    }
  } catch {
    // 读不到配置就当没有：宁可报"没配"，也不要跑到一半每一条都失败
  }
  return '没有可用的模型 key：评测要真调模型。'
    + '设环境变量 DEEPSEEK_API_KEY，或先 `weflow-cli config set deepseekApiKey`。'
}
