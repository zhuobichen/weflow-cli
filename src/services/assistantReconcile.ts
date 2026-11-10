/**
 * 事实对账（记忆架构的「离线 Dreaming」最小版）。
 *
 * 只做一件**确定性**的事：拿每条事实的 `sourceQuote` 去原始流水里找原话。
 *
 * - 找得到 → `verified = true`，`evidence` 记下是哪天、第几条；
 * - 找不到 → `verified = false`，`evidence = 'not-in-transcript'`（**不是删除**：可能是流水
 *   已经被 7 天保留期清掉了，删错比留错更贵）；
 * - 事实没有 `sourceQuote`（旧数据） → 跳过，只在统计里记一笔 `no-quote`。
 *
 * 刻意**不做**的两件事：
 *
 * 1. **不用 LLM 判"原话是否支持这个结论"**。那需要一次模型调用/条，而且判错的代价是删掉
 *    一条真事实。这一版只回答"有没有原话"，把"原话是否支持"留给人工或下一版。
 * 2. **不自动删除**。对账的输出是"可信度"，不是"清理"——证据不全 ≠ 事实为假。
 */
import type { AssistantMemory } from './assistantMemory.js'
import { readRecentDays } from './assistantTranscript.js'

export interface ReconcileResult {
  /** 本轮检查了几条 */
  checked: number
  /** 找到了原话 */
  verified: number
  /** 没找到原话（保留，但标为未核实） */
  notFound: number
  /** 没有 sourceQuote，无法对账 */
  noQuote: number
}

/** 归一化：去掉空白与标点，只用于**比较**（与 `normalizeFact` 同口径但这里不 import，避免环） */
function norm(text: string): string {
  return String(text ?? '')
    .replace(/\s+/g, '')
    .replace(/[，。、；：！？,.;:!?（）()【】[\]{}"'「」『』·—～~]/g, '')
}

/**
 * 对账一个用户的事实。返回统计。
 *
 * 成本：读最近 `days` 天的流水（每天几百 KB）+ 一次 O(事实数 × 流水条数) 的子串查找。
 * 事实上限 30 条、流水 7 天，实测量级在毫秒到几十毫秒，可以在提取之后顺手跑。
 */
export function reconcileFacts(
  memory: AssistantMemory,
  userId: string,
  now = Date.now(),
  days = 7,
): ReconcileResult {
  const result: ReconcileResult = { checked: 0, verified: 0, notFound: 0, noQuote: 0 }
  const facts = memory.facts(userId)
  if (!facts.length) return result

  const corpus = readRecentDays(days, now)
    .filter(e => e.role === 'user')
    .map(e => ({ ts: e.ts, text: norm(e.content) }))
  if (!corpus.length) return result          // 没有流水可对：保持原状态，不误判

  for (const fact of facts) {
    result.checked++
    const quote = norm(fact.sourceQuote ?? '')
    if (quote.length < 2) {
      result.noQuote++
      continue
    }
    const hit = corpus.find(e => e.text.includes(quote))
    if (hit) {
      const day = new Date(hit.ts).toISOString().slice(0, 10)
      memory.markFactVerification(userId, fact.content, true, `transcript:${day}`)
      result.verified++
    } else {
      memory.markFactVerification(userId, fact.content, false, 'not-in-transcript')
      result.notFound++
    }
  }
  return result
}
