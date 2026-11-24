/**
 * 网络层重试（2026-11-24）。
 *
 * 由来：评测 `--repeats 3` 连跑三轮，绕不开的失败全是
 * 「The operation was aborted due to timeout」「fetch failed」——**与模型能力无关**，
 * 却把整条用例判成失败，报告读起来像"模型退化了"。
 *
 * 关键是**区分**：可重试的（连接层、超时、429、5xx）退避重试；不可重试的
 * （401/403 key 不对、400 请求错、404 模型名错）立即失败——重试只会把同一个错误
 * 重复三次还多花时间。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-retry-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const svcMod = await import('../src/services/assistantService.js')
const { AssistantService } = svcMod as any

/** 取私有方法：这些判据是纯函数，值得单独钉住 */
const proto = AssistantService.prototype as any

function callFetchWithRetry(svc: any, fakeFetch: any, attempts = 3) {
  const original = globalThis.fetch
  globalThis.fetch = fakeFetch
  return proto.fetchWithRetry.call(svc, 'http://x/chat/completions', {}, attempts)
    .finally(() => { globalThis.fetch = original })
}

test('连接层错误要重试，成功即返回', async () => {
  let calls = 0
  const svc = new AssistantService()
  const res = await callFetchWithRetry(svc, async () => {
    calls += 1
    if (calls < 3) throw Object.assign(new TypeError('fetch failed'), { cause: { code: 'ECONNRESET' } })
    return { status: 200, ok: true, text: async () => '', json: async () => ({}) }
  })
  assert.equal(calls, 3, '前两次失败要重试，第三次成功')
  assert.equal((res as any).status, 200)
})

test('超时（TimeoutError）要重试', async () => {
  let calls = 0
  const svc = new AssistantService()
  await callFetchWithRetry(svc, async () => {
    calls += 1
    if (calls === 1) throw Object.assign(new Error('aborted'), { name: 'TimeoutError' })
    return { status: 200, ok: true, text: async () => '', json: async () => ({}) }
  })
  assert.equal(calls, 2)
})

test('用户主动取消（AbortError）**不**重试——重试等于无视取消', async () => {
  let calls = 0
  const svc = new AssistantService()
  await assert.rejects(
    () => callFetchWithRetry(svc, async () => {
      calls += 1
      throw Object.assign(new Error('aborted'), { name: 'AbortError' })
    }),
    /aborted/,
  )
  assert.equal(calls, 1, '取消只该调一次')
})

test('401/403/400/404 **不**重试：重试只是把同一个错误重复三次', async () => {
  for (const status of [400, 401, 403, 404]) {
    let calls = 0
    const svc = new AssistantService()
    const res = await callFetchWithRetry(svc, async () => {
      calls += 1
      return { status, ok: false, text: async () => 'err', json: async () => ({}) }
    })
    assert.equal((res as any).status, status)
    assert.equal(calls, 1, `HTTP ${status} 不该重试`)
  }
})

test('429 / 5xx 要重试（限流与服务器抖动都是临时性的）', async () => {
  for (const status of [429, 500, 503]) {
    let calls = 0
    const svc = new AssistantService()
    const res = await callFetchWithRetry(svc, async () => {
      calls += 1
      if (calls < 2) return { status, ok: false, text: async () => 'busy', json: async () => ({}) }
      return { status: 200, ok: true, text: async () => '', json: async () => ({}) }
    })
    assert.equal(calls, 2, `HTTP ${status} 应该重试一次`)
    assert.equal((res as any).status, 200)
  }
})

test('重试耗尽：把最后一次的错误抛出去，不吞掉', async () => {
  const svc = new AssistantService()
  await assert.rejects(
    () => callFetchWithRetry(svc, async () => { throw new TypeError('fetch failed') }),
    /fetch failed/,
  )
})
