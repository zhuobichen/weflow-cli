/**
 * 守卫补问**撞轮次上限**时，不许用占位文案覆盖原答复（2026-11-24）。
 *
 * 由来：读 pi 的 core loop 时对照发现的。
 * `handleMessage` 的守卫段原来无条件 `reply = second.reply`，
 * 而 `second.outcome` 可能是 `'rounds-exhausted'`——那时 `reply` 是
 * 「这轮处理太复杂了…」这类占位文案。拿它覆盖一句**已经说出口的真答复**，
 * 等于为了纠正一个小毛病，把整个回答换成了错误提示。
 *
 * 守卫的原则是"纠错，不是否决"（源码里那句话）。补问没跑完整 = 纠错没成功，
 * **保留原答复**才符合这条原则。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-keep-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantService } = await import('../src/services/assistantService.js')

const REAL_ANSWER = '联系人A最后一条消息是 11月23日 22:47 发的，内容被严格模式屏蔽了。'

test('补问撞上限：保留原答复，不被占位文案覆盖', async () => {
  const svc: any = new AssistantService()
  let calls = 0
  // 第一次正常答（且触发守卫），第二次补问撞上限
  svc.runReactLoop = async () => {
    calls += 1
    if (calls === 1) {
      return { reply: '已为你整合最近3天的日报内容。' + REAL_ANSWER, toolCalls: 0, outcome: 'answered' }
    }
    return { reply: '(这轮处理太复杂了, 换个问法试试?)', toolCalls: 0, outcome: 'rounds-exhausted' }
  }
  svc.maybeLexiconRoute = async () => 0
  svc.maybeFastRoute = async () => 0

  const reply = String(await svc.handleMessage('keep-answer-user', '联系人A最后一条消息是什么', 'text'))
  assert.ok(!/这轮处理太复杂/.test(reply),
    '占位文案不该覆盖原答复，实际得到：' + reply.slice(0, 120))
  assert.ok(/联系人A|22:47/.test(reply), '原答复里的有用信息要保留')
})
