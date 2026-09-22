/**
 * 调 Python 脚本的唯一入口（`pythonBridge`）。
 *
 * 抽这一层的原因是此前有**两份**实现：`get_todos` 用 `execFile`（参数走 argv），路由用
 * `spawn`（请求走 stdin），各自一套超时与错误处理。工具只会越加越多，所以先收敛。
 *
 * 这些测试盯四件事：
 * 1. JSON 能取到——整份 stdout 是 JSON，或**最后一行**是 JSON（有的脚本前面会打进度行）；
 * 2. 取不到时**说清是哪一种失败**：超时 / 退出码非 0 / 没有 JSON / 脚本自己报 success:false；
 * 3. stdin 能传进去（路由那条路要用）；
 * 4. 失败**不抛异常**——调用方（工具分支）要的是"成不成功"，不是一层 try/catch。
 *
 * 全部用注入的假 runner，不 spawn 真进程。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const bridge = await import('../src/services/pythonBridge.js')
const { runPythonJson, setScriptRunner, parseJsonFrom } = bridge

interface Fake {
  script: string
  args: string[]
  stdin?: string
}

function install(run: (call: Fake) => Promise<{ stdout: string; stderr: string; code: number | null }>): Fake[] {
  const calls: Fake[] = []
  setScriptRunner(async (script, args, options) => {
    const call = { script, args, stdin: options.stdin }
    calls.push(call)
    return run(call)
  })
  return calls
}

const ok = (stdout: string, stderr = '') => async () => ({ stdout, stderr, code: 0 })

test.after(() => setScriptRunner(null))

test('a script that prints one JSON object is parsed', async () => {
  install(ok('{"success": true, "items": [1, 2]}'))
  const result = await runPythonJson('anything.py', ['--json'])
  assert.equal(result.ok, true)
  assert.deepEqual(result.data.items, [1, 2])
})

test('JSON on the last line is found even when progress lines come first', async () => {
  // route_cards 就是这种：先打给人看的进度行，最后一行才是 JSON
  install(ok('正在查询…\n命中 2 个会话\n{"success": true, "ranked": []}'))
  const result = await runPythonJson('route_cards.py', ['ask', 'x'])
  assert.equal(result.ok, true)
  assert.deepEqual(result.data.ranked, [])
})

test('a non-zero exit is reported as such, with the stderr tail', async () => {
  install(async () => ({ stdout: '', stderr: 'line1\n缺少 TypeSafe key', code: 2 }))
  const result = await runPythonJson('reply_debt.py', [])
  assert.equal(result.ok, false)
  assert.match(result.error!, /退出码 2/)
  assert.match(result.stderr!, /缺少 TypeSafe key/)
})

test('exit 0 without JSON is a distinct failure from a non-zero exit', async () => {
  install(ok('我打印了一段给人看的话，没有 JSON'))
  const result = await runPythonJson('x.py', [])
  assert.equal(result.ok, false)
  assert.match(result.error!, /没有给出可解析的 JSON/)
})

test('a script that reports success:false is a failure with its own message', async () => {
  install(ok('{"success": false, "error": "库锁了"}'))
  const result = await runPythonJson('x.py', [])
  assert.equal(result.ok, false)
  assert.match(result.error!, /库锁了/)
})

test('a timeout arrives as a failure, not as an exception', async () => {
  setScriptRunner(async () => { throw new Error('脚本超时（100ms）') })
  const result = await runPythonJson('slow.py', [], { timeoutMs: 100 })
  assert.equal(result.ok, false)
  assert.match(result.error!, /超时/)
})

test('stdin is passed through (the router sends its request that way)', async () => {
  const calls = install(ok('{"success": true}'))
  await runPythonJson('decide.py', ['--request', '-'], { stdin: '{"state":"x"}' })
  assert.equal(calls[0].stdin, '{"state":"x"}')
  assert.deepEqual(calls[0].args, ['--request', '-'])
})

test('the script argument is a name under scripts/, not a caller-built path', async () => {
  const calls = install(ok('{"success": true}'))
  await runPythonJson('extract_todos.py', ['list'])
  assert.match(calls[0].script, /scripts[\\/]extract_todos\.py$/)
})

test('parseJsonFrom tolerates junk before the JSON and refuses junk alone', () => {
  assert.equal(parseJsonFrom('不是 JSON').ok, false)
  assert.equal(parseJsonFrom('').ok, false)
  assert.deepEqual(parseJsonFrom('进度\n{"a":1}').data, { a: 1 })
  assert.deepEqual(parseJsonFrom('{"a":1}').data, { a: 1 })
  // 别被骗：只有一半的 JSON 不算
  assert.equal(parseJsonFrom('{"a":1').ok, false)
})
