import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
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

test('assistant daemon mutations require preview and confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-assistant-daemon-'))

  try {
    for (const action of ['start', 'stop']) {
      const preview = runCli(home, ['assistant', action, '--dry-run', '--json'])
      assert.equal(preview.status, 0, preview.stderr || preview.stdout)
      assert.equal(JSON.parse(preview.stdout).dryRun, true)

      const refused = runCli(home, ['assistant', action, '--json'])
      assert.equal(refused.status, 1, refused.stderr || refused.stdout)
      assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')
    }

    for (const args of [['listen'], ['assistant', 'run']]) {
      const preview = runCli(home, [...args, '--dry-run', '--json'])
      assert.equal(preview.status, 0, preview.stderr || preview.stdout)
      assert.equal(JSON.parse(preview.stdout).interactiveRequired, true)

      const refused = runCli(home, [...args, '--yes', '--json'])
      assert.equal(refused.status, 1, refused.stderr || refused.stdout)
      assert.equal(JSON.parse(refused.stdout).code, 'INTERACTIVE_REQUIRED')
    }
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('assistant log JSON reports metadata without returning private log content', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-assistant-log-'))
  const privateLine = 'synthetic private conversation content'

  try {
    const stateDir = join(home, '.weflow-cli')
    mkdirSync(stateDir, { recursive: true })
    writeFileSync(join(stateDir, 'assistant.log'), `${privateLine}\nsecond line\n`, 'utf8')

    const result = runCli(home, ['assistant', 'log', '--lines', '10', '--json'])
    assert.equal(result.status, 0, result.stderr || result.stdout)
    assert.deepEqual(JSON.parse(result.stdout), {
      success: true,
      available: true,
      requestedLines: 10,
      returnedLineCount: 2,
    })
    assert.equal(result.stdout.includes(privateLine), false)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
