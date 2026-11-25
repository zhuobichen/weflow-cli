/**
 * D-205：工具明确说"未找到"，答复却给出具体标题/数字/来源 → 顶回去。
 * 真实事故：read_favorite 返回未找到，模型却编了整篇摘要（含 2.5~3.8 倍等），
 * 下一轮又说"没有这篇文章"——前后冲突、严重幻觉。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { fabricatedAfterNotFound } from '../src/services/assistantGuard.js'

test('编造标题+数字 → 拦', () => {
  const r = fabricatedAfterNotFound({
    reply: '我已调用 read_favorite 读取了《大模型推理成本与性能的权衡》。摘要：参数翻倍成本增加 2.5～3.8 倍。',
    toolText: '(收藏中未找到「大模型推理成本与性能的权衡」)',
  })
  assert.ok(r)
  assert.equal(r.mechanism, 'fabricated-after-not-found')
})

test('编造来源 → 拦', () => {
  const r = fabricatedAfterNotFound({
    reply: '你最近收藏的包括：《如何评估大模型表现》（来源：科技洞察）。',
    toolText: '(收藏中未找到相关收藏)',
  })
  assert.ok(r, '书名号标题+来源标注应被拦')
})

test('诚实说没找到 → 不拦', () => {
  const r = fabricatedAfterNotFound({
    reply: '你的收藏里没有找到这篇文章。',
    toolText: '(收藏中未找到「大模型推理成本与性能的权衡」)',
  })
  assert.equal(r, null)
})

test('工具没说未找到 → 不拦（哪怕答复有数字）', () => {
  const r = fabricatedAfterNotFound({
    reply: '你收藏了 12 条，成本增加 2.5 倍。',
    toolText: '共1564条, 前8条:\n· 某文章 (某号) — 摘要',
  })
  assert.equal(r, null, '工具给了内容时不该触发这条')
})

test('空工具结果 → 不拦', () => {
  assert.equal(fabricatedAfterNotFound({ reply: '《某标题》2.5倍', toolText: '' }), null)
})
