import { test } from 'node:test'
import assert from 'node:assert/strict'
import { unconfiguredHint, unconfiguredHintInline } from '../src/utils/unconfiguredHint.js'

/**
 * "还没配置"时给的指引必须**照着能做**。
 * 起因：Linux 上 `sessions` 说"请先运行 weflow-cli init"，而 init 在 Linux 4.1.12.26+
 * 取不到密钥——用户照做只会再撞一次墙（issue #8 同类的"提示与实现不一致"）。
 */
test('Linux 上给的是口令/外部源三条路，不是"运行 init"', { skip: process.platform !== 'linux' }, () => {
  const hint = unconfiguredHint()
  assert.match(hint, /passphrase/)
  assert.match(hint, /verify-passphrase-all/)
  assert.match(hint, /--rss-feed/)
  assert.doesNotMatch(hint, /^请先运行 weflow-cli init$/)
})

test('非 Linux 上保持原来那句（Windows 的 init 确实能提密钥）', { skip: process.platform === 'linux' }, () => {
  assert.equal(unconfiguredHint(), '请先运行 weflow-cli init')
})

test('单行版本不含换行，便于塞进 JSON', () => {
  const inline = unconfiguredHintInline()
  assert.doesNotMatch(inline, /\n/)
  assert.ok(inline.length > 10)
})
