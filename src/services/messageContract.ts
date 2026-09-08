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
  messages: Array<Message & { messageType: WeFlowMessageType }>
}

export function toWeFlowMessages(messages: Message[]): WeFlowMessageEnvelope['messages'] {
  return messages.map((message) => ({
    ...message,
    messageType: MESSAGE_TYPE_NAMES[message.localType] || 'other',
  }))
}

export function createWeFlowEnvelope(messages: Message[], generatedAt = new Date().toISOString()): WeFlowMessageEnvelope {
  return {
    schema: 'weflow-message/v1',
    source: 'weflow-cli',
    generatedAt,
    messages: toWeFlowMessages(messages),
  }
}
