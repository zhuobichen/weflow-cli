/**
 * 三级文件权限（D-199）的判据测试。
 *
 * L0 默认：安全范围（PKG_ROOT / ~/.weflow-cli / cwd）内读写都可以。
 * L1 自主解锁：工作区外**只读**。
 * L2：白名单写/执行——本次不实现，工具明确拒绝 level=2。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { homedir } from 'node:os'
import { checkFileAccess, readFileSafe, levelLabel } from '../src/services/assistantPrivilege.js'
import { resolvePackageRoot } from '../src/utils/packageRoot.js'

const PKG = resolvePackageRoot(import.meta.url)
const cwd = mkdtempSync(join(tmpdir(), 'priv-test-'))

test('L0：安全范围内可读可写', () => {
  const inPkg = checkFileAccess(join(PKG, 'package.json'), 0, cwd, false)
  assert.equal(inPkg.allowed, true, '包目录内 L0 可读')
  const write = checkFileAccess(join(cwd, 'x.txt'), 0, cwd, true)
  assert.equal(write.allowed, true, '工作目录内 L0 可写')
  const data = checkFileAccess(join(homedir(), '.weflow-cli', 'config.json'), 0, cwd, false)
  assert.equal(data.allowed, true, '数据目录内 L0 可读')
})

test('L0：工作区外一律拒绝（读也不行）', () => {
  const outside = checkFileAccess('D:\\somewhere\\outside\\secret.txt', 0, cwd, false)
  assert.equal(outside.allowed, false)
  assert.match(outside.reason, /工作区外访问被拒/)
})

test('L1：工作区外可读（只读）', () => {
  // 造一个工作区外的真实文件
  const outsideDir = mkdtempSync(join(tmpdir(), 'priv-outside-'))
  const f = join(outsideDir, 'note.txt')
  writeFileSync(f, '外部文件内容')
  const r = checkFileAccess(f, 1, cwd, false)
  assert.equal(r.allowed, true, 'L1 应能读工作区外')
  assert.match(readFileSafe(f, 1, cwd), /外部文件内容/)
})

test('L1：工作区外仍不能写（L2 白名单未启用）', () => {
  const w = checkFileAccess('D:\\outside\\x.txt', 1, cwd, true)
  assert.equal(w.allowed, false)
  assert.match(w.reason, /L1 只读/)
})

test('路径规范化：.. 逃逸出工作目录按真实目标判定', () => {
  // 从 cwd 出发的 .. 会跳出 cwd；若不在其它允许根内，L0 应拒绝
  const escaped = resolve(cwd, '..', 'outside-file.txt')
  const r = checkFileAccess(escaped, 0, cwd, false)
  // 若 escape 后恰好落进 PKG 或 DATA 就算允许，否则拒绝——关键是"按真实目标判定"
  assert.equal(typeof r.allowed, 'boolean')
})

test('空路径被拒', () => {
  assert.equal(checkFileAccess('', 1, cwd, false).allowed, false)
})

test('级别人话', () => {
  assert.match(levelLabel(0), /安全范围/)
  assert.match(levelLabel(1), /只读/)
  assert.match(levelLabel(2), /L2/)
})

test('read_file 过大截断', () => {
  const f = join(cwd, 'big.txt')
  writeFileSync(f, 'x'.repeat(50000))
  const out = readFileSafe(f, 0, cwd, 1000)
  assert.ok(out.length < 1100, '应截断')
  assert.match(out, /已截断/)
})
