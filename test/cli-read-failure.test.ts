/**
 * CLI 侧的接线：`sessions` / `messages` 在**已初始化但读不出来**时必须说出原因，
 * 而不是像以前那样返回 `success: true` + 空结果（issue #16 的核心诉求）。
 *
 * 为什么不用打桩：ChatService 在 CLI 里是进程内单例，打桩要么改生产代码暴露注入口，
 * 要么测到的是一个和真实链路无关的假对象。这里改用**真实 CLI 进程 + 一份真实的加密库**，
 * 让失败真的发生。
 *
 * 造失败的方法：用 sqlcipher3 生成一个**正常加密**的库（密钥 A），再用**密钥 B** 的配置去读。
 * 这复刻的正是 issue #16 的现象——连接建立成功、首次查询得到 SQLITE_NOTADB(26)。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CLI = join(process.cwd(), 'dist', 'bin', 'weflow-cli.js')

const GOOD_KEY = 'a'.repeat(64)
const BAD_KEY = 'b'.repeat(64)

/** 用 sqlcipher3 造一个真实加密库；环境不满足时返回 null（调用方 skip） */
function makeEncryptedDb(home: string): string | null {
  const dataDir = join(home, 'fake_data')
  const msgDir = join(dataDir, 'db_storage', 'message')
  mkdirSync(msgDir, { recursive: true })
  const dbPath = join(msgDir, 'message_0.db')
  writeFileSync(dbPath, '')      // sqlcipher 需要文件存在

  const py = `
import sys
try:
    from sqlcipher3 import dbapi2 as sq
except Exception:
    sys.exit(3)
c = sq.connect(r"${dbPath.replace(/\\/g, '/')}")
c.execute("PRAGMA key = \\"x'${GOOD_KEY}'\\";")
c.execute("CREATE TABLE session (username TEXT, type INTEGER)")
c.execute("INSERT INTO session VALUES ('wxid_demo', 0)")
c.commit()
c.close()
`
  try {
    execFileSync('python', ['-c', py], { encoding: 'utf8', windowsHide: true })
    return existsSync(dbPath) ? dbPath : null
  } catch {
    return null
  }
}

function runCli(args: string[], home: string): { stdout: string; stderr: string; code: number } {
  try {
    const stdout = execFileSync(process.execPath, [CLI, ...args], {
      encoding: 'utf8',
      env: { ...process.env, HOME: home, USERPROFILE: home },
      timeout: 180_000,
      windowsHide: true,
    })
    return { stdout, stderr: '', code: 0 }
  } catch (e: any) {
    return { stdout: String(e.stdout ?? ''), stderr: String(e.stderr ?? ''), code: e.status ?? -1 }
  }
}

/** 造一个"已初始化"的家目录：库是真实加密的，但配置里的密钥是错的 */
function brokenHome(): string | null {
  const home = mkdtempSync(join(tmpdir(), 'weflow-rf-'))
  const db = makeEncryptedDb(home)
  if (!db) return null
  const dir = join(home, '.weflow-cli')
  mkdirSync(dir, { recursive: true })
  // `isConfigured()` 认的是 dbPath + decryptKey 这一组；只给 dataDir 会走"未初始化"出口，
  // 测不到本用例要覆盖的"已初始化但读不出来"。
  writeFileSync(join(dir, 'config.json'), JSON.stringify({
    version: 1,
    dbPath: db,
    decryptKey: BAD_KEY,
    dataDir: join(home, 'fake_data'),
  }), 'utf8')
  return home
}

test('sessions：已初始化但读不出来时，JSON 是 success:false 且带可执行的下一步', (t) => {
  const home = brokenHome()
  if (!home) return t.skip('本机没有可用的 sqlcipher3，跳过需要真实加密库的用例')

  const r = runCli(['sessions', '--json'], home)
  const start = r.stdout.indexOf('{')
  assert.ok(start >= 0, `没有输出 JSON：${r.stdout}${r.stderr}`)
  const json = JSON.parse(r.stdout.slice(start))

  assert.equal(json.success, false, `读失败不能报 success:true（实际：${r.stdout.slice(0, 300)}）`)
  assert.equal(json.code, 'READ_FAILED')
  // 密钥错时，有的版本在"连接"阶段就失败（拿不到可用的库），有的到"查询"阶段才失败（26）。
  // 两者都是"读不出来"，对用户的含义相同，所以这里不锁死是哪一种。
  assert.ok(['connect-failed', 'query-failed'].includes(json.failure?.code),
    `failure.code 应为连接/查询失败之一，实际 ${json.failure?.code}`)
  assert.ok(json.failure?.message, 'failure.message 要说人话')
  assert.ok(json.failure?.next, 'failure.next 要是可执行的动作')
  assert.deepEqual(json.sessions, [])
})

test('sessions：读失败时退出码非 0（脚本能据此判断，而不是当成空列表）', (t) => {
  const home = brokenHome()
  if (!home) return t.skip('本机没有可用的 sqlcipher3')
  const r = runCli(['sessions', '--json'], home)
  assert.notEqual(r.code, 0, '读失败必须有非 0 退出码')
})

test('messages：读不出来时同样带 failure，而不是「未找到消息」', (t) => {
  const home = brokenHome()
  if (!home) return t.skip('本机没有可用的 sqlcipher3')
  const r = runCli(['messages', 'wxid_demo', '--json'], home)
  const start = r.stdout.indexOf('{')
  assert.ok(start >= 0, `没有输出 JSON：${r.stdout}${r.stderr}`)
  const json = JSON.parse(r.stdout.slice(start))
  assert.equal(json.success, false)
  assert.equal(json.code, 'READ_FAILED')
  assert.ok(json.failure?.message)
})
