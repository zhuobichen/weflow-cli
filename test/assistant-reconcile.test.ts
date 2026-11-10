/**
 * 事实对账（离线 Dreaming 的最小版）。
 *
 * 只做确定性判断：拿 `sourceQuote` 去原始流水里找原话。
 * 找到 → 已核实；找不到 → 标未核实但**不删**（流水可能已过保留期）。
 *
 * HOME 指到临时目录后才 import（流水与记忆都落在 ~/.weflow-cli 下）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-reconcile-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantMemory } = await import('../src/services/assistantMemory.js')
const { appendTurn } = await import('../src/services/assistantTranscript.js')
const { reconcileFacts } = await import('../src/services/assistantReconcile.js')

/** 造一个只带 facts 的记忆实例（不跑 LLM） */
function memoryWith(facts: any[]) {
  const m = new AssistantMemory() as any
  m.facts = () => facts
  m.markFactVerification = (userId: string, content: string, verified: boolean, evidence: string) => {
    const f = facts.find(x => x.content === content)
    if (!f) return false
    f.verified = verified
    f.evidence = evidence
    return true
  }
  return m
}

test('原话在流水里 → 标记已核实，evidence 指出对应的天', () => {
  const ts = Date.now()
  appendTurn({ ts, role: 'user', content: '以后回复前先读一下回复规则' })
  const fact = { content: '回复前先读回复规则', ts, sourceQuote: '以后回复前先读一下回复规则' }
  const m = memoryWith([fact])

  const r = reconcileFacts(m, 'u1', ts)
  assert.equal(r.checked, 1)
  assert.equal(r.verified, 1)
  assert.equal(fact.verified, true)
  assert.match(fact.evidence!, /^transcript:\d{4}-\d{2}-\d{2}$/)
})

test('流水里找不到原话 → 标未核实，但**不删除**事实', () => {
  const ts = Date.now()
  const fact = { content: '一条没有依据的事实', ts, sourceQuote: '这句话根本没说过' }
  const m = memoryWith([fact])

  const r = reconcileFacts(m, 'u2', ts)
  assert.equal(r.notFound, 1)
  assert.equal(fact.verified, false)
  assert.equal(fact.evidence, 'not-in-transcript')
  assert.ok(fact.content, '事实本体必须还在——证据不全不等于事实为假')
})

test('没有 sourceQuote 的旧事实跳过，不误判成"没依据"', () => {
  const ts = Date.now()
  const fact = { content: '旧记忆没有溯源', ts }
  const m = memoryWith([fact])

  const r = reconcileFacts(m, 'u3', ts)
  assert.equal(r.noQuote, 1)
  assert.equal(r.notFound, 0)
  assert.equal(fact.verified, undefined, '不该给它凭空加一个 false')
})

test('没有流水时原样返回，不改任何事实状态', () => {
  const m = memoryWith([{ content: 'x', ts: Date.now(), sourceQuote: 'y' }])
  // 把"现在"推到 100 天后：保留窗口里自然没有文件，模拟"流水已被清理"
  const r = reconcileFacts(m, 'u-empty', Date.now() + 100 * 86400000)
  assert.equal(r.checked, 0)
})

test('只对用户的句子，不拿助手自己说的话去"自证"', () => {
  const ts = Date.now()
  appendTurn({ ts, role: 'assistant', content: '助手自己复述了一遍这个说法' })
  const fact = { content: '助手复述的说法', ts, sourceQuote: '助手自己复述了一遍这个说法' }
  const m = memoryWith([fact])

  const r = reconcileFacts(m, 'u4', ts)
  assert.equal(r.notFound, 1, '助手自己说的话不能作为事实依据')
})
