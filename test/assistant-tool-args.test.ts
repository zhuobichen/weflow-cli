/**
 * 工具参数的**宽松强转**：抹平同一个值的不同 JSON 写法，**不负责"帮模型改对"**。
 *
 * pi 的原文（extensions.md:174）：
 * > Before validation, loosely typed arguments are coerced: numeric strings to numbers,
 * > `"true"` to `true`, a single value to a one-item array, `null` in an optional property dropped.
 *
 * 我们的真实缺口**不在字符串数字上**（`boundedToolInteger` 已用 `Number()` 兜住），
 * 而在 `boolean`：`args.confirm !== true` 遇到 `"true"` 判成"没确认"，
 * `args.with_text === true` 遇到 `"true"` 判成 false。**两种都是静默降级、不报错**——
 * 模型以为用户看到正文了，用户只拿到片段。
 *
 * 注意：这里的字段名取自**真实 TOOL_DEFS**（`search_semantic.with_text`、
 * `get_messages.limit`、`search_semantic.confirm` 等）。第一版我假设了
 * `export_chat.confirm`/`targets`，而它 schema 里根本没有这两个字段——
 * 三条测试直接红了，正好证明这层逻辑确实**只按 schema 转**，不会凭空造字段。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-coerce-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { coerceToolArgs } = await import('../src/services/assistantToolArgs.js')

test('布尔：字符串 "true"/"false" 要转成真布尔', () => {
  const a = coerceToolArgs('search_semantic', { query: 'x', with_text: 'true' })
  assert.equal(a.with_text, true, '"true" 必须转成 true，否则 === true 判成 false（静默降级）')
  const b = coerceToolArgs('search_semantic', { query: 'x', with_text: 'false' })
  assert.equal(b.with_text, false)
  // 已经是布尔的不要动
  assert.equal(coerceToolArgs('search_semantic', { with_text: true }).with_text, true)
})

test('布尔：数字 1/0 也认（模型有时给数字）', () => {
  assert.equal(coerceToolArgs('search_semantic', { with_text: 1 }).with_text, true)
  assert.equal(coerceToolArgs('search_semantic', { with_text: 0 }).with_text, false)
})

test('数字：数字字符串转成数字', () => {
  assert.equal(coerceToolArgs('get_messages', { contact: '甲', limit: '20' }).limit, 20)
  // 非数字字符串保持原样：转不动就不转，让后面的校验去报错
  assert.equal(coerceToolArgs('get_messages', { limit: 'abc' }).limit, 'abc')
  // 带单位的也不转（"20 条" 不是数字）
  assert.equal(coerceToolArgs('get_messages', { limit: '20 条' }).limit, '20 条')
})

test('null：可选属性上的 null 直接丢掉', () => {
  const out = coerceToolArgs('get_messages', { contact: '甲', since: null })
  assert.equal('since' in out, false, '可选属性给 null 等于"没给"，不该留成 String(null)')
  assert.equal(out.contact, '甲')
})

test('schema 里没有的字段：原样保留，不乱转也不删', () => {
  // export_chat 的真实 schema 只有 contact/limit/format，没有 confirm
  const out = coerceToolArgs('export_chat', { contact: '甲', confirm: 'true' })
  assert.equal(out.confirm, 'true', '没声明类型的字段不该被猜着转')
  assert.equal(out.contact, '甲')
})

test('不认识的工具：原样返回，绝不乱转', () => {
  const args = { a: 'true', b: '1', c: null }
  assert.deepEqual(coerceToolArgs('不存在的工具', args), args)
})
