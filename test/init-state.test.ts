import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * issue #13（2026-11-02）：用户跑过 `init` 但它没成功，`check` 只说"未初始化"，
 * 于是用户把它理解成"取不到密钥"。这里的测试锁住"失败原因会被记下来、并被表达出来"。
 */
const home = mkdtempSync(join(tmpdir(), 'wf-initstate-'))
process.env.HOME = home
process.env.USERPROFILE = home

const mod = await import('../src/services/initState.js')

test.after(() => {
  rmSync(home, { recursive: true, force: true })
})

test('失败被记录后能读回来，且带退出码与时间', () => {
  mod.clearInitFailure()
  mod.recordInitFailure(1, '未找到微信数据目录')

  const failure = mod.readInitFailure()
  assert.ok(failure, '应当能读回记录')
  assert.equal(failure!.code, 1)
  assert.equal(failure!.message, '未找到微信数据目录')
  assert.ok(!Number.isNaN(new Date(failure!.at).getTime()), '时间应当可解析')
})

test('describeInitFailure 会把原因和"重跑"写进给用户看的三行', () => {
  mod.recordInitFailure(1, '--json 只输出预览，不会真正初始化')
  const lines = mod.describeInitFailure(mod.readInitFailure())
  assert.ok(lines, '有记录时必须给出说明')
  const text = lines!.join('\n')
  assert.match(text, /未初始化/)
  assert.match(text, /上次 init 没有成功/)
  assert.match(text, /--json 只输出预览/, '必须复述上次的具体原因')
  assert.match(text, /weflow-cli init/, '必须告诉用户下一步')
})

test('没有记录时 describeInitFailure 返回 null（调用方走旧文案）', () => {
  mod.clearInitFailure()
  assert.equal(mod.readInitFailure(), null)
  assert.equal(mod.describeInitFailure(null), null)
})

test('成功路径清掉旧记录', () => {
  mod.recordInitFailure(1, '上一次失败')
  assert.ok(mod.readInitFailure())
  mod.clearInitFailure()
  assert.equal(mod.readInitFailure(), null)
})

test('状态写在 ~/.weflow-cli/init-state.json，不污染 config.json', () => {
  mod.clearInitFailure()
  mod.recordInitFailure(2, '坏掉的记录')
  const file = mod.initStateFile()
  assert.ok(file.endsWith(join('.weflow-cli', 'init-state.json')), file)
  assert.ok(file.startsWith(home), '应当落在用户主目录下')
  const raw = JSON.parse(readFileSync(file, 'utf8'))
  assert.equal(raw.code, 2)
})
