import test from 'node:test'
import assert from 'node:assert/strict'
import {
  collectMessagesInRange,
  collectMessagesInRangeDetailed,
} from '../src/services/messageQuery.js'
import type { Message } from '../src/types.js'

function message(localId: number, createTime: number): Message {
  return {
    localId,
    serverId: `synthetic-${localId}`,
    localType: 1,
    createTime,
    isSend: 0,
    senderUsername: 'synthetic-sender',
    senderDisplay: 'synthetic-sender',
    content: 'synthetic content',
    rawContent: 'synthetic content',
    parsedContent: 'synthetic content',
  }
}

/** Newest-first pages, the order the real readers return. */
function pager(all: Message[], pageSize: number) {
  const calls: Array<{ limit: number; offset: number }> = []
  const fetchPage = async (limit: number, offset: number): Promise<Message[]> => {
    calls.push({ limit, offset })
    // Honour the caller's limit even when it is smaller than the page size.
    return all.slice(offset, offset + Math.min(limit, pageSize))
  }
  return { fetchPage, calls }
}

const PAGE = 500

test('an exhausted conversation reports exhausted, not mayHaveMore', async () => {
  const all = Array.from({ length: 120 }, (_, i) => message(i, 1_700_000_000 + i)).reverse()
  const { fetchPage } = pager(all, PAGE)
  const result = await collectMessagesInRangeDetailed(fetchPage, 0, {})
  assert.equal(result.messages.length, 120)
  assert.equal(result.stoppedBy, 'exhausted')
  assert.equal(result.mayHaveMore, false)
  assert.equal(result.truncatedEarly, false)
})

test('reaching the requested from bound stops with range, not a warning', async () => {
  const all = Array.from({ length: 900 }, (_, i) => message(i, 1_700_000_000 + i)).reverse()
  const { fetchPage } = pager(all, PAGE)
  const result = await collectMessagesInRangeDetailed(fetchPage, 0, { from: 1_700_000_500 })
  assert.equal(result.stoppedBy, 'range')
  assert.equal(result.truncatedEarly, false)
  assert.ok(result.messages.every(m => m.createTime >= 1_700_000_500))
})

test('hitting the limit reports mayHaveMore', async () => {
  const all = Array.from({ length: 900 }, (_, i) => message(i, 1_700_000_000 + i)).reverse()
  const { fetchPage } = pager(all, PAGE)
  const result = await collectMessagesInRangeDetailed(fetchPage, 10, {})
  assert.equal(result.messages.length, 10)
  assert.equal(result.stoppedBy, 'limit')
  assert.equal(result.mayHaveMore, true)
})

test('a short page before from is reached is flagged as early truncation', async () => {
  // A live database can hand back a short page while rows are being written,
  // because offset paging shifts underneath. The old loop treated that exactly
  // like "the conversation ended" and stopped without saying so.
  const all = Array.from({ length: 900 }, (_, i) => message(i, 1_700_000_000 + i)).reverse()
  let call = 0
  const fetchPage = async (limit: number, offset: number): Promise<Message[]> => {
    call += 1
    // Second page comes back short even though older rows exist.
    if (call === 2) return all.slice(offset, offset + 100)
    return all.slice(offset, offset + Math.min(limit, PAGE))
  }
  const result = await collectMessagesInRangeDetailed(
    fetchPage, 0, { from: 1_700_000_000 })
  assert.equal(result.stoppedBy, 'short-page')
  assert.equal(result.truncatedEarly, true)
  assert.equal(result.mayHaveMore, true)
  assert.ok(result.messages.length < all.length, 'rows were left unread')
})

test('a short page with no from bound is not called early truncation', async () => {
  // Without a requested lower bound there is nothing to have fallen short of.
  const all = Array.from({ length: 120 }, (_, i) => message(i, 1_700_000_000 + i)).reverse()
  const { fetchPage } = pager(all, PAGE)
  const result = await collectMessagesInRangeDetailed(fetchPage, 0, {})
  assert.equal(result.stoppedBy, 'exhausted')
  assert.equal(result.truncatedEarly, false)
})

test('the original collector keeps its behaviour and return shape', async () => {
  const all = Array.from({ length: 900 }, (_, i) => message(i, 1_700_000_000 + i)).reverse()
  const { fetchPage } = pager(all, PAGE)
  const messages = await collectMessagesInRange(fetchPage, 0, { from: 1_700_000_500 })
  assert.ok(Array.isArray(messages), 'still a bare array for existing callers')
  assert.ok(messages.every(m => m.createTime >= 1_700_000_500))
})

test('both collectors agree on which messages are in range', async () => {
  const all = Array.from({ length: 640 }, (_, i) => message(i, 1_700_000_000 + i)).reverse()
  const plain = await collectMessagesInRange(
    pager(all, PAGE).fetchPage, 0, { from: 1_700_000_200, to: 1_700_000_600 })
  const detailed = await collectMessagesInRangeDetailed(
    pager(all, PAGE).fetchPage, 0, { from: 1_700_000_200, to: 1_700_000_600 })
  assert.deepEqual(detailed.messages, plain)
})
