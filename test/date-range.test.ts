import test from 'node:test'
import assert from 'node:assert/strict'
import { parseLocalDateOrIso, resolveExportDateRange } from '../src/utils/dateRange.js'

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
