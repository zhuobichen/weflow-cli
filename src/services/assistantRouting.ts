import type { WechatInboundMessage } from '../types.js'

export type AssistantAccessReason =
  | 'allowed'
  | 'direct-not-whitelisted'
  | 'group-not-whitelisted'
  | 'group-sender-missing'
  | 'group-sender-not-whitelisted'
  | 'group-mention-required'

export interface AssistantAccessPolicy {
  directWhitelist: string
  groupWhitelist: string
  requireGroupMention: boolean
}

export interface AssistantAccessDecision {
  allowed: boolean
  reason: AssistantAccessReason
}

function parseList(value: string): Set<string> {
  return new Set(value.split(/[,;\s]+/).map(item => item.trim()).filter(Boolean))
}

export function evaluateAssistantAccess(
  message: Pick<WechatInboundMessage, 'conversationType' | 'conversationId' | 'senderId' | 'mentionedBot'>,
  policy: AssistantAccessPolicy,
): AssistantAccessDecision {
  const directWhitelist = parseList(policy.directWhitelist)
  if (message.conversationType !== 'group') {
    return directWhitelist.has(message.senderId)
      ? { allowed: true, reason: 'allowed' }
      : { allowed: false, reason: 'direct-not-whitelisted' }
  }

  if (!parseList(policy.groupWhitelist).has(message.conversationId)) {
    return { allowed: false, reason: 'group-not-whitelisted' }
  }
  if (!message.senderId) return { allowed: false, reason: 'group-sender-missing' }
  if (!directWhitelist.has(message.senderId)) {
    return { allowed: false, reason: 'group-sender-not-whitelisted' }
  }
  if (policy.requireGroupMention && !message.mentionedBot) {
    return { allowed: false, reason: 'group-mention-required' }
  }
  return { allowed: true, reason: 'allowed' }
}

function firstString(record: Record<string, any>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function isBotMentioned(record: Record<string, any>, botId: string): boolean {
  if (record.is_at_bot === true || record.is_mentioned === true || record.mentioned_bot === true) return true
  if (!botId) return false
  for (const key of ['at_user_ids', 'mentioned_user_ids', 'mention_list']) {
    const value = record[key]
    if (Array.isArray(value) && value.some(item => String(item) === botId)) return true
  }
  return false
}

/**
 * Accept group routing only when the upstream payload explicitly identifies a group.
 * Unknown payloads remain direct messages so a platform change cannot silently enable groups.
 */
export function resolveInboundRouting(record: Record<string, any>, botId = ''): Pick<WechatInboundMessage, 'conversationType' | 'conversationId' | 'senderId' | 'mentionedBot'> {
  const fromUserId = firstString(record, ['from_user_id', 'fromUserId'])
  const groupId = firstString(record, ['chatroom_id', 'chat_room_id', 'group_id', 'groupId', 'room_id', 'roomId'])
    || (fromUserId.endsWith('@chatroom') ? fromUserId : '')
  if (!groupId) {
    return {
      conversationType: 'direct',
      conversationId: fromUserId,
      senderId: fromUserId,
      mentionedBot: false,
    }
  }

  const explicitSender = firstString(record, ['sender_id', 'sender_user_id', 'member_id', 'from_member_id'])
  return {
    conversationType: 'group',
    conversationId: groupId,
    senderId: explicitSender || (fromUserId !== groupId ? fromUserId : ''),
    mentionedBot: isBotMentioned(record, botId),
  }
}
