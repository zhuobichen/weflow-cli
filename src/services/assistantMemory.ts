/**
 * 三层记忆 (参考 LangGraph checkpointer + Mem0 范式，工程细节对照 deepseek-harness 的压缩纪律):
 *   L1 工作记忆  最近若干轮原文 (滑入滑出，按**预算比率**与条数两道闸)
 *   L2 滚动摘要  滑出的旧轮次压缩成摘要，跨窗口保留脉络
 *   L3 长期事实  从对话中提取的持久信息 (偏好/项目/关系)，带溯源，注入系统提示
 * 持久化: ~/.weflow-cli/assistant_memory.json（带 `version`，见下）
 *
 * ## 文件格式与版本
 *
 * ```json
 * { "version": 1,
 *   "users": { "<会话id>": {
 *     "working": [{"role":"user","content":"…"}],
 *     "summary": "…",
 *     "facts": [{"content":"…","ts":0,"sourceTurn":6,"sourceQuote":"…","usedAt":0}],
 *     "turnCount": 12 } } }
 * ```
 *
 * **什么算结构变更、要 bump 版本**（这条判据本身就是格式的一部分）：
 * 字段改名/删除、字段含义或单位变了、键空间变了（例如 `users` 这一层）。**只加可选字段不算**——
 * 读者忽略不认识的字段即可。当前 `MEMORY_FORMAT_VERSION = 1`。
 *
 * **读不出来时怎么办**：v0（没有 `version`、顶层直接是会话 id，历史遗留）**迁移**到 v1；
 * 版本等于当前值的正常读；**其它一律拒绝**（不猜、不迁移），并把原文件**改名留档**再以空状态启动。
 * 静默丢弃用户记忆是不能接受的，所以留档这一步是强制的，`loadIssue` 会把话说出来。
 */
import { join } from 'path'
import { existsSync, readFileSync, writeFileSync, mkdirSync, renameSync } from 'fs'
import os from 'os'

const MEMORY_FILE = join(os.homedir(), '.weflow-cli', 'assistant_memory.json')

export const MEMORY_FORMAT_VERSION = 1

/** 输入预算（字符）。中文约 1 token/字，按 24k 字留足余量给系统提示与工具结果 */
export const CONTEXT_BUDGET_CHARS = 24000
/** 窗口占用达预算这个比例就压缩 */
export const WORKING_TRIGGER_RATIO = 0.8
/** 压完最多保留预算的这个比例（按字符） */
export const WORKING_RETAIN_RATIO = 0.16
/** 保留的条数下限：预算算出来太少时按这个来，别把小窗口压空 */
export const WORKING_MIN_TURNS = 6
/** 条数上限：与预算闸并行，谁先到谁生效 */
export const WORKING_MAX = 16
export const SUMMARY_MAX = 800
export const FACTS_MAX = 30
export const FACT_EXTRACT_EVERY = 6
/** 每次注入事实的字符预算：30 条 × 72 字 ≈ 2.1KB 全塞进去是纯噪声，按相关度取一部分 */
export const FACTS_INJECT_BUDGET_CHARS = 1200
/** 两条事实的包含关系达到这个比例才当"同一条"（长的、更具体的那条胜出） */
const FACT_SAME_RATIO = 0.5

export interface ChatTurn { role: 'user' | 'assistant'; content: string }

export interface Fact {
  content: string
  ts: number
  /** 从第几个用户轮抽出来的；与 `sourceQuote` 一起，为"这条记忆对不对"留核对依据 */
  sourceTurn?: number
  /** 触发这条事实的那句用户话（截断） */
  sourceQuote?: string
  /** 最近一次被检索/注入的时间。用来排序与识别陈旧——只增不减的记忆最容易在这上面腐化 */
  usedAt?: number
}

interface UserMemory {
  working: ChatTurn[]
  summary: string
  facts: Fact[]
  turnCount: number
}

/** LLM 调用器抽象 (由 assistantService 注入, 避免循环依赖) */
export type LlmCaller = (messages: ChatTurn[], maxTokens?: number) => Promise<string>

function chars(turns: ChatTurn[]): number {
  return turns.reduce((n, t) => n + (t.content?.length ?? 0), 0)
}

/** 事实的归一化形式：去掉空白与常见标点，再小写。只用于**比较**，不用于存储 */
export function normalizeFact(text: string): string {
  return String(text ?? '')
    .replace(/\s+/g, '')
    .replace(/[，。、；：！？,.;:!?（）()【】[\]{}"'「」『』·—～~]/g, '')
    .toLowerCase()
}

export class AssistantMemory {
  private states: Record<string, UserMemory> = {}
  /** 本进程修改过的用户 (save 时只回写这些用户, 其余保留磁盘最新值) */
  private dirtyUsers = new Set<string>()
  /** 加载时出了什么值得说出来的事（版本不认识 → 已留档；文件坏了 → 已留档）。空串＝没事 */
  private loadIssue = ''

  constructor() { this.load() }

  /** 加载期的问题。调用方（服务启动时）应当把它**打出来**，而不是让用户面对一份空记忆 */
  get problem(): string { return this.loadIssue }

  private normalizeUser(s: any): UserMemory {
    const facts: Fact[] = Array.isArray(s?.facts)
      ? s.facts
        .filter((f: any) => f && typeof f.content === 'string' && f.content.trim())
        .map((f: any) => ({
          content: String(f.content),
          ts: Number(f.ts) || 0,
          ...(Number.isFinite(Number(f.sourceTurn)) ? { sourceTurn: Number(f.sourceTurn) } : {}),
          ...(typeof f.sourceQuote === 'string' && f.sourceQuote ? { sourceQuote: f.sourceQuote } : {}),
          ...(Number.isFinite(Number(f.usedAt)) ? { usedAt: Number(f.usedAt) } : {}),
        }))
      : []
    return {
      working: Array.isArray(s?.working)
        ? s.working.filter((t: any) => t && typeof t.content === 'string')
        : [],
      summary: typeof s?.summary === 'string' ? s.summary : '',
      facts,
      turnCount: Number(s?.turnCount) || 0,
    }
  }

  /** 把读不出来的文件改名留档——**不许静默丢弃** */
  private quarantine(reason: string): void {
    const target = `${MEMORY_FILE}.unreadable-${new Date().toISOString().replace(/[:.]/g, '-')}`
    try {
      renameSync(MEMORY_FILE, target)
      this.loadIssue = `${reason}；原文件已留档为 ${target.split(/[\\/]/).pop()}`
    } catch (error: any) {
      this.loadIssue = `${reason}；留档也失败了（${error?.message ?? error}），未改动原文件`
    }
    this.states = {}
  }

  private load(): void {
    if (!existsSync(MEMORY_FILE)) return
    let raw: any
    try {
      raw = JSON.parse(readFileSync(MEMORY_FILE, 'utf8'))
    } catch (error: any) {
      // 坏 JSON 也留档：它可能是用户唯一的一份记忆，不能就地丢弃
      this.quarantine(`记忆文件无法解析（${error?.message?.slice(0, 80) ?? error}）`)
      return
    }
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      this.quarantine('记忆文件不是一个对象')
      return
    }

    // v0：没有 version，顶层就是会话 id（历史遗留）→ 迁移到 v1
    if (raw.version === undefined) {
      for (const [uid, state] of Object.entries<any>(raw)) {
        if (typeof state !== 'object' || state === null) continue
        this.states[uid] = this.normalizeUser(state)
      }
      if (Object.keys(this.states).length) {
        this.loadIssue = `记忆文件是 v0 格式，已迁移到 v${MEMORY_FORMAT_VERSION}（下次保存时落盘）`
        for (const uid of Object.keys(this.states)) this.dirtyUsers.add(uid)
      }
      return
    }

    if (raw.version !== MEMORY_FORMAT_VERSION) {
      // 不猜、不迁移：版本比我们新（或看不懂）时，宁可留档重来，也不把对方的字段读歪
      this.quarantine(`记忆文件版本是 ${JSON.stringify(raw.version)}，本程序只认 ${MEMORY_FORMAT_VERSION}`)
      return
    }

    const users = raw.users
    if (users === undefined) return
    if (typeof users !== 'object' || users === null || Array.isArray(users)) {
      this.quarantine('记忆文件的 users 字段不是一个对象')
      return
    }
    for (const [uid, state] of Object.entries<any>(users)) {
      this.states[uid] = this.normalizeUser(state)
    }
  }

  save(): void {
    try {
      // 读改写合并: MCP server / assistant 守护进程并发写同一文件,
      // 只回写本进程修改过的用户, 防止旧快照覆盖其他进程刚写入的数据 (同 configService 策略)
      const merged: Record<string, UserMemory> = {}
      try {
        if (existsSync(MEMORY_FILE)) {
          const disk = JSON.parse(readFileSync(MEMORY_FILE, 'utf8'))
          const diskUsers = disk?.version === MEMORY_FORMAT_VERSION ? disk.users
            : disk?.version === undefined ? disk : null
          if (diskUsers && typeof diskUsers === 'object') {
            for (const [uid, s] of Object.entries<any>(diskUsers)) {
              if (this.dirtyUsers.has(uid)) continue
              merged[uid] = this.normalizeUser(s)
            }
          }
        }
      } catch { /* 磁盘损坏 → 只落内存态（上面 load 已经留过档了） */ }
      for (const uid of this.dirtyUsers) {
        if (this.states[uid]) merged[uid] = this.states[uid]
      }
      const dir = join(os.homedir(), '.weflow-cli')
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
      const payload = { version: MEMORY_FORMAT_VERSION, users: merged }
      const tmp = `${MEMORY_FILE}.tmp`
      writeFileSync(tmp, JSON.stringify(payload, null, 1), 'utf8')
      renameSync(tmp, MEMORY_FILE)
      this.states = merged
      this.dirtyUsers.clear()
    } catch { /* 持久化失败不阻断对话 */ }
  }

  private state(userId: string): UserMemory {
    if (!this.states[userId]) {
      this.states[userId] = { working: [], summary: '', facts: [], turnCount: 0 }
    }
    return this.states[userId]
  }

  reset(userId: string): void {
    delete this.states[userId]
    this.dirtyUsers.add(userId)
    this.save()
  }

  addTurn(userId: string, role: 'user' | 'assistant', content: string): void {
    this.dirtyUsers.add(userId)
    const s = this.state(userId)
    s.working.push({ role, content })
    if (role === 'user') s.turnCount++
  }

  /** L1 窗口 (喂给 LLM 的近期原文) */
  workingWindow(userId: string): ChatTurn[] {
    return this.state(userId).working
  }

  summary(userId: string): string { return this.state(userId).summary }
  facts(userId: string): Fact[] { return this.state(userId).facts }
  userCount(): number { return Object.keys(this.states).length }

  /** 是哪道闸要压：条数（短轮次堆多了）还是预算（长轮次把窗口撑爆了） */
  compressionGate(userId: string): 'count' | 'budget' | 'both' | null {
    const s = this.state(userId)
    const byCount = s.working.length > WORKING_MAX
    const byBudget = chars(s.working) > CONTEXT_BUDGET_CHARS * WORKING_TRIGGER_RATIO
    if (byCount && byBudget) return 'both'
    if (byCount) return 'count'
    if (byBudget) return 'budget'
    return null
  }

  needsCompression(userId: string): boolean { return this.compressionGate(userId) !== null }

  /** 压完保留多少条。**两道闸的保留规则不同，这是有意的**：
   *
   *  - 条数闸（一堆短轮次堆到 16 条）：保留最近的一半，下限 `WORKING_MIN_TURNS`——这里条数才是
   *    问题所在，留一半正好把窗口减半；
   *  - 预算闸（几条长轮次就把预算撑爆）：按**字符**保留预算的 16%，条数只保底 1 条。
   *    这两条约束会互斥——6000 字的轮次保 6 条就是 36000 字，远超 24k 预算。**字符上限优先**，
   *    因为它是硬预算；条数下限在小窗口下才有意义。
   *  - 两道同时触发：取两者中**更小的**（更保守）。
   *
   *  无论哪条，都卡在 `[1, 窗口长度-1]`：算出"保留 6 条"而窗口只有 5 条时，`slice(-1)` 会只留
   *  1 条，下限形同虚设（实测撞出来的）；也至少要压出去一条，否则"压了"却没动。
   */
  private retainCount(turns: ChatTurn[], gate: 'count' | 'budget' | 'both'): number {
    const cap = CONTEXT_BUDGET_CHARS * WORKING_RETAIN_RATIO
    let used = 0
    let byChars = 0
    for (let i = turns.length - 1; i >= 0; i--) {
      const size = turns[i].content?.length ?? 0
      if (byChars > 0 && used + size > cap) break
      used += size
      byChars++
      if (used >= cap) break
    }
    const countKeep = Math.max(WORKING_MIN_TURNS, Math.floor(turns.length / 2))
    const budgetKeep = Math.max(1, byChars)
    const want = gate === 'count' ? countKeep : gate === 'budget' ? budgetKeep : Math.min(countKeep, budgetKeep)
    return Math.min(Math.max(want, 1), turns.length - 1)
  }

  /** L2: 窗口该压时，把最旧的那些压进摘要 */
  async compressIfNeeded(userId: string, llm: LlmCaller): Promise<boolean> {
    const s = this.state(userId)
    if (!this.needsCompression(userId)) return false
    this.dirtyUsers.add(userId)

    const keep = this.retainCount(s.working, this.compressionGate(userId) ?? 'count')
    const out = s.working.slice(0, s.working.length - keep)
    s.working = s.working.slice(s.working.length - keep)

    try {
      const merged = await llm([{ role: 'user', content: buildSummaryPrompt(s.summary, out) }], 900)
      s.summary = merged.trim().slice(0, SUMMARY_MAX)
    } catch {
      // LLM 失败时降级: 粗暴截断拼接, 保住不丢
      const brief = out.map(t => `${t.role === 'user' ? '用户' : '助手'}: ${t.content.slice(0, 40)}`).join(' / ')
      s.summary = `${s.summary} ${brief}`.slice(-SUMMARY_MAX)
    }
    return true
  }

  /** L3: 定期从最近对话提取持久事实 */
  async extractFactsIfNeeded(userId: string, llm: LlmCaller): Promise<number> {
    const s = this.state(userId)
    if (s.turnCount === 0 || s.turnCount % FACT_EXTRACT_EVERY !== 0) return 0
    const recent = s.working.slice(-FACT_EXTRACT_EVERY)
    if (!recent.length) return 0
    this.dirtyUsers.add(userId)

    // 溯源：这一批事实是从"第几个用户轮"抽出来的，触发的那句用户话也记下来
    const lastUser = [...recent].reverse().find(t => t.role === 'user')
    const sourceTurn = s.turnCount
    const sourceQuote = (lastUser?.content ?? '').replace(/\s+/g, ' ').slice(0, 40)

    try {
      const raw = await llm([{ role: 'user', content: buildFactPrompt(recent) }], 300)
      const m = raw.match(/\[[\s\S]*\]/)
      if (!m) return 0
      const items: unknown[] = JSON.parse(m[0])
      let added = 0
      for (const item of items) {
        if (typeof item !== 'string' || !item.trim()) continue
        if (this.rememberFact(s, item.trim().slice(0, 60), { sourceTurn, sourceQuote })) added++
      }
      if (s.facts.length > FACTS_MAX) s.facts = s.facts.slice(-FACTS_MAX)
      return added
    } catch {
      return 0
    }
  }

  /** L3 检索: 关键词命中；命中即记 `usedAt`（谁被用过是排序与"陈旧"判断的依据） */
  searchFacts(userId: string, keyword: string): Fact[] {
    const kw = keyword.trim()
    if (!kw) return []
    const s = this.state(userId)
    const hits = s.facts.filter(f =>
      f.content.includes(kw) || kw.split(/\s+/).some(w => w.length >= 2 && f.content.includes(w)))
    if (hits.length) {
      const now = Date.now()
      for (const f of hits) f.usedAt = now
      this.dirtyUsers.add(userId)
    }
    return hits
  }

  /** L3 写入 (save_memory 工具用, 立即生效) */
  addFact(userId: string, content: string, source?: { sourceTurn?: number; sourceQuote?: string }): boolean {
    this.dirtyUsers.add(userId)
    const s = this.state(userId)
    const added = this.rememberFact(s, content, source)
    if (added && s.facts.length > FACTS_MAX) s.facts = s.facts.slice(-FACTS_MAX)
    this.save()
    return added
  }

  /**
   * 记忆一条事实，返回**是否新增或更新**。三种判定：
   *
   * - 归一化后相同 → 重复，什么都不做；
   * - 一条包含另一条 → 取**更长的那条**（更具体），短的那条被顶掉；
   * - 其它 → 新增。
   *
   * 包含式去重的固有模糊写在测试里：两个恰好互为前缀的短事实（`事实1` 与 `事实10`），
   * 长的那条会顶掉短的。真实事实极少这样，而"更具体的胜出"在真实场景里是对的。
   */
  private rememberFact(s: UserMemory, content: string, source?: { sourceTurn?: number; sourceQuote?: string }): boolean {
    const text = content.trim()
    if (!text) return false
    const norm = normalizeFact(text)
    for (let i = 0; i < s.facts.length; i++) {
      const existing = s.facts[i]
      const other = normalizeFact(existing.content)
      if (norm === other) {
        // 同一条：把溯源补全（旧的可能是没有溯源的版本），不动时间戳
        if (source?.sourceTurn !== undefined && existing.sourceTurn === undefined) {
          existing.sourceTurn = source.sourceTurn
          existing.sourceQuote = source.sourceQuote
        }
        return false
      }
      const contains = other.includes(norm) || norm.includes(other)
      if (!contains) continue
      const ratio = Math.min(norm.length, other.length) / Math.max(norm.length, other.length)
      if (ratio < FACT_SAME_RATIO) continue          // 只是碰巧包含，算两条
      if (norm.length > other.length) {
        // 新的更具体 → 顶掉旧的，保留它最早的时间戳
        s.facts[i] = { content: text, ts: existing.ts || Date.now(), ...(source ?? {}) }
        return true
      }
      return false                                    // 旧的已经更具体，不动
    }
    s.facts.push({ content: text, ts: Date.now(), ...(source ?? {}) })
    return true
  }
}

/** L2 的压缩提示词。**固定节骨架 + 合并律**：空节写 `(none)`，永不删节。
 *
 *  为什么固定成节：自由摘要最容易在压缩时悄悄丢信息（丢了自己也不知道）。固定节至少让
 *  每一次压缩都必须回答"待办有没有、错误有没有、下一步有没有"。
 *  为什么写合并律：滚动摘要腐化的正解是"保留仍然成立的、丢掉过期的、合成一份"，并明确
 *  **禁止逐字复制前任**——否则摘要会一层层掺水。
 */
export function buildSummaryPrompt(previous: string, turnsOut: ChatTurn[]): string {
  const transcript = turnsOut.map(t => `${t.role === 'user' ? '用户' : '助手'}: ${t.content}`).join('\n')
  return `把下面的对话压缩进已有摘要。**输出必须是这 8 个节，一节不删**；某一节没有内容就写 (none)：

## 用户诉求
## 技术要点
## 涉及的文件与命令
## 错误与修复
## 待办
## 当前进展
## 下一步
## 关键上下文

合并规则：保留**仍然成立**的事实，丢掉已经过期的，合成**一份**摘要。
不许逐字复制旧摘要，也不许把已经解决的错误当成待办留着。
总长不超过 800 字，直接输出摘要正文，不要任何前后缀。

[已有摘要]
${previous || '(无)'}

[新增对话]
${transcript}`
}

/** L3 的抽取提示词。要求可核对：每条事实都能在对话里指出来源 */
export function buildFactPrompt(recent: ChatTurn[]): string {
  const transcript = recent.map(t => `${t.role === 'user' ? '用户' : '助手'}: ${t.content}`).join('\n')
  return `从对话中提取关于用户的持久事实(背景/偏好/项目/人际关系/惯例)。每条不超过40字。
只提取**用户自己说过的、长期成立的**信息；助手自己的推测不算。
只输出 JSON 字符串数组,最多3条,没有则输出 []。不要提取临时性内容。

[对话]
${transcript}`
}

/** 字符二元组集合——中文没有词边界，用二元组算重合比"包含"稳，也不用引模型 */
function bigrams(text: string): Set<string> {
  const norm = normalizeFact(text)
  const out = new Set<string>()
  for (let i = 0; i + 1 < norm.length; i++) out.add(norm.slice(i, i + 2))
  return out
}

function overlap(a: Set<string>, b: Set<string>): number {
  if (!a.size || !b.size) return 0
  let hit = 0
  for (const g of a) if (b.has(g)) hit++
  return hit / Math.min(a.size, b.size)
}

/**
 * 该把哪几条事实放进这次请求：**先相关、再近期用过、最后按时间**，总量受字符预算约束。
 *
 * 为什么不全量注入：30 条 × ~72 字 ≈ 2.1KB **每轮**都发，而且与当前问题无关的那些是纯噪声——
 * 噪声不只是花钱，它是"模型被无关信息带偏"的来源之一。
 *
 * **相关度决定"谁进"，展示保持时序**：入选的按时间先后排列（读起来像一份清单，而不是一堆碎片），
 * 没进的按相关度+近期用过来决定顺序。
 *
 * 返回未被选中的条数，调用方要**如实说出来**（`另有 N 条未列出`）。谎报"全部记忆如下"比少给
 * 几条更糟：模型会以为自己看到了全部。
 */
export function selectFactsForInjection(facts: Fact[], question: string,
                                        budgetChars = FACTS_INJECT_BUDGET_CHARS): { selected: Fact[]; withheld: number } {
  if (!facts.length) return { selected: [], withheld: 0 }
  const q = bigrams(question || '')
  const qText = normalizeFact(question || '')

  const scored = facts.map((f, index) => {
    const fText = normalizeFact(f.content)
    // 直接包含（整串或关键片段）算强相关；否则用二元组重合
    const direct = qText.length >= 2 && (fText.includes(qText) || qText.includes(fText))
    const sim = direct ? 1 : overlap(q, bigrams(f.content))
    return { fact: f, index, rank: sim * 10 + (f.usedAt ? 0.5 : 0) + (f.ts ? 0.1 : 0) }
  })

  scored.sort((a, b) => b.rank - a.rank || a.index - b.index)
  const selected: Fact[] = []
  let used = 0
  for (const item of scored) {
    const size = item.fact.content.length + 2
    if (selected.length && used + size > budgetChars) break
    selected.push(item.fact)
    used += size
  }
  // 保持原来的顺序（时间先后），读起来才像一份清单而不是一堆碎片
  selected.sort((a, b) => facts.indexOf(a) - facts.indexOf(b))
  return { selected, withheld: facts.length - selected.length }
}

const FRAME_OPEN = 'weflow-local-data'
const FRAME_CLOSE = '/' + FRAME_OPEN

/**
 * 把一段**本地数据**包进框里再进提示词，并转义内容里出现的框标签。
 *
 * 防的是"框欺骗"：聊天正文、文章正文、事实里完全可能写着 `</weflow-local-data>` 再跟一段
 * 像系统指令的话——不转义就等于让数据自己把框关上、直接以系统身份说话。转义后它只能是数据。
 * （这不等于防住提示词注入本身：数据里仍可以有指令性文字；我们只保证它**关不掉这个框**。）
 */
export function frameLocalData(label: string, body: string): string {
  // 用 split/join 而不是正则：这里只需要"把框标签的尖括号改掉一个字"，正则只会给它引入转义问题。
  // 开标签与闭标签都要挡：数据里出现任一形式，都不能让它把框关上。
  const safe = String(body ?? '')
    .split('<' + FRAME_OPEN).join('<‹' + FRAME_OPEN)
    .split('<' + FRAME_CLOSE).join('<‹' + FRAME_CLOSE)
  const crlf = String.fromCharCode(10)
  return '<' + FRAME_OPEN + ' source="' + label + '">' + crlf + safe + crlf + '<' + FRAME_CLOSE + '>'
}


