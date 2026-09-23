import test from 'node:test'
import assert from 'node:assert/strict'
import { parseLocalDateOrIso, resolveExportDateRange, resolveSince, resolveUntil, nowLine }
  from '../src/utils/dateRange.js'

test('date-only export bounds use the local calendar day', () => {
  const range = resolveExportDateRange({ date: '2026-09-07' })
  const expectedStart = Math.floor(new Date(2026, 8, 7, 0, 0, 0, 0).getTime() / 1000)
  const expectedEnd = Math.floor(new Date(2026, 8, 7, 23, 59, 59, 999).getTime() / 1000)
  assert.deepEqual(range, { from: expectedStart, to: expectedEnd })
})

test('rich HTML validates date but keeps the date argument for media export', () => {
  assert.deepEqual(resolveExportDateRange({ date: '2026-09-07', preserveDateForRichHtml: true }), {
    from: undefined,
    to: undefined,
  })
})

test('date range rejects invalid, conflicting, and reversed values', () => {
  assert.throws(() => parseLocalDateOrIso('2026-02-30'), /导出日期无效/)
  assert.throws(() => resolveExportDateRange({ date: '2026-09-07', from: '2026-09-01' }), /不能与/)
  assert.throws(() => resolveExportDateRange({ from: '2026-09-08', to: '2026-09-07' }), /不能晚于/)
})

// ------------------------------------------------- 助手用的时间表达式

test('相对时间：3d / 2w / 12h / 裸数字', () => {
  const now = new Date(2026, 8, 23, 15, 30).getTime()   // 2026-09-23 15:30 本地
  const day = (y: number, m: number, d: number) => Math.floor(new Date(y, m - 1, d).getTime() / 1000)

  assert.equal(resolveSince('3d', now), day(2026, 9, 20), '三天前 = 那天零点，不是精确 72 小时')
  assert.equal(resolveSince('2w', now), day(2026, 9, 9))
  assert.equal(resolveSince('3', now), day(2026, 9, 20), '裸数字按天算')
  assert.equal(resolveSince('12h', now), Math.floor((now - 12 * 3600_000) / 1000), '小时按精确时长')
})

test('绝对日期：本地零点；上界含当天', () => {
  const start = resolveSince('2026-09-16')!
  const end = resolveUntil('2026-09-16')!
  assert.equal(new Date(start * 1000).getHours(), 0)
  assert.equal(new Date(end * 1000).getDate(), 16)
  assert.ok(end - start > 86_000, '上界要盖住一整天')
})

test('空值回 undefined；看不懂的抛错而不是猜一个窗口', () => {
  assert.equal(resolveSince(undefined), undefined)
  assert.equal(resolveSince(''), undefined)
  assert.throws(() => resolveSince('上周三'), /DateRangeError|日期无效/)
})

test('当前时间那一行：年月日 + 星期 + 时分', () => {
  const line = nowLine(new Date(2026, 8, 23, 9, 5))
  assert.match(line, /^2026-09-23（星期三）09:05$/)
})
