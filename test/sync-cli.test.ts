import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

/** Runs the real CLI with HOME redirected, so nothing touches the user's state. */
function runCli(home: string, args: string[]) {
  return spawnSync(process.execPath, [
    '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'), ...args,
  ], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USERPROFILE: home },
  })
}

function withHome(run: (home: string) => void): void {
  const home = mkdtempSync(join(tmpdir(), 'weflow-sync-cli-'))
  try {
    run(home)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
}

/** Parse the JSON payload, which some commands pretty-print across many lines. */
function payload(stdout: string): any {
  const start = stdout.indexOf('{')
  if (start < 0) throw new Error(`no JSON payload in output: ${stdout.slice(0, 120)}`)
  return JSON.parse(stdout.slice(start))
}

test('sync is discoverable and declares it cannot offer a stable cursor', () => {
  withHome((home) => {
    const result = runCli(home, ['capabilities', '--json'])
    const body = payload(result.stdout)
    const sync = body.workflows?.sync
    assert.ok(sync, 'sync should be registered in capabilities (D-018)')
    assert.equal(sync.stateSchema, 'weflow-sync/v1')
    assert.equal(sync.jobSchema, 'weflow-job/v1')
    assert.equal(sync.confirmationRequired, true)
    assert.equal(sync.readsLocalData, true)
    assert.equal(sync.invokesAI, false)
    // D-027: no stable cursor until every backend supports one.
    assert.equal(sync.stableCursor, false)
    assert.equal(sync.previewResolvesTalker, false)
    // The pre-existing export contract must be untouched by this work.
    assert.equal(body.read?.exports?.incrementalRead?.stableCursor, false)
    assert.equal(body.read?.exports?.versionedContract, 'weflow-message/v1')
  })
})

test('a sync preview reports the plan without touching local data', () => {
  withHome((home) => {
    const result = runCli(home, ['sync', 'run', 'wxid_test_contact', '--dry-run', '--json'])
    const body = payload(result.stdout)
    assert.equal(result.status, 0)
    assert.equal(body.dryRun, true)
    assert.equal(body.action, 'sync.run')
    assert.equal(body.talkerResolved, false)
    assert.equal(body.readsLocalChat, true)
    assert.equal(body.stableCursor, false)
    // A preview that named the talker would have had to read the database.
    assert.ok(!result.stdout.includes('wxid_test_contact'),
      'preview must not echo the identifier back')
  })
})

test('json mode refuses to write without an explicit --yes', () => {
  withHome((home) => {
    const result = runCli(home, ['sync', 'run', 'wxid_test_contact', '--json'])
    const body = payload(result.stdout)
    assert.equal(result.status, 1)
    assert.equal(body.code, 'CONFIRMATION_REQUIRED')
    assert.equal(body.success, false)
    // Nothing may have been written by the refused run.
    assert.equal(existsSync(join(home, '.weflow-cli', 'sync')), false)
  })
})

test('sync validates its numeric and date arguments before reading anything', () => {
  withHome((home) => {
    const overlap = runCli(home, ['sync', 'run', 'wxid_test_contact', '--overlap', '-1', '--json'])
    assert.equal(overlap.status, 1)
    assert.equal(payload(overlap.stdout).code, 'INVALID_ARGUMENT')

    const limit = runCli(home, ['sync', 'run', 'wxid_test_contact', '--limit', 'NaN', '--json'])
    assert.equal(limit.status, 1)
    assert.equal(payload(limit.stdout).code, 'INVALID_ARGUMENT')

    const date = runCli(home, ['sync', 'run', 'wxid_test_contact', '--since', '2026-02-30', '--json'])
    assert.equal(date.status, 1)
    assert.equal(payload(date.stdout).code, 'INVALID_DATE')

    const both = runCli(home, ['sync', 'run', 'wxid_test_contact', '--since', '2026-09-01', '--full', '--json'])
    assert.equal(both.status, 1)
    assert.equal(payload(both.stdout).code, 'INVALID_ARGUMENT')
  })
})

test('status on a fresh machine is empty, not an error', () => {
  withHome((home) => {
    const result = runCli(home, ['sync', 'status', '--json'])
    const body = payload(result.stdout)
    assert.equal(result.status, 0)
    assert.deepEqual(body.scopes, [])
    assert.equal(body.schema, 'weflow-sync/v1')
    // The flag stays visible even when there is nothing to report.
    assert.equal(body.stableCursor, false)
  })
})

test('status for an unknown conversation reports not-found', () => {
  withHome((home) => {
    const result = runCli(home, ['sync', 'status', 'wxid_never_synced', '--json'])
    assert.equal(result.status, 1)
    assert.equal(payload(result.stdout).code, 'SYNC_STATE_NOT_FOUND')
  })
})

test('an unconfigured machine is refused by name, not by stack trace', () => {
  withHome((home) => {
    const result = runCli(home, ['sync', 'verify', 'wxid_test_contact', '--json'])
    assert.equal(result.status, 1)
    assert.equal(payload(result.stdout).code, 'NOT_INITIALIZED')
  })
})
