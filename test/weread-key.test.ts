import { test } from 'node:test'
import assert from 'node:assert/strict'
import { resolveWereadApiKey, WereadService } from '../src/services/wereadService.js'

// 微信读书的 key 有**两个来源**：官方配置文档给的写法是环境变量 `WEREAD_API_KEY`，
// 而助手的 `get_weread` 可用性判据读的是配置项 `wereadApiKey`。
//
// 2026-09-28 实测踩到的正是"只认一个"：按 CLI 提示把配置项配好之后，助手那边能用了，
// `weread shelf` 却回"未设置 WEREAD_API_KEY" —— 用户看不出来是两条路各读各的。
// 所以这里钉的是"两个来源都认"，以及优先级。

test('只有环境变量时用它', () => {
  assert.equal(resolveWereadApiKey('wrk-from-env', ''), 'wrk-from-env')
})

test('只有配置项时也认（这是 2026-09-28 补的那条路）', () => {
  assert.equal(resolveWereadApiKey('', 'wrk-from-config'), 'wrk-from-config')
  assert.equal(resolveWereadApiKey(undefined, 'wrk-from-config'), 'wrk-from-config')
})

test('两个都有时环境变量优先', () => {
  // 官方文档明确建议"不要把 api-key 直接扔给 AI"，所以让它能盖过落盘的那个
  assert.equal(resolveWereadApiKey('wrk-env', 'wrk-config'), 'wrk-env')
})

test('空白一律当没配', () => {
  // `config set` 允许把值清空，别把一串空白当成 key 发出去
  assert.equal(resolveWereadApiKey('   ', '\t\n'), '')
  assert.equal(resolveWereadApiKey(null, null), '')
  assert.equal(resolveWereadApiKey(undefined, undefined), '')
})

test('前后空白会被去掉', () => {
  assert.equal(resolveWereadApiKey('  wrk-env  ', ''), 'wrk-env')
})

test('不传 key 时构造器自己找（环境变量这条）', () => {
  // 配置项那条路依赖本机真实配置，测不了确定值；它由端到端验过：
  // 配好 wereadApiKey、重启助手、问"我在读什么书"，工具能返回书架。
  const saved = process.env.WEREAD_API_KEY
  try {
    process.env.WEREAD_API_KEY = 'wrk-env-probe'
    assert.equal(new WereadService().apiKey, 'wrk-env-probe')
  } finally {
    if (saved === undefined) delete process.env.WEREAD_API_KEY
    else process.env.WEREAD_API_KEY = saved
  }
})
