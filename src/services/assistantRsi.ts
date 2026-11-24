/**
 * RSI（Recursive Self-Improvement，递归自我改进）的**度量环**（D-170）。
 *
 * ## 先说什么不是 RSI
 *
 * 「让模型改写自己的提示词，再拿分数挑一版」看起来像 RSI，实际上是**对着评测集过拟合**：
 * 分数会涨，能力不会涨，换一批问法就掉回去。这个仓库不接受这种"进步"——它与
 * `docs/DECISIONS.md` 里反复出现的口径冲突（判结果不判措辞、负向结论要有阳性对照）。
 *
 * 这里做的是另一件事：**改进的对象是 harness，不是模型**。
 *
 * 便宜的小模型（qwen-flash、本地 8B）与强模型的差距，大部分不在"写字"，而在三件确定性
 * 的事上：①该调工具时不调；②调了但挑错/参数错；③拿不到数据时编一个。这三件事都可以用
 * **确定性代码**兜住（词法路由、自述守卫、失败提醒），不需要模型更聪明。所以 RSI 的每一次
 * 迭代都是：量一次分 → 把失败分到"该由哪个机制兜"的桶里 → 上那个机制 → 再量一次。
 *
 * ## 三条规矩（对应三次真实的坑）
 *
 * 1. **分桶只认判据原文**。`judge()` 的 problem 字符串是唯一输入，不做语义猜测——
 *    按"没调用 X"分词，而不是按"我觉得这是路由问题"。
 * 2. **防过拟合要分开报**。用例按 id 的稳定哈希切成 `tuned`（参与改进）与 `holdout`
 *    （不参与）。**只看 tuned 涨、holdout 不涨，就是过拟合的信号**，报告里要把它写出来，
 *    而不是把两个数平均成一个更好看的数。
 * 3. **账本只追加**。每次带 `--record` 的跑法写一行，跨轮对比才看得见"这次改动到底
 *    是净赚还是拆东墙补西墙"。不追加就会退化成"凭记忆说上次多少分"。
 *
 * 需要注意的边界：holdout 只有十几条，噪声很大，它**不是判决**，是"要不要警惕"的提示。
 * 报告里按这个措辞写，别把 11 条用例的小样本说成统计显著。
 */
import { appendFileSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { EVAL_CASES, EVAL_CATEGORIES, type CaseResult, type EvalCategory } from './assistantEval.js'
import type { EvalReport } from './assistantEvalReport.js'

/**
 * 失败桶。**每一个都要指向一个具体的 harness 机制**——指向"提示词再写清楚点"的桶没有
 * 可执行的下文，写进来只会让报告看起来更长。
 */
export type FailureBucket =
  | 'no-tool'            // 该调工具却没调
  | 'over-query'         // 调太多次（超上限）
  | 'answer-missing'     // 答复里缺了该有的内容
  | 'answer-forbidden'   // 答复里出现不该有的内容
  | 'args-mismatch'      // 参数不对
  | 'empty-not-noticed'  // 空结果没被当成空结果
  | 'wrote-when-forbidden'
  | 'called-forbidden'
  | 'memory-miss'        // 长期记忆没落住
  | 'exception'
  | 'other'

export interface BucketSpec {
  bucket: FailureBucket
  label: string
  /** 判据原文里能认出这个桶的前缀（`judge()` 生成的字符串） */
  signatures: string[]
  /**
   * 今天由哪个机制兜。**写的是代码里的机制名，不是愿望**：
   * `lexicon` = `assistantIntent.ts`；`claimed-query` = `assistantGuard.ts`；
   * `exhausted-loop` = `assistantService.ts` 的轮数上限；`dedupe` = 同参数去重。
   */
  mechanisms: string[]
  /** 没兜住的缺口该怎么补——只在报告里给人看，不自动做 */
  advice: string
}

export const BUCKET_SPECS: BucketSpec[] = [
  { bucket: 'no-tool', label: '该查却没查',
    signatures: ['没调用 '],
    mechanisms: ['lexicon', 'claimed-query'],
    advice: '词法快路径先把这份数据摆到眼前；仍有漏网的说法就补 `INTENT_RULES`，不要改提示词' },
  { bucket: 'over-query', label: '查得太多',
    signatures: ['超过上限 '],
    mechanisms: ['dedupe', 'exhausted-loop'],
    advice: '先看 `toolBudget` 的实测分布再决定是收紧预算还是加去重' },
  { bucket: 'answer-missing', label: '答复缺内容',
    signatures: ['答复里没有该有的东西'],
    mechanisms: ['lexicon'],
    advice: '多因"没拿到数据还在编"。先解决 `no-tool` 桶，这一桶通常跟着好' },
  { bucket: 'answer-forbidden', label: '答复越界',
    signatures: ['答复里出现了不该有的东西'],
    mechanisms: ['claimed-query'],
    advice: '越界是边界提示的事：把"不许"写成可检验的判据，别指望模型自觉' },
  { bucket: 'args-mismatch', label: '参数不对',
    signatures: ['没有任何一次工具调用的参数匹配'],
    mechanisms: ['lexicon'],
    advice: '相对时间是高频错误源——系统提示里已经给了当前时间，仍错就说明需要参数规范化' },
  { bucket: 'empty-not-noticed', label: '空结果没被当空',
    signatures: ['没有任何一次工具调用"没给出内容"'],
    mechanisms: ['claimed-query'],
    advice: '模型倾向于"总得查一次"。这条桶的修法是守卫，不是更多工具' },
  { bucket: 'wrote-when-forbidden', label: '不该写却写了',
    signatures: ['实际写成了'],
    mechanisms: [],
    advice: '写操作的通路要单独审：这类失败不能靠提示词，只能靠工具侧闸门' },
  { bucket: 'called-forbidden', label: '调了不该调的',
    signatures: ['不该调用 '],
    mechanisms: [],
    advice: '同上：禁用面靠工具表与闸门，不靠模型的克制' },
  { bucket: 'memory-miss', label: '记忆没落住',
    signatures: ['长期记忆里没有'],
    mechanisms: [],
    advice: '记忆维度与工具无关，看 `assistantMemory` 的抽取提示与去重' },
  { bucket: 'exception', label: '抛异常',
    signatures: ['抛异常'],
    mechanisms: [],
    advice: '先看异常原文：这一类是代码问题，不是模型问题' },
]

const BUCKET_BY_SIGNATURE: Array<{ prefix: string; bucket: FailureBucket }> =
  BUCKET_SPECS.flatMap(spec => spec.signatures.map(prefix => ({ prefix, bucket: spec.bucket })))
    // 长前缀优先：`超过上限` 与 `工具调用 N 次` 都可能同时出现，先认更具体的
    .sort((a, b) => b.prefix.length - a.prefix.length)

/** 一条判据原文属于哪个桶。认不出算 `other`（**不硬塞进最像的那个**——那会掩盖新失败型态） */
export function bucketOfProblem(problem: string): FailureBucket {
  for (const { prefix, bucket } of BUCKET_BY_SIGNATURE) {
    if (problem.includes(prefix)) return bucket
  }
  return 'other'
}

/** 一条用例属于哪些桶（一条用例可能同时踩两个桶，例如"没调工具"+"答复缺内容"） */
export function bucketsOfCase(problems: string[]): FailureBucket[] {
  const out: FailureBucket[] = []
  for (const problem of problems) {
    const bucket = bucketOfProblem(problem)
    if (!out.includes(bucket)) out.push(bucket)
  }
  return out.length ? out : ['other']
}

/**
 * 稳定哈希切分：**只依赖用例 id**，所以同一份评测集在任何机器、任何一轮里切出来的
 * holdout 都一样。用 `Math.random` 或按顺序切会让"这轮 holdout 里有什么"每次都变，
 * 那样 tuned/holdout 的对比就没有意义了。
 */
function stableHash(text: string): number {
  let hash = 5381
  for (let i = 0; i < text.length; i++) hash = ((hash << 5) + hash + text.charCodeAt(i)) & 0x7fffffff
  return hash
}

/** holdout 的比例分母：4 = 四分之一 */
export const HOLDOUT_MODULUS = 4

export function holdoutIds(ids: string[] = EVAL_CASES.map(c => c.id)): Set<string> {
  return new Set(ids.filter(id => stableHash(id) % HOLDOUT_MODULUS === 0))
}

export interface BucketRow {
  bucket: FailureBucket
  label: string
  /** 落到这一桶的用例 id */
  cases: string[]
  mechanisms: string[]
  advice: string
}

export interface RsiReport {
  /** 参评用例数（不含未跑） */
  cases: number
  score: number
  /** 参与改进的那部分（score 的同口径） */
  tuned: { cases: number; passed: number; score: number }
  /** 不参与改进的那部分。**只看它有没有跟着涨** */
  holdout: { cases: number; passed: number; score: number }
  buckets: BucketRow[]
  /** 失败的用例 id（按维度分好，便于挑一批重跑） */
  failingByCategory: Array<{ category: EvalCategory; label: string; cases: string[] }>
  /**
   * 每条失败用例的**判据原文**与答复开头。
   *
   * 为什么要有：`verdict` 只说得上"哪个桶最大"，而真正动手时要看"那句判据到底写了什么"。
   * 没有这一栏，诊断就得靠单条重跑（`--case`）去猜，而单条重跑与整轮跑常常不是同一条路径。
   */
  failures: Array<{ caseId: string; category: EvalCategory; problems: string[]; answerPreview: string }>
  /** 一句话结论：这轮该先修哪个桶 */
  verdict: string
}

function rate(passed: number, total: number): number {
  return total ? passed / total : 0
}

function tally(report: EvalReport, ids: Set<string>): { cases: number; passed: number; score: number } {
  const rows = report.perCase.filter(c => ids.has(c.caseId))
  const passed = rows.filter(c => c.passRate === 1).length
  return { cases: rows.length, passed, score: rate(passed, rows.length) }
}

/**
 * 把一轮评测变成"下一步该改哪里"。
 *
 * `report` 给分与通过情况，`results` 给判据原文——两者都要：判据原文才能分桶，
 * 而分数是**重复跑之后**的口径（`results` 里是同一条用例的多次观测，直接数会重复计）。
 */
export function buildRsiReport(report: EvalReport, results: CaseResult[]): RsiReport {
  const holdout = holdoutIds()
  const tunedIds = new Set(EVAL_CASES.map(c => c.id).filter(id => !holdout.has(id)))
  const failing = report.perCase.filter(c => c.passRate < 1)

  // 分桶：同一用例的多次重复里出现过的所有判据原文都算（重复跑更能暴露不稳定型态）
  const byBucket = new Map<FailureBucket, string[]>()
  const problemsByCase = new Map<string, string[]>()
  for (const result of results) {
    if (!result.problems.length) continue
    const list = problemsByCase.get(result.spec.id) ?? []
    for (const problem of result.problems) if (!list.includes(problem)) list.push(problem)
    problemsByCase.set(result.spec.id, list)
  }
  for (const [caseId, problems] of problemsByCase) {
    for (const bucket of bucketsOfCase(problems)) {
      const list = byBucket.get(bucket) ?? []
      if (!list.includes(caseId)) list.push(caseId)
      byBucket.set(bucket, list)
    }
  }

  const buckets: BucketRow[] = BUCKET_SPECS
    .map(spec => ({
      bucket: spec.bucket,
      label: spec.label,
      cases: (byBucket.get(spec.bucket) ?? []).sort(),
      mechanisms: spec.mechanisms,
      advice: spec.advice,
    }))
    // 只列真的出现了的桶，但排在最前的是**有现成机制可兜**的那些：那才是这一轮能立刻动的
    .filter(row => row.cases.length > 0)
    .sort((a, b) => (b.mechanisms.length - a.mechanisms.length) || (b.cases.length - a.cases.length))

  const failingByCategory: RsiReport['failingByCategory'] = []
  for (const [category, label] of Object.entries(EVAL_CATEGORIES) as Array<[EvalCategory, string]>) {
    const cases = failing.filter(c => c.category === category).map(c => c.caseId)
    if (cases.length) failingByCategory.push({ category, label, cases })
  }

  const top = buckets[0]
  const verdict = !failing.length
    ? '全过。仍要看 holdout 与稳定性——一次全过不等于稳定。'
    : top
      ? `先修「${top.label}」（${top.cases.length} 条），兜它的机制是 ${top.mechanisms.join(' + ') || '（目前没有，要新写）'}`
      : '有失败但一个桶都没认出来：判据原文出现了新模式，先看 problems 原文再决定要不要加桶。'

  const tuned = tally(report, tunedIds)
  const held = tally(report, holdout)
  // 失败细节：取**最后一次**观测（重复跑时，最后一次最接近"现在这条路径走成什么样"）
  const failures: RsiReport['failures'] = []
  for (const row of failing) {
    const last = [...results].reverse().find(result => result.spec.id === row.caseId)
    failures.push({
      caseId: row.caseId,
      category: row.category,
      problems: row.problems.slice(0, 3),
      answerPreview: (last?.observed.answer ?? '').replace(/\s+/g, ' ').slice(0, 140),
    })
  }
  return {
    cases: report.cases,
    score: report.score,
    tuned,
    holdout: held,
    buckets,
    failingByCategory,
    failures,
    verdict,
  }
}

const pct = (v: number) => `${(v * 100).toFixed(1)}%`

/** 给人看的一份 RSI 报告。终端与 PR 描述共用一份文本，避免两处说法不一致。 */
export function formatRsiReport(rsi: RsiReport,
                                options: { model?: string; temperature?: number; baseline?: RsiLedgerEntry } = {}): string {
  const lines: string[] = []
  lines.push('# 助手 harness 自我改进报告（RSI）')
  lines.push('')
  if (options.model) {
    // 温度必须印出来：0 和 0.4 的分数不可比（0 量的是 harness，0.4 还量了采样噪声）
    const temp = options.temperature === undefined ? '' : `　温度 ${options.temperature}`
    lines.push(`模型 \`${options.model}\`${temp}　`)
  }
  lines.push(`总分 **${pct(rsi.score)}**（用例 ${rsi.cases} 条）`)
  lines.push('')
  lines.push(`| 分区 | 用例 | 通过 | 分数 |`)
  lines.push(`| --- | --- | --- | --- |`)
  // 空分区印 `—` 而不是 0.0%：跑子集（`--case`/`--category`）时常常整片落在一边，
  // 那时 0.0% 是"没跑"而不是"全错"，而这两件事在报告里必须分开（与 `skipped` 同一条纪律）。
  lines.push(`| tuned（参与改进） | ${rsi.tuned.cases} | ${rsi.tuned.passed} | `
    + `${rsi.tuned.cases ? pct(rsi.tuned.score) : '—（没跑）'} |`)
  lines.push(`| holdout（不参与） | ${rsi.holdout.cases} | ${rsi.holdout.passed} | `
    + `${rsi.holdout.cases ? pct(rsi.holdout.score) : '—（没跑）'} |`)
  lines.push('')
  if (rsi.tuned.cases && rsi.holdout.cases && rsi.tuned.score - rsi.holdout.score > 0.2) {
    lines.push('> ⚠ tuned 明显高于 holdout（差 >20 个百分点）：要么是过拟合，要么是这十几条'
      + ' holdout 恰好偏难。**小样本，别当判决**——把它当成"下一轮要多看几眼"的提示。')
    lines.push('')
  }
  lines.push(`**下一步：**${rsi.verdict}`)
  lines.push('')

  if (rsi.buckets.length) {
    lines.push('| 失败桶 | 条数 | 兜它的机制 | 用例 |')
    lines.push('| --- | --- | --- | --- |')
    for (const row of rsi.buckets) {
      lines.push(`| ${row.label} | ${row.cases.length} | ${row.mechanisms.join(' + ') || '**无**'} | `
        + `${row.cases.slice(0, 4).join('、')}${row.cases.length > 4 ? ' …' : ''} |`)
    }
    lines.push('')
    for (const row of rsi.buckets) {
      if (row.mechanisms.length) continue
      lines.push(`- 「${row.label}」目前没有 harness 机制兜：${row.advice}`)
    }
    lines.push('')
  }

  if (rsi.failingByCategory.length) {
    lines.push('## 未通过的用例（按维度）')
    for (const group of rsi.failingByCategory) {
      lines.push(`- ${group.label}：${group.cases.join('、')}`)
    }
    lines.push('')
    lines.push('## 每一条的判据原文（诊断靠它，不靠单条重跑）')
    for (const failure of rsi.failures) {
      lines.push(`- **${failure.caseId}**：${failure.problems.join('；')}`)
      if (failure.answerPreview) lines.push(`  - 答复开头：${failure.answerPreview}`)
    }
    lines.push('')
  }

  if (options.baseline) {
    const base = options.baseline
    lines.push('## 与上一轮（账本）对比')
    const delta = rsi.score - base.score
    lines.push(`- 总分 ${pct(base.score)} → ${pct(rsi.score)}（${delta >= 0 ? '+' : ''}${(delta * 100).toFixed(1)} 个百分点）`)
    const prev = base.buckets
    const now = new Map(rsi.buckets.map(row => [row.bucket, row.cases.length]))
    const allBuckets = new Set([...Object.keys(prev), ...now.keys()])
    for (const bucket of allBuckets) {
      const before = prev[bucket] ?? 0
      const after = now.get(bucket as FailureBucket) ?? 0
      if (before === after) continue
      lines.push(`- ${bucket}：${before} → ${after}（${after < before ? '变少' : '**变多**'}）`)
    }
    lines.push('')
  }
  return lines.join('\n')
}

/** 账本一行。**只追加**，跨轮对比靠它。 */
export interface RsiLedgerEntry {
  ts: string
  model: string
  /**
   * 这一轮实际用的采样温度。**必须记**：温度不同的两份账本不可比——
   * 2026-11-24 之前默认钉 0，改成线上 0.4 后同一套代码从"稳定全过"变成
   * "偶发 2~3 条失败"，而账本里看不出差别，历史对比就此失真。
   */
  temperature?: number
  score: number
  tunedScore: number
  holdoutScore: number
  cases: number
  /** 桶 → 条数。**存条数而不是用例名**：用例改名不该让历史对比断掉 */
  buckets: Record<string, number>
  failing: string[]
}

/**
 * 账本路径。**每次用时算，不在模块加载时定死**——与审计、轨迹同一个理由：
 * 评测要把 `WEFLOW_AUDIT_DIR` 指到临时目录，加载时求值会让那个环境变量再也改不动它
 * （`assistantTrace.traceFile()` 的注释里记着这次学费）。
 */
export function ledgerPath(): string {
  const override = (process.env.WEFLOW_RSI_LEDGER ?? '').trim()
  if (override) return override
  const dir = (process.env.WEFLOW_AUDIT_DIR ?? '').trim()
  return dir
    ? join(dir, 'rsi-ledger.jsonl')
    : join(homedir(), '.weflow-cli', 'rsi-ledger.jsonl')
}

/** 账本最多留多少行 / 多少字节。超了裁到最近的，**不删文件**（本机 `rmSync` 会静默失败） */
const LEDGER_KEEP = 100
const LEDGER_MAX_BYTES = 256 * 1024

/** 读账本（最近的在后）。读不到就当空账本——**账本是记录，不是依赖**。 */
export function readLedger(limit = 50): RsiLedgerEntry[] {
  let text = ''
  try { text = readFileSync(ledgerPath(), 'utf8') } catch { return [] }
  const rows: RsiLedgerEntry[] = []
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try { rows.push(JSON.parse(trimmed) as RsiLedgerEntry) } catch { /* 坏行跳过 */ }
  }
  return rows.slice(-Math.max(1, limit))
}

/** 追加一行。返回写入路径（调用方要打印它）；写失败不抛——评测结论不该被记账失败挡住。 */
export function appendLedger(entry: RsiLedgerEntry): string {
  const path = ledgerPath()
  try {
    mkdirSync(dirname(path), { recursive: true })
    appendFileSync(path, JSON.stringify(entry) + '\n', 'utf8')
    // 裁剪：只在超限时做，且用「读最近 N 行 + 重写」而不是删文件
    const size = statSync(path).size
    if (size > LEDGER_MAX_BYTES) {
      const keep = readLedger(LEDGER_KEEP)
      writeFileSync(path, keep.map(row => JSON.stringify(row)).join('\n') + '\n', 'utf8')
    }
  } catch { /* 记账失败不影响结论 */ }
  return path
}

export function toLedgerEntry(
  rsi: RsiReport, model: string, now = new Date(), temperature?: number,
): RsiLedgerEntry {
  const buckets: Record<string, number> = {}
  for (const row of rsi.buckets) buckets[row.bucket] = row.cases.length
  return {
    ts: now.toISOString(),
    model,
    ...(temperature === undefined ? {} : { temperature }),
    score: rsi.score,
    tunedScore: rsi.tuned.score,
    holdoutScore: rsi.holdout.score,
    cases: rsi.cases,
    buckets,
    failing: rsi.failingByCategory.flatMap(g => g.cases),
  }
}
