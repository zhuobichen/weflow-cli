import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  JOB_SCHEMA,
  SYNC_SCHEMA,
  SyncStateStore,
  UnknownSchemaError,
  emptySyncState,
  makeJobId,
  sanitizeForState,
  talkerSlug,
} from '../src/services/syncState.js'

function withStore(run: (store: SyncStateStore, dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), 'weflow-sync-'))
  try {
    run(new SyncStateStore(join(dir, 'sync'), join(dir, 'jobs')), dir)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

const TALKER = 'wxid_synthetic_contact'

test('a fresh checkpoint carries the versioned schema and conservative defaults', () => {
  const state = emptySyncState(TALKER)
  assert.equal(state.schema, SYNC_SCHEMA)
  assert.equal(state.schemaVersion, 1)
  assert.equal(state.talker, TALKER)
  // Scope falls back to the talker when no display name is known.
  assert.equal(state.scope, TALKER)
  assert.equal(state.coveredFrom, null)
  // Both timestamps start null and are advanced separately.
  assert.equal(state.lastAttempt, null)
  assert.equal(state.lastSuccessfulRun, null)
  // mayHaveMore is a conservative flag - never optimistic.
  assert.equal(state.mayHaveMore, false)
  // Nothing has been read yet, so coverage cannot claim anything.
  assert.equal(state.coverage, 'unverified')
  assert.deepEqual(state.warnings, [])
})

test('checkpoint round-trips through disk with its schema written', () => {
  withStore((store, dir) => {
    store.write({
      ...emptySyncState(TALKER, 'Synthetic Contact'),
      coveredFrom: '2026-09-01T00:00:00+08:00',
      coveredTo: '2026-09-19T08:59:59+08:00',
      recordsRead: 1200,
      recordsDeduplicated: 14,
      recordsReturned: 1186,
      shardsRead: 4,
      coverage: 'complete',
      lastAttempt: '2026-09-19T09:02:10+08:00',
      lastSuccessfulRun: '2026-09-19T09:02:10+08:00',
      checkpoint: { newestCreateTime: 1789000000, overlapSeconds: 300 },
    })
    const back = store.read('wechat-nt', TALKER)
    assert.equal(back?.recordsRead, 1200)
    assert.equal(back?.coverage, 'complete')
    assert.equal(back?.scope, 'Synthetic Contact')
    assert.equal(back?.checkpoint.newestCreateTime, 1789000000)
    // The on-disk file must name its version, which no older state file does.
    const raw = JSON.parse(readFileSync(store.statePath('wechat-nt', TALKER), 'utf8'))
    assert.equal(raw.schema, SYNC_SCHEMA)
  })
})

test('a checkpoint belonging to another conversation is not reused', () => {
  withStore((store) => {
    store.write({ ...emptySyncState(TALKER), recordsRead: 5 })
    // Returning another conversation's coverage would silently claim data was read.
    assert.equal(store.read('wechat-nt', 'wxid_synthetic_other'), null)
  })
})

test('conversation names differing only in punctuation get separate files', () => {
  withStore((store) => {
    const a = talkerSlug('wxid_a@chatroom')
    const b = talkerSlug('wxid_a_chatroom')
    // Without the hash suffix both sanitize to the same readable prefix.
    assert.notEqual(a, b)
    assert.equal(store.statePath('wechat-nt', 'wxid_a@chatroom'),
      store.statePath('wechat-nt', 'wxid_a@chatroom'))
  })
  // The readable part must not leak a full display name into a directory listing.
  assert.ok(talkerSlug('施显晟的群聊').length < 20)
})

test('an unknown schema is refused instead of being overwritten', () => {
  withStore((store) => {
    const path = store.statePath('wechat-nt', TALKER)
    const foreign = { schema: 'weflow-sync/v2', talker: TALKER, future: true }
    store.write(emptySyncState(TALKER))
    writeFileSync(path, JSON.stringify(foreign), 'utf8')

    assert.throws(() => store.read('wechat-nt', TALKER), UnknownSchemaError)
    // Reading failed, so nothing may have touched the file.
    assert.deepEqual(JSON.parse(readFileSync(path, 'utf8')), foreign)
  })
})

test('secrets and absolute paths never reach the state file', () => {
  withStore((store) => {
    store.write({
      ...emptySyncState(TALKER),
      // Simulate a caller stuffing things that must not persist.
      ...({ ntKey: 'x'.repeat(64), scope: 'C:\\Users\\someone\\xwechat_files' } as any),
      warnings: ['DATABASE_CONNECTION_FAILED'],
    })
    const raw = readFileSync(store.statePath('wechat-nt', TALKER), 'utf8')
    assert.ok(!raw.includes('x'.repeat(64)), 'key material leaked')
    assert.ok(!raw.includes('xwechat_files'), 'absolute path leaked')
    assert.ok(raw.includes('<path>'), 'path should be replaced, not silently dropped')
    // A structured reason survives; it is a category, not content.
    assert.ok(raw.includes('DATABASE_CONNECTION_FAILED'))
  })
})

test('sanitizeForState redacts key-shaped hex and both path styles', () => {
  const cleaned = sanitizeForState({
    windows: 'key was ' + 'a'.repeat(64) + ' and dir C:\\Users\\x\\db',
    posix: '/home/user/secret and /Users/other/secret',
    plain: ['no secrets here', 'relative/path/ok'],
  }) as any
  assert.equal(cleaned.windows.includes('a'.repeat(64)), false)
  assert.ok(cleaned.windows.includes('<path>'))
  assert.equal(cleaned.posix.includes('/home/user'), false)
  assert.equal(cleaned.posix.includes('/Users/other'), false)
  // Ordinary text must survive untouched.
  assert.deepEqual(cleaned.plain, ['no secrets here', 'relative/path/ok'])
})

test('list returns every scope newest attempt first and skips corrupt files', () => {
  withStore((store, dir) => {
    store.write({ ...emptySyncState('wxid_synthetic_a'), lastAttempt: '2026-09-19T09:00:00+08:00' })
    store.write({ ...emptySyncState('wxid_synthetic_b'), lastAttempt: '2026-09-20T09:00:00+08:00' })
    writeFileSync(join(dir, 'sync', 'broken.json'), '{ not json', 'utf8')

    assert.deepEqual(store.list().map(s => s.talker),
      ['wxid_synthetic_b', 'wxid_synthetic_a'])
  })
})

test('job records round-trip and recent jobs come back newest first', () => {
  withStore((store) => {
    const base = {
      schema: JOB_SCHEMA,
      kind: 'sync.messages',
      state: 'running' as const,
      createdAt: '2026-09-19T09:00:00+08:00',
      updatedAt: '2026-09-19T09:00:00+08:00',
      window: { from: 1788000000, to: 1789000000 },
      progress: { completed: 0, total: 10 },
      resumeable: true,
      result: null,
      error: null,
    }
    store.writeJob({ ...base, jobId: 'job_20260919_0001' })
    store.writeJob({ ...base, jobId: 'job_20260919_0002', state: 'partial' })

    assert.equal(store.readJob('job_20260919_0001')?.state, 'running')
    // partial is a first-class outcome: callers must see it was not a success.
    assert.equal(store.readJob('job_20260919_0002')?.state, 'partial')
    assert.deepEqual(store.recentJobs().map(j => j.jobId),
      ['job_20260919_0002', 'job_20260919_0001'])
  })
})

test('a missing job directory is empty, not an error', () => {
  withStore((store) => {
    assert.deepEqual(store.recentJobs(), [])
    assert.equal(store.readJob('job_20260919_9999'), null)
    assert.deepEqual(store.list(), [])
  })
})

test('job ids sort by day', () => {
  const first = makeJobId(new Date('2026-09-19T09:00:00Z'))
  const second = makeJobId(new Date('2026-09-20T09:00:00Z'))
  assert.match(first, /^job_\d{8}_\d{4}$/)
  assert.ok(first < second, `${first} should sort before ${second}`)
})
