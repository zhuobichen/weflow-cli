/**
 * 三层记忆 (参考 LangGraph checkpointer + Mem0 范式):
 *   L1 工作记忆  最近 N 轮原文 (滑入滑出)
 *   L2 滚动摘要  滑出的旧轮次压缩成摘要, 跨窗口保留脉络
 *   L3 长期事实  从对话中提取的持久信息 (偏好/项目/关系), 注入系统提示
 * 持久化: ~/.weflow-cli/assistant_memory.json
 */
import { join } from 'path'
import { existsSync, readFileSync, writeFileSync, mkdirSync, renameSync } from 'fs'
import os from 'os'

const MEMORY_FILE = join(os.homedir(), '.weflow-cli', 'assistant_memory.json')

const WORKING_MAX = 16 // 超过则最旧一半转摘要
const SUMMARY_MAX = 800
const FACTS_MAX = 30
const FACT_EXTRACT_EVERY = 6

export interface ChatTurn { role: 'user' | 'assistant'; content: string }
export interface Fact { content: string; ts: number }

interface UserMemory {
  working: ChatTurn[]
  summary: string
  facts: Fact[]
  turnCount: number
}

/** LLM 调用器抽象 (由 assistantService 注入, 避免循环依赖) */
export type LlmCaller = (messages: ChatTurn[], maxTokens?: number) => Promise<string>

export class AssistantMemory {
  private states: Record<string, UserMemory> = {}
  /** 本进程修改过的用户 (save 时只回写这些用户, 其余保留磁盘最新值) */
  private dirtyUsers = new Set<string>()

  constructor() { this.load() }

  private normalize(s: any): UserMemory {
    return {
      working: Array.isArray(s.working) ? s.working : [],
      summary: typeof s.summary === 'string' ? s.summary : '',
      facts: Array.isArray(s.facts) ? s.facts : [],
      turnCount: s.turnCount || 0,
    }
  }

  private load(): void {
    try {
      if (existsSync(MEMORY_FILE)) {
        const raw = JSON.parse(readFileSync(MEMORY_FILE, 'utf8'))
        for (const [uid, s] of Object.entries<any>(raw)) {
          this.states[uid] = this.normalize(s)
        }
      }
    } catch { this.states = {} }
  }

  save(): void {
    try {
      // 读改写合并: MCP server / assistant 守护进程并发写同一文件,
      // 只回写本进程修改过的用户, 防止旧快照覆盖其他进程刚写入的数据 (同 configService 策略)
      const merged: Record<string, UserMemory> = {}
      try {
        if (existsSync(MEMORY_FILE)) {
          const disk = JSON.parse(readFileSync(MEMORY_FILE, 'utf8'))
          for (const [uid, s] of Object.entries<any>(disk)) {
            if (this.dirtyUsers.has(uid)) continue
            merged[uid] = this.normalize(s)
          }
        }
      } catch { /* 磁盘损坏 → 只落内存态 */ }
      for (const uid of this.dirtyUsers) {
        if (this.states[uid]) merged[uid] = this.states[uid]
      }
      const dir = join(os.homedir(), '.weflow-cli')
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
      const tmp = `${MEMORY_FILE}.tmp`
      writeFileSync(tmp, JSON.stringify(merged), 'utf8')
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

  /** L2: 窗口溢出时把最旧一半压进摘要 */
  async compressIfNeeded(userId: string, llm: LlmCaller): Promise<boolean> {
    const s = this.state(userId)
    if (s.working.length <= WORKING_MAX) return false
    this.dirtyUsers.add(userId)

    const out = s.working.slice(0, Math.floor(s.working.length / 2))
    s.working = s.working.slice(out.length)

    try {
      const prompt = `将以下对话压缩为摘要,与已有摘要合并。保留:关键事实、决定、人名、时间、待办。输出不超过500字,直接输出摘要正文,不要任何前后缀。

[已有摘要]
${s.summary || '(无)'}

[新增对话]
${out.map(t => `${t.role === 'user' ? '用户' : '助手'}: ${t.content}`).join('\n')}`
      const merged = await llm([{ role: 'user', content: prompt }], 600)
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

    try {
      const prompt = `从对话中提取关于用户的持久事实(背景/偏好/项目/人际关系/惯例)。每条不超过40字。只输出 JSON 字符串数组,最多3条,没有则输出 []。不要提取临时性内容。

[对话]
${recent.map(t => `${t.role === 'user' ? '用户' : '助手'}: ${t.content}`).join('\n')}`
      const raw = await llm([{ role: 'user', content: prompt }], 300)
      const m = raw.match(/\[[\s\S]*\]/)
      if (!m) return 0
      const items: unknown[] = JSON.parse(m[0])
      let added = 0
      for (const item of items) {
        if (typeof item !== 'string' || !item.trim()) continue
        const fact = item.trim().slice(0, 60)
        // 去重: 与既有事实包含关系判断
        if (s.facts.some(f => f.content.includes(fact) || fact.includes(f.content))) continue
        s.facts.push({ content: fact, ts: Date.now() })
        added++
      }
      if (s.facts.length > FACTS_MAX) s.facts = s.facts.slice(-FACTS_MAX)
      return added
    } catch {
      return 0
    }
  }

  /** L3 检索: 关键词命中 (为未来向量检索预留同一接口) */
  searchFacts(userId: string, keyword: string): Fact[] {
    const kw = keyword.trim()
    if (!kw) return []
    return this.state(userId).facts.filter(f =>
      f.content.includes(kw) || kw.split(/\s+/).some(w => w.length >= 2 && f.content.includes(w)))
  }

  /** L3 写入 (save_memory 工具用, 立即生效) */
  addFact(userId: string, content: string): boolean {
    this.dirtyUsers.add(userId)
    const s = this.state(userId)
    if (s.facts.some(f => f.content.includes(content) || content.includes(f.content))) return false
    s.facts.push({ content, ts: Date.now() })
    if (s.facts.length > FACTS_MAX) s.facts = s.facts.slice(-FACTS_MAX)
    this.save()
    return true
  }
}
