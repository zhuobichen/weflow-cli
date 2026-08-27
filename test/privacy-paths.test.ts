import test from 'node:test'
import assert from 'node:assert/strict'
import { homedir } from 'node:os'
import { expandHomePath } from '../src/utils/pathUtils.js'
import { maskMessageBodyText, redactText } from '../src/services/assistantPrivacy.js'

test('expands home directory prefixes without changing other paths', () => {
  assert.equal(expandHomePath('~'), homedir())
  assert.equal(expandHomePath('~/data'), `${homedir()}/data`)
  assert.equal(expandHomePath('C:\\data'), 'C:\\data')
  assert.equal(expandHomePath(''), '')
})

test('redacts common outbound PII in balanced mode', () => {
  const result = redactText(
    '电话 13812345678，邮箱 user@example.com，链接 https://example.com/a 密钥 sk-abcdefghijklmnop',
    'balanced',
    false,
  )

  assert.equal(result.redactions, 4)
  assert.equal(result.safe, '电话 [电话]，邮箱 [邮箱]，链接 [链接] 密钥 [密钥]')
})

test('does not redact open or local inference input', () => {
  const text = '13812345678 user@example.com'
  assert.deepEqual(redactText(text, 'open', false), { safe: text, redactions: 0 })
  assert.deepEqual(redactText(text, 'balanced', true), { safe: text, redactions: 0 })
})

test('masks third-party message bodies only in strict cloud mode', () => {
  assert.equal(maskMessageBodyText('私密消息', 'strict', false), '[内容4字已按严格模式屏蔽]')
  assert.equal(maskMessageBodyText('私密消息', 'balanced', false), '私密消息')
  assert.equal(maskMessageBodyText('私密消息', 'strict', true), '私密消息')
})
