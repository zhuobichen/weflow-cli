/**
 * 图片怎么进请求体（`toApiMessages`）。
 *
 * 加图片的风险不在"发得出去"，在**发不出去的时候看起来像发出去了**：
 * - 展开漏一条路径，模型看不见图却照答，下游无法区分"没图"与"图没送到"；
 * - strict 模式（第三方聊天正文不出境）本该拦住图片，漏一层就是聊天图片出境；
 * - `images` 不是 API 字段，忘了剥掉就是多塞一个私有字段给第三方。
 *
 * 所以这些用例盯四件事：不带的原样透传、带的正斜展开成多模态数组、不许时**留话说**而不是
 * 静默少发、以及 `images` 绝不出现在请求体里。纯函数，不联网、不读配置。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { toApiMessages } = await import('../src/services/assistantService.js')

const img = (b64: string, mime = 'image/jpeg', localId?: number) => ({ b64, mime, localId })

test('不带图片的消息原样透传（连对象都不复制）', () => {
  const messages = [
    { role: 'system' as const, content: '系统提示' },
    { role: 'user' as const, content: '你好' },
    { role: 'tool' as const, content: '(结果)', tool_call_id: 'call_1' },
  ]
  const { messages: out, imagesSent, imagesDropped } = toApiMessages(messages, true)

  assert.equal(out.length, 3)
  assert.equal(out[0], messages[0], '不带图片的消息不该被重新构造')
  assert.equal(imagesSent, 0)
  assert.equal(imagesDropped, 0)
})

test('带图片时展开成多模态数组：先文本，再各张图', () => {
  const { messages: out, imagesSent } = toApiMessages(
    [{ role: 'user', content: '看看这两张', images: [img('AAA', 'image/png', 11), img('BBB')] }], true)

  assert.equal(imagesSent, 2)
  const content = out[0].content as any
  assert.ok(Array.isArray(content), 'content 应该是数组')
  assert.deepEqual(content[0], { type: 'text', text: '看看这两张' })
  assert.deepEqual(content[1], { type: 'image_url', image_url: { url: 'data:image/png;base64,AAA' } })
  assert.deepEqual(content[2], { type: 'image_url', image_url: { url: 'data:image/jpeg;base64,BBB' } })
})

test('tool 消息带图片时 tool_call_id 不能丢', () => {
  const { messages: out } = toApiMessages(
    [{ role: 'tool', content: '(看完了)', tool_call_id: 'call_9', images: [img('AAA')] }], true)

  assert.equal(out[0].tool_call_id, 'call_9')
  assert.ok(Array.isArray(out[0].content))
})

test('不许发图时留在正文里说清，而不是静默少发', () => {
  const { messages: out, imagesSent, imagesDropped } = toApiMessages(
    [{ role: 'user', content: '看看这张', images: [img('AAA'), img('BBB')] }], false)

  assert.equal(imagesSent, 0)
  assert.equal(imagesDropped, 2)
  assert.equal(typeof out[0].content, 'string')
  assert.match(out[0].content as string, /看看这张/)
  assert.match(out[0].content as string, /2 张图片未随本轮发出/)
})

test('images 绝不出现在请求体里（它不是 API 字段）', () => {
  const { messages: out } = toApiMessages(
    [{ role: 'user', content: 'x', images: [img('AAA', 'image/jpeg', 7)] }], true)
  // 两种情形都要剥掉：发出去的、和被拦下的
  const blocked = toApiMessages([{ role: 'user', content: 'x', images: [img('AAA')] }], false)
  for (const m of [out[0], blocked.messages[0]]) {
    assert.equal(JSON.stringify(m).includes('"images"'), false, 'JSON 里不该有 images 键')
    assert.equal('images' in m, true, '字段在对象里但值为 undefined（JSON 会丢掉）')
    assert.equal(m.images, undefined)
  }
})

test('审计数字对得上：几张图、哪几条消息', () => {
  const { imagesSent } = toApiMessages([
    { role: 'user', content: 'a', images: [img('A', 'image/jpeg', 31)] },
    { role: 'assistant', content: '嗯' },
    { role: 'user', content: 'b', images: [img('B', 'image/jpeg', 32)] },
  ], true)
  assert.equal(imagesSent, 2)
})

test('空 images 数组按"没有图片"处理', () => {
  const msg = { role: 'user' as const, content: 'x', images: [] as any[] }
  const { messages: out, imagesSent, imagesDropped } = toApiMessages([msg], true)
  assert.equal(out[0], msg, '空数组不该被当成有图片')
  assert.equal(imagesSent, 0)
  assert.equal(imagesDropped, 0)
})
