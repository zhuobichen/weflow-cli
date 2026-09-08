import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
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

test('core read commands reject invalid pagination before database access', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-read-args-'))
  const cases = [
    ['sessions', '--limit', '-1', '--json'],
    ['messages', 'wxid_test_contact', '--limit', 'NaN', '--json'],
    ['messages', 'wxid_test_contact', '--offset', '-1', '--json'],
    ['contacts', '--limit', '1.5', '--json'],
    ['fav', 'list', '--offset', '-1', '--json'],
  ]

  try {
    for (const args of cases) {
      const result = runCli(home, args)
      assert.equal(result.status, 1, result.stderr || result.stdout)
      assert.equal(JSON.parse(result.stdout).code, 'INVALID_ARGUMENT')
    }
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('message timestamp bounds are validated and wired to range queries', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-message-range-'))

  try {
    const invalid = runCli(home, ['messages', 'wxid_test_contact', '--start', '20', '--end', '10', '--json'])
    assert.equal(invalid.status, 1, invalid.stderr || invalid.stdout)
    assert.equal(JSON.parse(invalid.stdout).code, 'INVALID_ARGUMENT')

    const source = readFileSync(join(process.cwd(), 'bin', 'weflow-cli.ts'), 'utf8')
    assert.match(source, /chatService\.getMessagesInRange\(talker, limit \+ offset, start, end\)/)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('favorite export returns structured argument failures', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-favorite-export-'))

  try {
    const format = runCli(home, ['fav', 'export', 'xml', '--json-result'])
    assert.equal(format.status, 1, format.stderr || format.stdout)
    assert.equal(JSON.parse(format.stdout).code, 'INVALID_FORMAT')

    const limit = runCli(home, ['fav', 'export', 'json', '--limit', '-1', '--json-result'])
    assert.equal(limit.status, 1, limit.stderr || limit.stdout)
    assert.equal(JSON.parse(limit.stdout).code, 'INVALID_ARGUMENT')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('evidence review exposes a bounded machine-readable entry point', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-evidence-review-'))

  try {
    const invalid = runCli(home, ['evidence-review', 'wxid_test_contact', '--limit', '-1', '--json'])
    assert.equal(invalid.status, 1, invalid.stderr || invalid.stdout)
    assert.equal(JSON.parse(invalid.stdout).code, 'INVALID_ARGUMENT')

    const preview = runCli(home, ['evidence-review', 'wxid_test_contact', '--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    assert.equal(JSON.parse(preview.stdout).readsLocalChat, true)
    assert.equal(preview.stdout.includes('wxid_test_contact'), false)

    const refused = runCli(home, ['evidence-review', 'wxid_test_contact', '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')

    const notInitialized = runCli(home, ['evidence-review', 'wxid_test_contact', '--yes', '--json'])
    assert.equal(notInitialized.status, 1, notInitialized.stderr || notInitialized.stdout)
    assert.equal(JSON.parse(notInitialized.stdout).code, 'NOT_INITIALIZED')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('local reader commands reject unsafe ports before starting a process', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-reader-port-'))

  try {
    const status = runCli(home, ['daily-server', '--status', '--port', '8765&whoami', '--json'])
    assert.equal(status.status, 1, status.stderr || status.stdout)
    assert.equal(JSON.parse(status.stdout).code, 'INVALID_ARGUMENT')

    const preview = runCli(home, ['daily-server', '--date', '2026-09-08', '--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    assert.equal(JSON.parse(preview.stdout).action, 'daily-reader.start')
    assert.equal(JSON.parse(preview.stdout).loopbackOnly, true)

    const refused = runCli(home, ['daily-server', '--date', '2026-09-08', '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')

    const aliasPreview = runCli(home, ['fav-server', '--date', '2026-09-08', '--dry-run', '--json'])
    assert.equal(aliasPreview.status, 0, aliasPreview.stderr || aliasPreview.stdout)
    assert.equal(JSON.parse(aliasPreview.stdout).compatibilityAlias, 'fav-server')

    const aliasRefused = runCli(home, ['fav-server', '--date', '2026-09-08', '--json'])
    assert.equal(aliasRefused.status, 1, aliasRefused.stderr || aliasRefused.stdout)
    assert.equal(JSON.parse(aliasRefused.stdout).code, 'CONFIRMATION_REQUIRED')

    const favorite = runCli(home, ['fav-server', '--port', '70000'])
    assert.equal(favorite.status, 1, favorite.stderr || favorite.stdout)
    assert.match(favorite.stdout, /port/)

    const invalidDate = runCli(home, ['daily-server', '--status', '--date', '2026-02-30', '--json'])
    assert.equal(invalidDate.status, 1, invalidDate.stderr || invalidDate.stdout)
    assert.equal(JSON.parse(invalidDate.stdout).code, 'INVALID_DATE')

    const invalidStats = runCli(home, ['daily-stats', '--days', '999999', '--json'])
    assert.equal(invalidStats.status, 1, invalidStats.stderr || invalidStats.stdout)
    assert.equal(JSON.parse(invalidStats.stdout).code, 'INVALID_ARGUMENT')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
