/**
 * Jev 多选能力（2026-11-25 实测，供 D-191 参考）。
 *
 * 问题：`choice` 是单选题（互斥概率、argmax 唯一），那 Jev 能不能一次选多个工具？
 * 答案：能——用**多个独立的 `noul` 判断题**，一次 API 调用里并行问 N 个工具。
 *
 * 这个测试不联网，钉住的是协议解读（防止将来有人把 systemone 当单选）：
 * - `noul` 校验只要求 0~1 概率，**没有 argmax / 互斥 / 和≈1 的约束**；
 * - 因此 N 个 noul 答案可以同时为高（多选），也可以同时为低（都不用）。
 *
 * 真机实测（jev-1.14-20261014，一次调用 ~$0.000018）：
 *   "整理甲乙聊天 + 看待办 + 今天公众号推了什么" →
 *   get_messages 0.90 / get_todos 0.95 / get_daily_report 0.90 / get_stats 0.09
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const client = readFileSync(join(ROOT, 'scripts', 'jev_client.py'), 'utf8')

test('noul 校验只认概率范围，没有 argmax / 互斥 / 和≈1 约束（所以能多选）', () => {
  const fn = client.slice(client.indexOf('def _check_noul'), client.indexOf('def build_openrouter_prompt'))
  assert.match(fn, /0\.0 <= float\(value\) <= 1\.0/, '必须校验概率范围')
  assert.doesNotMatch(fn, /argmax/, 'noul 不该有 argmax 约束——那是 choice 的')
  assert.doesNotMatch(fn, /sum\(/, 'noul 不该要求概率和≈1——那是 choice 互斥的')
  assert.doesNotMatch(fn, /互斥|mutually/, 'noul 各题独立')
})

test('choice 校验有 argmax 与键集一致（所以它是单选，不能当多选用）', () => {
  const fn = client.slice(client.indexOf('def _check_choice'), client.indexOf('def _check_score'))
  assert.match(fn, /choice == argmax|picked != top/, 'choice 必须 argmax——单选的证据')
  assert.match(fn, /missing or extra/, 'choice 的 probabilities 键必须等于 criteria——封闭集合')
})

test('一次调用多问题的成本注释还在（多问一个问题边际成本近零）', () => {
  assert.match(client, /12 个问题.*0\.91s/, '删掉这个实测记录就等于丢了"多选不贵"的依据')
})
