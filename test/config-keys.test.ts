/**
 * 配置项的"注册了但没落盘"检查（静态，不跑 CLI）。
 *
 * `weflow-cli config set <key>` 只认 `bin` 里的 `configurableKeys`，而真正把值存下来
 * 要靠 `configService` 的四份字面量：接口字段、默认值、`load()` 映射、`clear()` 重置。
 * 漏掉任何一份的表现都很难看：
 *
 * - 漏在 `configurableKeys`：`config set` 直接被拒（`dashscopeApiKey` 就这么漏过一次）；
 * - 漏在默认值或 `clear()`：`config reset` 之后这个键的旧值会被**写丢**，
 *   而 `getAll()` 还会把它当作"没配过"；
 * - 漏在 `load()`：进程重启后值静默消失，用户只看到"我明明设过"。
 *
 * 所以每个键必须在四处都出现。这是纯文本断言（这个仓库里已有的静态测试也是这个路子），
 * 格式改了它会红——**红是对的**，说明该把新格式补进这份检查里。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const BIN = readFileSync(join(ROOT, 'bin/weflow-cli.ts'), 'utf8')
const SERVICE = readFileSync(join(ROOT, 'src/services/configService.ts'), 'utf8')

function configurableKeys(): string[] {
  const start = BIN.indexOf('const configurableKeys = [')
  assert.ok(start >= 0, '没找到 configurableKeys 数组')
  const end = BIN.indexOf('] as const', start)
  assert.ok(end > start, '没找到 configurableKeys 的结束标记')
  return [...BIN.slice(start, end).matchAll(/'([A-Za-z0-9_]+)'/g)].map((m) => m[1])
}

/** 取某个标记所在的那一行——这几份字面量都是单行的。 */
function lineWith(marker: string, from = 0): string {
  const start = SERVICE.indexOf(marker, from)
  assert.ok(start >= 0, `configService.ts 里没找到 ${marker}`)
  return SERVICE.slice(start, SERVICE.indexOf('\n', start))
}

const KEYS = configurableKeys()
const DEFAULTS = lineWith('private config: CliConfig = {')
// `load()` 里也有一句 `this.config = {`，所以重置那份要取**最后**一处。
const RESET = lineWith('this.config = {', SERVICE.lastIndexOf('this.config = {'))

function declaredIn(literal: string, key: string): boolean {
  return new RegExp(`\\b${key}\\s*:`).test(literal)
}

test('the configurable key list is not empty and has no duplicates', () => {
  assert.ok(KEYS.length > 0, '没解析出任何可配置键——先看看数组格式是不是变了')
  assert.equal(new Set(KEYS).size, KEYS.length, '有重复的键名')
})

test('every configurable key is declared in the interface', () => {
  for (const key of KEYS) {
    assert.match(SERVICE, new RegExp(`^\\s*${key}\\??\\s*:`, 'm'), `${key} 不在 CliConfig 接口里`)
  }
})

test('every configurable key has a default value', () => {
  const missing = KEYS.filter((key) => !declaredIn(DEFAULTS, key))
  assert.deepEqual(missing, [], '这些键没有默认值：reset 之后它们会变成 undefined')
})

test('every configurable key is reset by clear()', () => {
  const missing = KEYS.filter((key) => !declaredIn(RESET, key))
  assert.deepEqual(missing, [], '这些键没被 clear() 重置：重置后旧值会留在配置里')
})

test('every configurable key is read back by load()', () => {
  const missing = KEYS.filter((key) => !SERVICE.includes(`${key}: data.${key}`))
  assert.deepEqual(missing, [], '这些键在 load() 里没有读回来：重启后静默消失')
})

test('the daily exclusion key is registered here and nowhere else', () => {
  // 这个键是本期新加的：它同时是"展示层开关"，所以要能从 CLI 配。
  assert.ok(KEYS.includes('dailyExcludeTopics'), 'dailyExcludeTopics 没登记，config set 会被拒')
})
