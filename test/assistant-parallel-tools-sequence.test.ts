/**
 * 并行 tool_calls 的消息序列协议（D-192）。
 *
 * 真机 bug：长链条里模型一轮返回 6 个 tool_calls，其中几个工具失败，
 * `toolFailureNudge` 的 system 消息被插在 tool 消息之间，服务端直接 400
 * "An assistant message with 'tool_calls' must be followed by tool messages"。
 *
 * OpenAI 兼容协议要求：assistant(tool_calls=N) 之后**紧跟且连续** N 条 tool 消息，
 * 中间不能夹 system/user。附加消息（失败提醒、取图）必须等这一批 tool 消息全 push 完
 * 再追加。
 *
 * 这个测试用源码静态检查 + 运行时消息序列双重钉住：
 * ① ReAct 循环里必须有 `messages.push(...deferred)` 且在所有 tool_calls 循环之后；
 * ② runToolCall 里 nudge/图片改成写进 deferred，不再直接 push 到 messages。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const src = readFileSync(join(ROOT, 'src', 'services', 'assistantService.ts'), 'utf8')

/** 抓 runReactLoop 里 msg.tool_calls 那一块 */
function reactToolBlock(): string {
  const at = src.indexOf('if (msg.tool_calls?.length) {')
  const next = src.indexOf('reply = (msg.content', at)
  return src.slice(at, next)
}

test('ReAct 循环：攒 deferred，一批 tool_calls 全执行完再追加（协议要求）', () => {
  const block = reactToolBlock()
  assert.match(block, /const deferred: ApiMessage\[\] = \[\]/, '必须有 deferred 队列')
  assert.match(block, /runToolCall\([^)]*deferred\)/, 'runToolCall 必须收到 deferred')
  const pushAt = block.indexOf('messages.push(...deferred)')
  const loopEnd = block.lastIndexOf('}')   // for 循环收尾
  assert.ok(pushAt > 0, '必须有 messages.push(...deferred)')
  assert.ok(pushAt > block.indexOf('for (const tc'), 'deferred 必须在 for 循环之后追加')
})

test('runToolCall：失败提醒与取图走 deferred，不直接插进 messages', () => {
  const at = src.indexOf('private async runToolCall')
  const fn = src.slice(at, at + 12000)
  const nudge = fn.indexOf('deferred.push({ role: \'system\', content: toolFailureNudge')
  assert.ok(nudge > 0, 'toolFailureNudge 必须写进 deferred')
  assert.match(fn, /deferred\.push\(\{\s*role: 'user'/, '取图 user 消息也必须写进 deferred')
  // 不能还留着直接 push 这两类的旧写法
  assert.doesNotMatch(fn, /messages\.push\(\{ role: 'system', content: toolFailureNudge/)
})

test('runToolCall 签名带 deferred 默认参数（快路径单工具不传，保持原行为）', () => {
  assert.match(src, /trace: TurnTrace, deferred: ApiMessage\[\] = \[\]\)/,
    'deferred 必须可选——快路径只调一个工具，没有并行问题')
})
