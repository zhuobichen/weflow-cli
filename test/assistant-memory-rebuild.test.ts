/**
 * 摘要可重建（2026-11-24）。
 *
 * 由来：`compressIfNeeded` 就地改写 `s.summary`，而实测发现**模型几轮前的编造会顺着摘要
 * 一路传下去**（脏桶答"联系人A 19:42 某条消息内容"，干净桶答"22:47"）。当时的补丁只是
 * "编造不进窗口"——那只堵住新污染；**已经被压进 summary 的旧污染没有任何办法清掉**，
 * 因为原始对话当时就被删了。
 *
 * 加了 archive 之后，摘要**可以重算**。这条测试钉住"重建真的会换掉旧摘要"。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-rebuild-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantMemory, WORKING_MAX } = await import('../src/services/assistantMemory.js')

test('重建摘要：用归档里的原话重算，替换掉旧摘要', async () => {
  const mem: any = new AssistantMemory()
  const uid = 'rebuild-user'

  // 先制造一次压缩，让 archive 有料
  for (let i = 0; i < WORKING_MAX + 4; i += 1) {
    mem.addTurn(uid, i % 2 === 0 ? 'user' : 'assistant', `原始内容-${i}`)
  }
  // 压缩用一个"会写出被污染摘要"的假 LLM
  await mem.compressIfNeeded(uid, async () => '（旧摘要：里面有编造 19:42 某条消息内容）')
  assert.match(mem.summary(uid), /旧摘要/, '先确认旧摘要已写入')

  // 重建：这次 LLM 返回干净摘要
  let seenPrompt = ''
  const rebuilt = await mem.rebuildSummary(uid, async (msgs: any[]) => {
    seenPrompt = String(msgs?.[0]?.content ?? '')
    return '（新摘要：只有原始对话里真有的内容）'
  })

  assert.ok(rebuilt, '重建应该成功')
  assert.match(mem.summary(uid), /新摘要/, '摘要被替换')
  assert.doesNotMatch(mem.summary(uid), /旧摘要/, '旧摘要不再存在')
  assert.match(seenPrompt, /原始内容-0/, '重算时喂进的是归档里的原话')
})

test('没有归档时重建返回 null，不动现有摘要', async () => {
  const mem: any = new AssistantMemory()
  const uid = 'rebuild-empty'
  mem.addTurn(uid, 'user', '还没压缩过')
  const before = mem.summary(uid)
  const rebuilt = await mem.rebuildSummary(uid, async () => '不该被用到')
  assert.equal(rebuilt, null)
  assert.equal(mem.summary(uid), before, '摘要不该被改动')
})

test('archiveTurnCount 报出归档里有几轮原话', async () => {
  const mem: any = new AssistantMemory()
  const uid = 'rebuild-count'
  for (let i = 0; i < WORKING_MAX + 6; i += 1) mem.addTurn(uid, 'user', `t${i}`)
  await mem.compressIfNeeded(uid, async () => '（摘要）')
  assert.ok(mem.archiveTurnCount(uid) > 0, '压缩后归档里应该有原话')
})
