import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

/**
 * `decide` 是一个**出境调用**（state 由调用方给，会被发到决策模型），所以它要
 * 守住和 `awaiting` / `search` 同一道门；而它的 `--dry-run` 不读本地、不联网，
 * 所以在 CI 里能完整地端到端跑一遍——这是这条命令最值得自动化的部分。
 */
function runCli(home: string, args: string[]) {
  return spawnSync(process.execPath, [
    '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'), ...args,
  ], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USERPROFILE: home },
  })
}

function payload(text: string): any {
  const start = text.indexOf('{')
  if (start < 0) throw new Error(`no JSON payload in output: ${text.slice(0, 200)}`)
  return JSON.parse(text.slice(start))
}

function withHome(run: (home: string) => void): void {
  const home = mkdtempSync(join(tmpdir(), 'weflow-decide-'))
  try {
    run(home)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
}

function writeRequest(home: string, content: unknown): string {
  const path = join(home, 'request.json')
  writeFileSync(path, JSON.stringify(content), 'utf8')
  return path
}

test('decide is discoverable and declares what leaves the machine', () => {
  withHome((home) => {
    const body = payload(runCli(home, ['capabilities', '--json']).stdout)
    const decide = body.primitives?.decide
    assert.ok(decide, 'decide should be registered in capabilities (D-018)')
    assert.equal(decide.invokesAI, true)
    assert.equal(decide.readsLocalData, false)
    assert.equal(decide.confirmationRequired, true)
    assert.equal(decide.sendsCallerProvidedState, true)
    // 这一版刻意不暴露成 MCP 工具：远端客户端不该能驱动本机出境调用。
    assert.equal(decide.mcpExposed, false)
  })
})

test('a dry run validates and reports the shape without any egress', () => {
  withHome((home) => {
    const request = writeRequest(home, {
      state: '一段文本',
      questions: { 甲: { type: 'noul' }, 乙: { type: 'choice', criteria: { a: 'x' } } },
    })
    const result = runCli(home, ['decide', '--request', request, '--dry-run', '--json'])
    const body = payload(result.stdout)
    assert.equal(result.status, 0)
    assert.equal(body.dryRun, true)
    assert.equal(body.questionCount, 2)
    assert.equal(body.readsLocalData, false)
  })
})

test('a malformed request is rejected locally, never sent', () => {
  withHome((home) => {
    // 服务端会回 422，但那条错误说不出"你本来想干什么"——所以必须在本机挡住。
    const request = writeRequest(home, { state: 'x', questions: { 甲: { type: '猜' } } })
    const result = runCli(home, ['decide', '--request', request, '--dry-run', '--json'])
    const body = payload(result.stdout)
    assert.equal(body.code, 'INVALID_REQUEST')
    assert.notEqual(result.status, 0)
  })
})

test('decide refuses to run on a machine path without --yes', () => {
  withHome((home) => {
    const request = writeRequest(home, { state: 'x', questions: { 甲: { type: 'noul' } } })
    const result = runCli(home, ['decide', '--request', request, '--json'])
    const body = payload(result.stdout)
    assert.equal(body.code, 'CONFIRMATION_REQUIRED')
    assert.equal(body.questionCount, 1)
    assert.notEqual(result.status, 0)
  })
})
