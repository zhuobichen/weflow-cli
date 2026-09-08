import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

function runCli(home: string, args: string[], extraEnv: NodeJS.ProcessEnv = {}) {
  return spawnSync(process.execPath, [
    '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'), ...args,
  ], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USERPROFILE: home, ...extraEnv },
  })
}

test('favorite database key uses environment-backed preview and confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-favorite-key-'))
  const configFile = join(home, '.weflow-cli', 'config.json')
  const secret = 'a'.repeat(64)
  const env = { WEFLOW_TEST_FAV_KEY: secret }
  const base = ['fav', 'set-key', '--from-env', 'WEFLOW_TEST_FAV_KEY']

  try {
    const preview = runCli(home, [...base, '--dry-run', '--json'], env)
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    assert.equal(JSON.parse(preview.stdout).dryRun, true)
    assert.equal(preview.stdout.includes(secret), false)
    assert.equal(existsSync(configFile), false)

    const refused = runCli(home, [...base, '--json'], env)
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')
    assert.equal(existsSync(configFile), false)

    const written = runCli(home, [...base, '--yes', '--json'], env)
    assert.equal(written.status, 0, written.stderr || written.stdout)
    assert.equal(JSON.parse(written.stdout).changed, true)
    assert.equal(written.stdout.includes(secret), false)
    assert.equal(readFileSync(configFile, 'utf8').includes(secret), false)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
