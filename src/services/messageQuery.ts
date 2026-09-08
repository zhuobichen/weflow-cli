import type { Message } from '../types.js'

export interface MessageTimeRange {
  from?: number
  to?: number
}

export async function collectMessagesInRange(
  fetchPage: (limit: number, offset: number) => Promise<Message[]>,
  limit: number,
  range: MessageTimeRange,
  pageSize = 500,
): Promise<Message[]> {
  const result: Message[] = []
  let offset = 0

  while (true) {
    const page = await fetchPage(pageSize, offset)
    if (page.length === 0) break

    let oldestTimestamp = Number.POSITIVE_INFINITY
    for (const message of page) {
      const timestamp = Number(message.createTime)
      if (!Number.isFinite(timestamp)) continue
      oldestTimestamp = Math.min(oldestTimestamp, timestamp)
      if (range.from !== undefined && timestamp < range.from) continue
      if (range.to !== undefined && timestamp > range.to) continue
      result.push(message)
      if (limit > 0 && result.length >= limit) return result
    }

    offset += page.length
    if (page.length < pageSize) break
    if (range.from !== undefined && oldestTimestamp < range.from) break
  }

  return result
}
