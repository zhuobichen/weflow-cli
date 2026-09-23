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

/**
 * 助手工具用的时间表达式：`2026-09-16`（本地零点）、`3d` / `2w` / `12h`、以及裸数字（= 天数）。
 * 回**秒**——与消息里的 `createTime` 同一单位。
 *
 * 为什么允许相对写法：模型没法可靠地把"上周三"算成日期（系统提示里给了当前时间也只是够用），
 * 而"过去三天"这种说法只要一个 `3d` 就表达了，不用它自己数日子。
 *
 * 日/周**按当天零点**而不是精确 24 小时前：人说的"三天"指的是那三天，不是一个瞬时。
 * 给不出明确的窗口就抛——**猜一个比报错更糟**，模型会以为它查的正是自己说的那段时间。
 */
export function resolveSince(value: unknown, nowMs = Date.now()): number | undefined {
  const text = String(value ?? '').trim()
  if (!text) return undefined
  const relative = text.match(/^(\d+)\s*([dhw])$/i)
  if (relative) {
    const amount = Number(relative[1])
    const unit = relative[2].toLowerCase()
    if (unit === 'h') return Math.floor((nowMs - amount * 3_600_000) / 1000)
    return startOfDayMs(nowMs - amount * (unit === 'w' ? 7 : 1) * 86_400_000)
  }
  if (/^\d+$/.test(text)) return startOfDayMs(nowMs - Number(text) * 86_400_000)
  return parseLocalDateOrIso(text)
}

/** 上界：`2026-09-16` 指**含当天**（当天 23:59:59），因为"到那天为止"总是指一整天。 */
export function resolveUntil(value: unknown): number | undefined {
  const text = String(value ?? '').trim()
  if (!text) return undefined
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return parseLocalDateOrIso(text, true)
  return parseLocalDateOrIso(text)
}

function startOfDayMs(ms: number): number {
  const date = new Date(ms)
  date.setHours(0, 0, 0, 0)
  return Math.floor(date.getTime() / 1000)
}

/** 给系统提示/工具输出用的一行当前时间。测试要能固定它，所以 `now` 可注入。 */
export function nowLine(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  const week = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()]
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}（星期${week}）`
       + `${pad(now.getHours())}:${pad(now.getMinutes())}`
}
