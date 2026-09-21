import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

/**
 * `awaiting` 读的是**聊天正文**，比日报的文章正文敏感一档，所以它必须和 `search`
 * 守同一条规矩：--dry-run 预览、--yes 才真跑。这些测试盯的就是这道门有没有关上，
 * 以及它有没有按 D-018 出现在 capabilities 里。
 *
 * 真跑一次需要本机微信数据与配置，所以这里**只**验证不需要数据的那部分：
 * 注册、以及未确认时会被拦住。预览的实际行为（报出会话数与字符数）是本地验证过的。
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

function payload(stdout: string): any {
  const start = stdout.indexOf('{')
  if (start < 0) throw new Error(`no JSON payload in output: ${stdout.slice(0, 200)}`)
  return JSON.parse(stdout.slice(start))
}

function withHome(run: (home: string) => void): void {
  const home = mkdtempSync(join(tmpdir(), 'weflow-awaiting-'))
  try {
    run(home)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
}

test('awaiting is discoverable and declares that it sends chat text outward', () => {
  withHome((home) => {
    const body = payload(runCli(home, ['capabilities', '--json']).stdout)
    const awaiting = body.read?.awaiting
    assert.ok(awaiting, 'awaiting should be registered in capabilities (D-018)')
    assert.equal(awaiting.readsLocalChat, true)
    assert.equal(awaiting.invokesAI, true)
    assert.equal(awaiting.writesNothing, true)
    assert.equal(awaiting.confirmationRequired, true)
    assert.ok(awaiting.preview, 'the preview command should be advertised')
  })
})

test('awaiting refuses to run from a machine path without --yes', () => {
  withHome((home) => {
    const result = runCli(home, ['awaiting', '--json'])
    const body = payload(result.stdout)
    assert.equal(body.success, false)
    assert.equal(body.code, 'CONFIRMATION_REQUIRED')
    assert.notEqual(result.status, 0)
  })
})

test('the confirmation gate comes before any chat content is read', () => {
  // 拦住它的那一步不该产生任何子进程或读取——否则"要确认"就只是句客气话。
  withHome((home) => {
    const result = runCli(home, ['awaiting', '--json'])
    assert.ok(!result.stdout.includes('会话'),
      `refusal should not carry scan output: ${result.stdout.slice(0, 200)}`)
    assert.ok(!result.stderr.includes('Traceback'),
      'refusal should not have run the Python script')
  })
})
