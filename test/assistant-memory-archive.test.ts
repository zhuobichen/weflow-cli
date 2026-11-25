/**
 * 压缩归档（2026-11-24，读 pi 的会话模型后加的最小形态）。
 *
 * pi 把「存什么」和「模型看到什么」彻底分离：摘要只是一个**事件**，原始消息一条不删，
 * 拼上下文时才二选一——**摘要错了可以重新压**。
 *
 * 我们原来是就地改写：`s.working = s.working.slice(...)`，压进去的错了，
 * 原始对话就**永久没了**。今天"记忆混进模型编造"那个坑难修，根子就在这里。
 *
 * 这个测试钉住的是：**被压掉的那几轮，原话还在**。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-archive-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantMemory, WORKING_MAX, ARCHIVE_BATCHES } = await import('../src/services/assistantMemory.js')

const fakeLlm = async () => '（摘要）'

test('压缩之后，被压掉的原话能在 archive 里找到', async () => {
  const mem: any = new AssistantMemory()
  const uid = 'archive-user'
  // 塞满到超过窗口，触发压缩
  for (let i = 0; i < WORKING_MAX + 6; i += 1) {
    mem.addTurn(uid, i % 2 === 0 ? 'user' : 'assistant', `第${i}轮的关键内容-A${i}`)
  }
  const before = mem.workingWindow(uid).length
  const compressed = await mem.compressIfNeeded(uid, fakeLlm)
  assert.ok(compressed, '应该触发了压缩')

  const after = mem.workingWindow(uid).length
  assert.ok(after < before, '窗口应该变小')

  const archive = mem.state(uid).archive ?? []
  assert.ok(archive.length > 0, '被压掉的轮次要进 archive')
  const all = archive.flatMap((b: any) => b.turns.map((t: any) => t.content)).join('\n')
  assert.match(all, /关键内容-A0/, '最早那轮的原文应该还能在归档里找到')
})

test('归档只留最近 ARCHIVE_BATCHES 批，不会无限膨胀', async () => {
  const mem: any = new AssistantMemory()
  const uid = 'archive-cap'
  for (let round = 0; round < ARCHIVE_BATCHES + 3; round += 1) {
    for (let i = 0; i < WORKING_MAX + 2; i += 1) {
      mem.addTurn(uid, 'user', `轮${round}-${i}`)
    }
    await mem.compressIfNeeded(uid, fakeLlm)
  }
  const archive = mem.state(uid).archive ?? []
  assert.ok(archive.length <= ARCHIVE_BATCHES,
    `归档批次数 ${archive.length} 超过上限 ${ARCHIVE_BATCHES}`)
})

test('归档不进上下文：提示词里看不到它', () => {
  const mem: any = new AssistantMemory()
  const uid = 'archive-not-in-prompt'
  mem.addTurn(uid, 'user', '这是一句不该出现在提示词归档段的话-XXX')
  // summary 与 facts 才是进上下文的东西，archive 不在其中
  const injected = String(mem.summary(uid) ?? '') + String((mem.facts(uid) ?? []).map((f: any) => f.content).join())
  assert.doesNotMatch(injected, /归档段的话/, 'archive 不该混进 summary 或 facts')
})
