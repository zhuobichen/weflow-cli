/**
 * 关键回归：**撞上限时守卫与兜底都不许跑**。
 *
 * 前面那两个测试只证明了 `outcome` 被正确赋值。但真正的 bug 是"守卫拿占位文案去修"，
 * 所以必须证明**调用方真的用了 `answered` 这个信号**。
 *
 * 造法：让 `runReactLoop` 永远撞上限（每轮都只调工具），同时让占位文案**含承诺词**
 * （`impossibleOffer` 会命中）。如果调用方没挡住，`stripImpossibleOffers` 就会把它换成
 * "这件事我做不到…"——那正是修复前会发生的错误形态。修复后，答复必须**原样**是占位文案。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-outcome2-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantService } = await import('../src/services/assistantService.js')
const { impossibleOffer } = await import('../src/services/assistantGuard.js')

/**
 * 占位文案的**变体**：故意含承诺词，模拟"哪天文案一改就炸"的情况。
 *
 * 这一句是实测出来的：`impossibleOffer` 对它会返回"导出（本产品只能导出聊天记录…）"。
 * 第一版我随手写的「要不要我帮你拆成几步？」其实是 `null`——**夹具无效**，
 * 那条测试会永远绿。所以下面有一句显式断言钉住"这个夹具确实会触发"，
 * 免得哪天判词改了、测试悄悄变成空转。
 */
const PROMISING_PLACEHOLDER = '这轮处理太复杂了，要我帮你导出一下吗？'

test('撞上限 + 占位文案含承诺词：答复必须原样保留，不许被兜底改写', async () => {
  // 先确认这个占位文案**确实**会被判成承诺（否则这条测试没意义）
  assert.ok(impossibleOffer(PROMISING_PLACEHOLDER),
    '夹具无效：这段占位文案应该被判成承诺，否则测不出问题')

  const svc: any = new AssistantService()
  // 让 runReactLoop 永远撞上限，且占位文案是含承诺词的那个
  svc.runReactLoop = async () => ({
    reply: PROMISING_PLACEHOLDER,
    toolCalls: 0,
    outcome: 'rounds-exhausted',
  })
  svc.maybeLexiconRoute = async () => 0
  svc.maybeFastRoute = async () => 0

  const reply = await svc.handleMessage('outcome-guard-user', '随便问点什么', 'text')

  assert.equal(reply, PROMISING_PLACEHOLDER,
    '撞上限时的占位文案不该被"删掉做不到的提议"改写——那不是模型说的话')
  assert.doesNotMatch(reply, /这件事我做不到/,
    '兜底逻辑不许碰占位文案')
})
