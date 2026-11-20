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
  const text = normalizeRelativeDate(String(value ?? ''))
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

/**
 * 中文相对说法 → 工具认得的写法（`0d` / `2d` / `3d`）。
 *
 * **为什么在这一层做，而不是让模型自己算**：`resolveSince` 的注释里已经写了那件事——模型
 * 没法可靠地把"上周三"算成日期，所以工具收 `3d` 这种写法。但中文用户嘴里说出来的是
 * 「前天」「昨天」「大前天」，不是 `2d`。让模型先做两次翻译（中文→天数→参数）等于在一个
 * 它本来就不擅长的步骤上多给一次出错机会：实测千问 flash 在「前天的聊天里说了什么」这种
 * 问法上干脆**不带 since**，于是按"最近 N 条"取，取到的根本不是那天。
 *
 * 只收**能唯一确定**的那几个（今天/昨天/前天/大前天/前天这类、`N天前`）。「上周」「上个月」
 * 故意不收：它们指日历上的上一周/上一月，"7 天前"与"上周"不是一回事，猜一个比如实报错更糟
 * （同这个文件里那条"猜一个比报错更糟"）。
 */
const RELATIVE_DAY_WORDS: Array<[RegExp, string]> = [
  [/^今天$|^今日$/, '0d'],
  [/^昨天$|^昨日$/, '1d'],
  [/^前天$|^前日$/, '2d'],
  [/^大前天$/, '3d'],
]

/**
 * **模糊的"最近"**：不该报错，就当用户没给时间窗（工具默认给最近若干条）。
 *
 * 实测（2026-11-20，qwen-flash）：「甲最近跟我说了什么」它把 `since` 填成 `"最近"`，
 * 而 `resolveSince` 认不出这个词**直接抛错**，于是工具返回「时间看不懂」，模型接着答
 * 「请给个具体时间范围」——用户被自己的助手挡在门外，而他一分钟前刚说过"最近"。
 *
 * 判定：这类词**不指定窗口**，所以正确的处理是 `undefined`（= 用默认条数），不是报错，
 * 也不是猜一个 7 天。这与"猜一个比报错更糟"是同一条原则的两面：既不猜，也不要因为没信息就失败。
 */
const VAGUE_RECENT = /^(最近|近期|这几天|这两天|这段时间|最近几天|前阵子|最近一段)$/

/**
 * **能落成一个"肯定包含用户所指"的窗口**的说法：给超集，不去猜精确边界。
 *
 * 「上周」严格说是日历上的上一周，与"7 天前"不是一回事——但 7 天窗口**一定包含**它，
 * 多给几条消息不会误导，而报错会让模型去反问用户。区别在于：模糊词（上面那张表）当"没给"，
 * 这里是当"给了一个宽窗口"，两者都不猜具体是哪一天。
 */
const WINDOW_WORDS: Array<[RegExp, string]> = [
  [/^(这周|本周|上周|这一周)$/, '7d'],
  [/^(这个月|本月|上个月|上月)$/, '30d'],
]

export function normalizeRelativeDate(text: string): string {
  const trimmed = text.trim()
  if (VAGUE_RECENT.test(trimmed)) return ''
  // 先剥掉"上午/下午/晚上/中午"这类**时段修饰**再认日期。
  //
  // 2026-11-20 实测：用户说「我和彪弟昨天下午聊了什么」，模型把 since 填成用户的原话
  // `"昨天下午"`——而词表只认孤零零的 `"昨天"`，于是 `resolveSince` 抛「导出日期无效」，
  // 工具返回"时间看不懂"，助手再让用户"明确说昨天下午"（用户刚说过）。
  // 时段词不改变窗口边界（"昨天下午"仍按昨天整天取），所以剥掉就是正确做法，不是猜。
  const stripped = trimmed.replace(/(早上|上午|中午|下午|傍晚|晚上|夜里|凌晨|半夜)/g, '').trim()
  const source = stripped || trimmed
  for (const [pattern, value] of RELATIVE_DAY_WORDS) {
    if (pattern.test(source)) return value
  }
  for (const [pattern, value] of WINDOW_WORDS) {
    if (pattern.test(source)) return value
  }
  const daysAgo = source.match(/^(\d+)\s*天前$/)
  if (daysAgo) return `${daysAgo[1]}d`
  return trimmed
}

/** 上界：`2026-09-16` 指**含当天**（当天 23:59:59），因为"到那天为止"总是指一整天。 */
export function resolveUntil(value: unknown): number | undefined {
  const text = normalizeRelativeDate(String(value ?? ''))
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
