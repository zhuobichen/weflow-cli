import type { Message } from '../types.js'

export type WeFlowMessageType =
  | 'text' | 'image' | 'voice' | 'card' | 'video' | 'emoji'
  | 'location' | 'link' | 'call' | 'system' | 'quote' | 'other'

const MESSAGE_TYPE_NAMES: Record<number, WeFlowMessageType> = {
  1: 'text',
  3: 'image',
  34: 'voice',
  42: 'card',
  43: 'video',
  47: 'emoji',
  48: 'location',
  49: 'link',
  50: 'call',
  10000: 'system',
  10002: 'quote',
}

export interface WeFlowMessageEnvelope {
  schema: 'weflow-message/v1'
  source: 'weflow-cli'
  generatedAt: string
  coverage?: WeFlowMessageCoverage
  messages: Array<Message & { messageType: WeFlowMessageType }>
}

export interface WeFlowMessageCoverage {
  requestedFrom?: number
  requestedTo?: number
  requestedLimit: number
  returned: number
  mayHaveMore: boolean
  oldestCreateTime?: number
  newestCreateTime?: number
}

export function toWeFlowMessages(messages: Message[]): WeFlowMessageEnvelope['messages'] {
  return messages.map((message) => ({
    ...message,
    messageType: MESSAGE_TYPE_NAMES[message.localType] || 'other',
  }))
}

export function createWeFlowEnvelope(
  messages: Message[],
  generatedAt = new Date().toISOString(),
  coverage?: Omit<WeFlowMessageCoverage, 'returned' | 'oldestCreateTime' | 'newestCreateTime' | 'mayHaveMore'>,
): WeFlowMessageEnvelope {
  let oldestCreateTime: number | undefined
  let newestCreateTime: number | undefined
  for (const message of messages) {
    const timestamp = Number(message.createTime)
    if (!Number.isFinite(timestamp)) continue
    oldestCreateTime = oldestCreateTime === undefined ? timestamp : Math.min(oldestCreateTime, timestamp)
    newestCreateTime = newestCreateTime === undefined ? timestamp : Math.max(newestCreateTime, timestamp)
  }
  const result: WeFlowMessageEnvelope = {
    schema: 'weflow-message/v1',
    source: 'weflow-cli',
    generatedAt,
    messages: toWeFlowMessages(messages),
  }
  if (coverage) {
    result.coverage = {
      ...coverage,
      returned: messages.length,
      mayHaveMore: coverage.requestedLimit > 0 && messages.length >= coverage.requestedLimit,
      ...(oldestCreateTime !== undefined ? {
        oldestCreateTime,
        newestCreateTime,
      } : {}),
    }
  }
  return result
}
