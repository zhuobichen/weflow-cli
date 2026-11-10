/**
 * 助手原始流水（L1 底账）。
 *
 * 存在的理由只有一个：`facts` 里的 `sourceQuote` 只是**截断**，窗口一压缩原话就没了，
 * 抽错的事实再也无法对照。所以这层只追加、不改写，作为将来对账的证据源。
 *
 * HOME 指到临时目录后才 import：文件落在 ~/.weflow-cli/assistant_transcript/ 下。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-transcript-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const {
  appendTurn, readDay, readRecentDays, pruneTranscript,
  TRANSCRIPT_LINE_MAX, TRANSCRIPT_RETENTION_DAYS, transcriptDir,
} = await import('../src/services/assistantTranscript.js')

function dayOf(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

test('一轮原话按天追加，能原样读回来（含角色与时间）', () => {
  const ts = Date.now()
  assert.equal(appendTurn({ ts, role: 'user', content: '以后回复先读回复规则', session: 's1' }), true)
  const rows = readDay(dayOf(ts))
  const hit = rows.find(r => r.content.includes('回复规则'))
  assert.ok(hit, '刚写的那条要能读回来')
  assert.equal(hit!.role, 'user')
  assert.equal(hit!.ts, ts)
  assert.equal(hit!.session, 's1')
})

test('空白内容不落盘（避免流水被空行塞满）', () => {
  const before = readDay(dayOf(Date.now())).length
  assert.equal(appendTurn({ ts: Date.now(), role: 'user', content: '   \n ' }), false)
  assert.equal(readDay(dayOf(Date.now())).length, before)
})

test('超长内容截断到上限——流水是证据，不是全文存档', () => {
  const ts = Date.now()
  appendTurn({ ts, role: 'assistant', content: 'x'.repeat(TRANSCRIPT_LINE_MAX + 500) })
  const row = readDay(dayOf(ts)).at(-1)!
  assert.ok(row.content.length <= TRANSCRIPT_LINE_MAX + 50, `实际 ${row.content.length}`)
})

test('原话按原样留存（不打码）——否则对账时对不上原句', () => {
  // 刻意**不**过 privacyGate：按 assistantPrivacy 的既有判据，脱敏只用于"工具挖出的第三方数据"，
  // 用户自己的话按原意处理；而且 assistant_memory.json 里的 working/facts 本来就是明文。
  const ts = Date.now()
  const sentence = '我住在成都，以后回复先读回复规则'
  appendTurn({ ts, role: 'user', content: sentence })
  const row = readDay(dayOf(ts)).at(-1)!
  assert.equal(row.content, sentence)
})

test('流水靠文件权限保护：目录 0700、文件 0600', () => {
  if (process.platform === 'win32') return   // Windows 上 chmod 是空操作，这条只对 POSIX 有意义
  const ts = Date.now()
  appendTurn({ ts, role: 'user', content: '权限检查' })
  const { statSync } = require('node:fs') as typeof import('node:fs')
  const fileMode = statSync(join(transcriptDir(), `${dayOf(ts)}.jsonl`)).mode & 0o777
  const dirMode = statSync(transcriptDir()).mode & 0o777
  assert.equal(fileMode & 0o077, 0, `文件对同组/其他人不该可读：${fileMode.toString(8)}`)
  assert.equal(dirMode & 0o077, 0, `目录对同组/其他人不该可读：${dirMode.toString(8)}`)
})

test('按天轮转：不同日子落在不同文件', () => {
  const today = Date.now()
  const yesterday = today - 86400000
  appendTurn({ ts: today, role: 'user', content: '今天这句' })
  appendTurn({ ts: yesterday, role: 'user', content: '昨天那句' })
  const names = readdirSync(transcriptDir())
  assert.ok(names.includes(`${dayOf(today)}.jsonl`))
  assert.ok(names.includes(`${dayOf(yesterday)}.jsonl`))
})

test('保留期之外的文件会被清掉，期内的不动', () => {
  const now = Date.now()
  const old = now - (TRANSCRIPT_RETENTION_DAYS + 2) * 86400000
  mkdirSync(transcriptDir(), { recursive: true })
  writeFileSync(join(transcriptDir(), `${dayOf(old)}.jsonl`), '{}\n', 'utf8')
  const keep = join(transcriptDir(), `${dayOf(now)}.jsonl`)
  writeFileSync(keep, readFileSync(keep, 'utf8') || '{}\n', 'utf8')

  const removed = pruneTranscript(now)
  assert.ok(removed.includes(`${dayOf(old)}.jsonl`), '过期文件该被删')
  assert.ok(readdirSync(transcriptDir()).includes(`${dayOf(now)}.jsonl`), '今天的不能删')
})

test('清理不碰不是自己命名的文件（别动别人的东西）', () => {
  mkdirSync(transcriptDir(), { recursive: true })
  const foreign = join(transcriptDir(), 'notes.txt')
  writeFileSync(foreign, 'x', 'utf8')
  pruneTranscript(Date.now())
  assert.ok(readdirSync(transcriptDir()).includes('notes.txt'), '非 YYYY-MM-DD.jsonl 的东西不能被删')
})

test('目录不可写时不抛（写流水失败不该让助手这一轮失败）', () => {
  const original = process.env.HOME
  process.env.HOME = join(HOME, 'not-a-dir', '\u0000invalid')
  // 模块在 import 时已经绑定了 HOME，这里只验证"任何情况下都不抛"
  assert.doesNotThrow(() => appendTurn({ ts: Date.now(), role: 'user', content: '测试' }))
  process.env.HOME = original
})

test('readRecentDays 按时间升序给出跨天的流水', () => {
  const now = Date.now()
  appendTurn({ ts: now - 86400000, role: 'user', content: '较早的一句' })
  appendTurn({ ts: now, role: 'user', content: '较晚的一句' })
  const rows = readRecentDays(2, now)
  const iEarlier = rows.findIndex(r => r.content === '较早的一句')
  const iLater = rows.findIndex(r => r.content === '较晚的一句')
  assert.ok(iEarlier >= 0 && iLater >= 0)
  assert.ok(iEarlier < iLater, '较早的应当排在前面')
})
