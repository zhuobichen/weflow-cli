/**
 * "面板用哪个记忆桶" —— **这决定了它跟微信里那个是不是同一个大脑**。
 *
 * 断言的是**返回的那个值**，不是"界面说了哪句话"。这样"界面上要如实说用的是哪个桶"
 * 这件事才有东西可测：界面读的就是这里返回值里的 `note` 与 `userId`。
 *
 * 三条分支的后果不一样：
 * - 恰好一条白名单 → 共用（那正是坐在这台机器前的人）；
 * - 零条 → 只能用自己的桶，且**必须说出来**：用户以为在共用一个大脑，实际没有，是这条设计里
 *   最容易让人踩的脚枪；
 * - 两条以上 → **不猜**。猜错等于把对话记到一个不是你的桶里。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { resolvePanelUserId, PANEL_FALLBACK_BUCKET } = await import('../src/panel/userId.js')

test('白名单恰好一条 → 就用它，标记为共用', () => {
  const r = resolvePanelUserId({ whitelist: 'wxid_me', configured: '' })
  assert.equal(r.userId, 'wxid_me')
  assert.equal(r.shared, true)
  assert.equal(r.needsAnswer, false)
  assert.match(r.note, /共用一个大脑/)
})

test('白名单为空 → 落到自己的桶，且**要求界面问一次**', () => {
  const r = resolvePanelUserId({ whitelist: '', configured: '' })
  assert.equal(r.userId, PANEL_FALLBACK_BUCKET)
  assert.equal(r.shared, false)
  assert.equal(r.needsAnswer, true)
  assert.match(r.note, /分开/, '要说清它是分开的记忆，别让人以为共用了')
})

test('白名单两条以上 → 不猜，要求问一次，并把候选列出来', () => {
  const r = resolvePanelUserId({ whitelist: 'wxid_a, wxid_b', configured: '' })
  assert.equal(r.needsAnswer, true)
  assert.deepEqual(r.candidates, ['wxid_a', 'wxid_b'])
  assert.equal(r.shared, false)
})

test('显式配置永远优先，且不再问', () => {
  const r = resolvePanelUserId({ whitelist: 'wxid_a, wxid_b', configured: 'wxid_b' })
  assert.equal(r.userId, 'wxid_b')
  assert.equal(r.needsAnswer, false)
  assert.equal(r.shared, true)
})

test('显式配置成白名单外的人：照用，但如实说是分开的记忆', () => {
  const r = resolvePanelUserId({ whitelist: 'wxid_a', configured: 'panel' })
  assert.equal(r.userId, 'panel')
  assert.equal(r.shared, false)
  assert.match(r.note, /分开/)
})

test('分隔符与空白都认（逗号/分号/空格，和助手白名单同一套解析）', () => {
  assert.equal(resolvePanelUserId({ whitelist: ' wxid_me ', configured: '' }).userId, 'wxid_me')
  assert.equal(resolvePanelUserId({ whitelist: 'wxid_a;wxid_b', configured: '' }).candidates.length, 2)
  assert.equal(resolvePanelUserId({ whitelist: 'wxid_a  wxid_b', configured: '' }).candidates.length, 2)
})

test('undefined / 非字符串不会炸（配置缺省时就是这样）', () => {
  assert.equal(resolvePanelUserId({}).userId, PANEL_FALLBACK_BUCKET)
  assert.equal(resolvePanelUserId({ whitelist: undefined, configured: undefined }).needsAnswer, true)
  assert.equal(resolvePanelUserId({ whitelist: null as any, configured: null as any }).userId, PANEL_FALLBACK_BUCKET)
})
