import test from 'node:test'
import assert from 'node:assert/strict'
import { collectMessagesInRange } from '../src/services/messageQuery.js'
import type { Message } from '../src/types.js'

function message(createTime: number): Message {
  return {
    localId: createTime,
    serverId: String(createTime),
    localType: 1,
    createTime,
    isSend: 0,
    senderUsername: null,
    content: String(createTime),
    rawContent: String(createTime),
    parsedContent: String(createTime),
  }
}

test('message range pagination applies limit after date filtering', async () => {
  const source = [600, 500, 400, 300, 200, 100].map(message)
  const offsets: number[] = []
  const result = await collectMessagesInRange(async (limit, offset) => {
    offsets.push(offset)
    return source.slice(offset, offset + limit)
  }, 2, { from: 100, to: 350 }, 2)

  assert.deepEqual(result.map(item => item.createTime), [300, 200])
  assert.deepEqual(offsets, [0, 2, 4])
})

test('message range pagination stops after crossing the lower boundary', async () => {
  const source = [600, 500, 400, 300, 200, 100].map(message)
  let calls = 0
  const result = await collectMessagesInRange(async (limit, offset) => {
    calls += 1
    return source.slice(offset, offset + limit)
  }, 0, { from: 350 }, 2)

  assert.deepEqual(result.map(item => item.createTime), [600, 500, 400])
  assert.equal(calls, 2)
})
