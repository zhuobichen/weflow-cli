import { test } from 'node:test'
import assert from 'node:assert/strict'
import { planInitFix } from '../src/services/initFix.js'

/** check --fix 的执行计划：推断要保守，宁可不做也不猜。 */
test('已配置时不给计划', () => {
  assert.equal(planInitFix({ configured: true, detectedPath: 'D:/wx', lastInitError: null }), null)
})

test('探测不到数据目录时不猜（返回 null）', () => {
  assert.equal(planInitFix({ configured: false, detectedPath: null, lastInitError: null }), null)
  assert.equal(planInitFix({ configured: false, detectedPath: '   ', lastInitError: null }), null)
})

test('找到目录但没初始化：给出带 --path 的计划', () => {
  const plan = planInitFix({ configured: false, detectedPath: 'D:/wx', lastInitError: null })
  assert.ok(plan)
  assert.deepEqual(plan!.args, ['init', '--path', 'D:/wx'])
  assert.match(plan!.command, /^weflow-cli init --path D:\/wx$/)
  assert.match(plan!.reason, /还没有初始化/)
})

test('上次 init 失败时，原因进到计划里（这就是 issue #13 的场景）', () => {
  const plan = planInitFix({
    configured: false,
    detectedPath: 'D:/wx',
    lastInitError: { message: '--json 只输出预览，不会真正初始化' },
  })
  assert.ok(plan)
  assert.match(plan!.reason, /--json 只输出预览/)
})

test('含空格的路径在展示命令里加引号（复制即可用）', () => {
  const plan = planInitFix({ configured: false, detectedPath: 'D:/微信 数据', lastInitError: null })
  assert.ok(plan)
  assert.match(plan!.command, /"D:\/微信 数据"/)
})
