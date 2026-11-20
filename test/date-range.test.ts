import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeRelativeDate, parseLocalDateOrIso, resolveExportDateRange, resolveSince, resolveUntil, nowLine }
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

test('中文相对说法：能唯一确定的那几个直接认', () => {
  const now = new Date(2026, 8, 23, 15, 30).getTime()   // 2026-09-23 15:30 本地
  const day = (y: number, m: number, d: number) => Math.floor(new Date(y, m - 1, d).getTime() / 1000)
  assert.equal(resolveSince('今天', now), day(2026, 9, 23))
  assert.equal(resolveSince('昨天', now), day(2026, 9, 22))
  assert.equal(resolveSince('前天', now), day(2026, 9, 21))
  assert.equal(resolveSince('大前天', now), day(2026, 9, 20))
  assert.equal(resolveSince('5天前', now), day(2026, 9, 18))
})

test('模糊的"最近"不是错误，而是"没给窗口"', () => {
  // 实测：模型会把 since 填成「最近」，而旧实现直接抛错 → 工具回「时间看不懂」→
  // 模型于是答"请给个具体时间范围"，用户被自己的助手挡在门外。
  for (const word of ['最近', '这几天', '这两天', '近期', '这段时间']) {
    assert.equal(resolveSince(word), undefined, `「${word}」应当按"没给"处理，而不是抛错`)
    assert.equal(resolveUntil(word), undefined)
  }
})

test('含混但有明确超集的说法：给宽窗口，不猜具体哪天', () => {
  const now = new Date(2026, 8, 23, 15, 30).getTime()
  const day = (y: number, m: number, d: number) => Math.floor(new Date(y, m - 1, d).getTime() / 1000)
  // 7 天窗口**一定包含**"上周"，多给几条不会误导；而报错会让模型去反问用户
  assert.equal(resolveSince('上周', now), day(2026, 9, 16))
  assert.equal(resolveSince('这周', now), day(2026, 9, 16))
  assert.equal(resolveSince('上个月', now), day(2026, 8, 24))
})

test('当前时间那一行：年月日 + 星期 + 时分', () => {
  const line = nowLine(new Date(2026, 8, 23, 9, 5))
  assert.match(line, /^2026-09-23（星期三）09:05$/)
})

test('带时段修饰的相对日期要认得（"昨天下午" ≠ 看不懂）', () => {
  // 2026-11-20 实测：用户说「我和彪弟昨天下午聊了什么」，模型把 since 填成用户原话
  // "昨天下午"，而词表只认孤立的"昨天" → resolveSince 抛错 → 工具说"时间看不懂" →
  // 助手反过来让用户"明确说昨天下午"（他刚说过）。时段词不改变窗口边界，剥掉即可。
  const now = new Date('2026-11-20T10:00:00+08:00').getTime()
  for (const text of ['昨天下午', '昨天 下午', '昨天上午', '昨天晚上', '昨天晚上']) {
    assert.equal(normalizeRelativeDate(text), '1d', text)
    assert.equal(resolveSince(text, now), resolveSince('昨天', now), text)
  }
  assert.equal(normalizeRelativeDate('前天晚上'), '2d')
  assert.equal(normalizeRelativeDate('今天下午'), '0d')
  // 时段词单独出现时仍然认不出（不猜）
  assert.throws(() => resolveSince('下午', now))
})
