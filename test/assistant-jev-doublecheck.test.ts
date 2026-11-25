/**
 * 双重判断守卫（D-191）：Jev 保底说要用工具，模型却没调、还给了具体事实 → 顶回去。
 *
 * 关键边界（D-190 的教训）：诚实拒绝**不拦**——上次"on 模式逼用户同意查"反而更差。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { unfulfilledJevSuggestionPushback } from '../src/services/assistantGuard.js'
import { hasConcreteLocalClaim } from '../src/services/assistantAttribution.js'

test('具体本机事实：时间戳 / 数量 / 引号原文都认', () => {
  assert.equal(hasConcreteLocalClaim('甲最后一条是11月23日22:47发的'), true)
  assert.equal(hasConcreteLocalClaim('[11/23 22:47] 甲: 收到'), true)
  assert.equal(hasConcreteLocalClaim('你一共有 15 个会话'), true)
  assert.equal(hasConcreteLocalClaim('原文是「收到，我今晚看」'), true)
})

test('诚实退路不算具体事实（不拦）', () => {
  for (const text of ['我不知道', '查不到', '你告诉我名字我再查', '我手上没有甲的聊天记录']) {
    assert.equal(hasConcreteLocalClaim(text), false, `诚实答复不该被当作具体事实: ${text}`)
  }
})

test('全局正则 lastIndex 不串味（连续调用同一句都判 true）', () => {
  const text = '甲是11月23日22:47发的'
  assert.equal(hasConcreteLocalClaim(text), true)
  assert.equal(hasConcreteLocalClaim(text), true, '第二次调用必须同样判 true')
})

test('三重条件缺一不可', () => {
  const suggested = ['get_messages', 'get_todos']
  // Jev 没建议 → 不拦
  assert.equal(unfulfilledJevSuggestionPushback({ reply: '甲22:47发的', toolCalls: 0, suggested: [], hasConcreteClaim: true }), null)
  // 调了工具 → 不拦（模型做了第二次判断）
  assert.equal(unfulfilledJevSuggestionPushback({ reply: '甲22:47发的', toolCalls: 2, suggested, hasConcreteClaim: true }), null)
  // 没给具体事实（诚实） → 不拦
  assert.equal(unfulfilledJevSuggestionPushback({ reply: '我不知道', toolCalls: 0, suggested, hasConcreteClaim: false }), null)
})

test('三重条件全中 → 顶回去，且消息里说明参数由模型自己填', () => {
  const p = unfulfilledJevSuggestionPushback({
    reply: '甲最后一条是11月23日22:47发的', toolCalls: 0,
    suggested: ['get_messages', 'get_todos'], hasConcreteClaim: true,
  })
  assert.ok(p)
  assert.equal(p.mechanism, 'unfulfilled-jev-suggestion')
  assert.match(p.message, /参数由你自己/)
  assert.match(p.message, /查不到.*说明/, '必须给诚实退路')
})
