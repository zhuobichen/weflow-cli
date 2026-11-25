/**
 * 命令行路径的记忆改动**必须真的落盘**（2026-11-24 实测踩到的）。
 *
 * `save()` 是手动调用的，`dirtyUsers` 只在 service 收尾流程里被消费。
 * 而 `assistant memory --reset-summary` / `--rebuild-summary` 是**命令行直接调**，
 * 不经过那个收尾流程。
 *
 * 第一版漏了显式 save()：命令报"摘要已清空（原 800 字）"，**再读出来还是 800 字**。
 * 这是最典型的"命令说做了、实际没做"——比报错更难发现。
 *
 * 这条测试用**新实例重读**来验证落盘：只查内存不算数。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-persist-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantMemory, WORKING_MAX } = await import('../src/services/assistantMemory.js')

test('resetSummary 会落盘：新实例重读是空的', async () => {
  const mem: any = new AssistantMemory()
  const uid = 'persist-reset'
  for (let i = 0; i < WORKING_MAX + 4; i += 1) mem.addTurn(uid, 'user', `内容${i}`)
  await mem.compressIfNeeded(uid, async () => '（这是一段摘要）')
  assert.ok(mem.summary(uid).length > 0, '先确认有摘要')
  mem.save()

  mem.resetSummary(uid)

  // **关键：换一个新实例重读**（只查同一个对象的内存不算数——那正是第一版的坑）
  const fresh: any = new AssistantMemory()
  assert.equal(fresh.summary(uid), '', '新实例读到的摘要应该是空的（说明真的落盘了）')
})

test('rebuildSummary 会落盘：新实例重读到的是新摘要', async () => {
  const mem: any = new AssistantMemory()
  const uid = 'persist-rebuild'
  for (let i = 0; i < WORKING_MAX + 4; i += 1) mem.addTurn(uid, 'user', `原始${i}`)
  await mem.compressIfNeeded(uid, async () => '（旧摘要）')
  mem.save()

  const next = await mem.rebuildSummary(uid, async () => '（新摘要-已落盘）')
  assert.ok(next)

  const fresh: any = new AssistantMemory()
  assert.match(fresh.summary(uid), /新摘要-已落盘/, '新实例应读到重建后的摘要')
})
