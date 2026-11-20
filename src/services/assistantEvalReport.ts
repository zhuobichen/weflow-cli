/**
 * 评测**报告**：把一轮或多轮观测变成一个可比较的分数。
 *
 * 这块与 `assistantEval.ts` 的分工：
 * - `judge()` 说"这一条过不过"（纯函数，二值）；
 * - 这里说"**这一套评测整体多少分、哪个维度弱、和上次比是进步还是退步**"。
 *
 * ## 指标怎么定（为什么不是简单的"通过率"）
 *
 * 只有一个通过率的话，"85 分"没法指导改进——你不知道该修路由还是修记忆。
 * 所以分数按**维度**给，总分取加权平均，且把三类东西分开：
 *
 * | 指标 | 含义 | 为什么不合并 |
 * | --- | --- | --- |
 * | `passRate` | 硬断言全过的比例 | 唯一算"分数"的东西 |
 * | `softWarnRate` | 软断言（`answerShouldMatch`/`toolBudget`）没满足的比例 | 它测的是**质量**不是**正确性**，混进分数会让偶发抖动拉低分数 |
 * | `stability` | 同一用例多次重复的一致率 | 一次全过 ≠ 稳定；只有重复才能看出"抽奖" |
 *
 * ## 三条硬规矩
 *
 * 1. **没有观测的用例不算通过，也不算失败**：它记进 `skipped` 并单独列出。
 *    把"没跑"算成通过，是最坏的一种自欺——分数好看，覆盖面为零。
 * 2. **稳定性只在 repeats ≥ 2 时有意义**：单次跑出来的 `stability` 恒为 1，
 *    报告里要标成"未测"，而不是给个漂亮的 100%。
 * 3. **对比基线时要同时报"变好"和"变坏"**：只报提升的那几条，等于在挑对自己有利的数据。
 */
import { EVAL_CASES, EVAL_CATEGORIES, judge, type EvalCase, type EvalCategory, type Observation } from './assistantEval.js'

/** 一轮里某条用例跑了若干次的结果 */
export interface CaseRun {
  caseId: string
  /** 每次重复的观测，顺序即执行顺序 */
  observations: Observation[]
}

export interface CaseScore {
  caseId: string
  category: EvalCategory
  /** 跑了多少次 */
  repeats: number
  /** 多少次硬断言全过 */
  passed: number
  /** 通过率 0..1 */
  passRate: number
  /** 多少次有软警告 */
  softWarned: number
  /** 稳定性：repeat 之间结果是否一致（1=每次都一样）。repeats<2 时为 null（未测） */
  stability: number | null
  /**
   * 工具调用次数（各次重复里的最大值）。
   *
   * 存下来是为了**校准预算本身**：`toolBudget` 该给几，要看实测分布，
   * 而不是拍一个数字。没有这个字段时，只能看到"超了",看不到"超到几"。
   */
  toolCalls: number
  /** 失败细节（只留前几条，避免报告被淹） */
  problems: string[]
}

export interface CategoryScore {
  category: EvalCategory
  label: string
  cases: number
  passed: number
  total: number
  passRate: number
  softWarnRate: number
  /** 该维度里稳定性均值；全是单次跑则为 null */
  stability: number | null
}

export interface EvalReport {
  /** 用例数（不含未跑） */
  cases: number
  /** 硬断言通过的次数 / 总次数 */
  passed: number
  total: number
  /** 总分：按用例等权（不是按次数）——重复跑多的那条不该因此更有分量 */
  score: number
  softWarnRate: number
  stability: number | null
  categories: CategoryScore[]
  perCase: CaseScore[]
  /** 一次都没跑的用例（不算通过也不算失败） */
  skipped: string[]
  /** 总耗时（所有观测之和） */
  elapsedMs: number
  /** 模型往返次数之和 */
  cloudCalls: number
}

/** 软警告只看这两类：它们描述"质量不够"，不描述"做错了" */
function softWarnings(spec: EvalCase, observed: Observation): string[] {
  const out: string[] = []
  if (spec.expect.answerShouldMatch && !spec.expect.answerShouldMatch.test(observed.answer ?? '')) {
    out.push('应当出现的内容没出现')
  }
  const budget = spec.expect.toolBudget
  if (budget !== undefined && observed.tools.length > budget) {
    out.push(`工具调用 ${observed.tools.length} 次，超出预算 ${budget}`)
  }
  return out
}

function mean(values: number[]): number | null {
  if (!values.length) return null
  return values.reduce((a, b) => a + b, 0) / values.length
}

/**
 * 一条用例的分数。
 *
 * `stability` 的算法：**结果序列是否恒定**（全过 / 全不过都算稳定）。
 * 不按"通过次数/repeats"算——那与 passRate 重复，看不出"忽过忽不过"这个真正的信号。
 */
export function scoreCase(spec: EvalCase, runs: Observation): CaseScore
export function scoreCase(spec: EvalCase, run: CaseRun): CaseScore
export function scoreCase(spec: EvalCase, arg: CaseRun | Observation): CaseScore {
  const observations: Observation[] = 'observations' in (arg as CaseRun)
    ? (arg as CaseRun).observations
    : [arg as Observation]

  let passed = 0
  let softWarned = 0
  const problems: string[] = []
  const outcomes: boolean[] = []

  for (const observed of observations) {
    const issues = judge(spec, observed)
    const ok = issues.length === 0
    outcomes.push(ok)
    if (ok) passed += 1
    for (const p of issues) if (problems.length < 5) problems.push(p)
    if (softWarnings(spec, observed).length) softWarned += 1
  }

  const repeats = observations.length
  const allSame = outcomes.every(o => o === outcomes[0])
  return {
    caseId: spec.id,
    category: spec.category,
    repeats,
    passed,
    passRate: repeats ? passed / repeats : 0,
    softWarned,
    stability: repeats < 2 ? null : (allSame ? 1 : 1 - (outcomes.filter(o => o !== outcomes[0]).length / repeats)),
    toolCalls: observations.reduce((m, o) => Math.max(m, (o.tools ?? []).length), 0),
    problems,
  }
}

/** 汇总成报告。`runs` 里没出现的用例进 `skipped`。 */
export function buildReport(runs: CaseRun[]): EvalReport {
  const byId = new Map(runs.map(r => [r.caseId, r]))
  const perCase: CaseScore[] = []
  const skipped: string[] = []

  for (const spec of EVAL_CASES) {
    const run = byId.get(spec.id)
    if (!run || !run.observations.length) {
      skipped.push(spec.id)
      continue
    }
    perCase.push(scoreCase(spec, run))
  }

  // 维度汇总：**按用例等权**，不按次数——跑得多的那条不该因此更有分量
  const categories: CategoryScore[] = []
  for (const [id, label] of Object.entries(EVAL_CATEGORIES) as Array<[EvalCategory, string]>) {
    const rows = perCase.filter(c => c.category === id)
    if (!rows.length) continue
    const stabilities = rows.map(r => r.stability).filter((s): s is number => s !== null)
    categories.push({
      category: id,
      label,
      cases: rows.length,
      passed: rows.filter(r => r.passRate === 1).length,
      total: rows.length,
      passRate: rows.filter(r => r.passRate === 1).length / rows.length,
      softWarnRate: rows.filter(r => r.softWarned > 0).length / rows.length,
      stability: mean(stabilities),
    })
  }

  const totalObs = perCase.reduce((n, r) => n + r.repeats, 0)
  const passedObs = perCase.reduce((n, r) => n + r.passed, 0)
  const stabilities = perCase.map(r => r.stability).filter((s): s is number => s !== null)

  return {
    cases: perCase.length,
    passed: passedObs,
    total: totalObs,
    score: perCase.length ? perCase.filter(r => r.passRate === 1).length / perCase.length : 0,
    softWarnRate: perCase.length ? perCase.filter(r => r.softWarned > 0).length / perCase.length : 0,
    stability: mean(stabilities),
    categories,
    perCase,
    skipped,
    elapsedMs: runs.reduce((n, r) => n + r.observations.reduce((m, o) => m + (o.elapsedMs || 0), 0), 0),
    cloudCalls: runs.reduce((n, r) => n + r.observations.reduce((m, o) => m + (o.cloudCalls || 0), 0), 0),
  }
}

/**
 * 与基线比。**只比两次都跑过的用例**——拿"这次跳过了"和"上次通过了"比，
 * 会把覆盖率下降说成退步，那是另一种假信号。
 */
export interface RegressionDiff {
  improved: string[]
  regressed: string[]
  unchanged: number
  /** 无法比较的（任一侧没跑） */
  incomparable: string[]
}

export function diffAgainstBaseline(current: EvalReport, baseline: EvalReport): RegressionDiff {
  const cur = new Map(current.perCase.map(c => [c.caseId, c]))
  const base = new Map(baseline.perCase.map(c => [c.caseId, c]))
  const improved: string[] = []
  const regressed: string[] = []
  const incomparable: string[] = []
  let unchanged = 0

  for (const [id, now] of cur) {
    const was = base.get(id)
    if (!was) { incomparable.push(id); continue }
    if (now.passRate > was.passRate) improved.push(id)
    else if (now.passRate < was.passRate) regressed.push(id)
    else unchanged += 1
  }
  for (const id of base.keys()) if (!cur.has(id)) incomparable.push(id)
  return { improved, regressed, unchanged, incomparable }
}

/** 给人看的一份报告。不用 markdown 表格符号之外的东西——它会被贴进 issue 与终端。 */
export function formatReport(report: EvalReport, options: { title?: string; baseline?: EvalReport } = {}): string {
  const pct = (v: number) => `${(v * 100).toFixed(1)}%`
  const lines: string[] = []
  lines.push(options.title ? `# ${options.title}` : '# 助手评测报告')
  lines.push('')
  lines.push(`总分 **${pct(report.score)}**（${report.passed}/${report.total} 次通过 · 用例 ${report.cases} 条）`)
  lines.push('')
  lines.push('| 维度 | 用例 | 通过率 | 软警告 | 稳定性 |')
  lines.push('| --- | --- | --- | --- | --- |')
  for (const c of report.categories) {
    const stab = c.stability === null ? '未测（需 --repeats≥2）' : pct(c.stability)
    lines.push(`| ${c.label} | ${c.cases} | ${pct(c.passRate)} | ${pct(c.softWarnRate)} | ${stab} |`)
  }
  lines.push('')
  lines.push(`稳定性 **${report.stability === null ? '未测（单次跑不看稳定性）' : pct(report.stability)}**`
    + ` · 软警告 ${pct(report.softWarnRate)} · 模型调用 ${report.cloudCalls} 次`
    + ` · 总耗时 ${(report.elapsedMs / 1000).toFixed(1)}s`)

  const failed = report.perCase.filter(c => c.passRate < 1)
  if (failed.length) {
    lines.push('')
    lines.push('## 未通过')
    for (const c of failed) {
      lines.push(`- **${c.caseId}**（${EVAL_CATEGORIES[c.category]}）${c.passed}/${c.repeats}`)
      for (const p of c.problems.slice(0, 3)) lines.push(`  - ${p}`)
    }
  }
  if (report.skipped.length) {
    lines.push('')
    lines.push(`## 未跑（不计入分数）: ${report.skipped.join('、')}`)
  }
  if (options.baseline) {
    const d = diffAgainstBaseline(report, options.baseline)
    lines.push('')
    lines.push('## 与基线对比')
    if (d.improved.length) lines.push(`- 变好：${d.improved.join('、')}`)
    if (d.regressed.length) lines.push(`- **变坏**：${d.regressed.join('、')}`)
    lines.push(`- 持平：${d.unchanged} 条${d.incomparable.length ? ` · 无法比较：${d.incomparable.length} 条` : ''}`)
  }
  return lines.join('\n')
}
