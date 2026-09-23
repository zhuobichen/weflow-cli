/**
 * 端点文件：**发现机制 + 凭据**，以及"什么时候必须当作不存在"。
 *
 * 这里的失败方式全是静默的：读出一个崩溃残留的端点，面板就会连到一个没人应答的端口；
 * 认了不认识的文件版本，就会拿到一堆 `undefined` 拼出来的地址。
 * 所以纪律是**任何一种不可信都返回 null**，绝不返回一个"大概是它"的对象。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-panel-endpoint-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const ep = await import('../src/panel/endpoint.js')

function good(overrides: Record<string, unknown> = {}) {
  return {
    version: ep.PANEL_ENDPOINT_VERSION,
    service: ep.PANEL_SERVICE_ID,
    port: 8766,
    token: 'tok',
    pid: process.pid,          // 本进程一定活着
    startedAt: new Date().toISOString(),
    channel: 'local' as const,
    memoryBucket: 'panel',
    ...overrides,
  }
}

function writeRaw(value: unknown): void {
  mkdirSync(join(HOME, '.weflow-cli'), { recursive: true })
  writeFileSync(ep.endpointFile(), typeof value === 'string' ? value : JSON.stringify(value), 'utf8')
}

test.beforeEach(() => { ep.clearEndpoint() })

test('写进去再读出来是同一份（原子写，落盘的不是 .tmp）', () => {
  assert.equal(ep.writeEndpoint(good()), true)
  assert.equal(existsSync(ep.endpointFile() + '.tmp'), false, '不该留下 .tmp')
  const back = ep.readEndpoint()
  assert.equal(back?.port, 8766)
  assert.equal(back?.memoryBucket, 'panel')
})

test('文件不存在 → null（不是抛，也不是空对象）', () => {
  assert.equal(ep.readEndpoint(), null)
})

test('坏 JSON → null', () => {
  writeRaw('{ 这不是 json')
  assert.equal(ep.readEndpoint(), null)
})

test('版本不认识 → null（不猜、不迁移）', () => {
  writeRaw(good({ version: 99 }))
  assert.equal(ep.readEndpoint(), null)
})

test('service 不是我们 → null（防止把别人的端口当成助手）', () => {
  writeRaw(good({ service: 'weflow-daily-reader' }))
  assert.equal(ep.readEndpoint(), null)
})

test('端口不是合法整数 → null', () => {
  for (const port of [0, 70000, 1.5, '8766']) {
    writeRaw(good({ port }))
    assert.equal(ep.readEndpoint(), null, `port=${port} 不该通过`)
  }
})

test('进程已经没了 → null（崩溃残留是靠探活兜的，不是靠删文件）', () => {
  // 这条对应"退出时不删文件"那个事实：`stopDaemon` 发的是 SIGTERM，Node 默认不跑清理，
  // 所以文件一定会残留。**正确做法是探活**，不是指望清理。
  writeRaw(good({ pid: 999999999 }))
  assert.equal(ep.readEndpoint(), null)
})

test('每一轮生成的 token 都不一样，且足够长', () => {
  const a = ep.generateToken()
  const b = ep.generateToken()
  assert.notEqual(a, b)
  assert.ok(a.length >= 32, `太短了：${a.length}`)
  assert.match(a, /^[A-Za-z0-9_-]+$/, 'base64url，不该带 = 或 /')
})

test('token 比较：长度不等不抛异常，只是不匹配', () => {
  const tok = ep.generateToken()
  assert.equal(ep.tokenMatches(tok, tok), true)
  assert.equal(ep.tokenMatches(tok, tok + 'x'), false, '长度不等——timingSafeEqual 会抛，所以要先比长度')
  assert.equal(ep.tokenMatches(tok, tok.slice(0, -1)), false)
  assert.equal(ep.tokenMatches(tok, undefined), false)
  assert.equal(ep.tokenMatches(tok, 12345), false)
  assert.equal(ep.tokenMatches(tok, ''), false)
})

test('写盘用的是 0600 —— 但 Windows 上这是空操作，别把它当安全保证', () => {
  // 注释存在的意义大于断言：Windows 上 Node 的 chmod 只能映射只读位，
  // 真正起作用的是 %USERPROFILE%\.weflow-cli\ 的默认 ACL。所以这里只在非 Windows 上断言。
  ep.writeEndpoint(good())
  const raw = readFileSync(ep.endpointFile(), 'utf8')
  assert.match(raw, /"token"/, '文件里当然有 token —— 它是给本机客户端读的')
  if (process.platform !== 'win32') {
    assert.equal(statSync(ep.endpointFile()).mode & 0o777, 0o600)
  }
})
