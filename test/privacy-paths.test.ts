import test from 'node:test'
import assert from 'node:assert/strict'
import { homedir } from 'node:os'
import { expandHomePath } from '../src/utils/pathUtils.js'
import { maskMessageBodyText, redactText } from '../src/services/assistantPrivacy.js'
import { buildEvidenceManifest, buildEvidenceReviewInput, writeEvidencePackage } from '../src/services/evidenceService.js'
import { evaluateAssistantAccess, resolveInboundRouting } from '../src/services/assistantRouting.js'
import { isCoverImage, safeChildPath, safeDate } from '../src/utils/mcpSecurity.js'
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { Message } from '../src/types.js'
import { DbPathService } from '../src/core/dbPathService.js'

test('expands home directory prefixes without changing other paths', () => {
  assert.equal(expandHomePath('~'), homedir())
  assert.equal(expandHomePath('~/data'), `${homedir()}/data`)
  assert.equal(expandHomePath('C:\\data'), 'C:\\data')
  assert.equal(expandHomePath(''), '')
})

test('normalizes WeChat data, account, and database subdirectory paths', () => {
  const root = mkdtempSync(join(tmpdir(), 'weflow-db-path-'))
  const account = join(root, 'wxid_test_account')
  const dbStorage = join(account, 'db_storage')
  const message = join(dbStorage, 'message')
  mkdirSync(message, { recursive: true })
  const service = new DbPathService()

  assert.equal(service.resolveDataRoot(root), root)
  assert.equal(service.resolveDataRoot(account), root)
  assert.equal(service.resolveDataRoot(dbStorage), root)
  assert.equal(service.resolveDataRoot(message), root)
  const customParent = join(root, 'Tencent', 'WeChatData')
  mkdirSync(join(customParent, 'xwechat_files'), { recursive: true })
  const nestedAccount = join(customParent, 'xwechat_files', 'wxid_nested_account')
  mkdirSync(join(nestedAccount, 'db_storage', 'message'), { recursive: true })
  assert.equal(service.resolveDataRoot(customParent), join(customParent, 'xwechat_files'))
  assert.equal(service.resolveDataRoot(join(root, 'missing')), null)
  assert.deepEqual(service.scanWxids(message).map(item => item.wxid), ['wxid_test_account'])
})

test('redacts common outbound PII in balanced mode', () => {
  const result = redactText(
    '电话 13812345678，邮箱 user@example.com，链接 https://example.com/a 密钥 sk-abcdefghijklmnop',
    'balanced',
    false,
  )

  assert.equal(result.redactions, 4)
  assert.equal(result.safe, '电话 [电话]，邮箱 [邮箱]，链接 [链接] 密钥 [密钥]')
})

test('does not redact open or local inference input', () => {
  const text = '13812345678 user@example.com'
  assert.deepEqual(redactText(text, 'open', false), { safe: text, redactions: 0 })
  assert.deepEqual(redactText(text, 'balanced', true), { safe: text, redactions: 0 })
})

test('masks third-party message bodies only in strict cloud mode', () => {
  assert.equal(maskMessageBodyText('私密消息', 'strict', false), '[内容4字已按严格模式屏蔽]')
  assert.equal(maskMessageBodyText('私密消息', 'balanced', false), '私密消息')
  assert.equal(maskMessageBodyText('私密消息', 'strict', true), '私密消息')
})

test('rejects MCP path traversal and invalid dates', () => {
  const root = join(tmpdir(), 'weflow-test-root')
  assert.equal(safeChildPath(root, 'article.md'), join(root, 'article.md'))
  assert.equal(safeChildPath(root, '../secret.txt'), null)
  assert.equal(safeChildPath(root, root), null)
  assert.equal(safeDate('2026-08-27'), '2026-08-27')
  assert.equal(safeDate('2026-8-27'), null)
  assert.equal(safeDate('../secret'), null)
})

test('accepts real image signatures and rejects non-images', () => {
  const dir = mkdtempSync(join(tmpdir(), 'weflow-cover-'))
  const png = join(dir, 'cover.bin')
  const text = join(dir, 'text.bin')
  writeFileSync(png, Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
  writeFileSync(text, 'not an image')
  assert.equal(isCoverImage(png), true)
  assert.equal(isCoverImage(text), false)
  assert.equal(isCoverImage(join(dir, 'missing.png')), false)
})

test('builds a deterministic local evidence manifest from synthetic messages', () => {
  const messages: Message[] = [
    {
      localId: 2,
      serverId: 'server-2',
      localType: 1,
      createTime: 1704067200,
      isSend: 0,
      senderUsername: 'wxid-a',
      content: 'second',
      rawContent: 'second',
      parsedContent: 'second',
    },
    {
      localId: 1,
      serverId: 'server-1',
      localType: 1,
      createTime: 1703980800,
      isSend: 1,
      senderUsername: 'wxid-b',
      content: 'first',
      rawContent: 'first',
      parsedContent: 'first',
    },
  ]
  const first = buildEvidenceManifest('person/group', messages, '  case note  ', '2026-08-27T00:00:00.000Z')
  const second = buildEvidenceManifest('person/group', messages, 'case note', '2026-08-27T00:00:00.000Z')

  assert.equal(first.messageCount, 2)
  assert.equal(first.firstMessageAt, '2023-12-31T00:00:00.000Z')
  assert.equal(first.lastMessageAt, '2024-01-01T00:00:00.000Z')
  assert.deepEqual(first.messageIds, [2, 1])
  assert.equal(first.caseNote, 'case note')
  assert.equal(first.messagesSha256, second.messagesSha256)
  assert.match(first.notice, /不代表任何法院/)
})

test('evidence package sanitizes talker names and writes local files', () => {
  const root = mkdtempSync(join(tmpdir(), 'weflow-evidence-'))
  const messages: Message[] = []
  const result = writeEvidencePackage(root, '../private\\chat', messages, 'x'.repeat(600))
  const packageName = readdirSync(root)[0]

  assert.match(packageName, /^_private_chat-/)
  assert.equal(JSON.parse(readFileSync(join(result.path, 'messages.json'), 'utf8')).length, 0)
  assert.equal(result.manifest.caseNote.length, 500)
  assert.equal(readFileSync(join(result.path, 'manifest.json'), 'utf8').includes('private\\chat'), false)
})

test('empty evidence manifests contain no fabricated timestamps', () => {
  const manifest = buildEvidenceManifest('empty', [], '')
  assert.equal(manifest.messageCount, 0)
  assert.equal(manifest.firstMessageAt, null)
  assert.equal(manifest.lastMessageAt, null)
  assert.deepEqual(manifest.messageIds, [])
})

test('cloud evidence review respects strict privacy mode', () => {
  const messages: Message[] = [{
    localId: 7,
    serverId: 'server-7',
    localType: 1,
    createTime: 1704067200,
    isSend: 0,
    senderUsername: 'wxid-a@example.com',
    content: '请在明天前还款，联系电话13812345678',
    rawContent: '请在明天前还款，联系电话13812345678',
    parsedContent: '请在明天前还款，联系电话13812345678',
  }]
  const strict = buildEvidenceReviewInput('对话', messages, 'strict', false)
  const local = buildEvidenceReviewInput('对话', messages, 'strict', true)

  assert.equal(strict.redactions, 0)
  assert.equal(strict.transcript.includes('请在明天前还款'), false)
  assert.equal(strict.transcript.includes('wxid-a@example.com'), false)
  assert.equal(strict.transcript.includes('对话'), false)
  assert.match(strict.transcript, /聊天正文已按严格隐私模式屏蔽/)
  assert.equal(local.transcript.includes('请在明天前还款'), true)
})

test('group assistant routing requires explicit metadata and all access controls', () => {
  const direct = resolveInboundRouting({ from_user_id: 'person-a' }, 'bot-a')
  const group = resolveInboundRouting({
    from_user_id: 'person-a',
    group_id: 'test-group',
    sender_id: 'person-a',
    mentioned_user_ids: ['bot-a'],
  }, 'bot-a')
  const policy = { directWhitelist: 'person-a', groupWhitelist: 'test-group', requireGroupMention: true }

  assert.deepEqual(direct, { conversationType: 'direct', conversationId: 'person-a', senderId: 'person-a', mentionedBot: false })
  assert.deepEqual(group, { conversationType: 'group', conversationId: 'test-group', senderId: 'person-a', mentionedBot: true })
  assert.deepEqual(evaluateAssistantAccess(group, policy), { allowed: true, reason: 'allowed' })
  assert.deepEqual(evaluateAssistantAccess({ ...group, mentionedBot: false }, policy), { allowed: false, reason: 'group-mention-required' })
  assert.deepEqual(evaluateAssistantAccess({ ...group, senderId: 'person-b' }, policy), { allowed: false, reason: 'group-sender-not-whitelisted' })
  assert.deepEqual(evaluateAssistantAccess({ ...group, conversationId: 'other-group' }, policy), { allowed: false, reason: 'group-not-whitelisted' })
})
