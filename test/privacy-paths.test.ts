import test from 'node:test'
import assert from 'node:assert/strict'
import { homedir } from 'node:os'
import { expandHomePath } from '../src/utils/pathUtils.js'
import { maskMessageBodyText, redactText } from '../src/services/assistantPrivacy.js'
import { buildEvidenceManifest, buildEvidenceReviewInput, writeEvidencePackage } from '../src/services/evidenceService.js'
import { evaluateAssistantAccess, resolveInboundRouting } from '../src/services/assistantRouting.js'
import { isAllowedWeChatArticleUrl, isCoverImage, safeChildPath, safeDate } from '../src/utils/mcpSecurity.js'
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import type { Message } from '../src/types.js'
import { DbPathService } from '../src/core/dbPathService.js'
import { WcdbCore } from '../src/core/wcdbCore.js'
import { createWeFlowEnvelope } from '../src/services/messageContract.js'
import { createPythonProcessEnv, safeSubprocessError } from '../src/utils/pythonProcessEnv.js'

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
  mkdirSync(join(root, 'not_an_account'), { recursive: true })
  assert.equal(service.scanWxidCandidates(join(root, 'not_an_account')).length, 0)
})

test('discovers NT databases from a custom xwechat_files root', () => {
  const root = mkdtempSync(join(tmpdir(), 'weflow-custom-nt-'))
  const xwechatRoot = join(root, 'xwechat_files')
  const wxid = 'wxid_test_account'
  const messageDir = join(xwechatRoot, wxid, 'db_storage', 'message')
  const contactDir = join(xwechatRoot, wxid, 'db_storage', 'contact')
  mkdirSync(messageDir, { recursive: true })
  mkdirSync(contactDir, { recursive: true })
  writeFileSync(join(messageDir, 'message_0.db'), Buffer.alloc(256, 1))
  writeFileSync(join(contactDir, 'contact.db'), Buffer.alloc(256, 2))

  const python = process.platform === 'win32' ? 'python' : 'python3'
  const script = join(process.cwd(), 'scripts', 'nt_decrypt.py')
  const result = spawnSync(python, [
    '-S',
    '-c',
    [
      'import importlib.util, json, sys',
      "spec = importlib.util.spec_from_file_location('nt_decrypt', sys.argv[1])",
      'mod = importlib.util.module_from_spec(spec)',
      'spec.loader.exec_module(mod)',
      'print(json.dumps(mod.find_nt_databases(sys.argv[2]), ensure_ascii=False))',
    ].join('; '),
    script,
    xwechatRoot,
  ], {
    encoding: 'utf8',
  })

  assert.equal(result.status, 0, result.stderr || result.stdout)
  const databases = JSON.parse(result.stdout)
  assert.ok(databases.some((db: { name: string }) => db.name === 'message/message_0.db'))
  assert.ok(databases.some((db: { name: string }) => db.name === 'contact/contact.db'))
})

test('does not forward API keys in Python child-process arguments', () => {
  const cliSource = readFileSync(join(process.cwd(), 'bin', 'weflow-cli.ts'), 'utf8')
  const pipelineSource = readFileSync(join(process.cwd(), 'scripts', 'pipeline.py'), 'utf8')
  const ntCoreSource = readFileSync(join(process.cwd(), 'src', 'core', 'ntCore.ts'), 'utf8')
  const wcdbCoreSource = readFileSync(join(process.cwd(), 'src', 'core', 'wcdbCore.ts'), 'utf8')
  const exportSource = readFileSync(join(process.cwd(), 'src', 'services', 'exportService.ts'), 'utf8')

  assert.doesNotMatch(cliSource, /(?:args|a)\.push\(['"]--api-key['"]/)
  assert.doesNotMatch(cliSource, /\[[^\]]*['"]--api-key['"][^\]]*(?:apiKey|opts\.apiKey)/)
  assert.doesNotMatch(pipelineSource, /step\d+_args\s*\+=\s*\[['"]--api-key['"]/)
  assert.doesNotMatch(ntCoreSource, /['"]--(?:key|salt|contact-key|contact-salt)['"]/)
  assert.doesNotMatch(ntCoreSource, /['"]--root['"]/)
  assert.doesNotMatch(exportSource, /['"]--(?:key|salt|passphrase|own-wxid)['"]/)
  assert.doesNotMatch(ntCoreSource, /args\.push\(['"]--(?:keyword|usernames)['"]/)
  assert.doesNotMatch(exportSource, /args\.push\(['"]--(?:name|out|cache-dir|account-dir|date)['"]/)
  assert.match(wcdbCoreSource, /PARAMETER_BINDING_UNSUPPORTED/)
  assert.doesNotMatch(cliSource, /args\.push\((?:query|question)/)
  assert.doesNotMatch(cliSource, /args\.push\(['"]--talker['"]/)
})

test('Python worker environments clear stale internal secrets', () => {
  const privateKeys = [
    'WEFLOW_NT_KEY',
    'WEFLOW_VAULT_QUESTION',
    'WEFLOW_SEARCH_QUERY',
    'WEFLOW_RAG_QUESTION',
    'WEFLOW_RAG_TALKER',
    'WEFLOW_REPORT_TALKERS',
    'WEFLOW_SCAN_ROOT',
    'WEFLOW_QUERY_KEYWORD',
    'WEFLOW_QUERY_USERNAMES',
    'WEFLOW_EXPORT_NAME',
    'WEFLOW_EXPORT_OUTPUT',
    'WEFLOW_EXPORT_CACHE_DIR',
    'WEFLOW_EXPORT_ACCOUNT_DIR',
    'WEFLOW_EXPORT_DATE',
  ] as const
  const previous = new Map(privateKeys.map(key => [key, process.env[key]]))
  for (const key of privateKeys) process.env[key] = `stale-${key.toLowerCase()}`
  try {
    const cleared = createPythonProcessEnv()
    const replaced = createPythonProcessEnv({ WEFLOW_NT_KEY: 'current-secret' })
    for (const key of privateKeys) assert.equal(cleared[key], undefined)
    assert.equal(replaced.WEFLOW_NT_KEY, 'current-secret')
  } finally {
    for (const key of privateKeys) {
      const value = previous.get(key)
      if (value === undefined) delete process.env[key]
      else process.env[key] = value
    }
  }
})

test('subprocess errors never repeat command arguments', () => {
  const error = Object.assign(new Error('Command failed: python worker.py --key synthetic-secret'), { code: 7 })
  const formatted = safeSubprocessError(error, 'worker failed')
  assert.equal(formatted, 'worker failed (exit 7)')
  assert.equal(formatted.includes('synthetic-secret'), false)
  assert.equal(formatted.includes('worker.py'), false)
})

test('WCDB rejects parameters until the native binding ABI supports them', async () => {
  const core = new WcdbCore() as any
  core.initialized = true
  core.handle = 1
  const result = await core.execQuery('contact', null, 'SELECT 1', ['untrusted'])
  assert.deepEqual(result, { success: false, error: 'PARAMETER_BINDING_UNSUPPORTED' })
})

test('syncs daily favorites without touching other report files', () => {
  const root = mkdtempSync(join(tmpdir(), 'weflow-daily-favorites-'))
  const date = '2026-09-01'
  const articleDir = join(root, date, 'AI')
  const article = join(articleDir, 'article.md')
  const favorite = join(root, date, '收藏', 'article.md')
  const python = process.platform === 'win32' ? 'python' : 'python3'
  const script = join(process.cwd(), 'scripts', 'sync_fav.py')

  try {
    mkdirSync(articleDir, { recursive: true })
    writeFileSync(article, '# Synthetic article\n', 'utf8')
    const add = spawnSync(python, [script, '--date', date, '--root', root, '--add', 'AI/article.md'], { encoding: 'utf8' })
    assert.equal(add.status, 0, add.stderr || add.stdout)
    assert.equal(readFileSync(favorite, 'utf8'), '# Synthetic article\n')

    const remove = spawnSync(python, [script, '--date', date, '--root', root, '--remove', 'AI/article.md'], { encoding: 'utf8' })
    assert.equal(remove.status, 0, remove.stderr || remove.stdout)
    assert.equal(readdirSync(join(root, date, '收藏')).length, 0)
    assert.equal(readFileSync(article, 'utf8'), '# Synthetic article\n')

    const outside = join(root, 'outside.md')
    const state = join(root, date, '.fav_state.json')
    writeFileSync(outside, '# Must remain outside favorites\n', 'utf8')
    writeFileSync(state, JSON.stringify(['../outside.md']), 'utf8')
    const maliciousState = spawnSync(python, [script, '--date', date, '--root', root], { encoding: 'utf8' })
    assert.equal(maliciousState.status, 0, maliciousState.stderr || maliciousState.stdout)
    assert.equal(readdirSync(join(root, date, '收藏')).length, 0)

    const maliciousAdd = spawnSync(python, [script, '--date', date, '--root', root, '--add', '../outside.md'], { encoding: 'utf8' })
    assert.equal(maliciousAdd.status, 1, maliciousAdd.stderr || maliciousAdd.stdout)
    assert.equal(readdirSync(join(root, date, '收藏')).length, 0)
    assert.equal(readFileSync(outside, 'utf8'), '# Must remain outside favorites\n')

    const missing = spawnSync(python, [script, '--date', '2026-09-02', '--root', root], { encoding: 'utf8' })
    assert.equal(missing.status, 1, missing.stderr || missing.stdout)
    assert.equal(missing.stdout.includes('收藏同步完成'), false)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('builds vault promotion outputs from synthetic notes without AI', () => {
  const vault = mkdtempSync(join(tmpdir(), 'weflow-vault-promote-'))
  const date = '2026-09-01'
  const notesDir = join(vault, '002_Literature', 'WeChat', date)
  const python = process.platform === 'win32' ? 'python' : 'python3'
  const ideasScript = join(process.cwd(), 'scripts', 'promote_ideas.py')
  const allScript = join(process.cwd(), 'scripts', 'promote_all.py')

  try {
    mkdirSync(notesDir, { recursive: true })
    for (let index = 1; index <= 5; index++) {
      writeFileSync(join(notesDir, `article-${index}.md`), [
        '---',
        `title: Synthetic AI article ${index}`,
        'source: Test source',
        'hasTopic: [[AI]]',
        'tags: [test]',
        '---',
        '',
        '## Related',
        `[[Concept ${index}]]`,
      ].join('\n'), 'utf8')
    }

    const ideas = spawnSync(python, [ideasScript, '--vault', vault, '--skip-ai'], { encoding: 'utf8' })
    assert.equal(ideas.status, 0, ideas.stderr || ideas.stdout)
    assert.equal(readFileSync(join(vault, '008_MOC', 'MOC-AI.md'), 'utf8').includes('Synthetic AI article'), true)

    const all = spawnSync(python, [allScript, '--vault', vault, '--skip-ai'], { encoding: 'utf8' })
    assert.equal(all.status, 0, all.stderr || all.stdout)
  } finally {
    rmSync(vault, { recursive: true, force: true })
  }
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

test('accepts only strict HTTPS WeChat article URLs', () => {
  assert.equal(isAllowedWeChatArticleUrl('https://mp.weixin.qq.com/s/example'), true)
  assert.equal(isAllowedWeChatArticleUrl('http://mp.weixin.qq.com/s/example'), false)
  assert.equal(isAllowedWeChatArticleUrl('https://mp.weixin.qq.com.evil.test/s/example'), false)
  assert.equal(isAllowedWeChatArticleUrl('https://evil.test/?next=mp.weixin.qq.com'), false)
  assert.equal(isAllowedWeChatArticleUrl('https://user:pass@mp.weixin.qq.com/s/example'), false)
})

test('creates a bounded-compatible message contract and preserves unknown types', () => {
  const envelope = createWeFlowEnvelope([{
    localId: 1,
    serverId: 'synthetic-server-id',
    localType: 99999,
    createTime: 1704067200,
    isSend: 0,
    senderUsername: null,
    content: 'synthetic content',
    rawContent: 'synthetic content',
    parsedContent: 'synthetic content',
  }], '2026-09-07T00:00:00.000Z')

  assert.equal(envelope.schema, 'weflow-message/v1')
  assert.equal(envelope.source, 'weflow-cli')
  assert.equal(envelope.messages[0].messageType, 'other')
  assert.equal(envelope.messages[0].localType, 99999)
  assert.equal(envelope.coverage, undefined)
  const bounded = createWeFlowEnvelope(envelope.messages, '2026-09-07T00:00:00.000Z', { requestedLimit: 1 })
  assert.equal(bounded.coverage?.returned, 1)
  assert.equal(bounded.coverage?.mayHaveMore, true)
  const complete = createWeFlowEnvelope(envelope.messages, '2026-09-07T00:00:00.000Z', { requestedLimit: 0 })
  assert.equal(complete.coverage?.mayHaveMore, false)
  assert.equal(JSON.stringify(envelope).includes('ntKey'), false)
  assert.equal(JSON.stringify(envelope).includes('dbPath'), false)
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
