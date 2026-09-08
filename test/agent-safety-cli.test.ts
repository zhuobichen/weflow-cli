import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

function runCli(home: string, args: string[]) {
  return spawnSync(process.execPath, [
    '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'), ...args,
  ], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USERPROFILE: home },
  })
}

test('database key capture is previewable but cannot execute in JSON mode', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-dbkey-'))
  try {
    const preview = runCli(home, ['dbkey', '--force', '--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    const previewData = JSON.parse(preview.stdout)
    assert.equal(previewData.action, 'dbkey.capture')
    assert.equal(previewData.interactiveRequired, true)

    const refused = runCli(home, ['dbkey', '--force', '--yes', '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'INTERACTIVE_REQUIRED')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('initialization is previewable but remains human-gated', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-init-preview-'))
  const privatePath = join(home, 'synthetic-private-data')
  try {
    const preview = runCli(home, ['init', '--path', privatePath, '--full-scan', '--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    const previewData = JSON.parse(preview.stdout)
    assert.equal(previewData.action, 'initialize')
    assert.equal(previewData.explicitPathProvided, true)
    assert.equal(previewData.fullScanRequested, true)
    assert.equal(preview.stdout.includes(privatePath), false)

    const refused = runCli(home, ['init', '--path', privatePath, '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'INTERACTIVE_REQUIRED')
    assert.equal(refused.stdout.includes(privatePath), false)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('account scan JSON returns only a count', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-scan-home-'))
  const root = mkdtempSync(join(tmpdir(), 'weflow-scan-root-'))
  const privateAccount = 'wxid_synthetic_private'
  try {
    mkdirSync(join(root, privateAccount, 'db_storage'), { recursive: true })
    const result = runCli(home, ['scan', '--path', root, '--json'])
    assert.equal(result.status, 0, result.stderr || result.stdout)
    assert.deepEqual(JSON.parse(result.stdout), { success: true, accountCount: 1 })
    assert.equal(result.stdout.includes(root), false)
    assert.equal(result.stdout.includes(privateAccount), false)
  } finally {
    rmSync(home, { recursive: true, force: true })
    rmSync(root, { recursive: true, force: true })
  }
})

test('Vault RAG requires preview and confirmation before reading local knowledge', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-vault-rag-'))
  const privateQuestion = 'synthetic private question'
  try {
    const preview = runCli(home, ['vault', 'rag', privateQuestion, '--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    const previewData = JSON.parse(preview.stdout)
    assert.equal(previewData.action, 'vault.rag')
    assert.equal(previewData.sendsSelectedContextToAi, true)
    assert.equal(preview.stdout.includes(privateQuestion), false)

    const refused = runCli(home, ['vault', 'rag', privateQuestion, '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')
    assert.equal(refused.stdout.includes(privateQuestion), false)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('semantic search requires confirmation without echoing the private query', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-semantic-query-'))
  const privateQuery = 'synthetic private semantic query'
  try {
    const preview = runCli(home, ['search', privateQuery, '--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    assert.equal(JSON.parse(preview.stdout).action, 'semantic-search.query')
    assert.equal(preview.stdout.includes(privateQuery), false)

    const refused = runCli(home, ['search', privateQuery, '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')
    assert.equal(refused.stdout.includes(privateQuery), false)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('RAG chat requires confirmation and keeps interactive mode human-only', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-rag-chat-'))
  const privateQuestion = 'synthetic private RAG question'
  const privateTalker = 'synthetic private conversation'
  try {
    const preview = runCli(home, ['chat', privateQuestion, '--talker', privateTalker, '--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    const previewData = JSON.parse(preview.stdout)
    assert.equal(previewData.action, 'rag-chat.query')
    assert.equal(previewData.conversationRestricted, true)
    assert.equal(preview.stdout.includes(privateQuestion), false)
    assert.equal(preview.stdout.includes(privateTalker), false)

    const refused = runCli(home, ['chat', privateQuestion, '--talker', privateTalker, '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')

    const interactive = runCli(home, ['chat', '--yes', '--json'])
    assert.equal(interactive.status, 1, interactive.stderr || interactive.stdout)
    assert.equal(JSON.parse(interactive.stdout).code, 'INTERACTIVE_REQUIRED')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('machine-driven daily generation requires explicit confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-daily-confirmation-'))
  try {
    const refused = runCli(home, ['daily', '--date', '2026-09-08', '--no-ai', '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    const result = JSON.parse(refused.stdout)
    assert.equal(result.code, 'CONFIRMATION_REQUIRED')
    assert.equal(result.aiEnabled, false)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
