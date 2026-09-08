import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
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

test('message sending requires a read-only preview and explicit confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-send-'))
  const target = 'wxid_test_send'
  const media = join(home, 'synthetic-image.png')

  try {
    writeFileSync(media, 'synthetic media', 'utf8')
    const allowed = runCli(home, ['whitelist', 'add', target, '--yes', '--json'])
    assert.equal(allowed.status, 0, allowed.stderr || allowed.stdout)

    const preview = runCli(home, ['send', target, 'synthetic message', '--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    const previewData = JSON.parse(preview.stdout)
    assert.equal(previewData.success, true)
    assert.equal(previewData.dryRun, true)
    assert.equal(previewData.action, 'send')

    const refused = runCli(home, ['send', target, 'synthetic message', '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')

    const invalidRate = runCli(home, ['send', target, 'synthetic message', '--dry-run', '--json', '--rate-max', '-1'])
    assert.equal(invalidRate.status, 1, invalidRate.stderr || invalidRate.stdout)
    assert.equal(JSON.parse(invalidRate.stdout).code, 'INVALID_RATE_LIMIT')

    const mediaPreview = runCli(home, ['send', target, 'media', '--image', media, '--dry-run', '--json'])
    assert.equal(mediaPreview.status, 0, mediaPreview.stderr || mediaPreview.stdout)
    assert.match(JSON.parse(mediaPreview.stdout).preview, /synthetic-image\.png/)
    assert.equal(mediaPreview.stdout.includes(home), false)

    const invalidMedia = runCli(home, ['send', target, 'media', '--file', join(home, 'missing.txt'), '--dry-run', '--json'])
    assert.equal(invalidMedia.status, 1, invalidMedia.stderr || invalidMedia.stdout)
    assert.equal(JSON.parse(invalidMedia.stdout).code, 'INVALID_MEDIA_FILE')

    const conflicting = runCli(home, ['send', target, 'media', '--image', media, '--file', media, '--dry-run', '--json'])
    assert.equal(conflicting.status, 1, conflicting.stderr || conflicting.stdout)
    assert.equal(JSON.parse(conflicting.stdout).code, 'CONFLICTING_MEDIA')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
