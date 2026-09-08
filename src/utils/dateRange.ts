export class DateRangeError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message)
  }
}

export function parseLocalDateOrIso(value: string | undefined, endOfDay = false): number | undefined {
  if (!value) return undefined
  let timestamp: number
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (match) {
    const [, year, month, day] = match
    const date = new Date(
      Number(year),
      Number(month) - 1,
      Number(day),
      endOfDay ? 23 : 0,
      endOfDay ? 59 : 0,
      endOfDay ? 59 : 0,
      endOfDay ? 999 : 0,
    )
    timestamp = date.getFullYear() === Number(year) &&
      date.getMonth() === Number(month) - 1 &&
      date.getDate() === Number(day)
      ? date.getTime()
      : Number.NaN
  } else {
    timestamp = Date.parse(value)
  }
  if (Number.isNaN(timestamp)) throw new DateRangeError('INVALID_DATE', '导出日期无效')
  return Math.floor(timestamp / 1000)
}

export function resolveExportDateRange(options: {
  date?: string
  from?: string
  to?: string
  preserveDateForRichHtml?: boolean
}): { from?: number; to?: number } {
  if (options.date && (options.from || options.to)) {
    throw new DateRangeError('CONFLICTING_DATE_FILTERS', '--date 不能与 --from/--to 同时使用')
  }

  let from = parseLocalDateOrIso(options.from)
  let to = parseLocalDateOrIso(options.to, true)
  if (options.date) {
    const dateStart = parseLocalDateOrIso(options.date)
    const dateEnd = parseLocalDateOrIso(options.date, true)
    if (!options.preserveDateForRichHtml) {
      from = dateStart
      to = dateEnd
    }
  }
  if (from !== undefined && to !== undefined && from > to) {
    throw new DateRangeError('INVALID_DATE_RANGE', '起始日期不能晚于结束日期')
  }
  return { from, to }
}
