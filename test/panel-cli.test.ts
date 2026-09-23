/**
 * `weflow-cli panel` 的端到端：确认门、状态、以及**真的问一句**。
 *
 * 最后那条是关键——`panel ask` 就是"面板减去像素"：悬浮窗走的是同一个端点、同一套规矩，
 * 所以"面板与微信共用一个大脑 / 共用一个配额"这些事，能在没有显示器的 CI 上被端到端地测。
 *
 * 这里会**真起一个端点**（桩助手，不联网、不烧钱），并把端点文件写进临时家目录，
 * 于是 CLI 那一侧走的是完整的读取与鉴权路径。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawn } from 'node:child_process'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-panel-cli-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { startPanelServer } = await import('../src/panel/server.js')

/**
 * 跑一次 CLI。**必须是异步 spawn，不能用 `spawnSync`**：
 * 桩端点是**在这个测试进程里**起来的，而 `spawnSync` 会阻塞事件循环——
 * 于是 CLI 发的 HTTP 请求永远等不到应答，两边互锁到超时。
 * （第一版就是这么写的，现象是 `--status` 报 TIMEOUT、`--ask` 挂满 200 秒被 SIGTERM。）
 */
function runCli(args: string[], timeoutMs = 60_000): Promise<{ status: number | null; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [
      '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'), ...args,
    ], { cwd: process.cwd(), env: { ...process.env, HOME, USERPROFILE: HOME } })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (d: Buffer) => { stdout += d.toString() })
    child.stderr.on('data', (d: Buffer) => { stderr += d.toString() })
    const timer = setTimeout(() => child.kill(), timeoutMs)
    child.on('close', (code) => { clearTimeout(timer); resolve({ status: code, stdout, stderr }) })
  })
}

function parse(stdout: string): any {
  return JSON.parse(stdout.slice(stdout.indexOf('{')))
}

/** 起一个桩端点并写好端点文件；返回收尾函数 */
async function bootEndpoint(outcome: any = { status: 'replied', text: '收到' }) {
  const calls: string[] = []
  const service: any = {
    isChannelActive: () => false,
    quotaState: () => ({ used: 7, limit: 100 }),
    ask: async (_bucket: string, text: string) => { calls.push(text); return outcome },
  }
  const server = await startPanelServer({
    service, memoryBucket: 'wxid_me', memoryNote: '记忆桶：wxid_me（与微信共用一个大脑）',
    channel: 'local', port: 0,
  })
  return { server, calls, close: () => server.close() }
}

// ---------------------------------------------------------------- 确认门

test('panel --dry-run --json 只预览，并**声明后果**：会起守护进程、关窗后它还在', async () => {
  const r = await runCli(['panel', '--dry-run', '--json'])
  assert.equal(r.status, 0, r.stderr || r.stdout)
  const body = parse(r.stdout)
  assert.equal(body.dryRun, true)
  assert.equal(body.action, 'panel.open')
  assert.equal(body.keepsRunningAfterClose, true, '关了窗口守护进程还在跑，这件事必须写在预览里')
  assert.equal(typeof body.willStartDaemon, 'boolean')
  assert.ok(['electron', 'browser', 'none'].includes(body.shell))
})

test('machine 模式不给 --yes 一律拒（不许 AI 静默弹窗/起进程）', async () => {
  for (const args of [['panel', '--json'], ['panel', '--ask', '你好', '--json']]) {
    const r = await runCli(args)
    assert.equal(r.status, 1, r.stdout)
    assert.equal(parse(r.stdout).code, 'CONFIRMATION_REQUIRED')
  }
})

// ---------------------------------------------------------------- 只读状态

test('守护进程没跑时，--status 给 PANEL_NOT_RUNNING 而不是挂死', async () => {
  const r = await runCli(['panel', '--status', '--json'])
  assert.equal(r.status, 1)
  assert.equal(parse(r.stdout).code, 'PANEL_NOT_RUNNING')
})

test('守护进程没跑时，--ask 也如实说，而不是假装答了', async () => {
  const r = await runCli(['panel', '--ask', '你好', '--yes', '--json'])
  assert.equal(r.status, 1)
  assert.equal(parse(r.stdout).code, 'PANEL_NOT_RUNNING')
})

test('--ask 空字符串 → 参数错误', async () => {
  const r = await runCli(['panel', '--ask', '   ', '--yes', '--json'])
  assert.equal(r.status, 1)
  assert.equal(parse(r.stdout).code, 'INVALID_ARGUMENT')
})

// ---------------------------------------------------------------- 真端点

test('端点起来了：--status 报出端口、配额、以及**用的是哪个记忆桶**', async () => {
  const ep = await bootEndpoint()
  try {
    const r = await runCli(['panel', '--status', '--json'])
    assert.equal(r.status, 0, r.stderr || r.stdout)
    const body = parse(r.stdout)
    assert.equal(body.success, true)
    assert.equal(body.port, ep.server.port)
    assert.deepEqual(body.quota, { used: 7, limit: 100 })
    assert.equal(body.memoryBucket, 'wxid_me')
    assert.match(body.memoryNote, /一个大脑/, '界面上要能看见是不是共用')
    assert.equal(JSON.stringify(body).includes(ep.server.endpoint.token), false, '状态里不许带 token')
  } finally { await ep.close() }
})

test('panel ask 真的把话交给了助手，并把答复原样带回来', async () => {
  const ep = await bootEndpoint({ status: 'replied', text: '你最近在关注 AI+法律' })
  try {
    const r = await runCli(['panel', '--ask', '我最近在关注什么', '--yes', '--json'])
    assert.equal(r.status, 0, r.stderr || r.stdout)
    const body = parse(r.stdout)
    assert.equal(body.success, true)
    assert.equal(body.status, 'replied')
    assert.equal(body.reply, '你最近在关注 AI+法律')
    assert.deepEqual(ep.calls, ['我最近在关注什么'])
  } finally { await ep.close() }
})

test('配额用尽走的是"回答"而不是"错误"：exit 0，正文是那句话', async () => {
  const ep = await bootEndpoint({ status: 'quota-exceeded', text: '今日额度已用完, 明天再来吧' })
  try {
    const r = await runCli(['panel', '--ask', '再问一句', '--yes', '--json'])
    assert.equal(r.status, 0, '这不是失败，是一个回答')
    const body = parse(r.stdout)
    assert.equal(body.status, 'quota-exceeded')
    assert.match(body.reply, /额度已用完/)
  } finally { await ep.close() }
})

test('端点文件里的 pid 已经死了 → 当作没在运行（崩溃残留靠探活兜住）', async () => {
  const ep = await bootEndpoint()
  const { writeFileSync, readFileSync } = await import('node:fs')
  const { endpointFile } = await import('../src/panel/endpoint.js')
  const file = endpointFile()
  const original = readFileSync(file, 'utf8')
  try {
    const broken = JSON.parse(original)
    broken.pid = 999999999                      // 一个一定不存在的 pid
    writeFileSync(file, JSON.stringify(broken), 'utf8')
    const r = await runCli(['panel', '--status', '--json'])
    assert.equal(r.status, 1)
    assert.equal(parse(r.stdout).code, 'PANEL_NOT_RUNNING')
  } finally {
    writeFileSync(file, original, 'utf8')       // 还原，别影响后面的收尾
    await ep.close()
  }
})
