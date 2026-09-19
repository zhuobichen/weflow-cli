import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

/**
 * End-to-end acceptance on synthetic data.
 *
 * Everything here runs the real CLI against a synthetic encrypted NT store, so
 * the full path is exercised - CLI, configService, ntCore, the Python reader,
 * shard merging - without any real WeChat data or credential. See
 * test/fixtures/make_synthetic_nt.py for what the fixture contains and for the
 * two traps that make a fixture silently empty.
 */
const TALKER = 'wxid_synthetic_contact'
const PER_SHARD = 2
const SHARDS = 2
const EXPECTED = PER_SHARD * SHARDS

interface Fixture { home: string; talker: string; expected: number }

/**
 * The fixture needs Python with sqlcipher3, which a Node-only environment
 * (including CI's node job) may not have. Skipping with a reason beats failing
 * on something the suite has no control over - but the reason is printed, so a
 * silently skipped suite is still visible.
 */
const SKIP_REASON: string | false = (() => {
  const probe = spawnSync('python', ['-c', 'import sqlcipher3'], { encoding: 'utf8' })
  if (probe.status === 0) return false
  return 'python + sqlcipher3 unavailable, so the synthetic NT store cannot be built'
})()

const skip = SKIP_REASON ? { skip: SKIP_REASON } : {}

function buildFixture(): Fixture {
  const home = mkdtempSync(join(tmpdir(), 'weflow-e2e-'))
  const built = spawnSync('python', [
    join(process.cwd(), 'test', 'fixtures', 'make_synthetic_nt.py'),
    '--out', home, '--talker', TALKER,
    '--per-shard', String(PER_SHARD), '--shards', String(SHARDS), '--json',
  ], { cwd: process.cwd(), encoding: 'utf8' })
  assert.equal(built.status, 0, `fixture build failed: ${built.stderr}`)
  return { home, talker: TALKER, expected: EXPECTED }
}

function runCli(fixture: Fixture, args: string[]) {
  return spawnSync(process.execPath, [
    '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'), ...args,
  ], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: { ...process.env, HOME: fixture.home, USERPROFILE: fixture.home },
    timeout: 180_000,
  })
}

function payload(stdout: string): any {
  const start = stdout.indexOf('{')
  if (start < 0) throw new Error(`no JSON payload: ${stdout.slice(0, 200)}`)
  return JSON.parse(stdout.slice(start))
}

function withFixture(run: (fixture: Fixture) => void): void {
  const fixture = buildFixture()
  try {
    run(fixture)
  } finally {
    rmSync(fixture.home, { recursive: true, force: true })
  }
}

test('the CLI reads a synthetic encrypted store end to end', skip, () => {
  withFixture((fixture) => {
    const sessions = payload(runCli(fixture, ['sessions', '--limit', '5', '--json']).stdout)
    assert.equal(sessions.success, true)
    // The fixture records one sender in Name2Id, which is what sessions lists.
    assert.equal(sessions.sessions.length, 1)

    const messages = payload(runCli(fixture, ['messages', TALKER, '--limit', '50', '--json']).stdout)
    assert.equal(messages.success, true)
    assert.equal(messages.messages.length, fixture.expected)
    // Shard merging: both shards contributed, and the merge is newest-first.
    const times = messages.messages.map((m: any) => m.createTime)
    assert.deepEqual(times, [...times].sort((a, b) => b - a))
  })
})

test('both read paths agree on a synthetic store', skip, () => {
  withFixture((fixture) => {
    // The invariant that caught the shard bug: json (TypeScript) and html
    // (Python) read the same conversation through different code.
    const json = payload(runCli(fixture, ['export', TALKER, 'json', '--limit', '50', '--json']).stdout)
    assert.equal(json.success, true)
    assert.equal(json.count, fixture.expected)

    const out = join(fixture.home, 'exported')
    const html = runCli(fixture, ['export', TALKER, 'html', '--limit', '50', '--output', out, '--non-interactive'])
    assert.equal(html.status, 0, html.stderr.slice(-300))
    const produced = readdirSync(out).filter(name => name.endsWith('.html'))
    assert.ok(produced.length > 0, 'html export produced no files')
  })
})

test('a sync run reports complete coverage and names every shard', skip, () => {
  withFixture((fixture) => {
    const result = payload(runCli(fixture,
      ['sync', 'run', TALKER, '--since', '2023-01-01', '--yes', '--json']).stdout)

    assert.equal(result.success, true)
    assert.equal(result.coverage, 'complete')
    assert.equal(result.recordsReturned, fixture.expected)
    assert.equal(result.shards.scanned, SHARDS)
    assert.equal(result.shards.opened, SHARDS)
    assert.equal(result.shards.failed, 0)
    // Per-shard row counts are the thing that was previously invisible.
    for (const item of result.shards.items) {
      assert.equal(item.hasTalkerTable, true, `${item.name} should hold this talker`)
      assert.equal(item.rowsForTalker, PER_SHARD)
      assert.ok(!item.name.includes('/') && !item.name.includes('\\'),
        'the report must carry basenames only')
    }
  })
})

test('a second run is bounded by the checkpoint, not by the whole history', skip, () => {
  withFixture((fixture) => {
    const first = payload(runCli(fixture,
      ['sync', 'run', TALKER, '--since', '2023-01-01', '--yes', '--json']).stdout)
    assert.equal(first.recordsReturned, fixture.expected)

    // The first run's checkpoint is now the starting point, and the overlap
    // deliberately re-reads the boundary rather than trusting a bare
    // timestamp as a cursor (D-027).
    const second = payload(runCli(fixture, ['sync', 'run', TALKER, '--yes', '--json']).stdout)
    assert.equal(second.success, true)
    assert.equal(second.window.overlapSeconds, 300)
    // The window starts one overlap back from the newest message the first run
    // recorded, which the fixture pins to a known timestamp.
    const newestRecorded = 1_700_000_000 + (SHARDS - 1) * 1000 + (PER_SHARD - 1) * 10
    assert.equal(second.window.from, newestRecorded - 300)

    // The fixture spaces messages ten seconds apart, so a 300s overlap reaches
    // back past the two newest - not the whole conversation.
    assert.equal(second.recordsReturned, 2)
    assert.ok(second.recordsReturned < fixture.expected)
  })
})

test('status reflects the run and verify re-reads the recorded window', skip, () => {
  withFixture((fixture) => {
    runCli(fixture, ['sync', 'run', TALKER, '--since', '2023-01-01', '--yes', '--json'])

    const status = payload(runCli(fixture, ['sync', 'status', '--json']).stdout)
    assert.equal(status.success, true)
    assert.equal(status.scopes.length, 1)
    assert.equal(status.scopes[0].coverage, 'complete')
    assert.equal(status.scopes[0].shardsFailed, 0)
    // A stable cursor is deliberately not on offer (D-027).
    assert.equal(status.stableCursor, false)

    const verify = payload(runCli(fixture, ['sync', 'verify', TALKER, '--json']).stdout)
    assert.equal(verify.success, true, JSON.stringify(verify.checks))
    for (const check of verify.checks) {
      assert.ok(check.ok || check.skipped, `${check.name} failed`)
    }
  })
})

test('a shard that cannot be opened degrades to unverified, not to success', skip, () => {
  withFixture((fixture) => {
    runCli(fixture, ['sync', 'run', TALKER, '--since', '2023-01-01', '--yes', '--json'])

    // Corrupt one shard *in place*. Deleting it would not work: a shard that
    // is not there is simply not scanned, and the run correctly reports
    // complete over what remains. "Cannot be opened" requires it to exist.
    const msgDir = join(fixture.home, 'db_storage', 'message')
    const victim = join(msgDir, 'message_1.db')
    writeFileSync(victim, Buffer.from('not a database at all'))
    assert.ok(existsSync(victim))

    const result = payload(runCli(fixture,
      ['sync', 'run', TALKER, '--since', '2023-01-01', '--yes', '--json']).stdout)
    // The remaining shard is readable, so the read is not wrong - but whether
    // the missing shard held messages for this talker is unknowable.
    assert.equal(result.coverage, 'unverified')
    assert.ok(result.warnings.some((w: string) => w.startsWith('shard-unverified:')),
      `expected a shard warning, got ${JSON.stringify(result.warnings)}`)
  })
})

test('the sync checkpoint carries no paths, keys or message bodies', skip, () => {
  withFixture((fixture) => {
    runCli(fixture, ['sync', 'run', TALKER, '--since', '2023-01-01', '--yes', '--json'])

    const syncDir = join(fixture.home, '.weflow-cli', 'sync')
    const files = readdirSync(syncDir).filter(name => name.endsWith('.json'))
    assert.equal(files.length, 1)
    const raw = readFileSync(join(syncDir, files[0]), 'utf8')
    const state = JSON.parse(raw)

    assert.equal(state.schema, 'weflow-sync/v1')
    // D-001/D-002/D-025: state is local, but must survive being pasted anywhere.
    assert.ok(!raw.includes(fixture.home), 'an absolute path leaked into the state')
    assert.ok(!raw.includes('synthetic message'), 'a message body leaked into the state')
    assert.ok(!/[0-9a-f]{32,}/.test(raw), 'something key-shaped leaked into the state')
    assert.equal(state.shards.items.every((i: any) => !i.name.includes('/')), true)

    // The file name must not carry the display name either.
    assert.ok(!files[0].includes(' '), `state file name looks like a display name: ${files[0]}`)
  })
})
