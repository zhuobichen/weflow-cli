import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

/**
 * `draft` 会把一段**聊天正文**发给判断模型与生成模型，所以它和 `awaiting` 一样必须
 * 预览—确认两道门。这里盯的是那道门，以及它在 capabilities 里如实把"不发送"说出来。
 *
 * 真跑一次需要本机微信数据、两个 key，还会花钱，所以这里**只**验证不需要数据和 key 的部分：
 * 注册、以及没确认时会被拦住。预览与真跑的实测（本机真跑过一次，读过三条候选）在
 * `docs/PROJECT_STATE.md` 里如实记着。
 *
 * 顺带钉住一条与助手工具那边**同样的**规矩：拒绝要发生在**读正文之前**。
 * 只是"嘴上说要确认"，实际已经读过库、发过请求，那不是门。
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
  const home = mkdtempSync(join(tmpdir(), 'weflow-draft-'))
  try {
    run(home)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
}

test('draft is discoverable and declares what it reads, invokes, and does NOT send', () => {
  withHome((home) => {
    const body = payload(runCli(home, ['capabilities', '--json']).stdout)
    const draft = body.read?.draft
    assert.ok(draft, 'draft should be registered in capabilities (D-018)')
    assert.equal(draft.readsLocalChat, true)
    assert.equal(draft.invokesAI, true)
    assert.equal(draft.writesNothing, true)
    assert.equal(draft.confirmationRequired, true)
    // 这条比 writesNothing 更要紧：它是机器可读的"绝不发送"
    assert.equal(draft.sendsNothing, true)
    assert.ok(draft.preview, 'the preview command should be advertised')
  })
})

test('draft refuses to run from a machine path without --yes', () => {
  withHome((home) => {
    const result = runCli(home, ['draft', '某某', '--json'])
    const body = payload(result.stdout)
    assert.equal(body.success, false)
    assert.equal(body.code, 'CONFIRMATION_REQUIRED')
    assert.notEqual(result.status, 0)
  })
})

test('the confirmation gate comes before any chat content is read', () => {
  // 拦住它的那一步不该产生任何子进程或读取——否则"要确认"就只是句客气话。
  withHome((home) => {
    const result = runCli(home, ['draft', '某某', '--json'])
    assert.ok(!result.stderr.includes('Traceback'),
      'refusal should not have run the Python script')
    // 拒绝的回话里只有参数回显，不该带出会话内容
    const body = payload(result.stdout)
    assert.equal(body.talker, '某某')
    assert.equal(body.readsLocalChat, true)
    assert.equal(Object.keys(body).some((k) => /消息|正文|lines|transcript/.test(k)), false,
      `拒绝里不该带正文相关字段：${Object.keys(body).join(',')}`)
  })
})

test('count 越界在**确认之前**就被拦下（否则用户确认完才被告知参数错了）', () => {
  withHome((home) => {
    const result = runCli(home, ['draft', '某某', '--count', '9', '--json'])
    assert.notEqual(result.status, 0)
    const body = payload(result.stdout)
    // 不许写成"输出里出现过 count 字样"——拒绝那条回话里也带 `count` 字段，
    // 那种断言在**参数检查根本没跑**的时候照样是绿的。
    assert.equal(body.code, 'INVALID_ARGUMENT')
    assert.equal(body.field, 'count')
    assert.notEqual(body.code, 'CONFIRMATION_REQUIRED', '参数错要先于确认出现')
  })
})
