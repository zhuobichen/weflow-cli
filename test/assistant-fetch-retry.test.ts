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

/**
 * `fetchWithRetry` 现在在 `assistantEngine.ts` 里**独立导出**（2026-11-24 抽取）。
 *
 * 原来这里是 `proto.fetchWithRetry.call(svc, ...)` —— 取私有方法的测法。
 * 抽成独立模块之后直接 import 就对，而且这正是抽取的好处之一：
 * **这些判据本来就不需要 `AssistantService` 实例**，之前靠"取私有方法"才测得到。
 */
const { fetchWithRetry } = await import('../src/services/assistantEngine.js')

function callFetchWithRetry(_svc: any, fakeFetch: any, attempts = 3, signal?: AbortSignal) {
  const original = globalThis.fetch
  globalThis.fetch = fakeFetch
  return fetchWithRetry('http://x/chat/completions', signal ? { signal } : {}, attempts)
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

// ---------------------------------------------------------------- 2026-11-24 补：退避与 Retry-After

test('退避期间可以被取消——不接 signal 等于用户点停止没有落点', async () => {
  // AGENTS.md 硬性门禁第 2 条。原来退避是裸 setTimeout，signal 只作用在 fetch 上，
  // 那几秒里取消无效。
  const svc = new AssistantService()
  const ctrl = new AbortController()
  const started = Date.now()
  let calls = 0
  const running = callFetchWithRetry(svc, async () => {
    calls += 1
    return { status: 429, ok: false, headers: new Headers(), text: async () => '', json: async () => ({}) }
  }, 3, ctrl.signal)
  setTimeout(() => ctrl.abort(), 120)
  await assert.rejects(() => running, /取消|abort/i)
  const elapsed = Date.now() - started
  assert.ok(elapsed < 1500, `取消后该立刻返回，实际等了 ${elapsed}ms`)
  assert.equal(calls, 1, '取消之后不该再发下一次请求')
})

test('听服务端的 Retry-After，而不是固守自己的退避', async () => {
  const svc = new AssistantService()
  let calls = 0
  const started = Date.now()
  const res = await callFetchWithRetry(svc, async () => {
    calls += 1
    if (calls === 1) {
      const h = new Headers()
      h.set('retry-after', '0')   // 服务端说立刻可重试
      return { status: 429, ok: false, headers: h, text: async () => '', json: async () => ({}) }
    }
    return { status: 200, ok: true, headers: new Headers(), text: async () => '', json: async () => ({}) }
  })
  assert.equal((res as any).status, 200)
  assert.ok(Date.now() - started < 400, 'Retry-After: 0 应当几乎不等待（自己的退避是 ~500ms）')
})

test('408 / 409 也算可重试', async () => {
  for (const status of [408, 409]) {
    let calls = 0
    const svc = new AssistantService()
    const res = await callFetchWithRetry(svc, async () => {
      calls += 1
      if (calls < 2) return { status, ok: false, headers: new Headers(), text: async () => '', json: async () => ({}) }
      return { status: 200, ok: true, headers: new Headers(), text: async () => '', json: async () => ({}) }
    })
    assert.equal(calls, 2, `HTTP ${status} 应该重试一次`)
    assert.equal((res as any).status, 200)
  }
})
