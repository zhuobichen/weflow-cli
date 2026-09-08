import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

test('Vault init previews writes and requires explicit confirmation in JSON mode', () => {
  const workspace = mkdtempSync(join(tmpdir(), 'weflow-vault-init-'))
  const home = mkdtempSync(join(tmpdir(), 'weflow-vault-home-'))
  const vault = join(workspace, 'private-vault')
  const entry = join(process.cwd(), 'bin', 'weflow-cli.ts')
  const tsx = import.meta.resolve('tsx')
  const run = (extra: string[]) => spawnSync(process.execPath, [
    '--import', tsx, entry, 'vault', 'init', '--path', vault, ...extra,
  ], {
    cwd: workspace,
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USERPROFILE: home },
  })

  try {
    const preview = run(['--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    const previewData = JSON.parse(preview.stdout)
    assert.equal(previewData.action, 'vault.init')
    assert.equal(previewData.fileCreateCount, 4)
    assert.equal(previewData.overwriteCount, 0)
    assert.equal(preview.stdout.includes(vault), false)
    assert.equal(existsSync(vault), false)

    const refused = run(['--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')
    assert.equal(existsSync(vault), false)

    const created = run(['--yes', '--json'])
    assert.equal(created.status, 0, created.stderr || created.stdout)
    assert.equal(JSON.parse(created.stdout).success, true)
    assert.equal(created.stdout.includes(vault), false)
    assert.equal(existsSync(join(vault, 'README.md')), true)

    writeFileSync(join(vault, 'README.md'), 'custom private content', 'utf8')
    const overwritePreview = run(['--dry-run', '--json'])
    assert.equal(overwritePreview.status, 0, overwritePreview.stderr || overwritePreview.stdout)
    assert.equal(JSON.parse(overwritePreview.stdout).overwriteCount, 4)
    assert.equal(readFileSync(join(vault, 'README.md'), 'utf8'), 'custom private content')
  } finally {
    rmSync(workspace, { recursive: true, force: true })
    rmSync(home, { recursive: true, force: true })
  }
})

test('Vault sync previews untracked files and requires confirmation without leaking the remote', () => {
  const workspace = mkdtempSync(join(tmpdir(), 'weflow-vault-sync-'))
  const home = mkdtempSync(join(tmpdir(), 'weflow-vault-home-'))
  const vault = join(workspace, 'output', 'wechat-vault')
  const entry = join(process.cwd(), 'bin', 'weflow-cli.ts')
  const tsx = import.meta.resolve('tsx')
  const remote = 'https://user:secret@example.invalid/private.git'
  const run = (extra: string[]) => spawnSync(process.execPath, [
    '--import', tsx, entry, 'vault', 'sync', '--repo', remote, ...extra,
  ], {
    cwd: workspace,
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USERPROFILE: home },
  })

  try {
    mkdirSync(vault, { recursive: true })
    writeFileSync(join(vault, 'note.md'), 'synthetic content', 'utf8')

    const preview = run(['--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    const previewData = JSON.parse(preview.stdout)
    assert.equal(previewData.changeCount, 1)
    assert.equal(previewData.initialized, false)
    assert.equal(preview.stdout.includes(remote), false)
    assert.equal(existsSync(join(vault, '.git')), false)

    const refused = run(['--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')
    assert.equal(refused.stdout.includes(remote), false)
    assert.equal(existsSync(join(vault, '.git')), false)
  } finally {
    rmSync(workspace, { recursive: true, force: true })
    rmSync(home, { recursive: true, force: true })
  }
})
