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

/** Why the paging loop stopped. The first two are expected; the last two are not. */
export type StopReason =
  /** Reached the requested `from` bound. */
  | 'range'
  /** A page came back empty - there is nothing older. */
  | 'exhausted'
  /** Collected the caller's limit. */
  | 'limit'
  /** A page shorter than pageSize ended the loop before `from` was reached. */
  | 'short-page'

export interface DetailedMessageCollection {
  messages: Message[]
  pages: number
  stoppedBy: StopReason
  mayHaveMore: boolean
  /**
   * A short page ended the loop while the requested `from` was still ahead
   * of us.
   *
   * `collectMessagesInRange` breaks on a short page, which is right when the
   * page is short because the conversation ended - but a live database can
   * also return a short page while rows are being written, because offset
   * paging shifts underneath. The two are indistinguishable from the result
   * alone, so this records the difference instead of assuming the benign one.
   */
  truncatedEarly: boolean
}

/**
 * `collectMessagesInRange` plus the reason it stopped.
 *
 * Kept separate rather than folded into the original: that one is covered by
 * existing tests and used by every export path, and changing what it returns
 * would change those callers.
 */
export async function collectMessagesInRangeDetailed(
  fetchPage: (limit: number, offset: number) => Promise<Message[]>,
  limit: number,
  range: MessageTimeRange,
  pageSize = 500,
): Promise<DetailedMessageCollection> {
  const messages: Message[] = []
  let offset = 0
  let pages = 0
  let stoppedBy: StopReason = 'exhausted'
  let reachedFrom = range.from === undefined

  while (true) {
    const page = await fetchPage(pageSize, offset)
    pages += 1
    if (page.length === 0) {
      stoppedBy = 'exhausted'
      reachedFrom = true
      break
    }

    let oldestTimestamp = Number.POSITIVE_INFINITY
    for (const message of page) {
      const timestamp = Number(message.createTime)
      if (!Number.isFinite(timestamp)) continue
      oldestTimestamp = Math.min(oldestTimestamp, timestamp)
      if (range.from !== undefined && timestamp < range.from) continue
      if (range.to !== undefined && timestamp > range.to) continue
      messages.push(message)
      if (limit > 0 && messages.length >= limit) {
        return { messages, pages, stoppedBy: 'limit', mayHaveMore: true, truncatedEarly: false }
      }
    }

    offset += page.length
    if (range.from !== undefined && oldestTimestamp < range.from) {
      stoppedBy = 'range'
      reachedFrom = true
      break
    }
    if (page.length < pageSize) {
      // A short *first* page is unambiguous: nothing has shifted yet, so the
      // conversation simply has fewer than pageSize messages. A short later
      // page is the ambiguous one offset paging can produce on a live
      // database, so it keeps its own name.
      stoppedBy = pages === 1 ? 'exhausted' : 'short-page'
      break
    }
  }

  return {
    messages,
    pages,
    stoppedBy,
    // A limit hit is handled above; anything else that is not 'range' may
    // still have older matching rows we never looked at.
    mayHaveMore: stoppedBy !== 'range' && stoppedBy !== 'exhausted',
    truncatedEarly: stoppedBy === 'short-page' && !reachedFrom,
  }
}
