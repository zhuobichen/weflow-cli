/**
 * `usage` 记录（2026-11-24，读 pi 的 ai-provider 后补的）。
 *
 * 原来 `envelope.usage` **被整条丢弃**——`DAILY_LIMIT` 按消息条数计，
 * 而"一百条"和"一百条各带 2 万 token"成本差几个数量级，护栏对烧钱是失真的。
 *
 * **2026-11-24 抽取后改法**：`usageFromEnvelope` 现在在 `assistantEngine.ts` 里独立导出，
 * 测试直接 import —— 原来靠 `svc.recordUsage(...)` 取私有方法，抽取让它变成了
 * 一个正经的纯函数。
 *
 * 这条测试钉的是**口径**，不是数字：各家 provider 的 usage 字段位置不同，
 * 我们要能吞下三种形态且不抛错。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-usage-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { usageFromEnvelope } = await import('../src/services/assistantEngine.js')

const read = (usage: any) => usageFromEnvelope({ usage })

test('OpenAI 形态：prompt/completion + 嵌套 details', () => {
  const u = read({
    prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500,
    prompt_tokens_details: { cached_tokens: 800 },
    completion_tokens_details: { reasoning_tokens: 120 },
  })
  assert.ok(u)
  assert.equal(u!.input, 1200)
  assert.equal(u!.cached, 800)
  assert.equal(u!.output, 300)
  assert.equal(u!.reasoning, 120)
  assert.equal(u!.total, 1500)
})

test('DeepSeek 形态：顶层 prompt_cache_hit_tokens', () => {
  const u = read({
    prompt_tokens: 900, completion_tokens: 200, total_tokens: 1100,
    prompt_cache_hit_tokens: 640,
  })
  assert.equal(u!.cached, 640, 'DeepSeek 的缓存命中在顶层')
})

test('Anthropic 形态：input/output + cache_read_input_tokens', () => {
  const u = read({ input_tokens: 500, output_tokens: 100, cache_read_input_tokens: 400 })
  assert.equal(u!.input, 500)
  assert.equal(u!.output, 100)
  assert.equal(u!.cached, 400)
})

test('没有 usage 时返回 null，不抛错', () => {
  assert.equal(usageFromEnvelope(undefined), null)
  assert.equal(usageFromEnvelope({}), null)
  assert.equal(usageFromEnvelope({ usage: null }), null)
})

test('缺字段只留 undefined，不编 0', () => {
  const u = read({ total_tokens: 100 })
  assert.equal(u!.total, 100)
  assert.equal(u!.input, undefined, '没给 input 就是 undefined，别编成 0')
  assert.equal(u!.output, undefined)
})
