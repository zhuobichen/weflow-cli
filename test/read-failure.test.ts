/**
 * 「读取失败」与「确实为空」必须分开报告（issue #16）。
 *
 * 现场是这样的：用户升级微信后，`sessions` / `messages` 都返回 `success: true` + 空结果，
 * 看起来像"数据没了"。而真相是读取那一步失败（错误码 26 = 不是数据库），
 * 报错只在更深的日志里。这会把人引向完全错误的排查方向。
 *
 * 这里钉的是「分类函数」这一层：给一段底层错误文本，能不能得出可执行的下一步。
 * CLI 侧的接线由 `cli-read-failure.test.ts` 覆盖。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { classifyReadError, connectFailure, emptyResult } =
  await import('../src/services/readFailure.js')

test('SQLITE_NOTADB(26) 归为"密钥不匹配"，并给出重取密钥的下一步', () => {
  for (const sample of [
    'SQLITE_NOTADB: file is not a database',
    'DatabaseError: file is not a database (code: 26)',
    'Error: not a database',
  ]) {
    const f = classifyReadError(sample)
    assert.equal(f.code, 'query-failed', sample)
    assert.match(f.message, /密钥/)
    assert.match(f.next, /init/, `下一步应当是可执行的动作：${f.next}`)
  }
})

test('"no such table" 指向数据目录核对，而不是重取密钥', () => {
  const f = classifyReadError('no such table: session')
  assert.equal(f.code, 'query-failed')
  assert.match(f.next, /check --report/)
  assert.doesNotMatch(f.next, /init/, '表不存在不是密钥问题，不该让人去 init')
})

test('"database is locked" 的建议是等待/关客户端，不是重装', () => {
  const f = classifyReadError('database is locked')
  assert.match(f.next, /重试|关闭微信/)
})

test('"unable to open" 指向路径/权限，建议核对数据目录', () => {
  const f = classifyReadError('unable to open database file')
  assert.match(f.next, /数据目录|init --path/)
})

test('无法归类的错误也给出可执行下一步，且保留原始文本', () => {
  const f = classifyReadError('some totally unknown failure')
  assert.equal(f.code, 'query-failed')
  assert.match(f.next, /check --report/)
  assert.equal(f.detail, 'some totally unknown failure')
})

test('没有原始错误时也不崩（detail 为空）', () => {
  const f = classifyReadError(undefined)
  assert.equal(f.code, 'query-failed')
  assert.equal(f.detail, undefined)
})

test('连接层失败与查询层失败是两个不同的 code', () => {
  const f = connectFailure('未初始化')
  assert.equal(f.code, 'connect-failed')
  assert.match(f.next, /check --report|init/)
  assert.notEqual(connectFailure(undefined).code, classifyReadError(undefined).code)
})

test('emptyResult 只在确实失败时给出说明——正常空结果不加噪音', () => {
  assert.equal(emptyResult('ok'), undefined)
  const f = emptyResult('query-failed')
  assert.ok(f)
  assert.match(f!.message, /不代表没有数据/, '这句话是给"以为数据丢了"的用户看的')
})
