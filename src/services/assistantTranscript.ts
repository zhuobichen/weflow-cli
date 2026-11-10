/**
 * 原始对话流水（记忆架构的 L1「底账」）。
 *
 * 为什么要有：`assistantMemory` 的 facts 只存 `sourceQuote` —— 触发那句话的**截断**。
 * 它证明"这句话被说过"，不证明"这件事成立"。窗口一压缩，原话就没了，于是抽错的事实
 * 再也没有东西可以对照（Anthropic 那篇文章叫它"错误记忆的级联崩溃"）。
 *
 * 所以这一层只做一件事：**把每轮原话按天追加落盘，永不改写**。它不参与推理，
 * 只作为将来对账（`ASSISTANT_MEMORY_PLAN.md` 的 P2）的证据源。
 *
 * ## 边界（写之前先看）
 *
 * - **不做出境脱敏**：`assistantPrivacy` 的既有判据是「工具挖出的第三方数据才脱敏；
 *   用户自己说的话按原意处理，用户对自己的话有处置权」。流水是用户自己的对话，
 *   与 `assistant_memory.json` 里已经明文存的 `working` / `facts` 同口径——
 *   在这里单方面打码，会让对账时连原话都对不上。
 *   取而代之的是**文件权限**：目录 0700、文件 0600（同 `configService` 的做法）。
 * - 单条截断 `TRANSCRIPT_LINE_MAX`：流水是证据，不是全文存档；长回复对账没价值。
 * - 按天轮转，默认保留 `TRANSCRIPT_RETENTION_DAYS` 天。**只增不减的库一定会腐化**，
 *   这是文章里明写的工程代价，不靠自觉。
 * - 所有写入都是 **best-effort**：写不进去只是少一份证据，绝不能让助手这一轮失败。
 */
import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, unlinkSync } from 'fs'
import { join } from 'path'
import os from 'os'

const TRANSCRIPT_DIR = join(os.homedir(), '.weflow-cli', 'assistant_transcript')

/** 单条内容上限（字符）。中文约 1 token/字，4000 字已远超对账所需 */
export const TRANSCRIPT_LINE_MAX = 4000

/** 保留天数。流水是证据源，不是历史档案；7 天够覆盖"这周说过什么"的对账窗口 */
export const TRANSCRIPT_RETENTION_DAYS = 7

export interface TranscriptEntry {
  ts: number
  role: 'user' | 'assistant'
  content: string
  session?: string
}

export function transcriptDir(): string {
  return TRANSCRIPT_DIR
}

function dayFile(day: string): string {
  return join(TRANSCRIPT_DIR, `${day}.jsonl`)
}

function dayOf(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/**
 * 追加一轮原话。返回是否真的写进去了。
 *
 * 刻意不抛：调用点在助手主链路上，写流水失败不该让用户收到"助手出错了"。
 */
export function appendTurn(entry: TranscriptEntry): boolean {
  try {
    const raw = String(entry.content ?? '')
    if (!raw.trim()) return false
    // 长内容先截断再脱敏：脱敏的代价与长度成正比，先砍掉尾巴更划算
    const safe = raw.length > TRANSCRIPT_LINE_MAX ? raw.slice(0, TRANSCRIPT_LINE_MAX) : raw
    mkdirSync(TRANSCRIPT_DIR, { recursive: true, mode: 0o700 })
    const line = JSON.stringify({
      ts: entry.ts,
      role: entry.role,
      content: safe,
      ...(entry.session ? { session: entry.session } : {}),
    }) + '\n'
    appendFileSync(dayFile(dayOf(entry.ts)), line, { encoding: 'utf8', mode: 0o600 })
    return true
  } catch {
    return false
  }
}

/** 读某一天的流水（对账用）。文件不存在返回空数组，不抛。 */
export function readDay(day: string): TranscriptEntry[] {
  try {
    const file = dayFile(day)
    if (!existsSync(file)) return []
    return readFileSync(file, 'utf8')
      .split('\n')
      .filter(Boolean)
      .map(line => {
        try { return JSON.parse(line) as TranscriptEntry } catch { return null }
      })
      .filter((v): v is TranscriptEntry => !!v && typeof v.content === 'string')
  } catch {
    return []
  }
}

/** 最近的 N 天（含今天），按时间升序。 */
export function readRecentDays(days = TRANSCRIPT_RETENTION_DAYS, now = Date.now()): TranscriptEntry[] {
  const out: TranscriptEntry[] = []
  for (let i = days - 1; i >= 0; i--) {
    out.push(...readDay(dayOf(now - i * 86400000)))
  }
  return out
}

/**
 * 删掉超过保留期的按天文件。返回删掉的文件名。
 *
 * 只在"助手空闲 / 启动"时调用，不在每轮调用——清理不是热路径的工作。
 */
export function pruneTranscript(now = Date.now(), keepDays = TRANSCRIPT_RETENTION_DAYS): string[] {
  const removed: string[] = []
  try {
    if (!existsSync(TRANSCRIPT_DIR)) return removed
    const cutoff = dayOf(now - keepDays * 86400000)
    for (const name of readdirSync(TRANSCRIPT_DIR)) {
      // 只认自己的命名（YYYY-MM-DD.jsonl），别去删别的文件
      const m = name.match(/^(\d{4}-\d{2}-\d{2})\.jsonl$/)
      if (!m) continue
      if (m[1] >= cutoff) continue
      try {
        unlinkSync(join(TRANSCRIPT_DIR, name))
        removed.push(name)
      } catch { /* 删不掉就下次再说 */ }
    }
  } catch { /* 目录读不了 = 没有可删的 */ }
  return removed
}
