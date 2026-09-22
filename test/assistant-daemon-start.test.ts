/**
 * 守护进程的启动路径（单元级，用假 spawn 驱动）。
 *
 * 这条路径原先错在两处，而且**两处都不报错**：
 *
 * 1. 子进程没带 `--yes`。`assistant run` 自己那层确认要读 stdin，而守护进程给它的
 *    stdin 是 `ignore`——没人能回答那个提示，子进程死在确认上；父进程却照样打印
 *    「✓ 守护进程已启动 (pid …)」，并写下 pid 文件。
 * 2. 那个被当作「负责确认」的环境变量 `WEFLOW_ASSISTANT_DAEMON=1` 没有任何代码读它，
 *    而且它是被当成**键**用的，设出来的变量名里带 `=`。
 *
 * 现在钉住三件事：spawn 参数必须带 `--yes`；环境里不许有名字带 `=` 的键（同时不许丢掉
 * 真正在用的 `PYTHONIOENCODING`）；启动后要先观察一小段时间，子进程立刻退出就报退出码
 * 与日志尾部，且**不写 pid 文件**——写了就是对外宣称它在运行，而进程已经没了。
 *
 * 不启真进程、不联网、不碰真实家目录（HOME 指到临时目录后才 import 模块）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-daemon-start-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME // Windows 上 os.homedir() 读的是这个；模块 load 时算出自录

const { startDaemon } = await import('../src/services/assistantDaemon.js')

const STATE_DIR = join(HOME, '.weflow-cli')
const PID_FILE = join(STATE_DIR, 'assistant.pid')
const LOG_FILE = join(STATE_DIR, 'assistant.log')

/** 能按剧本「活着」或「死掉」的假子进程 */
class FakeChild extends EventEmitter {
  pid: number | undefined = 4242
  exitCode: number | null = null
  signalCode: string | null = null
  stdout = new EventEmitter()
  stderr = new EventEmitter()

  die(code: number, stderrLine = ''): void {
    if (stderrLine) this.stderr.emit('data', Buffer.from(stderrLine + '\n'))
    this.exitCode = code
    this.emit('exit', code)
  }
}

interface Recorder {
  impl: (...args: any[]) => any
  calls: { args: string[]; options: any }[]
  child: FakeChild
}

function recorder(): Recorder {
  const calls: Recorder['calls'] = []
  const child = new FakeChild()
  return {
    calls,
    child,
    impl: (_cmd: string, args: string[], options: any) => {
      calls.push({ args, options })
      return child
    },
  }
}

function cleanup(): void {
  rmSync(PID_FILE, { force: true })
}

test('the daemon child is spawned with --yes, because its stdin is ignored', async () => {
  cleanup()
  const rec = recorder()
  const result = await startDaemon({ spawnImpl: rec.impl as any, settleMs: 5 })

  assert.equal(result.started, true)
  assert.equal(rec.calls.length, 1)
  assert.deepEqual(rec.calls[0].args.slice(-3), ['assistant', 'run', '--yes'])
  // 这条是因果，不是巧合：stdin 是 ignore，所以那个交互确认永远等不到答复。
  assert.equal(rec.calls[0].options.stdio[0], 'ignore')
  assert.equal(rec.calls[0].options.detached, true)
  assert.equal(readFileSync(PID_FILE, 'utf8').trim(), '4242')
})

test('the child environment has no malformed key and keeps the real ones', async () => {
  cleanup()
  const rec = recorder()
  await startDaemon({ spawnImpl: rec.impl as any, settleMs: 5 })

  const env = rec.calls[0].options.env as Record<string, string>
  const malformed = Object.keys(env).filter((key) => key.includes('='))
  assert.deepEqual(malformed, [], '环境里出现了名字带 = 的键（那个死掉的 marker 就是这样设的）')
  assert.equal(env.PYTHONIOENCODING, 'utf-8', '真正在用的变量不许被一起删掉')
})

test('a child that dies during the window is reported as a failure', async () => {
  cleanup()
  const rec = recorder()
  setTimeout(() => rec.child.die(3), 1) // 在观察窗口内退出
  const result = await startDaemon({ spawnImpl: rec.impl as any, settleMs: 40 })

  assert.equal(result.started, false)
  assert.match(result.error!, /code 3/)
  assert.equal(existsSync(PID_FILE), false, '进程已经没了，不许留下"在运行"的 pid 文件')
})

test('the failure message carries the log tail, not just an exit code', async () => {
  cleanup()
  const rec = recorder()
  setTimeout(() => rec.child.die(1, '缺少公众号数据库密钥'), 1)
  const result = await startDaemon({ spawnImpl: rec.impl as any, settleMs: 40 })

  assert.equal(result.started, false)
  assert.match(result.error!, /缺少公众号数据库密钥/)
})

test('a surviving child is the only case that reports success', async () => {
  cleanup()
  const rec = recorder()
  const result = await startDaemon({ spawnImpl: rec.impl as any, settleMs: 20 })

  assert.equal(result.started, true)
  assert.equal(result.pid, 4242)
  assert.ok(existsSync(PID_FILE))
})

test('an asynchronous spawn error is reported instead of crashing the caller', async () => {
  cleanup()
  const rec = recorder()
  setTimeout(() => rec.child.emit('error', new Error('spawn ENOENT')), 1)
  const result = await startDaemon({ spawnImpl: rec.impl as any, settleMs: 40 })

  assert.equal(result.started, false)
  assert.match(result.error!, /ENOENT/)
  assert.equal(existsSync(PID_FILE), false)
})

test('a daemon that is already alive is not started twice', async () => {
  writeFileSync(PID_FILE, String(process.pid), 'utf8') // 自己这个进程一定活着
  try {
    const rec = recorder()
    const result = await startDaemon({ spawnImpl: rec.impl as any, settleMs: 5 })
    assert.equal(result.started, false)
    assert.match(result.error!, /已在运行/)
    assert.equal(rec.calls.length, 0, '已经在跑就不该再 spawn 一个')
  } finally {
    cleanup()
  }
})

test('the daemon log records the start and the exit of a child that died', async () => {
  cleanup()
  const rec = recorder()
  setTimeout(() => rec.child.die(2), 1)
  await startDaemon({ spawnImpl: rec.impl as any, settleMs: 40 })

  const log = readFileSync(LOG_FILE, 'utf8')
  assert.match(log, /daemon starting/)
  assert.match(log, /daemon exited \(code 2\)/)
})
