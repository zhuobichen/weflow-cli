import { createHash } from 'crypto'
import { mkdirSync, writeFileSync } from 'fs'
import { join } from 'path'
import type { Message } from '../types.js'
import { redactText, type PrivacyMode } from './assistantPrivacy.js'

export interface EvidenceManifest {
  schemaVersion: '1.0'
  generatedAt: string
  source: 'local-wechat'
  authorization: 'user-authorized-local-data'
  talker: string
  caseNote: string
  messageCount: number
  firstMessageAt: string | null
  lastMessageAt: string | null
  messageIds: number[]
  messagesSha256: string
  notice: string
}

export interface EvidencePackageResult {
  path: string
  manifest: EvidenceManifest
}

export interface EvidenceReviewInput {
  transcript: string
  redactions: number
  localInference: boolean
}

const LEGAL_NOTICE = '本包用于本地证据整理，不代表任何法院或机构必然采信；请保留原设备和原始数据，并根据案件情况咨询专业律师。'

function sha256(value: string): string {
  return createHash('sha256').update(value, 'utf8').digest('hex')
}

function isoTime(timestamp: number | undefined): string | null {
  if (!timestamp || !Number.isFinite(timestamp)) return null
  return new Date(timestamp * 1000).toISOString()
}

function safePackageName(talker: string): string {
  const name = talker.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^\.+/, '')
  return (name || 'conversation').slice(0, 80)
}

export function buildEvidenceManifest(talker: string, messages: Message[], caseNote = '', generatedAt = new Date().toISOString()): EvidenceManifest {
  const serialized = JSON.stringify(messages, null, 2)
  const timestamps = messages.map(message => message.createTime).filter(Number.isFinite)
  return {
    schemaVersion: '1.0',
    generatedAt,
    source: 'local-wechat',
    authorization: 'user-authorized-local-data',
    talker,
    caseNote: caseNote.trim().slice(0, 500),
    messageCount: messages.length,
    firstMessageAt: timestamps.length ? isoTime(Math.min(...timestamps)) : null,
    lastMessageAt: timestamps.length ? isoTime(Math.max(...timestamps)) : null,
    messageIds: messages.map(message => message.localId).filter(Number.isFinite),
    messagesSha256: sha256(serialized),
    notice: LEGAL_NOTICE,
  }
}

export function writeEvidencePackage(outputRoot: string, talker: string, messages: Message[], caseNote = ''): EvidencePackageResult {
  const packagePath = join(outputRoot, `${safePackageName(talker)}-${Date.now()}`)
  mkdirSync(packagePath, { recursive: true })
  const messagesText = JSON.stringify(messages, null, 2)
  const manifest = buildEvidenceManifest(talker, messages, caseNote)
  writeFileSync(join(packagePath, 'messages.json'), messagesText, { encoding: 'utf8', flag: 'wx' })
  writeFileSync(join(packagePath, 'manifest.json'), JSON.stringify(manifest, null, 2), { encoding: 'utf8', flag: 'wx' })
  writeFileSync(join(packagePath, 'README.md'), [
    '# 本地聊天证据整理包',
    '',
    `- 消息数量：${manifest.messageCount}`,
    `- 生成时间：${manifest.generatedAt}`,
    `- 原始消息 SHA-256：\`${manifest.messagesSha256}\``,
    '',
    '## 使用边界',
    '',
    LEGAL_NOTICE,
    '',
    '请勿修改 `messages.json`。如需脱敏、摘要或分析，请复制后处理，并保留处理前后的文件哈希和操作记录。',
    '',
    '## 建议补充',
    '',
    '- 保留原设备、原始数据库和官方导出记录。',
    '- 记录导出人、导出时间、工具版本和数据范围。',
    '- 保存完整上下文，不要只截取对自己有利的片段。',
    '- 将本包交给律师或有权处理案件的机构复核。',
    '',
  ].join('\n'), { encoding: 'utf8', flag: 'wx' })
  return { path: packagePath, manifest }
}

export const evidenceLegalNotice = LEGAL_NOTICE

export function buildEvidenceReviewInput(talker: string, messages: Message[], mode: PrivacyMode, localInference: boolean): EvidenceReviewInput {
  let redactions = 0
  const safeMetadata = (value: string, fallback: string): string => {
    if (localInference) return value || fallback
    if (mode === 'strict') return fallback
    const result = redactText(value || fallback, mode, false)
    redactions += result.redactions
    return result.safe
  }
  const safeTalker = safeMetadata(talker, '[会话已按严格隐私模式屏蔽]')
  const lines = messages.map(message => {
    const rawBody = message.content || message.parsedContent || message.rawContent || ''
    const body = mode === 'strict' && !localInference
      ? `[聊天正文已按严格隐私模式屏蔽，共${rawBody.length}字]`
      : (() => {
          const result = redactText(rawBody, mode, localInference)
          redactions += result.redactions
          return result.safe
        })()
    const time = isoTime(message.createTime) || '时间未知'
    const sender = safeMetadata(message.senderUsername || '', '[发送方已按严格隐私模式屏蔽]')
    return `[消息ID:${message.localId}] [${time}] [发送方:${sender}] ${body}`
  })
  return {
    transcript: `会话：${safeTalker}\n消息数：${messages.length}\n\n${lines.join('\n')}`,
    redactions,
    localInference,
  }
}
