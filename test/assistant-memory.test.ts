/**
 * 助手的三层记忆（L1 工作窗口 / L2 滚动摘要 / L3 长期事实）。
 *
 * 这个文件此前**一个测试都没有**（193 行，零覆盖）。它有三处刻意"失败也不出声"的设计：
 * LLM 压缩失败时降级成粗暴截断（保住不丢）、事实提取失败返回 0、去重靠包含关系判断。
 * 这些地方出问题不会报错，只会让助手"悄悄变笨"——所以它们必须有测试，否则"很稳"和
 * "从来没跑过"这两种状态从外面分不出来。
 *
 * HOME 指到临时目录后才 import：记忆文件落在 ~/.weflow-cli/ 下，测试不许碰真实家目录。
 * 不联网（LLM 由注入的假 caller 提供）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-memory-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantMemory } = await import('../src/services/assistantMemory.js')

// 当前常量（assistantMemory.ts）：窗口 16 条、摘要 800 字、事实 30 条、每 6 个用户轮提取一次
const WORKING_MAX = 16
const FACT_EVERY = 6

let seq = 0
/** 每个用例一个独立的 userId：记忆是持久化的，用例之间不该互相看见 */
function newUser(): string {
  seq += 1
  return `u-test-${seq}`
}

function llmReturning(text: string, record?: string[]): any {
  return async (messages: any[]) => {
    record?.push(messages[0]?.content || '')
    return text
  }
}

test('turns land in the working window in order, with roles kept', () => {
  const memory = new AssistantMemory()
  const user = newUser()
  memory.addTurn(user, 'user', '第一句')
  memory.addTurn(user, 'assistant', '第一答')

  assert.deepEqual(memory.workingWindow(user), [
    { role: 'user', content: '第一句' },
    { role: 'assistant', content: '第一答' },
  ])
})

test('a window under the cap is not compressed', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  for (let i = 0; i < WORKING_MAX; i++) memory.addTurn(user, 'assistant', `第 ${i} 句`)

  const asked: string[] = []
  const compressed = await memory.compressIfNeeded(user, llmReturning('不该被调用', asked))

  assert.equal(compressed, false)
  assert.equal(asked.length, 0, '没超上限就不该去叫 LLM')
  assert.equal(memory.workingWindow(user).length, WORKING_MAX)
})

test('past the cap the older half moves into the summary and leaves the window', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  for (let i = 0; i < WORKING_MAX + 2; i++) memory.addTurn(user, 'user', `第 ${i} 句`) // 18 条

  const asked: string[] = []
  const compressed = await memory.compressIfNeeded(user, llmReturning('合并后的摘要', asked))

  assert.equal(compressed, true)
  assert.equal(memory.summary(user), '合并后的摘要')
  assert.equal(memory.workingWindow(user).length, 18 - 9, '压进去的是最旧的一半')
  // 压进去的必须是**最旧**的，不是最新的——否则脉络就断了
  assert.match(asked[0], /第 0 句/)
  assert.match(asked[0], /第 8 句/)
  assert.doesNotMatch(asked[0], /第 17 句/)
})

test('a failed compression still keeps the content, in a degraded form', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  for (let i = 0; i < WORKING_MAX + 1; i++) memory.addTurn(user, 'user', `第 ${i} 句`)

  const failing = async () => { throw new Error('LLM 挂了') }
  const compressed = await memory.compressIfNeeded(user, failing)

  assert.equal(compressed, true)
  assert.ok(memory.summary(user).length > 0, '压不了也不能丢：降级成截断拼接')
  assert.match(memory.summary(user), /第 0 句/)
})

test('the summary is capped', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  for (let i = 0; i < WORKING_MAX + 1; i++) memory.addTurn(user, 'user', `第 ${i} 句`)

  await memory.compressIfNeeded(user, llmReturning('长'.repeat(5000)))
  assert.equal(memory.summary(user).length, 800)
})

test('facts are extracted only every N user turns', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  const llm = llmReturning('["喜欢喝茶"]')

  for (let turn = 1; turn < FACT_EVERY; turn++) {
    memory.addTurn(user, 'user', `第 ${turn} 句`)
    assert.equal(await memory.extractFactsIfNeeded(user, llm), 0, `第 ${turn} 轮不该提取`)
  }
  memory.addTurn(user, 'user', `第 ${FACT_EVERY} 句`)
  assert.equal(await memory.extractFactsIfNeeded(user, llm), 1, '到点该提取了')
  assert.equal(memory.facts(user)[0].content, '喜欢喝茶')
})

test('assistant turns do not advance the fact-extraction cadence', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  for (let i = 0; i < FACT_EVERY; i++) memory.addTurn(user, 'assistant', '只有助手在说话')
  assert.equal(await memory.extractFactsIfNeeded(user, llmReturning('["X"]')), 0)
})

test('fact extraction finds the JSON array inside noisy model output', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  for (let i = 0; i < FACT_EVERY; i++) memory.addTurn(user, 'user', '随便说点')

  const noisy = '好的，我提取如下：\n["项目叫 weflow-cli", "周末常去爬山"]\n以上。'
  assert.equal(await memory.extractFactsIfNeeded(user, llmReturning(noisy)), 2)
  assert.deepEqual(memory.facts(user).map(f => f.content), ['项目叫 weflow-cli', '周末常去爬山'])
})

test('a fact that is already covered is not added again', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  memory.addFact(user, '项目叫 weflow-cli')
  for (let i = 0; i < FACT_EVERY; i++) memory.addTurn(user, 'user', '随便说点')

  // "项目叫 weflow-cli 并开源" 与已有事实互相包含 → 去重
  assert.equal(await memory.extractFactsIfNeeded(user, llmReturning('["项目叫 weflow-cli 并开源"]')), 0)
  assert.equal(memory.facts(user).length, 1)
})

test('a failed extraction adds nothing and does not throw', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  for (let i = 0; i < FACT_EVERY; i++) memory.addTurn(user, 'user', '随便说点')

  const boom = async () => { throw new Error('LLM 挂了') }
  assert.equal(await memory.extractFactsIfNeeded(user, boom), 0)
  assert.deepEqual(memory.facts(user), [])
})

test('non-string entries in the model output are ignored', async () => {
  const memory = new AssistantMemory()
  const user = newUser()
  for (let i = 0; i < FACT_EVERY; i++) memory.addTurn(user, 'user', '随便说点')

  assert.equal(await memory.extractFactsIfNeeded(
    user, llmReturning('["喜欢喝茶", 42, "", null, "周末去爬山"]')), 2)
})

test('facts are capped, keeping the newest', () => {
  const memory = new AssistantMemory()
  const user = newUser()
  for (let i = 1; i <= 35; i++) memory.addFact(user, `第 ${i} 号偏好`)

  const facts = memory.facts(user)
  assert.equal(facts.length, 30)
  assert.equal(facts[0].content, '第 6 号偏好', '丢掉的是最旧的')
  assert.equal(facts[29].content, '第 35 号偏好')
})

test('the containment rule also blocks a longer fact that starts with a shorter one', () => {
  // 去重用的是**互相包含**判断。代价：已有「事实 1」时，「事实 10」会被判成重复。
  // 实战里表现为"更具体的那条记不进来"（如已有「项目叫 weflow」，新的
  // 「项目叫 weflow-cli 并开源」也进不来）。写下来是为了下次有人问"我明明说了新事实
  // 却没记住"时有据可查——这是去重规则的代价，不是随机故障。
  const memory = new AssistantMemory()
  const user = newUser()
  memory.addFact(user, '事实 1')
  assert.equal(memory.addFact(user, '事实 10'), false)
  assert.deepEqual(memory.facts(user).map(f => f.content), ['事实 1'])
})

test('addFact refuses duplicates instead of piling them up', () => {
  const memory = new AssistantMemory()
  const user = newUser()
  assert.equal(memory.addFact(user, '喜欢喝茶'), true)
  assert.equal(memory.addFact(user, '喜欢喝茶'), false)
  assert.equal(memory.facts(user).length, 1)
})

test('searchFacts matches the keyword and any word of a multi-word query', () => {
  const memory = new AssistantMemory()
  const user = newUser()
  memory.addFact(user, '项目叫 weflow-cli')
  memory.addFact(user, '喜欢喝茶')

  assert.equal(memory.searchFacts(user, '喝茶').length, 1)
  assert.equal(memory.searchFacts(user, 'weflow 部署').length, 1, '按词命中')
  assert.equal(memory.searchFacts(user, '不存在的词').length, 0)
  assert.deepEqual(memory.searchFacts(user, ''), [], '空关键词不该把全部事实倒出来')
})

test('reset clears that user and leaves the others alone', () => {
  const memory = new AssistantMemory()
  const a = newUser()
  const b = newUser()
  memory.addTurn(a, 'user', '甲的话')
  memory.addFact(a, '甲的事实')
  memory.addTurn(b, 'user', '乙的话')

  memory.reset(a)
  assert.deepEqual(memory.workingWindow(a), [])
  assert.deepEqual(memory.facts(a), [])
  assert.equal(memory.workingWindow(b).length, 1)
})

test('memory survives a restart', () => {
  const user = newUser()
  const first = new AssistantMemory()
  first.addTurn(user, 'user', '重启前说的话')
  first.addFact(user, '重启前记住的事')
  first.save()

  const second = new AssistantMemory()   // 构造函数会 load
  assert.equal(second.workingWindow(user)[0].content, '重启前说的话')
  assert.deepEqual(second.facts(user).map(f => f.content), ['重启前记住的事'])
})
